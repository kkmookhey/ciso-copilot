# GCP Scanner Uplift — Design

> Status: approved (brainstorm 2026-05-22)
> Roadmap: major item #1 ("Scanner comprehensiveness uplift") — GCP leg.
> Predecessors: the AWS "Scan Execution v2" scanner
> (`docs/superpowers/specs/2026-05-21-scan-performance-design.md`) and the
> Azure scanner uplift
> (`docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md`).

## 1. Purpose

Bring the GCP cloud scanner up to the architecture the AWS and Azure
scanners already have: a three-stage, parallel, tier-aware pipeline that
runs on Fargate, writes through the unified entity model
(`entities` / `edges` / `findings`) via `unified_writer`, and records a
per-scan coverage map.

The current GCP scanner (`platform/lambda/shasta_runner_gcp/`) is a
legacy single-pass Lambda: it loops serially through 7 Shasta GCP
modules for **one project**, writes directly to the `findings` table
(skipping `entities` / `edges`), has no tiers, no parallelism, no
coverage map, no partial-scan state, and is invoked once per project.
This uplift replaces it and adds multi-project scanning.

## 2. Scope

**In scope:**
- Wrap the 7 existing Shasta GCP modules in the v2 three-stage pipeline.
- Fargate migration (`ciso-copilot-gcp-scan` task def).
- `unified_writer` adoption (entities + edges + findings).
- Tiering (Quick / Medium / Deep).
- A per-project footprint probe.
- The per-connection scan-row model (one scan row covers all selected
  projects).
- A **new org-level onboarding flow** so one grant enumerates every
  project under a GCP Organization.
- A web project picker (choose which projects to scan).

**Out of scope (deferred):**
- An in-repo GCP posture coverage engine (the AWS scanner's hand-written
  `coverage/` checks). GCP relies on the 7 Shasta modules for this
  uplift; an in-repo engine becomes a future slice once Shasta-module
  gaps are identified.

## 3. Key decisions (brainstorm outcomes)

1. **Scope unit = GCP project.** The project is the direct analog of the
   Azure subscription — it is the billing, IAM, enabled-API, and quota
   boundary, and every resource lives in exactly one project.

2. **Org-level onboarding primary, single-project fallback.** GCP
   customers routinely run 30–150+ projects (Google's own best practice
   is one project per application×environment). Per-project onboarding
   does not scale. The new org-level `onboard.sh` variant grants the
   reader service account read roles at the **Organization** node, so
   one grant lets the scanner enumerate every project. The existing
   single-project `onboard.sh` stays as the fallback for customers
   without a GCP Organization or without org-admin.

3. **Project discovery is hybrid (onboarding + scan time).** The
   `onboarding_gcp_complete` webhook enumerates projects once at connect
   so the web picker is populated immediately; the scanner's Stage 1
   re-enumerates on every scan and refreshes `scope.projects` so the
   picker stays current as the customer adds projects. A shared
   `project_discovery` helper keeps the logic DRY.

4. **Regions are not a scan dimension.** Shasta's GCP modules iterate
   `client.get_enabled_regions()` / `client.for_region()` internally
   (confirmed in `shasta/gcp/compute.py`). The scan unit is therefore
   `(project × module)` — no `project × region` nesting.

5. **`scan_pipeline.py` / `scan_state.py` are already shared.** The
   Azure uplift extracted these into `platform/lambda/scanner_core/`.
   GCP reuses that package — there is no Slice 0.

## 4. The GCP resource hierarchy (reference)

```
Organization          top; 1:1 with a Cloud Identity / Workspace domain. OPTIONAL.
  └─ Folder(s)         optional, nestable grouping. Up to ~10 deep.
       └─ Project      the scope unit. Every resource lives in exactly ONE project.
            └─ Resource  grouped by service; each is global, regional, or zonal.
```

Azure → GCP mapping: Tenant→Organization, Management Group→Folder,
**Subscription→Project**, Region→Region/Zone. There is no GCP equivalent
of an Azure Resource Group (GCP uses labels).

## 5. Architecture — the v2 GCP scanner

`platform/lambda/shasta_runner_gcp/` is rewritten to mirror
`shasta_runner_azure/`: a thin `main.py` orchestrator plus pure,
unit-testable adapter modules. `build.sh` copies `scanner_core/` and
`ai_scanner/` modules into `app/` at image build (same mechanism Azure
uses).

### 5.1 Adapter modules (this package — pure, no Shasta import at load)

- **`gcp_credential.py`** — builds the WIF `external_account`
  credential (lifted from the current `main.py` lines 65–89).
