# AI Security Slice 1 — Workspace shadow AI + Bedrock runtime + AI-BOM

> First slice of the AI Security sub-project. Targets the CISO worried about
> shadow AI: broadens discovery beyond Entra (Google Workspace scanner +
> AWS Bedrock runtime detection), adds new mapping rules against the
> **existing** 8 AI-specific framework packs already shipped in CME-v2,
> and ships a regulator-grade CycloneDX-ML AI-BOM export. Brainstormed
> 2026-06-04.
>
> **Spec correction 2026-06-04 (post-audit):** original draft of this
> spec claimed NIST AI RMF + OWASP LLM Top 10 were new framework packs.
> **Both are already shipped** — along with ISO 42001, EU AI Act, SOC 2
> + AI, NIST AI 600-1, OWASP Agentic, and MITRE ATLAS. `scanner_core/`
> is the canonical registry home (mirrored to 4 scanner copies via
> `sync_framework_map.py`); 13 mapping rules already populate the
> registry, including full tagging for the Entra `ai_signin_personal_tier`
> check. Slice 1's compliance work is reduced to adding ~8-10 *new
> mapping rules* for the new detectors' `check_id`s — no new packs, no
> registry rewrite. Re-framing is reflected throughout §1, §3, §4, §6.3,
> §7, §10 below.
>
> Cross-refs:
> - [`2026-05-24-compliance-mapping-engine-v2.md`](2026-05-24-compliance-mapping-engine-v2.md) — CME-v2 + `framework_registry` selectors + `framework_meta` (the substrate this slice extends)
> - [`2026-05-22-ai-visibility-v2-design.md`](2026-05-22-ai-visibility-v2-design.md) — AI Visibility v2, the Entra-only shadow-AI baseline this slice broadens
> - [`2026-05-28-mcp-connectors-design.md`](2026-05-28-mcp-connectors-design.md) — `_shared/mcp_oauth/` patterns (KMS-envelope encrypt + JIT refresh + advisory lock) that the Workspace OAuth flow reuses
> - [`2026-05-31-mcp-connectors-slice-2-design.md`](2026-05-31-mcp-connectors-slice-2-design.md) — `tenant_bot_connectors` shape that `tenant_workspace_oauth` mirrors

## 1. Goal and success criteria

**Goal:** make Shasta the AI-security platform a mid-market or enterprise
CISO will deploy for shadow-AI visibility. Today's coverage is Entra-only
sign-in detection plus the existing AI scanner (GitHub repos + cloud AI
components) feeding 8 AI-specific framework packs (NIST AI RMF, NIST AI
600-1, ISO 42001, EU AI Act, SOC 2 + AI, OWASP LLM Top 10, OWASP Agentic,
MITRE ATLAS) via 13 already-populated mapping rules in
`scanner_core/ai_framework_registry.json`. Slice 1 broadens discovery to a
second identity surface (Google Workspace), adds AWS Bedrock runtime
detection through the existing `event_router` (the inventory-side
`bedrock_model` entity kind already exists), wires the new detectors'
`check_id`s into the existing framework packs via new mapping rules, and
delivers a CycloneDX-ML AI-BOM export the CISO can hand to their auditor.

**Success criteria:**

1. A CISO connects Google Workspace to Shasta via OAuth admin consent in
   under 90 seconds, and within five minutes sees personal-tier sign-ins to
   external AI apps (chatgpt.com, claude.ai, perplexity.ai, etc.) listed in
   `/findings` and counted in a new **Shadow AI** row on `/ai`.
2. Within an existing AWS-connected tenant, every `bedrock-runtime:InvokeModel`
   (and sibling operations) call hitting the customer's CloudTrail surfaces
   in Shasta within 60 seconds as a `bedrock_invocation` rollup (per
   principal, per model, per day). Untracked Bedrock workloads surface as
   `aws_bedrock_invoke_high_volume` findings.
3. The existing AI-family framework tiles on `/ai` (NIST AI RMF + OWASP
   LLM Top 10 + EU AI Act + NIST AI 600-1 + ISO 42001 + MITRE ATLAS) all
   incorporate the new detectors' findings in their score % within one
   scan cycle after Slice 1 ships — i.e., the new mapping rules are
   wired and `compliance_summary` returns updated numbers.
4. Clicking **Export AI-BOM** on `/ai` downloads a valid CycloneDX-ML 1.6
   JSON file (validates against the public CycloneDX JSON schema) that
   inventories every AI entity the tenant has, with dependencies + AI-attached
   findings as vulnerabilities.
5. The shared `framework_meta` duplication between `ai_summary/` and
   `compliance_summary/` is consolidated (single canonical source — most
   likely `scanner_core/framework_meta.py` to live next to the existing
   `scanner_core/framework_registry.py` master + mirror script). The
   5-way duplication of `framework_registry.py` itself stays — that's by
   design (Lambda zip bundling needs the file local; `sync_framework_map.py`
   mirrors from the canonical `scanner_core/` master at build time). New
   scanners (Workspace) get added to the mirror script's target list.
6. Findings broadcast pipeline works for the new detectors without code
   change in `findings_subscriber/`. None of the Slice 1 default
   severities (high / medium / informational) qualify under the existing
   `publish_if_critical` gate; the success criterion is that *if* a
   detector is reconfigured to emit at `critical` (per-tenant
   `evidence_packet` override or future Slice 4 detectors), broadcast
   fires end-to-end. Verified in §10.3 smoke #6 by injecting a synthetic
   critical-fail finding directly into Aurora.
7. Post-merge re-smoke completes the end-to-end flow on KK's own Workspace
   tenant + an AWS account, and AI-BOM JSON validates clean against the
   CycloneDX-ML 1.6 schema.

## 2. Why this design (and what was reconsidered)

