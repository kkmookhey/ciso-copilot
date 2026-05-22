# Azure Scanner Uplift — Design

> Status: approved (brainstorm 2026-05-21)
> Roadmap: major item #1 ("Scanner comprehensiveness uplift") — Azure leg.
> Predecessor: the AWS "Scan Execution v2" scanner
> (`docs/superpowers/specs/2026-05-21-scan-performance-design.md`).

## 1. Purpose

Bring the Azure cloud scanner up to the architecture the AWS scanner
received in "Scan Execution v2": a three-stage, parallel, tier-aware
pipeline that runs on Fargate, writes through the unified entity model
(`entities` / `edges` / `findings`), and records a per-scan coverage map.

The current Azure scanner (`platform/lambda/shasta_runner_azure/`) is a
legacy single-pass Lambda: it loops serially through 12 Shasta Azure
modules for one subscription, writes directly to the `findings` table
(skipping `entities` / `edges`), has no tiers, no parallelism, no
coverage map, no partial-scan state, and is invoked once per
subscription. This uplift replaces it.

## 2. Scope

**In scope:** wrap the 12 existing Shasta Azure modules in the v2
pipeline; Fargate migration; `unified_writer` adoption; tiering; the
footprint probe; the per-connection scan-row model; a web subscription
picker.