- **`project_discovery.py`** — Stage 1 + 2.
  - `discover_projects(project_ids, probe)` → `{project_id: state}` where
    state ∈ `active` / `empty` / `unknown`. Same anti-blind-spot
    invariant as `subscription_discovery.py`: any probe exception or
    unexpected value classifies the project `unknown`; a project is
    never silently dropped.
  - `enumerate_projects(client)` → the live project list via Shasta's
    `GCPClient.list_projects()` (the hybrid refresh; also used by the
    onboarding webhook).
- **`gcp_units.py`** — `modules_for_tier(tier)` →
  `(phase1_mods, phase2_mods)`.
- **`gcp_findings.py`** — `convert_gcp_findings(shasta_findings,
  tenant_id, project_id)` → `FindingEmission` list; `project_entity()`
  and `org_entity()` builders.
- **`gcp_id_to_entity.py`** — maps Shasta GCP resource IDs to entity
  records (mirrors `azure_id_to_entity.py`).
- **`run.py`** — Fargate entry point: reads env vars, calls `handler`.

### 5.2 Shared modules (copied in by `build.sh`)

`scan_pipeline.py` (`ConcurrencyLimiter`, `ScanUnit`, `run_units`),
`scan_state.py` (`update_scan`, `record_scan_scope`), `unified_writer.py`
(`commit_scan`, `mark_scan_failed`), `detectors/base.py` (`EntityEmission`).

### 5.3 `main.py` orchestrator — the three stages

1. **Credential setup** — build the WIF credential, build the base
   `GCPClient`.
2. **Stage 1 — project eligibility.** In **org mode**: the project list
   is `scope.selected`; the orchestrator also calls `enumerate_projects`
   and writes the refreshed list back to `scope.projects`. In
   **project mode**: the single onboarded project.
3. **Stage 2 — footprint probe.** Per project, in parallel:
   `validate_credentials()` + `discover_services()` →
   `active` / `empty` / `unknown`. `empty` projects are skipped;
   `active` and `unknown` are scanned.
4. **Stage 3 — tier-aware parallel scan.** One `ScanUnit` per
   `(project × module)` through `scanner_core.run_units`, bounded by a
   `ConcurrencyLimiter`. Two-phase Quick: Phase 1 (First Signal) commits
   early, then Phase 2 (Crown Jewel). Each unit builds its own
   `GCPClient` via `client.for_project()` so concurrent units never
   share mutable SDK state.

Findings/entities/edges are committed through `unified_writer.commit_scan`.
A **project-keyed** coverage map — `{project_id: {state, modules_run,
errors}}` — is written via `record_scan_scope`. Final status is
`partial` if any project errored, else `completed`.

### 5.4 Tier split

The 7 Shasta GCP modules: `iam`, `storage`, `networking`, `encryption`,
`compute`, `logging`, `cloud_run`.

- **Quick** — Phase 1 (First Signal): `iam`, `storage` ·
  Phase 2 (Crown Jewel): `networking`, `encryption`, `compute`.
- **Medium** — all 7.
- **Deep** — all 7 + the AI pass (`ai_scanner` GCP coverage), matching
  AWS/Azure where Deep adds the AI sweep.

`compute` is the heaviest module (iterates every enabled region
internally), so it sits in Quick Phase 2, not Phase 1 — Phase 1 stays
the fast early-commit. **Open item for the plan:** confirm Quick
wall-clock with `compute` included against the ~3–5 min target, and
confirm whether the GCP `ai_pass` has real Vertex-AI coverage or is a
no-op today.

### 5.5 Connection mode

`scope.mode` ∈ `"org"` / `"project"` is written at onboarding and drives
both the scanner's Stage 1 and the web picker. Pre-uplift connections
have no `mode` — the scanner treats a missing `mode` as `"project"`.

## 6. Org-level onboarding

### 6.1 WIF constraint