- **Discovery + compliance together, not sequenced.** A discovery-first
  slice (3 new shadow-AI sources, compliance later) was considered; so was
  a compliance-first slice (framework packs + AI-BOM, new sources later).
  Both fail the demo test: discovery-only gives the CISO new data with no
  artifact to hand the auditor; compliance-only populates 25% of a
  framework dashboard. The chosen shape lands one demo beat per pillar
  (Workspace = discovery wow, Bedrock = platform-team wow, NIST AI RMF =
  CISO wow, AI-BOM = regulator wow) and they converge on the same page.
- **No `_shared/cme/` hoist needed — `scanner_core/` already is the
  canonical home.** Post-audit: `framework_registry.py` lives in
  `scanner_core/` as the master + is mirrored to ai_scanner +
  shasta_runner_azure + shasta_runner_entra + shasta_runner_gcp via
  `sync_framework_map.py`. The 5-way duplication is intentional (Lambda
  zips need the file local; mirror script keeps them in sync). What
  Slice 1 actually does: (a) add `shasta_runner_workspace` to the
  mirror target list, (b) `event_router` imports directly from
  `scanner_core/` (already a Lambda layer / shared module pattern that
  exists in this codebase), (c) consolidate the genuine duplication —
  `framework_meta.py` in `ai_summary/` and `compliance_summary/` —
  into one canonical copy (likely `scanner_core/framework_meta.py`).
- **Workspace OAuth, not service-account JSON.** Service-account JSON with
  Domain-Wide Delegation is the lower-friction implementation path but
  pushes JSON-handling friction onto the customer (every Workspace admin
  has a story about pasted JSON files going wrong). OAuth admin consent
  is the better customer UX; cost is a 2-4 week Google verification queue
  for restricted scopes, which has to kick off day 1 of implementation. The
  scopes are read-only so verification is straightforward; the calendar
  risk is acknowledged.
- **Bedrock InvokeModel via existing `event_router`, no per-prompt
  capture.** Per-request model-invocation logging (the equivalent of S3
  data events) is opt-in on the customer's account and emits the prompt +
  response text. Slice 1 stays on management events (always available, no
  customer config) and tracks who invoked which model how often. Per-prompt
  content analysis (data exfil detection) is a Slice 3+ conversation that
  requires legal review on prompt-content storage.
- **Daily rollup for Bedrock invocations, not per-call.** A busy Bedrock
  tenant could emit millions of InvokeModel calls per day. Storing one
  finding per call would blow up `findings` table size and the broadcast
  pipe. Per-day-per-principal-per-model rollup gives the same security
  signal at 1000x less storage.
- **CycloneDX-ML 1.6 over SPDX-AI 3.0 for Slice 1.** Both are credible AI-BOM
  formats. CycloneDX has stronger US security-tool mindshare and a mature
  Python library (`cyclonedx-python-lib`). SPDX-AI has more EU regulator
  mindshare but a less mature Python toolchain. CycloneDX in Slice 1; SPDX
  added in Slice 5 if customers request it. ~1 extra day to support both
  formats from the same internal data model when that time comes.
- **Use `cyclonedx-python-lib`, do not hand-roll JSON.** Library handles
  schema validation, `bom-ref` uniqueness, serial-number generation,
  timestamp formatting, and forward compatibility when CycloneDX-ML moves
  to 1.7 / 2.0. Saves ~200 LOC. The Shasta-specific mapping (entities →
  components, edges → dependencies, AI-attached findings → vulnerabilities)
  stays in `ai_bom_export/`. Library version + spec capability pinned in
  the implementation plan after verifying the latest release supports
  CycloneDX-ML 1.6 components — not invented here.
- **Per memory ("AI is a lens, not a silo") nothing splits to `/ai-only`
  surfaces.** Shadow-AI findings flow through the shared `/findings` table
  with normal severity / framework / risk-register treatment. The `/ai`
  page extends with two new rows (Shadow AI + Compliance) but does not
  fork into its own findings universe.

## 3. Scope

### In scope

- New container Lambda `shasta_runner_workspace/` — Google Workspace
  audit-log + admin-directory scanner. OAuth admin consent flow,
  KMS-envelope token storage, JIT refresh.
- Extension to existing `event_router/` for Bedrock InvokeModel +
  Converse + InvokeAgent + Retrieve event handling. Per-principal
  per-model per-day rollup writer.
- New `ai_bom_export/` Lambda behind `GET /v1/ai/bom?format=cyclonedx`.
  Streams CycloneDX-ML 1.6 JSON. Uses `cyclonedx-python-lib`.
- Schema migration `016_workspace_connector.sql` — new
  `tenant_workspace_oauth` table.
- **New mapping rules** added to `scanner_core/ai_framework_registry.json`
  (~8-10 new rules, one per new `check_id`): tag the new detectors'
  findings against the **already-shipped** NIST AI RMF, NIST AI 600-1,
  ISO 42001, EU AI Act, OWASP LLM Top 10, MITRE ATLAS. Pattern mirrors
  the existing rule `ai_signin_personal_tier_controls` (id, when, add_frameworks).
- **Update `sync_framework_map.py` target list** to include
  `shasta_runner_workspace` so the registry mirrors at build time.
- **Consolidate `framework_meta` duplication** — move from `ai_summary/`
  and `compliance_summary/` into one canonical location next to
  `scanner_core/framework_registry.py`; both consumers import from there.