**Out of scope (deferred):** an in-repo Azure posture coverage engine
(the AWS scanner's hand-written `coverage/` checks). Azure relies on the
12 Shasta modules for this uplift; an in-repo engine becomes a future
slice once Shasta-module gaps are identified.

## 3. Key decisions (brainstorm outcomes)

1. **Subscription scoping — web app, post-connect.** `onboard.sh` keeps
   granting the service principal `Reader` + `Security Reader` on every
   enabled subscription. After connecting, a web-app checklist lets the
   user choose which subscriptions to scan. Scoping is a runtime choice;
   re-pick anytime, no re-onboarding.
2. **Subscription is the atomic unit.** The footprint probe runs
   per-subscription (`active` / `empty` / `unknown`). The coverage map is
   keyed by `subscription_id`. Scan units = subscription × Shasta-module.
   Azure region appears only as a property on findings/entities, never as
   a scan-iteration axis — Shasta's `AzureClient` is subscription-scoped
   and enumerates the whole subscription at once; there is no cheap
   per-region gate analogous to AWS's `DescribeVpcs`.
3. **Wrap Shasta modules only** — no in-repo Azure coverage engine in
   this uplift (see §2).
4. **Architecture: shared scanner core + Azure adapter** (Approach B).
   Extract the genuinely cloud-agnostic pipeline modules into a shared
   package both scanners import; Azure becomes a thin cloud adapter.
   Rationale: `scan_pipeline.py` was deliberately written cloud-agnostic
   (`ScanUnit` / `run_units` / `ConcurrencyLimiter` have zero region or
   cloud assumptions), so its extraction is a clean *move*. Avoids a
   third divergent copy when GCP/Entra are uplifted. The alternative of
   keeping legacy direct-`findings` writes was rejected: it would leave
   Azure outside the unified entity model, contradicting the "findings
   carry all frameworks / no parallel surfaces" principle.
   **Note:** `scan_policy.py` is *not* shared. Despite a docstring
   claiming reusability, it is AWS-region-shaped (`build_scan_plan` takes
   `region_states`, returns a `per_region` dict of `RegionPlan`s with
   fields like `regional_shasta`). It stays AWS-specific in
   `shasta_runner`. Azure does not need it — every selected subscription
   gets the same tier-filtered module set, so Azure's tier logic is a
   trivial tier→modules map living in `azure_units.py` (§4.2).

## 4. Architecture

### 4.1 Shared scanner core — `platform/lambda/scanner_core/`

A new sibling package, copied into each scanner's Docker image by
`build.sh` (the pattern `shasta_runner` already uses to pull in
`ai_scanner` modules; the copies are `.gitignore`d). Contents:

- `scan_pipeline.py` — `run_units`, `ConcurrencyLimiter`, `ScanUnit`.
  Moves from `shasta_runner/app/`, unchanged.
- `scan_state.py` — **new.** Extracted from AWS `main.py`: the
  `scans` status/phase/stats update (`update_scan`) and the coverage-map
  writer (`record_scan_scope`). Cloud-shape-agnostic: `record_scan_scope`
  takes an already-shaped `scope` dict, so a region-keyed or
  subscription-keyed map both work. DB config is read lazily (inside the
  functions), so the module imports cleanly without env vars set.

Two modules that do **not** move:
- `scan_policy.py` — AWS-region-shaped; stays in `shasta_runner/app/`
  (see §3 decision #4).
- `unified_writer.py` — its canonical home stays `ai_scanner/`. It is
  already copied into `shasta_runner` and `entities_api` by their
  `build.sh`; the Azure scanner's `build.sh` copies it from `ai_scanner/`
  the same way. Re-homing it would touch three consumers plus
  `ai_scanner`'s own imports/tests for no functional gain.

### 4.2 Azure adapter — `platform/lambda/shasta_runner_azure/app/`

- `subscription_discovery.py` — Stage 1 reads the connection's
  `selected` subscription list. Stage 2 runs a parallel
  (`ThreadPoolExecutor`) per-subscription footprint probe: a cheap
  resource enumeration classifying each subscription `active` / `empty` /
  `unknown`. **Anti-blind-spot invariant:** any probe failure returns
  `unknown`, never `empty`.
- `azure_credential.py` — builds the service-principal credential once
  at process start. All selected subscriptions share one SP (only
  `subscription_id` varies), so the credential is connection-constant and
  safe to share across the unit threads; each unit constructs
  `AzureClient(subscription_id=…)` per subscription. No per-thread
  credential mutation.
- `azure_id_to_entity.py` — parses Azure Resource Manager IDs
  (`/subscriptions/{sub}/resourceGroups/{rg}/providers/{ns}/{type}/{name}`)
  into entity natural keys, analogous to AWS's `arn_to_entity.py`.
- `azure_units.py` — the unit builder: maps the 12 Shasta modules to
  `ScanUnit` objects per subscription, tier-filtered (see §6). Each unit
  runs one Shasta module against one subscription and returns
  `{entities, edges, findings}`.
- `run.py` — Fargate entrypoint, mirroring the AWS scanner's `run.py`.
- `main.py` — rewritten as the orchestrator: stages, two-phase Quick,
  shared-core calls. Cloud-specific; not shared.

### 4.3 Open verification (first plan task)

Shasta's `AzureClient` constructor signature — whether it accepts a
credential object or only reads `os.environ` via `DefaultAzureCredential`.
Either works given credentials are connection-constant, but the first
implementation task confirms it from Shasta source. **Shasta is
read-only — no edits.** If `AzureClient` is env-only, the one-time
`os.environ` injection at process start is the workaround (safe, because
the SP credentials are identical for every subscription).

## 5. Pipeline stages

One Fargate task scans all selected subscriptions of a connection in a
single run. Subscription is the inner parallelism axis (as region is for
AWS).

1. **Subscription eligibility** — read the connection's `selected`
   subscription list. No discovery; the user already chose.
2. **Footprint probe** — `ThreadPoolExecutor` across subscriptions →
   `active` / `empty` / `unknown`. `empty` subscriptions are skipped;
   `active` and `unknown` proceed to Stage 3.
3. **Tier-aware parallel scan** — `ScanUnit`s (subscription ×
   Shasta-module) submitted to `scanner_core.run_units`, bounded by
   per-module concurrency caps.

## 6. Tiers

Two-phase Quick mirrors the AWS first-signal → crown-jewel early-commit:
Phase 1 units run, `commit_scan` fires (findings visible early), then
Phase 2 units run and `commit_scan` fires again with the full set.
Medium and Deep are single-phase.

| Tier | Phase 1 — first signal | Phase 2 — crown jewel | Single-phase adds |
|---|---|---|---|
| **Quick** | `iam`, `governance` | `storage`, `networking`, `compute`, `encryption` | — |
| **Medium** | Quick set (both phases) | — | `databases`, `appservice`, `monitoring` |
| **Deep** | Medium set | — | `backup`, `diagnostic_settings`, `private_endpoints` |

Rationale: identity + subscription governance are fast and the
highest-signal read → commit first so findings appear within ~1 min.
Storage / networking / compute / encryption are the public-exposure and
data-at-rest crown jewels. Medium fills in resource-level posture; Deep
adds the resilience / observability long tail.

## 7. Infrastructure

- **Fargate task definition** — new `ciso-copilot-azure-scan` task def in
  `platform/lib/scan-stack.ts` (4 vCPU / 8 GB, entrypoint
  `python run.py`), same `shasta-runner-azure` ECR repo.
- **Invocation** — `onboarding_azure_complete` and the rescan path
  switch from `lambda.invoke` to `ecs:RunTask`, mirroring
  `onboarding_aws_complete`.
- **Legacy Lambda retired** — the Azure `DockerImageFunction` is removed.
  Cross-stack export removal must use the documented two-phase
  `--exclusively` deploy to avoid the CFN export-deadlock gotcha.

## 8. Data model

### 8.1 Scan-row model change

Today `onboarding_azure_complete` enqueues one `scans` row per
subscription. Under v2, one Fargate task scans all selected subscriptions
in one run → **one `scans` row per connection per scan**. `scans.tier`
and `scans.phase` are set as for AWS.

### 8.2 Coverage map (`scans.scope`)

Subscription-keyed, mirroring AWS's region-keyed map:

```json
{
  "subscriptions": {
    "<sub-id>": { "state": "active|empty|unknown",
                  "finding_count": 0, "module_results": {} }
  },
  "global": { }
}
```

### 8.3 Connection scope (`cloud_connections.scope`)

```json
{ "subscriptions": ["<all discovered>"], "selected": ["<chosen>"] }
```

`selected` defaults to all discovered subscriptions on first connect.

## 9. Web subscription picker

- A subscription checklist on the Azure connection (Connect page): every
  discovered subscription, checkboxes, Save.
- A small API to read and update `selected` on the connection scope.
- The next scan uses `selected`.
- `ScanProgress` already degrades gracefully; it gets a small adapter so
  an Azure scan renders per-subscription progress where an AWS scan
  renders per-region.

## 10. Testing

- `scan_pipeline` tests move with the module into `scanner_core/tests/`.
  (`scan_policy` and its tests stay in `shasta_runner`.)
- New Azure adapter tests: `subscription_discovery` (probe state machine
  + anti-blind-spot invariant), `azure_id_to_entity` (ARM-ID parsing),
  `azure_units` (tier filtering + two-phase split).
- **AWS-scanner regression** — after the core extraction, the existing
  102 AWS scanner tests must still pass. This gates Slice 0.
- Carried gotcha: `main.py` imports `shasta.*`, so it is not importable
  in the bare venv — verified structurally + via live scans, as for AWS.
- **E2E** — a live Azure scan needs a real Azure tenant with the SP
  onboarded. If no test tenant is available, E2E slips until one exists;
  unit + structural verification still gates the merge.

## 11. Slices

Multi-slice, like the AWS uplift. Each slice ships independently and gets
its own plan via the writing-plans skill.

- **Slice 0 — Shared core extraction.** Create `scanner_core/`; move
  `scan_pipeline` into it; add the new `scan_state` module (extracted
  from AWS `main.py`); wire `shasta_runner/build.sh` to copy
  `scanner_core/` modules into `app/`; update AWS `main.py` imports;
  regression-verify the AWS scanner (102 tests). No Azure change, no
  `ai_scanner` change. Low-risk; ships first.
- **Slice 1 — Azure v2 pipeline backend.** Azure adapter modules,
  `run.py`, Fargate task def, `ecs:RunTask` wiring, one-scan-row-per-
  connection, subscription-keyed coverage map, tiering. Scanner works
  end-to-end.
- **Slice 2 — Web subscription picker.** Checklist UI, scope API, the
  `ScanProgress` Azure adapter.
- **Deferred** — an in-repo Azure coverage engine (§2), a future slice.

## 12. Risks

- **Shasta `AzureClient` signature** — see §4.3. Mitigated by a
  read-only Shasta-source check as the first plan task; the env-only
  fallback is safe.
- **AWS-scanner regression from the core extraction** — mitigated by the
  Slice 0 test gate and by the extraction being a *move* of
  already-agnostic files.
- **Cross-stack export deadlock** when retiring the Azure Lambda —
  mitigated by the two-phase `--exclusively` deploy (documented in
  HANDOFF).
- **No test Azure tenant** — E2E verification dependency; see §10.