A Workload Identity Pool is a project-scoped resource — it cannot be
created at the Organization node. Org-mode onboarding therefore picks a
**host project** (the customer's current `gcloud` project) for the WIF
pool + reader service account, and grants the SA's read roles at the
**Organization** node.

### 6.2 New org variant of `cfn/gcp/onboard.sh`

The Connect page's GCP tile offers org-mode as the primary action and
single-project as a fallback link. The org script:

1. Resolves the **Organization ID** (`gcloud organizations list` —
   auto-pick if exactly one, else prompt).
2. Host project = current `gcloud` project; enables the existing API
   set there.
3. Creates the WIF pool + AWS provider in the host project (unchanged
   mechanics).
4. Creates the `ciso-copilot-reader` SA in the host project.
5. Grants read roles **at the org**:
   `gcloud organizations add-iam-policy-binding $ORG_ID` for
   `roles/iam.securityReviewer`, `roles/cloudasset.viewer`,
   `roles/logging.viewer`, plus `roles/browser` (required to *enumerate*
   projects org-wide).
6. Binds WIF impersonation on the SA.
7. POSTs back: `mode:"org"`, `org_id`, `host_project_id`,
   `host_project_number`, `sa_email`, `wif_pool`, `wif_provider`.

The legacy single-project `onboard.sh` stays as the fallback, unchanged
apart from posting `mode:"project"`.

### 6.3 `onboarding_gcp_complete` webhook

Branches on `mode`:

- **org** — store creds, then assume the WIF credential and call
  Resource Manager `projects.search` under the org; seed
  `scope.projects` (`{project_id: display_name}` for all discovered),
  `scope.selected` (default = all), `scope.mode = "org"`. Best-effort:
  if discovery fails, store creds anyway and let the scanner's Stage 1
  populate the picker (graceful degradation to scan-time discovery).
- **project** — the existing path; `scope.mode = "project"`.

## 7. Production triggers

Mirrors the Azure Slice 1b pattern.

- `onboarding_gcp_complete` and `connections_list._rescan_gcp` start
  **one** `ciso-copilot-gcp-scan` Fargate task per connection (all
  selected projects, one `scans` row) via `ecs:RunTask` — replacing the
  legacy `lambda.invoke`.
- `_rescan_gcp` becomes **tier-aware** (it currently takes no tier).
- The legacy `GcpRunner` `DockerImageFunction` is retired after the
  Fargate path is verified, via the clean two-phase deploy Azure used
  (Api stack first to drop imports, then Scan stack). The
  `shasta-runner-gcp` ECR repo stays — the Fargate task def uses it.
- Cross-stack export hygiene (the lesson from Azure's deploy deadlock):
  the task-def family is a literal, `iam:PassRole` uses a role-name
  pattern (`CisoCopilotScan-GcpScanTaskDef*`), so the GCP wiring creates
  zero new cross-stack export churn.

## 8. Web project picker

Mirrors the Azure Slice 2 subscription picker.

- `GET /connections` already returns each connection's `scope`. The
  existing `PATCH /connections/{id}` (`_update_scope`) generalises from
  subscriptions to projects: validate `selected` is a non-empty subset
  of `scope.projects`.
- The Connect page GCP connection rows get the expandable **project
  checklist** (Save → PATCH) plus the Quick/Medium/Deep `ScanPicker` —
  the checklist shows only when `scope.mode === "org"`. Single-project
  connections show just the `ScanPicker` (one project, nothing to pick).
- `ScanProgress` renders the per-project census for GCP scans from the
  project-keyed coverage map.

## 9. Slicing & build sequence

Four vertical slices, each end-to-end testable, mirroring the Azure
cadence. There is no Slice 0 — `scanner_core` already exists.

- **Slice 1a — v2 GCP scanner backend.** Adapter modules + `main.py`
  orchestrator + `ciso-copilot-gcp-scan` Fargate task def (CDK).
  Built against the *existing single-project onboarding* so it is
  verifiable end-to-end before org onboarding exists. Unit tests on
  every pure adapter. Live-verify a Quick scan on the existing GCP
  connection.
- **Slice 1b — production triggers on Fargate.** `_rescan_gcp` and
  `onboarding_gcp_complete` start the Fargate task; retire the legacy
  GCP Lambda. Still single-project. Live-verify a rescan through the
  real `POST /connections/{id}/rescan` API path.
- **Slice 2a — org-level onboarding.** The org `onboard.sh` variant,
  the `onboarding_gcp_complete` org branch, and project discovery
  (hybrid). Live-verify against a real GCP Organization.
- **Slice 2b — web project picker.** The project checklist + `PATCH`
  generalisation + `ScanProgress` per-project census. Build/typecheck
  green; visual behaviour smoke-noted (an agent cannot pass Google
  OAuth).

## 10. Testing

- Pure adapter modules get unit tests: `project_discovery`,
  `gcp_units`, `gcp_findings`, `gcp_credential`, `gcp_id_to_entity`.
- `main.py` imports `shasta.*`, so (as with Azure) it is verified
  structurally and via live scans, not in the gitignored `.venv`.
- The `scanner_core/tests/` suite must stay green.
- Each slice is gated on a live scan against a real GCP project /
  organization before it is considered done.

## 11. Open items to resolve during planning

1. The exact Quick Phase-1 / Phase-2 module split, validated against the
   ~3–5 min Quick wall-clock target with `compute` included.
2. Whether the GCP `ai_pass` has real Vertex-AI coverage or is a no-op —
   determines whether Deep's "+ AI pass" is meaningful for GCP today.
3. Whether `roles/browser` at the org is sufficient for
   `projects.search`, or a broader `roles/resourcemanager.organizationViewer`
   is needed.
4. Whether folder-scoped (not just org-scoped) grants should be a
   first-class onboarding option for customers who will not grant at the
   org root.