- UI extension to `/ai` (`AISummary.tsx`):
  - New **Shadow AI** row (3 count tiles)
  - **Export AI-BOM** button in page header
  - (No new Compliance row needed — the existing AI-family framework
    tiles on `/ai` already render NIST AI RMF / OWASP LLM Top 10 / EU
    AI Act / etc. via `compliance_summary` + `framework_meta.ai_family_meta()`;
    they'll auto-pick-up the new findings via the new mapping rules.)
- UI extension to `/connect-clouds` — Google Workspace tile + OAuth
  callback handling.
- Test coverage per §10. Manual smoke on KK's Workspace tenant + an
  AWS account.

### Out of scope (parked for Slice 2+)

- M365 Copilot usage reports (Slice 2)
- Slack / Notion / Atlassian AI audit logs (Slice 2)
- Mapping-rule deepening — broader coverage of NIST AI RMF / EU AI Act
  controls against existing detector check_ids (Slice 2 — incremental
  ruleset expansion on the already-shipped packs)
- AWS Bedrock per-prompt content capture + data-exfil detection (Slice 3)
- AI vendor risk inventory (Cranium-style) (Slice 3)
- Whitney prompt-injection Semgrep rules → OWASP LLM01 findings (Slice 4 —
  this bridges to the platform-team buyer)
- Monthly AI Posture board PDF + AI Risk Register section + daily
  Slack digest (Slice 5)
- SPDX-AI 3.0 export format (Slice 5 if customers ask)
- Integration tests against real Postgres (cross-cutting BACKLOG §D item;
  not blocking this slice)

## 4. Components and architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                Shasta                                    │
│                                                                          │
│  ┌────────────────────┐    ┌────────────────────┐                        │
│  │ shasta_runner_     │    │ event_router       │                        │
│  │   workspace        │    │  (Bedrock rules)   │                        │
│  │  (NEW Fargate)     │    │  (extended)        │                        │
│  └────────┬───────────┘    └─────────┬──────────┘                        │
│           │                          │                                   │
│           │   findings + entities    │   findings + entities             │
│           ▼                          ▼                                   │
│  ┌──────────────────────────────────────────────────────┐                │
│  │ scanner_core/  (already canonical)                   │                │
│  │  framework_registry.apply() tags findings.frameworks │                │
│  │   (selectors: check_id_eq, check_id_glob, domain,    │                │
│  │    resource_type_glob, ai_touching, evidence_packet_eq)│              │
│  │  + ai_framework_registry.json                        │                │
│  │   (8 AI packs + ~20 security/industry packs already  │                │
│  │    populated; 13 mapping rules → +8-10 NEW rules     │                │
│  │    in this slice for gws_* + aws_bedrock_* check_ids)│                │
│  │  + framework_meta.py (NEW — consolidated from        │                │
│  │    ai_summary/ + compliance_summary/ duplication)    │                │
│  └─────────────────────────┬────────────────────────────┘                │
│                            │                                             │
│                            ▼                                             │
│  ┌──────────────────────────────────────────────────────┐                │
│  │ findings  +  entities  +  edges                      │                │
│  │ (Aurora; existing schema, no shape changes)          │                │
│  └────────┬─────────────────────────┬───────────────────┘                │
│           │                         │                                    │
│           ▼                         ▼                                    │
│  ┌────────────────────┐   ┌────────────────────┐                         │
│  │ compliance_summary │   │ ai_bom_export      │  ← NEW                  │
│  │ (existing)         │   │ (cyclonedx-python- │                         │
│  │                    │   │  lib)              │                         │
│  └────────┬───────────┘   └─────────┬──────────┘                         │
│           │                         │                                    │
│           ▼                         ▼                                    │
│  ┌──────────────────────────────────────────────────────┐                │
│  │ /ai  AISummary.tsx                                   │                │
│  │  Exposure Score · Fail/Partial/Pass · Source         │                │
│  │  + NEW Shadow AI row                                 │                │
│  │  + NEW Compliance row (NIST AI RMF, OWASP LLM)       │                │
│  │  + NEW [Export AI-BOM] button                        │                │
│  └──────────────────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────────┘
```

### Trigger model

- **Workspace scanner**: on-demand from `connections_list` (Connect button)
  + hourly via EventBridge schedule. Workspace audit logs have ~10-30 min
  ingestion delay; hourly cadence is appropriate. Cursor-paginated on
  `id.time` so re-runs are deterministic.
- **Bedrock detector**: real-time via EventBridge → `event_router` Lambda.
  CloudTrail management events flow through the customer's existing
  EventBridge integration (no new customer config). Per-call entity
  upserts (`bedrock_model`, `bedrock_invocation` rollup row +
  `attributes.invocation_count++`) happen synchronously inside the
  router. A separate EventBridge-scheduled invocation at 00:05 UTC
  emits the `aws_bedrock_invoke_high_volume` finding once per
  (principal, modelId, day) above the configured threshold — this is
  the only piece that runs daily; the rollup row itself updates in
  real-time on every event.
- **AI-BOM export**: synchronous API call from the UI, on-demand only. No
  pre-computation, no caching in Slice 1.

### Reused infrastructure

| Need | Reused from |
|---|---|
| OAuth token storage (KMS envelope, per-row data key) | `_shared/mcp_oauth/crypto.py` (MCP Slice 1) |
| JIT refresh (advisory lock + re-read + UPDATE in one txn) | `_shared/mcp_oauth/admin_session.py` (MCP Slice 2 + 2026-06-03 fix) |
| Findings broadcast on CRITICAL | `_shared/broadcast_fanout.publish_if_critical` (MCP Slice 2.1; post-commit fix 2026-06-03) |
| Framework selectors + apply engine | `scanner_core/framework_registry.py` (already canonical; mirrored to all scanners via `sync_framework_map.py`) |
| 8 AI-family + ~20 security/industry framework packs | `scanner_core/ai_framework_registry.json` (already shipped; Slice 1 adds new mapping rules only) |
| `framework_meta` for UI display | NEW `scanner_core/framework_meta.py` (consolidates `ai_summary/` + `compliance_summary/` duplication) |
| Findings + entities + edges schema | Existing — no shape changes |
| Fargate container scanner pattern | `shasta_runner_azure` + `shasta_runner_gcp` |

## 5. Data model & migrations

### Migration `016_workspace_connector.sql`

```sql
CREATE TABLE tenant_workspace_oauth (
    tenant_id              UUID         NOT NULL REFERENCES tenants(tenant_id),
    workspace_domain       TEXT         NOT NULL,
    super_admin_email      TEXT         NOT NULL,
    access_token_enc       BYTEA,
    access_data_key_ct     BYTEA,
    access_expires_at      TIMESTAMPTZ,
    refresh_token_enc      BYTEA,
    refresh_data_key_ct    BYTEA,
    scopes                 TEXT[]       NOT NULL DEFAULT '{}',
    installed_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    revoked_at             TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, workspace_domain)
);

CREATE INDEX idx_tenant_workspace_oauth_active
  ON tenant_workspace_oauth (tenant_id)
  WHERE revoked_at IS NULL;
```

One row per (tenant_id, workspace_domain). Multi-workspace per tenant is
schema-supported but Slice 1 UI only handles single-workspace; multi-domain
is a Slice 3 conversation.

`cloud_connections` rows for Workspace use `kind='google_workspace'`,
`account_id=workspace_domain`, `evidence_packet` includes `workspace_oauth_id`
pointing to the row above. No `cloud_connections` schema change.

### No `findings` / `entities` / `edges` shape changes

All new detectors emit existing kinds + columns. Schema gotchas from
CLAUDE.md remain load-bearing:
- `findings.frameworks` is a JSON object (`{}`), not array
- `findings.check_id` is the rule identifier (no `kind` column)
- `findings.status` enum: `fail` / `pass` / `partial` / `not_assessed` /
  `not_applicable` (no `open`)
- `entities` has no `parent_id`; containment goes through `edges.kind`
- `findings.conn_id` FK → `cloud_connections.conn_id`

New entity kinds emitted (all fit the existing `entities.kind` text column):
- `ai_saas_app` (already exists — Workspace OAuth-grant detector emits these)
- `ai_user_signin` (already exists — Workspace personal-tier sign-ins)
- `ai_model` with attribute `provider='google'` (Gemini license inventory)
- `bedrock_model` (already exists in CME-v2 selector — Bedrock detector
  emits these)
- `bedrock_invocation` (NEW — per-day-per-principal-per-model rollup)
- `iam_principal` (already exists — Bedrock detector references)

New edge kinds (text column, no schema change):
- `workspace_user --grants-> ai_saas_app` (OAuth grant)
- `iam_principal --uses-> bedrock_model`

## 6. Detectors

### 6.1 Google Workspace scanner

OAuth scopes (all read-only, all admin-restricted, requires Google
verification):

| Scope | Purpose |
|---|---|
| `https://www.googleapis.com/auth/admin.reports.audit.readonly` | login + token + drive + saml audit-log events |
| `https://www.googleapis.com/auth/admin.directory.user.readonly` | user list + license assignments |
| `https://www.googleapis.com/auth/admin.directory.domain.readonly` | domain list (multi-domain workspaces; informational) |

First scan: pulls last 30 days of audit log events. Subsequent scans: pulls
from `MAX(id.time)` per detector, +1ms to avoid replay. Workspace audit log
entries are immutable + monotonically timestamped so cursor pagination is
safe.

Detectors emitted (framework tags auto-applied at write time by
`scanner_core.framework_registry.apply()` via the new mapping rules from §7):

| `check_id` | Trigger | Severity | Entity emitted | Frameworks tagged |
|---|---|---|---|---|
| `gws_ai_signin_personal_tier` | Login audit shows employee `@<workspace_domain>` signing into a known AI SaaS (`ai_saas_catalog.json` already seeded from Entra path: chatgpt.com / claude.ai / perplexity.ai / gemini.google.com / copilot.microsoft.com / huggingface.co / replicate.com / etc.) via personal Google account (not Workspace SSO) | high | `ai_user_signin` | `nist_ai_rmf: [GOVERN-3.2, MAP-2.1]`, `owasp_llm_top10: [LLM06]`, `iso_42001: [A.6.2.1]` (cross-tag for Slice 2) |
| `gws_ai_oauth_grant` | Token audit shows Workspace user granted OAuth scopes to a third-party AI app | high if scope ∈ {`drive.readonly`, `gmail.readonly`, `chat.spaces.readonly`}; medium otherwise; informational-only (not emitted) if scope ∈ {`userinfo.email`, `openid`} | `ai_saas_app` + `edge: workspace_user --grants-> ai_saas_app` | `nist_ai_rmf: [MAP-4.1, MANAGE-1.3]`, `owasp_llm_top10: [LLM06]` |
| `gws_drive_shared_to_ai_domain` | Drive audit shows file shared externally to an AI vendor domain (catalog: `@openai.com`, `@anthropic.com`, `@huggingface.co`, `@replicate.com`, etc.) | high | finding only (no new entity — references the existing Drive file by URN in `evidence_packet`) | `nist_ai_rmf: [MAP-4.1]`, `owasp_llm_top10: [LLM06]` |
| `gws_gemini_assigned` | Directory shows user has Gemini Pro / Duet AI license | informational (inventory) | `ai_model` (kind=`gemini`, attributes.provider=`google`, attributes.tier=`pro` or `duet`) | `nist_ai_rmf: [MAP-1.1]` (inventory) |

### 6.2 Bedrock InvokeModel detector (extension to `event_router`)

New EventBridge rule added to the existing pattern (per CLAUDE.md gotcha:
NO `source` filter, filter on `detail-type` + `detail.eventName` only):

```yaml
EventPattern:
  detail-type: ["AWS API Call via CloudTrail"]
  detail.eventName:
    - InvokeModel
    - InvokeModelWithResponseStream
    - Converse
    - ConverseStream
    - InvokeAgent
    - Retrieve
    - RetrieveAndGenerate
```

Per-event processing in `event_router.handler`:

1. Look up tenant by `recipientAccountId` → `cloud_connections.account_id`
   (existing path; same gotcha-aware resolution).
2. Extract `userIdentity.arn`, `requestParameters.modelId`, `awsRegion`,
   `sourceIPAddress`, `eventTime`.
3. Upsert `bedrock_model` entity (one per (modelId, region), tenant-scoped).
4. Upsert `bedrock_invocation` rollup entity (one per (principal, modelId,
   day, region); `attributes.invocation_count++`). Daily rollup keys are
   tenant-scoped; rollover at 00:00 UTC.
5. Upsert edge: `iam_principal --uses-> bedrock_model`.
6. Emit findings per the table below.

| `check_id` | Trigger | Severity | Frameworks tagged |
|---|---|---|---|
| `aws_bedrock_invoke_unsanctioned` | Invoking principal not in `cloud_connections.evidence_packet.bedrock_allowed_principals` (when that list is set; if unset, never emits) | medium | `nist_ai_rmf: [GOVERN-1.1, MANAGE-1.3]`, `owasp_llm_top10: [LLM08]` |
| `aws_bedrock_invoke_high_volume` | Daily rollup (00:05 UTC EventBridge) flags any (principal, modelId) with > 10000 invocations/day; threshold configurable per tenant in `cloud_connections.evidence_packet.bedrock_high_volume_threshold` | medium | `nist_ai_rmf: [MEASURE-2.3, MANAGE-2.2]` |
| `aws_bedrock_model_inventory` | First sighting of (modelId, region) tuple for a tenant | informational (inventory; not surfaced in /findings unless filtered) | `nist_ai_rmf: [MAP-1.1]` |
| `aws_bedrock_invoke_cross_region` | Principal in `awsRegion=A` invokes model in `awsRegion=B` where A ≠ B | low | `nist_ai_rmf: [MAP-4.1]` (data residency signal) |

Rate-limiting: per-tenant cap of 1000 events/minute through `event_router`
for Bedrock-class events (separate counter from SOC events). Overflow →
SQS DLQ + a "rate-limited" warning on the Bedrock detector card on `/ai`.
The cap is high enough not to trip in normal use; protects the router from
a runaway tenant. Configurable per tenant in
`cloud_connections.evidence_packet.bedrock_router_rate_limit`.

### 6.3 Framework auto-tagging at emit time

Today's pattern: scanner emits raw finding → `framework_registry.apply()`
enriches `findings.frameworks` JSON object via selector matching → INSERT.
The existing rule for `ai_signin_personal_tier` is the template:

```json
{
  "id": "ai_signin_personal_tier_controls",
  "when": {"check_id_eq": "ai_signin_personal_tier"},
  "add_frameworks": {
    "nist_ai_rmf":   ["GOVERN 3.2", "GOVERN 6.1"],
    "nist_ai_600_1": ["NIST.AI.600-1:2.4", "NIST.AI.600-1:2.8",
                      "NIST.AI.600-1:2.9", "NIST.AI.600-1:2.12"],
    "eu_ai_act":     ["Article 9", "Article 26"],
    "owasp_llm_top10": ["LLM02:2025"],
    "mitre_atlas":   ["AML.T0057"]
  }
}
```

Slice 1 adds one new rule per new `check_id` (8 total: 4 Workspace +
4 Bedrock). Selectors used:

- `check_id_eq` — exact match (used for all 8 new `check_id`s)
- `check_id_glob` — already wired for existing `sca_vuln:*` rules
- ~~`ai_touching` for Bedrock entity-kind inventory~~ — **dropped**.
  Per `docs/codebase/FINDINGS.md` §D, the `ai_touching` selector never
  fires because stub entities aren't backfilled into `entity_index`
  (`unified_writer.py:60-63`). All 8 new Slice 1 mappings use
  `check_id_eq` instead. Fixing `ai_touching` is a separate cross-cutting
  refactor and out of Slice 1 scope.

No new selector types in Slice 1. Full mapping rules (the ~8 new entries
plus the existing 13) live in `scanner_core/ai_framework_registry.json`.

## 7. Mapping rules added to existing framework packs

The 8 AI-family packs (NIST AI RMF, NIST AI 600-1, ISO 42001, EU AI Act,
SOC 2 + AI, OWASP LLM Top 10 2025, OWASP Agentic, MITRE ATLAS) are already
in `scanner_core/ai_framework_registry.json` with full `frameworks{}`
metadata + per-framework `rewrite_rules` + `control_descriptions`. The
20 security/industry packs (SOC 2, ISO 27001, FedRAMP, CIS AWS/Azure/GCP,
NIST 800-53, AWS FSBP, MS Cloud Security Benchmark, PCI DSS, HIPAA) are
already there too via `framework_meta.py`.

**Slice 1 adds ~8 new entries to the `rules` array** in
`scanner_core/ai_framework_registry.json`, one per new `check_id`:

| Rule `id` | `when.check_id_eq` | Frameworks tagged (control IDs) |
|---|---|---|
| `gws_ai_signin_personal_tier_controls` | `gws_ai_signin_personal_tier` | Same as existing `ai_signin_personal_tier_controls` (Workspace mirrors Entra: NIST AI RMF GOVERN 3.2/6.1, NIST AI 600-1 §2.4/2.8/2.9/2.12, EU AI Act Article 9/26, OWASP LLM02:2025, MITRE ATLAS AML.T0057) |
| `gws_ai_oauth_grant_controls` | `gws_ai_oauth_grant` | NIST AI RMF MAP 4.1 + MANAGE 1.3; EU AI Act Article 26; OWASP LLM06:2025 |
| `gws_drive_shared_to_ai_domain_controls` | `gws_drive_shared_to_ai_domain` | NIST AI RMF MAP 4.1 + GOVERN 6.1; NIST AI 600-1 §2.4 (data leakage); EU AI Act Article 10; OWASP LLM02:2025 |
| `gws_gemini_assigned_controls` | `gws_gemini_assigned` | NIST AI RMF MAP 1.1 (inventory); ISO 42001 A.6.2.1 |
| `aws_bedrock_invoke_unsanctioned_controls` | `aws_bedrock_invoke_unsanctioned` | NIST AI RMF GOVERN 1.1 + MANAGE 1.3; EU AI Act Article 9; OWASP LLM08:2025 (Excessive Agency) |
| `aws_bedrock_invoke_high_volume_controls` | `aws_bedrock_invoke_high_volume` | NIST AI RMF MEASURE 2.3 + MANAGE 2.2; OWASP LLM10:2025 (Unbounded Consumption) |
| `aws_bedrock_model_inventory_controls` | `aws_bedrock_model_inventory` | NIST AI RMF MAP 1.1; NIST AI 600-1 §2.4 |
| `aws_bedrock_invoke_cross_region_controls` | `aws_bedrock_invoke_cross_region` | NIST AI RMF MAP 4.1 (data residency); EU AI Act Article 9 |

Exact control IDs are interpretation calls subject to KK review during
implementation. Pattern follows the existing 13 rules (id, when,
add_frameworks). The rule format is schema-validated at module load via
the existing `validate_registry()`.

No new framework definitions. No new selectors. No new metadata. The
8 packs render on `/ai` today; after Slice 1 ships these rules, the new
detector findings start contributing to each pack's score.

## 8. AI-BOM export

### 8.1 Endpoint

`GET /v1/ai/bom?format=cyclonedx`

- Auth: existing JWT path (tenant from `claims.sub` → `users.tenant_id`
  per the canonical resolver in `voice_session._subject_from_claims` /
  `events_list._resolve_tenant_id`).
- Response: `Content-Type: application/vnd.cyclonedx+json; version=1.6`,
  `Content-Disposition: attachment;
   filename="shasta-ai-bom-<tenant-slug>-<YYYY-MM-DD>.cdx.json"`.
- Streaming not required (BOM size is bounded by tenant inventory; expected
  < 5MB for any realistic tenant).
- Format query param exists for future extensibility (`spdx-ai` in Slice 5).
  Slice 1 only supports `cyclonedx`. Unknown format → 400.

### 8.2 Library

`cyclonedx-python-lib` (the official CycloneDX OSS Python library).
Version pinned in `ai_bom_export/requirements.txt` at implementation time
after verifying current release supports CycloneDX-ML 1.6 components
(`MachineLearningModel`, ML-specific `properties`, ML dependency edges).
No invention here — implementation plan verifies the version.

The library handles:
- JSON shape & schema conformance (built-in schema validation)
- `bom-ref` uniqueness enforcement
- Serial number (`urn:uuid:...`) generation
- Timestamp formatting (ISO-8601 UTC)
- Forward compatibility when CycloneDX-ML moves to 1.7 / 2.0

What stays in `ai_bom_export/main.py`:

```python
# Pseudocode, not final
def handler(event, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})
    fmt = event.get("queryStringParameters", {}).get("format", "cyclonedx")
    if fmt != "cyclonedx":
        return _resp(400, {"error": "unknown_format", "supported": ["cyclonedx"]})

    entities = _select_ai_entities(tenant_id)         # WHERE kind IN _AI_RESOURCE_KINDS
    edges    = _select_ai_edges(tenant_id)            # WHERE source OR target IN ai entities
    findings = _select_ai_findings(tenant_id)         # WHERE attached to AI entity AND
                                                      # (frameworks ? 'owasp_llm_top10' OR check_id LIKE 'sca_vuln:%')

    bom = Bom()
    bom.metadata.tools.add(Tool(vendor="Transilience", name="Shasta", version=_git_sha()))

    for e in entities:
        bom.components.add(_entity_to_component(e))   # entity.kind → MachineLearningModel | Application | Library

    for edge in edges:
        bom.dependencies.add(Dependency(ref=edge.source, depends_on=[edge.target]))

    for f in findings:
        bom.vulnerabilities.add(_finding_to_vulnerability(f))

    return _resp(200, bom.to_json(), headers={
        "Content-Type": "application/vnd.cyclonedx+json; version=1.6",
        "Content-Disposition": f'attachment; filename="shasta-ai-bom-{tenant_slug}-{date_str}.cdx.json"',
    })
```

### 8.3 Mapping rules (`entities.kind` → CycloneDX `component.type`)

| `entities.kind` | CycloneDX `type` |
|---|---|
| `bedrock_model`, `ai_model`, `sagemaker_model`, `azure_openai_deployment`, `vertex_endpoint`, `gemini` | `machine-learning-model` |
| `ai_agent`, `ai_mcp_server` | `application` |
| `ai_framework` | `library` |
| `ai_saas_app` | `application` (with `supplier.name` = SaaS vendor) |
| `ai_tool` | `library` |
| `ai_vector_db` | `data` |
| `ai_prompt`, `ai_embedding` | `data` |
| `bedrock_invocation` | excluded from BOM (operational rollup, not inventory) |
| `ai_user_signin` | excluded from BOM (transient event, not asset) |

Custom Shasta properties on every component:
- `shasta:kind`        = `entities.kind`
- `shasta:detector_id` = `entities.detector_id`
- `shasta:resource_arn` = `entities.resource_arn`
- `shasta:discovered_at` = `entities.created_at` (ISO-8601)

### 8.4 Findings → vulnerabilities filter

Include a finding in `vulnerabilities[]` iff:
- It has `frameworks ? 'owasp_llm_top10'`, OR
- Its `check_id` matches `sca_vuln:*` AND it has an AI-touching entity in
  its attached entities

Severity mapping: Shasta `severity` (critical/high/medium/low/informational)
→ CycloneDX `rating.severity` (critical/high/medium/low/info).

## 9. UI surface

### 9.1 `/ai` (`AISummary.tsx`) extensions

Existing layout, top to bottom:
1. Title row
2. AI Exposure Score donut
3. Fail / Partial / Pass tiles (clickable → `/findings?status=…`)
4. Source tiles (AWS / Azure / Code / Entra, clickable → `/findings?cloud=…`)

New rows added below, in order:

**5. Shadow AI row** — three tiles:
- `Personal-tier sign-ins` — count of all `*ai_signin_personal_tier`
  findings (Entra + Workspace, last 30 days), drills into
  `/findings?check_id_prefix=ai_signin_personal_tier`
- `OAuth grants to AI vendors` — count of `gws_ai_oauth_grant`, drills
  into filtered findings
- `Unsanctioned Bedrock invocations` — count of
  `aws_bedrock_invoke_unsanctioned`, drills into filtered findings

**6. (No new Compliance row.)** The existing AI-family framework tiles
already on `/ai` (rendered via `compliance_summary` + `framework_meta.ai_family_meta()`)
auto-incorporate the new findings via the new mapping rules from §7. No
React work needed beyond ensuring the existing tiles re-fetch after a
fresh scan — they already do via `useEffect` on tenant/scan changes.

**Page header**: new `[Export AI-BOM]` button (top-right). Click triggers
`GET /v1/ai/bom?format=cyclonedx` → browser-side blob download as
`shasta-ai-bom-<tenant>-<date>.cdx.json`. Zero new dependencies; uses the
same blob-download path as the existing PDF/CSV export on `/findings`
(shipped 2026-06-03).

### 9.2 `/connect-clouds` extensions

New tile: **Google Workspace** (mirrors AWS / Azure / Entra / GCP card
pattern in `web/src/routes/ConnectClouds.tsx`):
- Card shows logo + "Google Workspace" + status badge
- Disconnected state: `[Connect with Google]` button → kicks off OAuth
  admin-consent flow → redirects to Google → callback to
  `https://api.shasta.io/v1/connectors/callback/workspace`
- Pending verification state: card greys out + tooltip "Awaiting Google
  app verification — your workspace will connect automatically once
  Google approves" (covers the calendar-risk window during Slice 1
  rollout)
- Connected state: green check + workspace domain + "Last scan: Nh
  ago" + `[Rescan]` / `[Disconnect]` buttons

### 9.3 No new web routes

Findings stay on `/findings` (filtered by `check_id_prefix` or
`framework`). AI inventory stays on `/ai/inventory`. Per the "AI is a
lens, not a silo" memory, this slice deliberately does not fork into
`/ai-only` surfaces.

## 10. Testing strategy

### 10.1 Unit tests

**`framework_meta` consolidation**:
- Pure refactor — move `FRAMEWORK_META` dict from `ai_summary/` and
  `compliance_summary/` into `scanner_core/framework_meta.py`. Both
  consumers re-import from the new location. Existing tests for
  `compliance_summary` + `ai_summary` stay green = consolidation is clean.
- `sync_framework_map.py` updated to include `shasta_runner_workspace`
  in its mirror target list — verify the registry JSON propagates to
  the new scanner image at build time.

**New mapping rules**:
- `validate_registry()` (existing function) runs at module import; the
  ~8 new rules must pass schema validation (id present, when keys ⊆
  `_KNOWN_SELECTORS`, add_frameworks keys ⊆ known framework keys,
  control IDs resolve through `rewrite_rules` where shorthand is used).
- New registry-application unit tests, one per new rule:
  - Feed synthetic finding with each new `check_id` → assert
    `findings.frameworks` matches the rule's `add_frameworks` block
    after `apply()` runs.
- 8 new tests total (one per `check_id`), mirroring the existing test
  pattern for `ai_signin_personal_tier_controls`.

**Workspace scanner**:
- Mock Google API SDK responses with fixture audit-log payloads.
- Per-detector unit test: feed fixture event → assert finding +
  entity emitted with expected shape.
- Cursor pagination test: scan 1 (pulls 30d), scan 2 (pulls from
  MAX(id.time)+1ms), assert no overlap.

**Bedrock detector**:
- Mock EventBridge event with realistic CloudTrail payload.
- Per-detector unit test for each of the 4 new `check_id`s.
- Daily rollup test: feed 24h of mock events, assert exactly one
  `bedrock_invocation` rollup row per (principal, modelId, day).

**AI-BOM export**:
- Library schema validation runs internally; supplement with semantic
  golden-file tests (not byte-exact — libraries change formatting between
  minor versions).
- Two golden fixtures: (a) tenant with full AI inventory + AI findings,
  (b) tenant with empty inventory (assert valid empty BOM).
- Format query-param test: `?format=cyclonedx` works, `?format=spdx-ai`
  returns 400, unset defaults to cyclonedx.

### 10.2 Integration tests

Out of scope per BACKLOG §D — every Python Lambda still mocks
`_rds.execute_statement`. This slice acknowledges the risk per the ICICI
demo lesson (PR #29, 2026-05-27) but does not fix it.

### 10.3 Manual smoke (KK)

1. **Workspace OAuth** — KK installs Shasta on kkmookhey.com Workspace
   tenant. OAuth consent screen renders; tokens land in
   `tenant_workspace_oauth`. First scan kicks off automatically.
2. **Workspace detectors** — within 5 min of first scan, see at least
   one personal-tier sign-in surface in `/findings`. Drill from the
   Shadow AI tile on `/ai` works.
3. **Bedrock** — using an AWS account already connected, invoke Bedrock
   via AWS CLI. Within 60s, see `bedrock_model` entity + (if a 24h
   period has elapsed) a `bedrock_invocation` rollup.
4. **Compliance tiles** — NIST AI RMF tile shows a non-zero score with
   at least 4-5 assessed sub-categories. OWASP LLM Top 10 tile shows
   non-zero (likely just LLM03 / LLM06 hits).
5. **AI-BOM** — click Export AI-BOM, download the file, validate with
   the public CycloneDX CLI (`cyclonedx validate --input-file …`).
   Must validate clean against the 1.6 schema.
6. **Broadcast** — manually `INSERT` a finding with
   `check_id='gws_drive_shared_to_ai_domain'`, `severity='critical'`,
   `status='fail'` into Aurora (severity overridden vs the detector's
   default `high` to exercise the broadcast gate). Verify Block Kit
   card lands in `#log-alerts` within 60s. This proves the pipeline
   works for new `check_id`s; no new wiring in this slice.

## 11. Open questions and risks

### Calendar risks

- **Google verification queue.** Restricted scopes (Admin SDK
  Reports + Directory) require app verification. Historical median is
  2-4 weeks; rare cases take longer. Implementation plan kicks this off
  in Sprint 1, develops against unverified client in dev, flips to
  verified client when approval lands. Slice 1 ships with the UI
  showing "Pending verification" tile state if verification isn't
  through — UX-acceptable; doesn't block other Slice 1 work.

### Technical risks

- **CloudTrail data event vs management event for Bedrock.** Bedrock's
  `InvokeModel` is documented as a management event but per-invocation
  detail may not be in the default CloudTrail trail; some customers
  filter management events from their CloudTrail. **Mitigation deferred
  to Slice 2** as a `aws_bedrock_no_events_received` health-signal
  detector — emits informational when Bedrock is provisioned in the
  account but zero events arrive over 7 days, with remediation pointing
  the customer to their CloudTrail configuration. Not in Slice 1 scope;
  documented here so it doesn't get lost.
- **Event volume on `event_router` from a busy Bedrock tenant.** Slice 1
  rate-limits per-tenant at 1000 events/min on `event_router` for
  Bedrock-class events. Above the cap, events go to SQS DLQ + a
  "rate-limited" warning tile appears on `/ai` Shadow AI row. The cap is
  high enough not to trip in normal use; protects router from runaway
  tenants. Configurable per tenant.
- **`cyclonedx-python-lib` version + CycloneDX-ML 1.6 capability.** Pin
  version after verifying at implementation time. If the latest release
  doesn't support 1.6 ML components, consider falling back to 1.5 or
  contributing the missing piece upstream. Per CLAUDE.md §7, no version
  number invented here.
- **Workspace audit log retention.** Google retains audit logs for 6
  months free-tier, longer on paid plans. First scan pulls last 30 days
  — this is enough for the demo but not for compliance audits going back
  further. Document this limit in the customer-facing OAuth grant
  screen.

### Compliance risks

- **NIST AI RMF mapping subjectivity.** Mappings of detectors to
  sub-categories are interpretation calls. Slice 1 publishes the
  mapping table inside the spec + a tooltip on each tile so customers
  can see what we're claiming. Open to community input via the public
  repo — same approach as the existing CME-v2 frameworks.
- **OWASP LLM Top 10 2025 spec stability.** OWASP revises this list
  every 2-3 years. Lock to the 2025 edition; future editions become a
  separate `owasp_llm_top10_2027` framework key when published.

## 12. Slice 2-5 sketch (not committed scope)

| Slice | Focus | Key adds |
|---|---|---|
| 2 | M365 Copilot + SaaS AI audit logs | Microsoft Graph Reports API for Copilot usage; Slack/Notion/Atlassian audit log integrations; deeper mapping-rule coverage against existing EU AI Act + ISO 42001 packs (new check_ids unlocking more controls); AI-BOM PDF render |
| 3 | AI risk depth | Bedrock per-prompt content analysis (with opt-in customer config); AI vendor risk inventory + posture score; PII-in-prompt heuristics |
| 4 | Code-side AI security (platform-team bridge) | Whitney Semgrep prompt-injection rules → `ai_code_finding` → OWASP LLM01 mapping; AI agent runtime tool-call monitoring |
| 5 | CISO actions + reporting | Monthly AI Posture board PDF; AI Risk Register integration; daily/weekly Slack digest; SPDX-AI 3.0 export |

## 13. References

- CISOBrief CME-v2 spec: `docs/superpowers/specs/2026-05-24-compliance-mapping-engine-v2.md`
- CISOBrief AI Visibility v2 spec: `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md`
- CISOBrief MCP Connectors Slice 1: `docs/superpowers/specs/2026-05-28-mcp-connectors-design.md`
- CISOBrief MCP Connectors Slice 2: `docs/superpowers/specs/2026-05-31-mcp-connectors-slice-2-design.md`
- NIST AI RMF 1.0: <https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf>
- OWASP LLM Top 10 2025: <https://owasp.org/www-project-top-10-for-large-language-model-applications/>
- CycloneDX-ML 1.6 spec: <https://cyclonedx.org/docs/1.6/json/#ml-bom>
- `cyclonedx-python-lib`: <https://github.com/CycloneDX/cyclonedx-python-lib>
- Google Workspace Admin SDK — Reports API: <https://developers.google.com/admin-sdk/reports/v1/get-start/getting-started>
- AWS Bedrock CloudTrail events: <https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html>
