# CISO Copilot v2 — Handoff & State

> Source of truth for the *current* state of the v2 build. Reload this at the
> top of every session. The PRD is `CISOBrief-v2.md`; this document records
> what's actually built, what was broken and fixed, and what still hurts.
>
> Last updated: 2026-05-25 (Compliance Mapping Engine v2 fully shipped
> across 4 slices — PRs #17–#20; plus tasks #53 + #54 follow-up PR #21:
> canonical `NIST.AI.600-1:2.N` IDs replace Shasta `GAI-N` shorthand,
> MITRE ATLAS control_descriptions now carry canonical v4 technique
> names, §2.1 CBRN + §2.10 Intellectual Property entries added.
> Earlier 2026-05-24: AI Visibility v2 S2.1 Entra licensing banner
> verified; restored the AiSummary Lambda + `/ai/summary` route after
> a stale-tree CDK deploy on 2026-05-23 silently wiped them. See
> gotcha block below.)

## 🚀 Compliance Mapping Engine v2 — shipped end-to-end (2026-05-25)

The architectural reset KK ordered ("Shasta isn't our binding framework.
We are building something phenomenally better… built it once, built it
right, architecturally sound for multi-modal evidence collection.") is
fully live on `main`. CME-v2 is now the binding crosswalk between scanner-
emitted control IDs and canonical published framework formats.

**What's live (PRs #17–#20):**

- **Two-stage pipeline** (`normalize` → `augment`) in `framework_registry.py`
  runs on every finding write. `unified_writer.commit_scan` (AWS/Azure/GCP/
  ai_scanner) + `shasta_runner_entra._enrich_param_lists_with_registry`
  (Entra) both hook the pipeline.
- **`ai_framework_registry.json`** declares 8 frameworks with `family`,
  `source_url`, `version`, `canonical_format`, `rewrite_rules` (~65 entries),
  `control_descriptions` (12 NIST §2 risks, 16 ATLAS techniques, 9 EU AI
  Act articles, 10 OWASP LLM, 2 ISO 42001, plus all NIST AI RMF GOVERN/
  MAP/MEASURE/MANAGE subcategories).
- **13 canonical augment rules**: 3 Slice E ai_signin_* + 10 baseline AWS
  check_id rules. All emit canonical published-format IDs directly so the
  augment stage does not need a second normalize pass.
- **`/ai/summary` + `/compliance/summary`** return `frameworks_meta` carrying
  `{name, family, source_url, version}` per framework.
- **Web `/ai` + Dashboard** render family-grouped tiles (security / ai /
  industry). All framework tiles + the `/findings?framework=` filter chip
  carry the §14.1 disclaimer ("Mapping only — not a compliance attestation.
  Verify with your auditor.") on hover.
- **CloudWatch observability**: `registry_apply_summary`,
  `normalize_rewrote_count`, `normalize_passthrough_count` log per scan.
- **Provenance**: every finding records `evidence_packet._registry_rule_ids`
  with the IDs of every rule that fired.
- **Forward-compat for §17.1 Findings History + §17.2 Evidence Ingestion**
  preserved in the spec — both deferred to future sub-projects.

### Tasks #53 + #54 follow-up (PR #21)

After CME-v2 shipped, the Shasta-shorthand tail still leaked. PR #21 closes
it strictly per D-2 ("canonical published format is binding"):

- **NIST AI 600-1**: all 12 Shasta `GAI-N` IDs now rewrite to canonical
  `NIST.AI.600-1:2.N` section anchors. GAI-5 and GAI-8 are multi-target where
  NIST splits the concern across two sections. GAI-6 (Data Poisoning) and
  GAI-7 (Prompt Injection) are documented as Shasta extensions and mapped
  to the closest NIST anchors (`§2.9` + `§2.12` for the former, `§2.9` for
  the latter).
- **2 NIST risks now in registry that no Shasta check covers**: `§2.1 CBRN
  Information or Capabilities` and `§2.10 Intellectual Property`. They sit
  in `control_descriptions` so a future baseline-rule can attach them
  without retroactively adding registry entries.
- **MITRE ATLAS labels**: `control_descriptions` values converted from
  plain strings to `{name, description}` so the canonical v4 technique
  short-name (e.g. `AML.T0010` = "AI Supply Chain Compromise") is registry-
  canonical instead of trusting potentially-drifted Shasta check titles.
- **Stray `GOVERN 1.6`** removed from `nist_ai_600_1.control_descriptions`
  (it belongs only in `nist_ai_rmf`).

### What you'll see post next rescan

Existing Aurora rows tagged `GAI-N` stay `GAI-N` until next purge — per
KK's "don't backfill; we purge periodically pre-prod" stance from the
CME-v2 spec. After the next AWS Medium rescan:

- New findings land tagged `NIST.AI.600-1:2.X` in `findings.frameworks`
  JSONB instead of `GAI-N`.
- `/findings?framework=nist_ai_600_1` continues to populate (the framework
  KEY didn't change; only the IDs within it did).
- The 10 new baseline rules (`baseline_bedrock_*`, `baseline_sagemaker_*`,
  `baseline_cloudtrail_ai_events`, `baseline_s3_training_data_versioned`)
  fire from the AWS scanner image already in ECR and produce visible
  CloudWatch `registry_apply_summary` deltas.

### Tests (full suite green)

- `pytest platform/lambda/scanner_core/tests/` — **70 passed**
- `pytest platform/lambda/shasta_runner_entra/app/tests/` — 12 passed
- `pytest platform/lambda/ai_summary/tests/` — 2 passed
- `pytest platform/lambda/findings_list/tests/` — 4 passed (needs DB env-var
  stubs; see Open items)

### Known UX gaps logged (not blockers)

- Framework tiles on `/ai` link to source docs but don't drill into
  `/findings?framework=<key>`. ~10 min web tweak.
- Redundant Entra ID P1/P2 hint copy in `AISummary.tsx:87` now overlaps
  with the Slice 2.1 connect-page banner — trim or cross-link.

## 🛠 `/ai` endpoint restored (2026-05-24)

Reactive fix during S2.1 verification — `/ai` was returning **500
Internal Server Error**. Root-cause + fix below; the lesson is the
load-bearing part.

**Symptom:** `https://shasta.transilience.cloud/ai` → "Failed to load
AI summary: 500 {\"message\": \"Internal server error\"}". `GET
/v1/ai/summary` without auth returned 401 (so the route looked
deployed), but `aws lambda list-functions` had no `AiSummaryFn` and
the deployed CFN template had **zero** `AiSummary*` references.

**Root cause:** `CisoCopilotApi` CFN events showed a deploy at
**2026-05-23 19:28 UTC** that explicitly DELETED `AiSummaryFnFB51D79F`,
`RestApiaisummaryC0CCE8FD` (the `/ai/summary` resource), the GET +
OPTIONS methods, both Lambda permissions, and the service role +
default policy. Timing math:
- `1f2b926` ("feat(api): wire /ai/summary route + AiSummaryFn") on the
  S1 branch — **2026-05-22 19:21 PDT**.
- The 19:28 UTC deploy — **2026-05-23 12:28 PDT**.
- PR #6 (S1) merged to main — **2026-05-23 17:03 PDT**.
A `cdk deploy CisoCopilotApi` ran between the commit and the merge,
from a tree (main, pre-merge) that didn't have the S1 code. CFN
reconciled the deployed stack against that tree and dropped the
"orphan" resources. The 401 from API Gateway after the wipe was a
residual stage-deployment quirk; authed calls hit the missing
integration and got 500.

**Fix:** Single redeploy from current main HEAD (`bdbf7e8`), which has
all three PRs (#6, #7, #8) merged in:
```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never
```
82s, 13/13 resources `UPDATE_COMPLETE` — pure ADD of one Lambda + IAM
role + policy + GET/OPTIONS methods + Lambda permissions + redeployed
the v1 stage. Verified: Lambda live (direct invoke returns structured
401 `no_tenant` on a synthetic event), `/ai/summary` resource back in
API Gateway (id `175ooa`, GET + OPTIONS), no-auth curl returns 401
(Cognito authorizer working).

**▶ Lesson — paid in debugging time:** **Never `cdk deploy <stack>`
from a tree that lacks feature work which has already been deployed
from another branch.** CDK/CFN treats the divergence as drift and
deletes the "extra" resources, silently. The 2026-05-22→2026-05-23
window had a 22-hour gap between S1's deployed code and S1's merge to
main, and a deploy from a different branch in that window wiped the
endpoint. Mitigation in practice: if you're about to deploy from a
non-main branch, `git fetch origin main && git diff origin/main..HEAD
-- platform/lib/` to confirm you're not regressing main's CDK surface.
Or — merge first, then deploy from main.

## 🚀 AI Visibility v2 — Slice 2.1 shipped (2026-05-24)

Follow-on polish to S2. Spec
`docs/superpowers/specs/2026-05-24-entra-licensing-banner-design.md`;
plan `docs/superpowers/plans/2026-05-24-entra-licensing-banner-plan.md`.
Built subagent-driven on branch **`feat/ai-visibility-v2-slice-2.1`**
(6 commits ahead of `main`).

**S2.1 — Entra Free-tier licensing banner — DONE.**

- **`ai_signin_pass.run_ai_signin_pass`** now returns a tuple
  `(param_lists, premium_required)`. The bool fires only on Microsoft's
  specific 403 error code
  `Authentication_RequestFromNonPremiumTenantOrB2CTenant`. Other 403s
  (revoked consent, missing scope) leave it `False`. Module-top
  constant `_LICENSING_ERROR_CODE` is the matched string. 3 new unit
  tests (12/12 total).
- **`shasta_runner_entra/main.py`** unpacks the tuple; calls new
  `_update_connection_premium_flag(conn_id, *, premium_required,
  signin_count)` helper:
  - On 403 (premium_required): `scope = jsonb_set(COALESCE(scope, '{}'::jsonb),
    '{signin_premium_required}', 'true'::jsonb)`.
  - On positive signal (signin_count > 0): `scope = scope #-
    '{signin_premium_required}'` (clear).
  - Else: no-op (ambiguous case — could be Premium tenant with no AI
    users).
  Both writes also bump `updated_at`. A second try/except around the
  helper means a write failure never fails the scan.
- **`web/src/lib/api.ts`** — added `signin_premium_required?: boolean`
  to `Connection.scope` (one-key extension; existing JSONB shape
  preserved).
- **`web/src/routes/ConnectClouds.tsx`** — exported `ConnectionRow`;
  added `LicensingBanner` component (amber-bordered card) with copy
  "Sign-in detection requires Microsoft Entra ID P1 or P2" + Microsoft
  docs link. Rendered inside the `<li>` when `cloud_type === 'entra'
  && scope?.signin_premium_required === true` (strict `=== true`
  check). 3 new vitest cases (122/122 total).
- **Connections endpoint reuse**: zero changes needed to
  `connections_list/main.py` — it already returned `scope` JSONB. Zero
  CDK changes, zero new Lambdas.
- **Deployed:** scanner image `sha256:9eb38f0c…` pushed; Lambda
  re-resolved `:latest`. Web bundle synced to S3; CloudFront
  invalidation `I4H208LEK1MQDTTFZ91YEARAD1` queued. Live at
  `shasta.transilience.cloud`.

**Live-verification (Task 5) — VERIFIED (2026-05-24, KK):** Entra
rescan against the Free-tier test tenant set
`scope.signin_premium_required = true`; amber banner renders under the
Entra row on `/connect` with the expected copy + Microsoft docs link.
S2.1 passes end-to-end.

**Execution notes:**
- Task 3 implementer dropped a socket mid-write — re-dispatched cleanly
  on retry. No partial state landed.
- Reviewer caught an unused `Iterable` import after the signature
  change; one-line fix committed (`0a8f721`).
- Plan's `_fetch_signins` body needed an `try/except ImportError`
  wrapper around the kiota SDK imports so the bare test venv could
  run the new tests without msgraph installed; production behavior
  unchanged.

**▶ NEXT** — S3 brainstorm (compliance mapping sweep + EU AI Act +
SOC 2 AI framework registry adds).

## 🚀 AI Visibility v2 — Slice 2 shipped (2026-05-23)

Sub-project **AI Visibility v2**, Slice 2 (S2). Spec
`docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md` (§9
amended 2026-05-23 with decision D-1); plan
`docs/superpowers/plans/2026-05-23-ai-visibility-v2-slice-2.md`. Built
subagent-driven on branch **`feat/ai-visibility-v2-slice-2`** (6
commits ahead of `main`). S1 also shipped same day — see its block
below (PR #6).

**S2 — AI sign-in pass inside the existing Entra runner — DONE.**

- **Piggyback architecture (decision D-1):** no new connector type, no
  new admin-consent flow, no new secret. AI sign-in scanning lands as
  an additional pass inside `shasta_runner_entra` alongside Shasta's
  existing Entra compliance checks. Customers who already have a
  `cloud_type='entra'` connection get S2 instantly on the next scan.
- **Pre-flight (Task 1 — KK-gated, pending):** `AuditLog.Read.All` on
  AAD app `093442df-dc5a-463e-84a4-9cff0a750bce`. The plan path
  assumes scope is present (Shasta already reads
  `user.signInActivity`); the try/except wrapper in the handler
  ensures a Graph 403 is non-fatal regardless. KK to confirm via
  Entra portal at convenience.
- **`ai_signin_pass.py`** (244 lines + 9 unit tests). Pure helpers
  (`load_catalog`, `match_app`, `signin_to_params`) + lazy-imported
  Graph paginator. Reads Graph `/auditLogs/signIns` (incremental by
  `createdDateTime`), matches against catalog, emits per-tier
  findings (`ai_signin_personal_tier` fail/high,
  `ai_signin_corp_tier` pass/low, `ai_signin_unknown_tier`
  fail/catalog-severity). Each carries `evidence_packet.is_ai='true'`
  + `evidence_packet.entra_upn=<user>` so the existing
  `/ai/summary` predicate + per-person query pick them up with zero
  read-side change.
- **`ai_saas_catalog.json`** — 30 curated AI SaaS apps (OpenAI,
  Anthropic, Cursor, GitHub Copilot, Perplexity, Google Gemini,
  Mistral, Cohere, HuggingFace, Midjourney, Notion AI, Otter, DeepL,
  Synthesia, Tabnine, Codeium, Sourcegraph Cody, DeepSeek, xAI Grok,
  Together.ai, Groq, AI21, etc.). Tightened post-implementation
  (`1a55533`) to drop substring-overreaching aliases
  (`Notion`/`Writer`/`Together`/`Bard`/`Cody`/`Runway`).
- **`_FINDING_INSERT_SQL` extended** in `shasta_runner_entra/main.py`
  to carry `evidence_packet`. Existing Shasta findings pass `{}` (no
  behavior change). New `_insert_finding_param_lists` helper handles
  the AI sign-in path.
- **Handler wiring:** `run_ai_signin_pass` runs after Shasta's
  `run_all_azure_entra_checks` and merges into the same batch path.
  A try/except around the AI pass ensures a Graph failure NEVER
  fails the underlying Shasta scan.
- **Deployed:** scanner image rebuilt + pushed (`sha256:9a75aca8…`);
  Lambda `ciso-copilot-shasta-runner-entra` updated via
  `update-function-code` to re-resolve `:latest` to the new digest.
  No CDK deploy needed. No web changes.

**Execution-time decisions:**
- `match_app` uses **case-insensitive substring matching**, not
  exact. Forgiving for Microsoft's renaming patterns
  ("ChatGPT Enterprise", "ChatGPT for Teams") but at false-positive
  risk for generic aliases — addressed via catalog tightening.
- **Entity emission deferred** — entra runner has no `unified_writer`
  today. Findings carry `evidence_packet.is_ai` for the AI-touching
  predicate's escape hatch; proper `ai_user_signin` entities can
  land in a follow-on refactor.

**Live-verification (Task 7) — partial (2026-05-23):**

Scan `b253e078-8db1-47ab-857c-36b6bc47c4ef` ran against KK's
Entra-connected test tenant. Outcome:
- **16 Shasta entra findings written** (existing posture checks
  unchanged — the try/except wrapper protected this path).
- **0 AI sign-in findings.** Graph returned 403
  `Authentication_RequestFromNonPremiumTenantOrB2CTenant`:
  > "Tenant is not a B2C tenant and doesn't have premium license"

  **`auditLogs/signIns` is gated on Entra ID P1 or P2 (Premium)** —
  Microsoft licensing requirement, not something we can work around
  in code. The free-tier tenant gets no sign-in data at all (the
  spec's earlier "7-day window" language was wrong and has been
  patched).
- **Infrastructure validated.** The full code path executed exactly
  as designed: catalog loaded, Graph paginator hit, 403 caught by
  the wrapper, scan continued and committed. To see real `ai_signin_*`
  findings, the test tenant needs an Entra ID P1/P2 license OR run
  against a customer tenant with Premium licensing.

**Follow-on UX work (not blocking S2):**
- Surface "Entra Free tier — sign-in detection requires P1/P2" banner
  on `/connect` so customers understand why Entra source tile reads
  zero AI findings even after a successful scan.

**Bug fix during smoke (`c45977f`):** the post-consent "Run your
first scan →" link was pointing at `${cdnDistribution}` (the GCP
onboarding asset CDN) instead of the canonical app domain. Returned
S3 XML AccessDenied when clicked. Patched to hardcode
`https://shasta.transilience.cloud`. `CisoCopilotApi` deployed.

**Deferred from S2 (per plan + spec):**
- Per-tenant sanctioned-app overrides + `ai_signin_unsanctioned_app`
  finding kind.
- Entity emission for `ai_user_signin` (entra runner needs
  `unified_writer` refactor).
- Framework tagging (NIST AI RMF / ISO 42001 / SOC 2 AI on
  `ai_signin_*` check IDs) — S3 work.

**▶ NEXT** — Slice 3 (compliance mapping sweep + EU AI Act + SOC 2 AI
framework registry adds). Brainstorm + plan separately. Also queued:
a `/connect` banner that surfaces the Entra Free tier P1/P2 licensing
constraint when a scan hits the 403 — small UX polish slice ("S2.1").

## 🚀 AI Visibility v2 — Slice 1 shipped (2026-05-22)

Sub-project **AI Visibility v2**, Slice 1 (S1). Strategy
`docs/superpowers/specs/2026-05-22-ai-security-strategy.md`; spec
`docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md`; plan
`docs/superpowers/plans/2026-05-22-ai-visibility-v2-slice-1.md`. Built
subagent-driven on branch **`feat/ai-visibility-v2-slice-1`** (7
commits ahead of `main`).

**S1 — Azure-AI cloud pass + Unified /ai view — DONE.**

- **Azure-AI pass** (`platform/lambda/shasta_runner_azure/app/ai_pass.py`,
  198 lines + 6 unit tests). Wraps Shasta's
  `discover_azure_ai_services` + `run_full_azure_ai_scan` +
  `enrich_findings_with_ai_controls` into the unified
  entity/edge/finding model. Entities emitted with `domain='cloud'`:
  `azure_openai_deployment`, `azure_ml_workspace`, `cognitive_service`
  (OpenAI-kind cognitive services skipped to avoid dup with the OpenAI
  branch). Findings carry NIST AI RMF + ISO 42001 + EU AI Act framework
  tags + Azure standard frameworks (`soc2`, `iso27001`, `mcsb`,
  `cis_azure`).
- **Tier gating**: `"ai"` added to `azure_units._MEDIUM_EXTRA`; Quick
  scans skip it, Medium + Deep run it. Per-(subscription, ai) ScanUnit
  via `_ai_unit` factory in `main.py` (mirrors `_module_unit`'s
  per-unit-fresh-client pattern). Scanner image rebuilt + pushed
  (`sha256:951ae7ba…`); `:latest` tag so no `CisoCopilotScan` redeploy
  was needed.
- **`/ai/summary` Lambda** (`platform/lambda/ai_summary/`) — new
  Cognito-authed endpoint at `GET /v1/ai/summary`. Returns `{ score,
  by_source, by_framework, top_people }`. `is_ai_touching` evaluated
  inline in SQL via JSONB `?|` over four AI framework keys + entity
  domain/kind allowlist + `evidence_packet ->> 'is_ai'` escape hatch.
  `_query_top_people` SQL groups by `LOWER(COALESCE(commit_author_email,
  iam_owner_email, entra_upn))`. **Schema delta from the spec:** the
  plan assumed `findings.attributes` JSONB and `entities.entity_id` —
  actual schema is `findings.evidence_packet` and `entities.id`.
  Fixed in `ai_summary/main.py` via the plan's Step 5 pre-flight check.
- **API stack** — `AiSummaryFn` Lambda + `/ai/summary` GET route in
  `api-stack.ts`; `CisoCopilotApi` deployed (`UPDATE_COMPLETE` —
  82s deploy, 13/13 resources). Note: `--hotswap` won't create new
  Lambdas, so this deploy used a full CFN update.
- **Web `/ai` route** (`web/src/routes/AISummary.tsx`, 207 lines + 2
  vitest cases). Tile layout: 3 score tiles (Fail/Partial/Pass), 4
  source tiles (AWS/Azure/Code/Entra — Entra labelled "coming in S2"),
  4 framework tiles (NIST AI RMF / ISO 42001 / SOC 2 AI / EU AI Act
  each with F/P/Pass mini-rollup), top-people table with empty-state
  copy "No identifiable AI users yet — connect Entra (S2) to populate."
  Added `api.aiSummary()` method to `web/src/lib/api.ts` matching the
  project's existing `call<T>` pattern (the plan's `apiGet` assumption
  was wrong — no such export existed). Built, synced to S3, CloudFront
  invalidation `IC7ZCIJ34431QOW27SRV34X0WR`.
- **Data state at ship:** 7790 findings in Aurora, 102 AI-touching
  (`is_ai_touching=true`). The score + by-source + by-framework all
  populate. **Top-people view is empty** — zero findings carry
  `commit_author_email` / `iam_owner_email` / `entra_upn` in
  `evidence_packet`. Documented limit; populates in S2 (Entra
  sign-ins) or future emitter patches (AI scanner + AWS owner-tag
  enrichment).
- **Reviewer-caught issues fixed during execution:**
  - `cis_aws_controls` was an AWS leftover copy-pasted into the Azure
    `ai_pass._STD_FRAMEWORK_ATTRS` dict — replaced with
    `cis_azure_controls → cis_azure` after the code reviewer caught
    it.
  - `_query_top_people` had non-deterministic `STRING_AGG` order —
    fixed with `ORDER BY` inside the aggregate.
  - `_AI_RESOURCE_KINDS` allowlist was missing the actual AI-scanner
    entity kinds (`ai_agent`, `ai_embedding`, `ai_framework`,
    `ai_mcp_server`, `ai_model`, `ai_prompt`, `ai_tool`,
    `ai_vector_db`) — added.
  - Module docstring claim that the predicate matches keys "starting
    with" the AI framework prefixes was wrong — the SQL uses exact
    equality via `?|`; docstring corrected.

**Deferred (out of S1 scope, per spec):**
- **S2** — Entra sign-in connector + per-person grouping.
- **S3** — Compliance mapping sweep + SOC 2 AI + EU AI Act registry
  (the four framework tiles render; SOC 2 AI and EU AI Act tiles will
  read zero until S3 maps the actual checks).
- **S4** — iOS push notifications + polish.
- **GCP-AI** — its own sub-project (Shasta has no `gcp/ai_*` today).

**Slice 1 live-verification — pending (KK-gated, Google OAuth):**
1. Open `https://shasta.transilience.cloud/ai` in an incognito window;
   sign in with Google.
2. Confirm the page renders "AI Exposure" title + three F/P/P tiles
   with non-zero numbers (today's data: 102 AI-touching findings;
   real F/P/Pass split visible in the tiles).
3. Confirm the by-source row shows AWS + Azure with counts; Code
   per-tenant; Entra zero with "coming in S2" label.
4. Confirm the by-framework row shows NIST AI RMF + ISO 42001 with
   counts; SOC 2 AI + EU AI Act both zero (S3 work).
5. Confirm Top AI Users shows the empty-state copy "No identifiable
   AI users yet — connect Entra (S2) to populate."
6. Open browser devtools → Network. Confirm `GET
   /v1/ai/summary` returned 200 with the documented JSON contract.
7. (Optional) Re-run a Medium Azure scan; refresh `/ai`; counts
   should hold steady or grow (never go negative).

**▶ NEXT (post-S1 view, superseded by S2 above)** — Slice 2 has now
shipped; see its block at the top of this file.

## 🚀 Scan Screen — Slice 2b shipped (2026-05-22)

Cross-cloud `/scan` surface. Spec
`docs/superpowers/specs/2026-05-22-scan-screen-design.md`; plan
`docs/superpowers/plans/2026-05-22-scan-screen-slice-2b.md`. Built
subagent-driven on branch **`feat/scan-screen-slice-2b`** (merged to
main 2026-05-22, commit `a57f528`).

- **New `/scan` route** (`web/src/routes/Scan.tsx`) — stacked cards,
  one per active connection. The `ScanCard` shell handles the header
  (cloud name + last-scan pill + "Never scanned" badge for
  `latest_scan === null`) and routes to a per-cloud body:
  - **AWS** — tier picker only.
  - **Azure** — subscription checklist (from `scope.subscription_names`,
    default = all selected) + tier.
  - **GCP** — project mode: tier only. Org mode: project checklist
    (from `scope.projects`, default = all) + tier. If the org
    connection has no projects yet (first scan after onboarding), the
    body shows a "Projects discover on the first scan" banner and the
    scanner enumerates when clicked.
  - **Entra** — no scope, no tier; just a Scan button.
  - A "Launch all scans" button at the page level fires every card in
    parallel at Quick tier (Promise.allSettled; partial failures
    surface in the per-card UI on next reload).
  - Live scan rendering: when a card's `latest_scan.status` is
    `running` (or a scan was just started locally), the body is
    replaced by `<ScanProgress>` until terminal, then it re-renders the
    picker form so the user can re-launch.
- **Onboarding webhooks dropped the auto-scan** — AWS / Azure / GCP /
  Entra. A freshly onboarded connection now lands in `/scan` with
  `latest_scan: null` and a "Never scanned" badge. The Entra HTML
  success page also redirects to `/scan` directly (link "Run your first
  scan →").
- **Connect page retrofit** — the per-row `ScanPicker` and the inline
  `SubscriptionPicker` are deleted (~144 lines net). Each connection
  row now shows only status + last-scan summary + Delete. The page
  polls `GET /connections` every 5s while any connection is non-active;
  a `pending → active` transition surfaces a toast in the top-right
  linking to `/scan` ("Your &lt;CLOUD&gt; connection is ready — Run
  your first scan →").
- **`PATCH /connections/{id}` validates against either subscriptions
  or projects** — `_update_scope` now accepts a non-empty subset of
  `scope.subscriptions` (Azure) OR `scope.projects` (GCP org), so the
  same endpoint supports both pickers without divergence.
- **Deployed:** `CisoCopilotApi` deployed (`UPDATE_COMPLETE`); web built
  (`tsc -b && vite build` clean), synced to S3, CloudFront invalidation
  `IB4TNKV8P0SR5A1FH4ZK2OYS17` queued. Live at
  `shasta.transilience.cloud`.
- **Browser-smoke pending** — an agent can't pass Google OAuth.
  Verification checklist:
  1. Open `https://shasta.transilience.cloud` in an incognito window.
  2. Sign in with Google.
  3. Click "Scan" in the nav. Confirm the page renders the existing
     GCP project connection as a card (AWS not connected on this
     tenant; Azure has the subscription picker).
  4. Click "Scan" on the GCP card. Confirm the card flips to
     `ScanProgress` and polls to completion.
  5. Visit "Connect clouds" — confirm the per-row pickers are gone;
     only Delete buttons remain.
  6. (Optional) Re-onboard a cloud to confirm the post-onboard toast
     appears on the Connect page.

## 🚀 GCP Scanner Uplift — Slice 2a shipped (code only — 2026-05-22)

Roadmap item #1, GCP leg. Plan
`docs/superpowers/plans/2026-05-22-gcp-scanner-uplift-slice-2a.md`.
Built subagent-driven on branch **`feat/gcp-scanner-slice-2a`** (merged
to main 2026-05-22, commit `5c7125d`).

**Slice 2a — org-level GCP onboarding — CODE DONE.**
- `cfn/gcp/onboard.sh` learned a `--org <ORG_ID>` flag. Without it the
  script runs the unchanged single-project flow; with it, reader roles
  bind at the **Organization** node (`securityReviewer`,
  `cloudasset.viewer`, `logging.viewer`, `browser`) and the POST body
  carries `mode=org`, `org_id`, `host_project_id`, `host_project_number`.
- `onboarding_gcp_complete` webhook branches on `mode`. Org mode stores
  the org scope (`mode=org`, `org_id`, `host_*`, `sa_email`, WIF refs,
  `projects={}`, `selected=[]`) and **does NOT auto-scan**. Project mode
  unchanged (still auto-scans).
- Scanner-side enumeration: `project_discovery.enumerate_projects` added
  (was deferred from Slice 1a, 4 new unit tests). `shasta_runner_gcp/
  main.py` calls it in Stage 1 for org-mode scans, writes the
  `{project_id: display_name}` map back to `cloud_connections.scope.
  projects` via a new `_record_projects` helper, and uses the
  enumerated list as `project_ids` if the trigger passed an empty
  subset.
- `run.py` accepts `MODE` (defaults `project`) and allows empty
  `PROJECT_IDS`; new optional `HOST_PROJECT_ID` for the org bootstrap.
  10 unit tests for `build_event` (43 scanner tests pass).
- `connections_list._rescan_gcp` routes on `scope.mode` — org branch
  reads `host_project_number`/`selected`; project branch unchanged.
  New Fargate env vars passed: `MODE`, `HOST_PROJECT_ID`.
- **Scope cut from spec**: the webhook does NOT pre-enumerate via
  Resource Manager (Approach C of the spec); enumeration happens
  lazily on the first scan. Trade-off: the project picker (Slice 2b)
  will be empty until the first scan completes (~3-5 min). Documented
  in the plan as an explicit, revisitable simplification — avoided
  bundling google-auth + an IAM-trust expansion into the webhook for
  tonight.
- **Deployed**: scanner image rebuilt + pushed
  (`sha256:8648e2e7…`); `CisoCopilotApi` + `CisoCopilotStatic` deployed
  (Static pushes the new `onboard.sh` to the CDN). Deployed webhook
  smoke-confirmed: a `mode=org` body missing the org fields returns
  400 `missing_fields`, proving the new branch is live.

**Slice 2a live-verification — pending (human-gated).** Requires
org-admin on a real GCP Organization. Procedure when ready:

1. In Cloud Shell of the customer's host project (org-admin signed in):
   ```bash
   curl -fsSL https://cdn.settlingforless.com/gcp/onboard.sh \
     | bash -s -- <EXTERNAL_ID> --org <ORG_ID>
   ```
   `<EXTERNAL_ID>` comes from the web app's "Add GCP" flow (which
   writes a pending row — for now, the web UI doesn't yet expose an
   "org" toggle; a tester can generate an external_id manually or wait
   for Slice 2b's web changes). `<ORG_ID>` is the numeric organisation
   id (`gcloud organizations list`).
2. Verify the connection lands `active`, `scope.mode='org'`,
   `projects={}`, `selected=[]`:
   ```sql
   SELECT scope FROM cloud_connections WHERE external_id='<EXTERNAL_ID>';
   ```
3. From the Connect page, click rescan on the new GCP row (or invoke
   `ConnectionsListFn` with the synthetic event pattern from the
   Slice 1b verification, swapping the conn_id).
4. Watch the scan: `region_discovery → first_signal → crown_jewel →
   done`. First scan performs the enumeration — confirm
   `scope.projects` is now populated and the scanner used the
   discovered list as `project_ids`.
5. Confirm findings landed: `SELECT count(*) FROM findings WHERE
   scan_id=...`.

## 🚀 GCP Scanner Uplift — Slice 1b shipped (2026-05-22)

Roadmap item #1, GCP leg. Plan
`docs/superpowers/plans/2026-05-22-gcp-scanner-uplift-slice-1b.md`.
Built subagent-driven on branch **`feat/gcp-scanner-slice-1b`** (merged
to main 2026-05-22, commit `e36e2e1`).

**Slice 1b — production Fargate triggers + legacy Lambda retired — DONE.**
- `onboarding_gcp_complete` and `connections_list._rescan_gcp` now start
  one `ciso-copilot-gcp-scan` Fargate task per connection via
  `ecs:RunTask` — no more `lambda.invoke` of the legacy scanner. The
  rescan path is tier-aware.
- Cross-stack export hygiene preserved: literal task-def family
  (`'ciso-copilot-gcp-scan'`), `iam:PassRole` covers the literal
  `ciso-copilot-gcp-scanner` task role + the `CisoCopilotScan-GcpScanTaskDef*`
  exec-role name-pattern — zero new cross-stack exports.
- **Live-verified:** a rescan through the real `ConnectionsListFn`
  Lambda (synthetic API Gateway event, real deployed function) ran scan
  `5f322c7a-…` `manual`/`completed`/`phase=done`, **102 findings, 46
  entities, 45 edges** — same shape as Slice 1a's manual verification,
  confirming the entire `POST /connections/{id}/rescan` → `_rescan_gcp`
  → `ecs.run_task` → Fargate path works end-to-end on production.
- **Legacy GCP Lambda retired** — `ciso-copilot-shasta-runner-gcp` is
  gone (`get-function` → `ResourceNotFoundException`). The Data API grant
  that lived on the Lambda was relocated onto `gcpScannerRole` so the
  Fargate task (which shares that role) keeps Aurora access.
- **Deploy gotcha paid in debugging time:** `npx cdk deploy
  CisoCopilotApi` (without `--exclusively`) pulled `CisoCopilotScan` in
  as a dependency and deployed Scan FIRST, hitting the export-still-in-use
  deadlock and rolling back cleanly. Resolved by deploying with
  `--exclusively`: Api first (drops the imports), then Scan (drops the
  Lambda + orphaned exports). Both stacks `UPDATE_COMPLETE`. The Azure
  1b deploy used the same `--exclusively` flag — the plan should always
  spell it out explicitly for two-phase deploys.

**▶ NEXT** (no slice in flight, 2026-05-22): the Scan-screen Slice 2b
and GCP Slice 2a (code) both shipped today. Open verification + dev work
in rough priority order:

1. **Browser-smoke verify Slice 2b** on `shasta.transilience.cloud` —
   checklist at the top of the 2b section above; KK-gated (Google OAuth).
2. **Expose the GCP "org" toggle** in the web onboarding flow so the
   `--org <ORG_ID>` path in `cfn/gcp/onboard.sh` is reachable from the
   UI. Unblocks Slice 2a's human verification (requires GCP org-admin).

## 🚀 GCP Scanner Uplift — Slice 1a shipped (2026-05-22)

Roadmap item #1, GCP leg. Spec
`docs/superpowers/specs/2026-05-22-gcp-scanner-uplift-design.md`; plan
`docs/superpowers/plans/2026-05-22-gcp-scanner-uplift-slice-1a.md`.
Built subagent-driven on branch **`feat/gcp-scanner-slice-1a`** (merged
to main 2026-05-22, commit `d87a839`).

**Slice 1a — v2 GCP scanner backend — DONE.** The GCP scanner
(`platform/lambda/shasta_runner_gcp/`) is now the v2 three-stage
pipeline, mirroring the Azure scanner:
- Six pure adapter modules (`gcp_credential`, `gcp_units`,
  `gcp_id_to_entity`, `gcp_findings`, `project_discovery`, `run.py`) —
  32 unit tests pass.
- `main.py` rewritten as the orchestrator: project discovery →
  tier-aware parallel project×Shasta-module `ScanUnit`s through
  `scanner_core.run_units` → `unified_writer.commit_scan`. Two-phase
  Quick. Legacy direct-`findings` writes gone.
- New `ciso-copilot-gcp-scan` Fargate task def (CDK); `build.sh` copies
  the shared `scanner_core`/`ai_scanner` modules.
- **Live-verified:** Quick scan `6977db63-…` on the GCP connection
  `219f41eb-…` (project `gen-lang-client-0693606939`) ran
  `completed`/`phase=done`, **102 findings (97 fail / 4 pass /
  1 partial), 46 entities, 45 edges**; all 5 Quick modules ran, zero
  errors; project-keyed `scope` coverage map written.
- **Bug paid in debugging time — WIF on Fargate.** First scan came back
  `partial` with every module failing "Unable to determine the AWS
  metadata server security credentials endpoint". google-auth's AWS
  external-account credential source reads AWS creds from env vars / EC2
  IMDS — neither is populated for an ECS Fargate task role (Fargate
  serves them via the container credentials endpoint). The legacy GCP
  scanner was a Lambda, where the env vars *are* set, so WIF worked
  there. Fixed (`683c90b`): `main.py` resolves creds via
  `boto3.Session().get_credentials()` (container-provider aware) and
  `gcp_credential.export_aws_credentials_to_env` exports them before the
  WIF credential is built.
- **Entity-coverage note:** 44 of 46 entities are `gcp_subnetwork` (the
  networking module's resource IDs parse cleanly); iam/storage/compute/
  encryption finding `resource_id`s mostly don't match
  `gcp_id_to_entity._KIND_MAP`, so those findings land with no subject
  entity (the intended graceful contract). Widening `_KIND_MAP` once the
  real per-module resource_id formats are sampled is a follow-up.
- **Not yet wired to production triggers** — invoked manually via
  `ecs run-task`. The legacy GCP Lambda still exists. Slice 1b wires the
  Fargate triggers + retires the legacy Lambda; Slice 2a adds org-level
  onboarding; Slice 2b is the project picker.

**▶ Sequencing decision (2026-05-22) — resolved:** before the Slice 2b
picker, we ran a short cross-cloud brainstorm for a unified **"Scan"
screen** (post-onboard landing where the user picks scope + tier per
cloud, replacing the silent auto-scan-on-onboard). It superseded the
Connect-page per-row picker Azure shipped, so 2b built the Scan screen
rather than a GCP-only Connect-page picker (now live — see top section).
Slices 1a/1b were unaffected.

## 🚀 Azure Scanner Uplift — Slice 0 shipped (2026-05-22)

Roadmap item #1, Azure leg. Spec
`docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md`; plan
`docs/superpowers/plans/2026-05-21-azure-scanner-uplift-slice-0.md`.
Built subagent-driven on branch **`feat/azure-scanner-uplift`** (landed
on main 2026-05-21, commit `1f52337`).

**Slice 0 — shared scanner core — DONE.** New package
`platform/lambda/scanner_core/` holds the cloud-agnostic pieces:
`scan_pipeline.py` (moved from `shasta_runner`) and a new `scan_state.py`
(`update_scan` + `record_scan_scope` — the `scans`-table writes,
extracted from AWS `main.py`; `record_scan_scope` takes a pre-shaped
`scope` dict so a region-keyed or subscription-keyed map both work).
`shasta_runner/build.sh` copies `scanner_core/` modules into `app/` at
image build (same mechanism as the `ai_scanner` copies). AWS `main.py`
now imports from `scan_state`; its inline `_update_scan` + DB-config
constants are gone. `scan_policy.py` and `unified_writer.py`
deliberately did NOT move (AWS-region-shaped / multi-consumer — see spec
§3-§4). **No Azure change yet — that's Slice 1.**

- Tests: AWS scanner suite 98 pass + `scanner_core/tests/` 11 pass
  (= the prior 102 baseline, `scan_pipeline`'s 4 now under
  `scanner_core/`).
- Deployed: `shasta-runner:latest` rebuilt + pushed
  (`sha256:a74c6af…`); `CisoCopilotScan` deployed.
- **Live-verified:** Quick scan `4b6d3b61-87dd-4663-bff7-4753ea809022`
  on conn `26e97477…` ran `completed`/`phase=done`/`tier=quick`, 61
  findings, 17-region scope object, ~3 min — confirming the refactored
  scanner runs end-to-end with zero behaviour regression.

**Slice 1a — v2 Azure scanner backend — DONE (2026-05-22).** Plan
`docs/superpowers/plans/2026-05-22-azure-scanner-uplift-slice-1a.md`;
built subagent-driven on branch **`feat/azure-scanner-slice-1a`** (landed
on main 2026-05-22, commit `aad5548`). The Azure scanner
(`platform/lambda/shasta_runner_azure/`)
is now the v2 three-stage pipeline:
- Five pure adapter modules (`azure_id_to_entity`, `azure_findings`,
  `subscription_discovery`, `azure_units`, `azure_credential`) + `run.py`
  — 26 unit tests pass.
- `main.py` rewritten as the orchestrator: subscription discovery →
  tier-aware parallel subscription×Shasta-module `ScanUnit`s through
  `scanner_core.run_units` → `unified_writer.commit_scan`. Two-phase
  Quick. Legacy direct-`findings` writes gone.
- New `ciso-copilot-azure-scan` Fargate task def (CDK); `build.sh`
  copies the shared `scanner_core`/`ai_scanner` modules.
- **Live-verified:** Quick scan `10ffeb40-…` on the Azure connection
  `79964b99-…` ran `completed`/`phase=done`, 72 findings, **16 entities
  across 5 Azure kinds** (the legacy scanner wrote zero entities).
  Subscription discovery classified one sub `active` (6 Quick modules
  ran), one `empty` (skipped). Subscription-keyed `scope` map written.
- **Not yet wired to production triggers** — invoked manually via
  `ecs run-task`. The legacy Azure Lambda still exists.

**Slice 1b — production triggers on Fargate — DONE (2026-05-22).** Plan
`docs/superpowers/plans/2026-05-22-azure-scanner-uplift-slice-1b.md`;
built subagent-driven on branch **`feat/azure-scanner-slice-1b`**.
- `onboarding_azure_complete` and `connections_list._rescan_azure` now
  start **one** `ciso-copilot-azure-scan` Fargate task per connection
  (all subscriptions, one `scans` row) via `ecs:RunTask` — no more
  per-subscription legacy `lambda.invoke`. The rescan path is
  tier-aware.
- **Live-verified:** a rescan through the real `POST /connections/{id}/
  rescan` API path ran scan `6cad579e-…` to `completed`/`phase=done`,
  72 findings, subscription-keyed scope.
- **Deploy gotcha paid in debugging time:** the first attempt deadlocked
  — the plan's Task 3 added a new `Scan→Api` cross-stack export (Azure
  task-def role ARNs) while removing the legacy Lambda dropped another,
  so neither stack-deploy order worked. Fixed by making the Azure wiring
  create **zero** cross-stack export churn: the task-def family is a
  literal, `iam:PassRole` uses a role-name pattern
  (`CisoCopilotScan-AzureScanTaskDef*`), and `AZURE_RUNNER_FN` stays
  wired (unused by code). The Api stack then deploys alone.
**Legacy Azure Lambda retired — DONE (2026-05-22).** `commit 887e140`.
The deferred follow-up: the `AzureRunner` `DockerImageFunction`
(`ciso-copilot-shasta-runner-azure`) + its cross-stack wiring
(`AZURE_RUNNER_FN` env vars, `grantInvoke`) removed. Shipped via the
clean two-phase deploy — `CisoCopilotApi` first (drops the imports),
then `CisoCopilotScan` (drops the Lambda + its orphaned exports) — which
worked first try since it was a pure removal with no competing new
export. The `shasta-runner-azure` ECR repo stays (the Fargate task def
uses it).

**Slice 2 — web subscription picker — DONE (2026-05-22).** Plan
`docs/superpowers/plans/2026-05-22-azure-scanner-uplift-slice-2.md`;
built subagent-driven on branch **`feat/azure-subscription-picker`**.
- `GET /connections` now returns each connection's `scope`; new
  `PATCH /connections/{id}` updates `scope.selected` (validates the
  list is a non-empty subset of the discovered subscriptions).
- `_rescan_azure` scans `scope.selected` (falls back to `subscriptions`
  for pre-picker connections); onboarding seeds `selected` = all.
- Web Connect page: an expandable subscription checklist on Azure
  connection rows (Save → PATCH), plus the Quick/Medium/Deep `ScanPicker`
  (the Azure row previously had only a flat Medium-only Rescan button).
  `ScanProgress` renders the per-subscription census for Azure scans.
- **Live-verified:** PATCH'd the Azure connection to one of its two
  subscriptions, ran a rescan — the scan's `scope.subscriptions` map
  contained only the selected subscription. Then restored to both.
- Web build/typecheck green; the picker's visual behaviour was not
  browser-tested (agent can't pass Google OAuth) — worth a glance.

**▶ AZURE SCANNER UPLIFT COMPLETE.** Slices 0, 1a, 1b, 2 + the legacy-
Lambda retirement are all shipped. The Azure scanner is the v2
three-stage Fargate pipeline, tier-aware, with user-chosen subscription
scoping — at parity with the AWS scanner. Next major item: see the
Roadmap below (Azure was roadmap #1's Azure leg; GCP / Entra uplifts
remain, or move to roadmap #2+).

## 🚀 AWS Scanner Uplift — state (2026-05-21)

Roadmap major item #1 ("Scanner comprehensiveness uplift"). **PR #4
(`feat/aws-scanner-uplift`, 55 commits) was merged to `main`** on
2026-05-21 (`e127eb8`), after a 3-reviewer whole-branch review.

**Specs (all approved) — `docs/superpowers/specs/`:**
- `2026-05-20-aws-scanner-uplift-design.md` — the overall uplift: tiered
  Quick/Medium/Deep scanning, 7-slice phasing.
- `2026-05-21-region-discovery-design.md` — superseded/extended by ↓.
- `2026-05-21-scan-performance-design.md` **(rev 3)** — "Scan Execution
  v2", the three-stage parallel scanner. **THE current design.**

**Plans — `docs/superpowers/plans/`:** `…slice-0.md`, `…slice-1.md`,
`…region-discovery.md`, `…scan-execution-v2-backend.md`.

### Slice 0 — SHIPPED
Scanner moved Lambda → **ECS Fargate** (`ciso-copilot-scan` cluster,
`ciso-copilot-aws-scan` task def). `scans.tier` column (migration 009).
Coverage scorecard `docs/coverage/aws-scorecard.{md,json}` anchored to
CIS / FSBP / PCI v4 / NIST 800-53. Onboarding triggers `ecs:RunTask`.

### Slice 1 — SHIPPED
In-repo posture **coverage engine** (`app/coverage/`): `model.py`,
`collectors/` + `checks/` for **SQS, Secrets Manager, ECR**, `registry`,
`engine`; tier-filtered checks; scorecard counts engine checks. Plus a
boto3 timeout `Config` (`app/aws_config.py`).

### Region discovery — built, then EXTENDED by Scan Execution v2
Added `region_discovery.py`. Scan Execution v2 **rewrote it** to the
four-state footprint probe; the region-discovery plan's design is
subsumed by the v2 spec.

### Credential fix — SHIPPED
`app/assumed_role.py` — `RefreshableCredentials`: a long multi-region
scan re-assumes the customer role automatically, never hits
`RequestExpired`.

### Scan Execution v2 — backend BUILT, VERIFIED, merged to main
The scanner is now a **three-stage parallel pipeline**: (1) region
eligibility, (2) four-state footprint probe
(`active`/`default_only`/`empty`/`unknown`), (3) tier-aware scan units
run through an in-task `ThreadPoolExecutor` (`scan_pipeline.py`) bounded
by per-service concurrency caps + adaptive retry. **Two-phase Quick**
(First Signal commits early, then Crown Jewel). Per-scan **coverage map**
in `scans.scope`. `scans.phase` column (migration 010). `GET
/v1/scans/{id}` scan-status API (`scans_status` lambda). Fargate task
4 vCPU / 8 GB. `PYTHONUNBUFFERED=1` so scan logs stream live.

New/rewritten modules: `scan_pipeline.py`, `scan_policy.py`,
`region_discovery.py` (rewritten), `main.py` handler (rewritten),
`assumed_role.py`, `scans_status/`. **101 scanner unit tests pass.**

**Plan tasks V2-1..V2-9 — done + two-stage reviewed.**
**V2-10 (build / deploy / E2E verify) — COMPLETE. Medium + Quick both VERIFIED:**
- Image rebuilt + pushed; `CisoCopilotScan` + `CisoCopilotApi` deployed
  (Api re-deployed post-merge for the correct callback URL).
- **Medium discovery scan `b3091a57-87b9-4eca-83cd-5dd812ec254f` —
  VERIFIED.** Completed cleanly: `status=completed`, `phase=done`. The
  four-state footprint probe classified **17 regions → 9 `active` /
  8 `default_only`** (0 errored); per-region coverage map written to
  `scans.scope`; **7,280 findings**. The v2 three-stage parallel
  pipeline works end-to-end.
- **Perf note:** that scan took **~49 min** (23:26→00:15) — completes
  cleanly (the pre-v2 serial scan ran 108 min and died on expired
  creds) but is over the ~15-25 min Medium target. Likely `ai_pass`
  running as a single serial unit + conservative per-service caps
  (flagged in spec §15). Tune later — not a blocker.
- **Quick scan `bb2d4bcb-1e7d-4748-b211-5365548994a6` — VERIFIED
  (2026-05-21).** Same conn as the Medium scan. Moved through
  `region_discovery → first_signal → crown_jewel → done`; **Phase-1
  early commit proven** — 72 findings observable while `phase` was
  still `crown_jewel`, 116 total at `done`. `status=completed`,
  17-region coverage map (9 active / 8 default_only, 0 errors).
  Ran **~4m20s** (00:23:54→00:28:14) — within the ~3-5 min target.
- **Scan-status API verified** — `GET /v1/scans/{id}` (via direct
  `ScansStatusFn` invoke with synthetic Cognito claims) returns
  `tier`/`status`/`phase`/`coverage_map`/`finding_count`, 200 OK.
- Minor note: `ecs describe-tasks` reported `exitCode: null` for the
  stopped Quick task (`stopCode: EssentialContainerExited`); DB state
  (`completed`/`done` + full coverage map + `finished_at`) is the
  authoritative success signal and confirms a clean run.
### Whole-branch review + PR #4 merge (2026-05-21)
Reviewed the full branch (55 commits, 63 files) via 3 parallel reviewers
(scanner pipeline / coverage engine / infra). All returned "merge with
fixes" — architecture sound, but real issues. **Fixed before merge:**
- **A** — `scans_status` selected `started_at`/`finished_at` without
  `::text`; the Data API dropped them → API returned null timestamps.
- **B** — onboarding inserted a scan row relying on the `phase` column
  default `'done'` → a fresh `queued` scan reported `phase=done`. Now
  inserts `phase='region_discovery'` explicitly.
- **C** — `main._absorb` dropped `global/*` unit failures from the
  coverage map → a failed IAM module left the scan `completed` not
  `partial`. Added a `"global"` bucket to `coverage_map`; `scans.scope`
  now carries it as a top-level `global` key (regions stays regional).
- **D** — `coverage/engine.py` didn't wrap `check.evaluate()`; one
  malformed resource threw and killed the whole region's findings. Now
  per-check try/except. New test `test_engine_survives_a_throwing_check`.
- **E (documented, not fixed)** — `run_units` `batch_timeout` does NOT
  bound wall-clock: the `ThreadPoolExecutor` `with`-block joins
  stragglers and `future.cancel()` no-ops a running unit. The real hang
  bound is the boto connect/read timeouts in `SCAN_BOTO_CONFIG`.
  Docstring rewritten to say so honestly.

102 scanner tests pass. **A + B are LIVE** — they touch `scans_status`
and `onboarding_aws_complete`, deployed with the `CisoCopilotApi` deploy
on 2026-05-21. **C / D / E are now LIVE too (2026-05-21)** — `build.sh`
rebuilt + pushed the `shasta-runner:latest` image
(`sha256:7cce1043…`); `CisoCopilotScan` deployed (reported "no changes"
— the ECS task def pins the `:latest` tag, which CDK does not diff, so
the next scan's `RunTask` pulls the updated image). C/D/E take effect on
the next AWS scan.

**Deferred from review (track for Slice 2 / follow-up):**
- Engine collector failures (e.g. missing `sqs:ListQueues`) are logged
  but not surfaced as `not_assessed` in `scans.scope` — a permission
  gap looks like a clean result (spec §10.1 accuracy lever).
- `ecs:RunTask` returns 200 with a `failures[]` array on capacity/subnet
  problems; `onboarding_aws_complete` doesn't inspect it → a task that
  failed to launch logs "started".
- Quick Phase 1 runs global units only — no per-region census, and the
  coverage map is written to `scans.scope` only after Phase 2. Spec
  §7.4/§10.1 and the code disagree; reconcile when building the web UX.
- Spec §8 still claims a wall-clock timeout bound (see E) — reconcile
  the spec or implement real cancellation.
- Engine check-matching is O(checks×resources) per service — fine at 3
  services, regroup before scaling to ~40.

### Scan Progress & Scan-Type UX — SHIPPED + MERGED (2026-05-21)
Scan-performance spec §10. Plan `docs/superpowers/plans/2026-05-21-scan-progress-ux.md`;
built subagent-driven (7 tasks, each spec- + quality-reviewed, plus a
final whole-branch review). Merged to `main` (`dad4c16`).
- **Backend:** `GET /connections` now carries each connection's
  `latest_scan` ({scan_id, tier, status, phase, started_at}); AWS rescan
  (`connections_list._rescan_aws`) is **tier-aware** and runs the v2
  Fargate scanner (was: legacy Lambda, no tier). Dead `SHASTA_RUNNER_FN`
  wiring removed.
- **Web:** shared `web/src/scan/` module (`useScanStatus` polling hook,
  `ScanTypeBadge`, `ScanProgress`, `scanLabels`); the ConnectClouds
  Quick/Medium/Deep scan picker + live progress card (AWS-only); the
  `/contact/deep-scan` Contact-Us route (Deep-tier gate); scan-type
  badges on the Findings / Risks / Dashboard headers.
- **Deployed:** `CisoCopilotApi` + `CisoCopilotScan` (a cross-stack
  export-removal deadlock from dropping the `shastaRunner` prop was
  resolved by the two-phase `--exclusively` deploy — Api first, then
  Scan). Web built + synced to S3 + CloudFront invalidated.
- **E2E verified:** a live tiered Quick rescan (`c660c70b-…`) moved
  `region_discovery → first_signal → crown_jewel → done`, 72 findings
  committed in Phase 1, 116 at completion. `GET /connections` returns
  `latest_scan`; the scan-status API serves the progress data the web
  polls. **The web UI rendering itself was not browser-tested** — verify
  the picker / progress card / badges visually next session.
- **Known limitation carried in:** the live region census in
  `ScanProgress` only shows at completion (the scanner writes the
  coverage map to `scans.scope` only after Phase 2 — the deferred item
  below). The progress card degrades gracefully (phase + finding count
  while running).

**Web UX browser-smoke (2026-05-21) — DONE.** KK smoke-tested the new
web UX on https://shasta.transilience.cloud/. Scan picker works; live
scan updates render correctly under the cloud being scanned. **One bug
found + fixed + deployed:** the `ScanPicker` dropdown
(`web/src/routes/ConnectClouds.tsx`) did not dismiss on outside-click —
it stayed stuck on screen if you didn't pick a tier. Added a
`pointerdown`/`Escape` close handler; web rebuilt + synced + CloudFront
invalidated. Also noted: `TEST_PLAN.md` T1.3 is stale — the email-first
sign-in flow is now ported to web (a `you@company.com` field, not the
old "Sign in with corporate account" button).

**UI/UX polish — first batch shipped (2026-05-21).** KK raised a
9-item UI/UX list; the three "looks-broken" ones were fixed + deployed
this session:
- **#2 — empty chat bubbles.** `MessageStream.tsx` now skips rendering
  any message bubble with empty/whitespace text (a tool-only turn or a
  failed/in-flight stream left blank "blob" bubbles). KK reported the
  blobs were transient — this is a defensive guard so they can't
  resurface.
- **#6 — policy editor modal title.** `PolicyEditor` takes an
  `initialTitle` prop; the list passes `p.title` so the header shows the
  name immediately instead of "Loading…".
- **#8 — AWS connection-row identifier.** When `account_identifier` is
  null (e.g. pending AWS rows), the row now shows `Added <date>` instead
  of a bare "—", so two AWS rows are distinguishable.

**▶ NEXT SESSION:** (1) The deferred PR-#4 review items below.
(2) The Azure scanner uplift brainstorm. (3) Incremental #6 — APNs push
end-to-end test. (4) The remaining 6 UI/UX-polish items KK deferred
(skeleton loaders, chat auto-titling, inline-editable risk Owner/Due,
single-bar "By cloud" chart, Connect-page layout, Trust-Center save
toast) — none blocking, batch in a dedicated UI-polish session.

### Gotchas paid in debugging time
- **Assumed-role creds expire at 1 h** → multi-region scans used to die
  with `RequestExpired`. Fixed via `RefreshableCredentials`.
- **Fargate cross-stack export deadlock** — a task-def ARN includes the
  revision number; exporting it ScanStack→ApiStack deadlocks CFN. Pass
  the stable *family name* (`scanTaskDefFamily`), not the ARN.
- **Scanner block-buffered stdout** → a running scan was invisible.
  Fixed with `PYTHONUNBUFFERED=1`.
- **EC2 rejects non-ASCII** in a security-group `description`.
- Scanner unit tests: `cd platform/lambda/shasta_runner &&
  ./.venv/bin/python -m pytest app/tests/` (the `.venv` is gitignored;
  `main.py` imports `shasta.*` so it is NOT importable in that venv —
  it's verified structurally + via live scans).

### ▶ Next major piece — Azure scanner uplift (brainstorm fresh)
Apply the same three-stage / parallel / tier-aware architecture
(`scan_pipeline.py` is deliberately AWS-free, meant to be lifted to a
shared location). **Open design question to resolve in the Azure
brainstorm:** Azure's scope unit is the **subscription** (within a
tenant), not the region. Should the scanner scan **all active
subscriptions**, or let the user **choose** which to scan (some
subscriptions are dev/throwaway)? KK's lean: let the user choose. Note
Azure also has **regions within each subscription** — the three-stage
probe likely nests (subscriptions × regions).

## 🚀 Incremental hardening — #1–#4 shipped + deployed (2026-05-20b)

Four scoped fixes, each TDD'd, committed, and deployed.

**Incremental #6 — APNs push end-to-end test — still pending** (deferred;
the scanner uplift took priority — see the top section for what's next).
Fire a synthetic "act now" finding and confirm the push notification
lands on KK's iPhone (APNs via SNS Mobile Push is wired but never
verified since the v2 cutover). After #6 the major roadmap begins —
see "Roadmap" below.

**Shipped this session:**
- **#1 — `ai_scanner` test rot fixed.** Deleted dead `writer.py` +
  `test_writer.py` (superseded by `unified_writer.py`); regenerated 4
  detector golden fixtures for SP1's `FindingEmission` schema. No deploy
  needed. Commit `6ab40af`.
- **#2 — `check_id → title` catalog.** `scripts/check_titles.py` — 292
  curated generic titles, served as `check_title` by `findings_list` /
  `findings_rollup`; web + iOS consume it; the old strip-heuristic is
  gone. Read-time (no rescan). Commit `7b0b614`. Deployed: API hotswap +
  web.
- **#3 — Bedrock / AI-Lambda inventory.** Shasta's
  `discover_aws_ai_services` drops Bedrock guardrails + AI-Lambda
  functions; the scanner now discovers them itself
  (`ai_pass.discover_bedrock_and_ai_lambdas`) and emits
  `bedrock_guardrail` / `lambda_ai_function` entities. Commit `361a559`.
  Deployed: `shasta-runner` image.
- **#4 — FedRAMP + PCI DSS mappings.** `scripts/framework_map.py` —
  287/292 checks mapped to NIST 800-53 Rev 5 + PCI DSS v4.0.1 controls;
  `merge_framework_map` applied at scan time in all 4 scanners + the AI
  pass. Commit `9f13b4b`. Deployed: web + all 4 scanner images. FedRAMP/
  PCI controls appear on findings at each cloud's next scan.

**#5 (iOS Policies / Questionnaires / Trust views) — cancelled.** iOS is
being rethought as a lightweight companion app (push alerting + hand-off
to Slack / Teams / Jira), not a web-feature port. See Roadmap → item 7.

**Shasta is reference-only.** `~/Projects/Shasta` (and the Shasta GitHub
repo) is a read-only dependency — never edit it. Shasta bugs are worked
around in *this* repo; #3 is the worked example.

### Roadmap

Incremental list: #1–#4 done, #5 cancelled, **#6 = APNs push test (next)**.

Major items, each its own brainstorm → spec → plan before build:
1. **Scanner comprehensiveness uplift** — AWS Security Hub parity, then
   bring Azure / GCP / Entra to the same depth + accuracy. (The first
   big change after #6.) **Slice 0 shipped 2026-05-21 — see the top
   section. Slices 1-6 remain.**
2. **Dynamic dashboards & reports** generated from chat.
3. **Tech-stack-aware threat-intel feeds** — beyond KEV: EPSS, NVD,
   vendor advisories, filtered per tenant.
4. **Unified vulnerability / risk prioritisation register.**
5. **Attack-path analysis** — graph-based, to crown-jewel assets.
6. **AI-powered MDR** — agentic detection + managed (reversible) response
   on the real-time event pipeline.
7. **iOS revamp** — companion app: push alerting + hand-off findings /
   issues to the team over Slack / Teams / Jira (MCP-based). Done after
   the first six majors.

## 🚀 AI Discovery — cloud-AI connector + findings overhaul (2026-05-20)

**Status: shipped, deployed, merged to `main`.** Spec:
`docs/superpowers/specs/2026-05-20-ai-discovery-connectors-design.md`.
Plan: `docs/superpowers/plans/2026-05-20-ai-discovery-cloud-ai.md`.

**What landed (cloud-AI connector — completes the Discovery module's
cloud surface):**

- **`shasta_runner/app/ai_pass.py`** — new module. Wraps Shasta's
  `discover_aws_ai_services` (SageMaker/Comprehend), `run_full_aws_ai_scan`
  (15 AWS-AI checks), and `compliance/ai` mapper. Folded into **every AWS
  scan** via the `shasta_runner` handler — no separate connection/trigger.
- AI services emit as `domain='cloud'` entities (`sagemaker_endpoint`,
  `sagemaker_model`, `sagemaker_training_job`, `comprehend_endpoint`) +
  `aws_account → contains` edges. AI findings carry `frameworks` with
  **NIST AI RMF / ISO 42001** (also EU AI Act, OWASP LLM, MITRE ATLAS)
  control IDs.
- **`unified_writer` fix** — `_insert_finding` previously hardcoded the
  `findings.frameworks` column to `'{}'`; it now persists
  `FindingEmission.frameworks`. This is what lets `compliance_summary`
  roll AI frameworks into the compliance view (and is a latent fix for
  cloud SOC 2 / CIS findings too — they were also losing framework data).

**Deployed:** `shasta-runner` image rebuilt + pushed to ECR; Lambda
`ciso-copilot-shasta-runner` updated (CodeSha256 `a81711b4…`). Empty-event
smoke test passed (all imports load in the Lambda runtime). **28 unit
tests pass.**

**First E2E (scan `053072ba`, 2026-05-20) — exposed a pre-existing
writer bug, now fixed.** The scan ran cleanly and `ai_pass` succeeded
(`ai_pass: 0 entities, 255 findings` — KK's account has no SageMaker/
Comprehend; the 15 AWS-AI checks produced 255 findings). But `commit_scan`
then failed with Postgres **42P18** (`could not determine data type of
parameter $8`) and rolled the whole transaction back — zero findings
written. Root cause: `unified_writer` passed nullable params
(`evidence_packet`, `subject_entity_id`) as typeless NULLs inside
`CASE WHEN :x IS NULL` — Postgres can't type a NULL-only param in an
`IS NULL` test. **This bug has silently broken every cloud scan since
SP1** (the 2026-05-19 scan failed the same way). Fixed (commit `d17c500`)
— plain typed `CAST(:x AS T)`; `shasta_runner` + `ai_scanner` images
rebuilt and redeployed.

**Second E2E exposed finding-ingestion bugs (2026-05-20) — fixed.** The
re-scan succeeded but the output was wrong: `unified_writer` hardcoded
every finding to `domain='ai'` + `status='fail'` (so cloud IAM/storage/
encryption checks showed inside the AI group, and `not_assessed`
"Unable to check …" per-region results showed as failures), and INSERTed
a fresh row per scan (counts doubled on every rescan). Fixed (commit
`62357b2`): `FindingEmission` now carries real `domain`/`status`/`region`;
`not_assessed`/`not_applicable` results are dropped at ingestion;
`_insert_finding` UPSERTs on a natural key `(tenant, conn, check_id,
resource_arn, region)`; migration `008` adds the unique index and purged
the 3,734 accumulated junk rows. `shasta_runner` + `ai_scanner` redeployed.

**Demo gate — PASSED** (scan `5c62e6d3`, 2026-05-20 23:30): 147 findings,
correctly categorized — storage 65, iam 20, **ai 17**, logging 14,
encryption 13, networking 10, monitoring 8; status 85 pass / 32 fail /
30 partial; zero `not_assessed` noise. (AWS scan takes ~13 min — wait for
`scan complete` in the logs before checking.)

**Findings UX rebuilt + deployed (2026-05-20).** The dashboard now shows
**Fail / Partial / Pass** tiles (was a single fail-only "Open findings");
the Findings page shows *all* findings with a user-chosen grouping —
**Status · Category · Cloud · Compliance Framework** (default Status) —
rolled up by check into cards with **generic titles** (quoted resource
names stripped; the real title + ARN are in the drill-in). Backend:
`findings_summary` returns `by_status`; `partial` added to
`ALLOWED_STATUSES`. This resolves the AI-group-overcount and Bug 5.

**Known limitations / open items:**
- ~~Bedrock model inventory~~ ✅ RESOLVED — incremental #3 (2026-05-20b).
  The scanner discovers Bedrock guardrails + AI-Lambda functions itself
  (`ai_pass.discover_bedrock_and_ai_lambdas`); Shasta untouched.
- ~~Generic finding titles heuristic~~ ✅ RESOLVED — incremental #2;
  replaced by the curated `check_id → title` catalog.
- ~~`ai_scanner` test rot~~ ✅ RESOLVED — incremental #1.
- No route-level web tests for `Dashboard.tsx` / `TopRisks.tsx` — the repo
  has no route-test precedent; verified via type-check + a live endpoint
  smoke test.

## 🚀 SP4 Phase 4a deployed — chat-first front door (text)

On branch `feat/sp4-chat-first` (SP1 + Slice 1b already merged to `main`).
Spec: `docs/superpowers/specs/2026-05-19-sp4-chat-first-design.md`. Plan:
`docs/superpowers/plans/2026-05-19-sp4-chat-first.md` (4 mini-slices; 4a done).

**What landed (Phase 4a — Shell + text chat):**

- **DB**: migration `006_conversations.sql` — `conversations` +
  `conversation_messages` tables (applied to prod Aurora).
- **`chat_session` Lambda** — one code asset (`platform/lambda/chat_session/`),
  deployed as TWO functions:
  - **`ChatSessionFn`** — `main.handler`, API Gateway REST. 7 routes:
    `POST/GET /v1/conversations`, `GET/PATCH/DELETE /v1/conversations/{id}`,
    `POST /v1/conversations/{id}/messages`, `POST /v1/conversations/{id}/voice`.
  - **`ChatStreamFn`** — `messages_stream`/`app.py` Starlette ASGI app under
    **Lambda Web Adapter**, Function URL with `RESPONSE_STREAM`. Serves
    `POST /v1/conversations/{id}/stream` — Anthropic streaming text turns,
    SSE (`data: {"type":"text-delta",...}` / `{"type":"done"}`).
    Function URL: `https://otc43ep2sidkuyv5uaxpclljsu0rkvbr.lambda-url.us-east-1.on.aws/`
- **Web** — `/` is now the chat surface (`ChatShell`: ModuleRail +
  ConversationRail + ChatCenter); the old Welcome page moved to `/dashboard`.
  Conversation CRUD + landing flow (load most-recent <24h or create fresh) +
  token-streamed assistant replies. Deployed to `shasta.transilience.cloud`.

**Gotcha paid in debugging time (load-bearing):**

- **AWS Lambda's managed Python runtime CANNOT do response streaming.**
  `InvokeMode: RESPONSE_STREAM` only streams on Node.js managed runtimes.
  The plan originally routed Anthropic streaming through a plain Python
  Lambda Function URL — it deployed but returned `'NoneType' has no
  attribute 'write'`. Fix: `ChatStreamFn` runs a Starlette app under
  **Lambda Web Adapter** (LWA layer `arn:aws:lambda:us-east-1:753240598075:layer:LambdaAdapterLayerX86:27`,
  env `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`, `AWS_LWA_INVOKE_MODE=response_stream`,
  handler `run.sh` → `uvicorn`). `ChatSessionFn` (REST only) is fine on the
  normal managed runtime.

**Phase 4a demo gate — first authed test FAILED, root cause fixed, awaiting retest.**
KK's first sign-in test (2026-05-20): message sent but no reply; refresh
showed the conversation row but no message text. Root cause: `ChatStreamFn`'s
`_verify_jwt` crashed importing `cryptography` —
`_rust.abi3.so: cannot open shared object file`. The `chatStreamAsset`
bundling installed the `cryptography` wheel (via `PyJWT[crypto]`) for the
host platform, not Lambda's linux x86_64. Every JWT verification failed →
streaming endpoint returned `unauthorized` for every request → no replies,
nothing persisted (the LWA app is what writes both user + assistant rows).
**Fixed** (commit `7c87069`): added `platform: 'linux/amd64'` + manylinux
x86_64 pip flags to the bundling, matching `AiGithubFn`. Redeployed +
verified the `.so` import error is gone. **Next: KK retries the authed
demo; if it passes, Phase 4b (tools + 8 artifact components).**

**SP4 bug-fix round (2026-05-20) — 6 bugs from KK's testing, all fixed + deployed:**
1. **Phantom "Bye"/empty voice messages** — Whisper hallucinates on silence.
   Fixed: client drops empty/whitespace transcripts; server VAD tuned
   (threshold 0.5→0.6, silence 500→700ms).
2. **No compliance-control details** — `TOOL_RULES` over-blocked. Fixed:
   the model may now answer GENERAL framework knowledge (what MCSB AM-1 /
   SOC2 CC2.1 require) from its own knowledge; customer-specific data stays
   tool-gated.
3. **Donut all-red / zero segments** — both `tools.ts` AND server
   `tools_dispatch.py` set explicit red segment colors. Fixed: hint sends
   no color; `ChartDonut`/`ChartBar` apply an 8-hue palette; zero segments
   muted.
4. **Voice reply split into 2 bubbles** + **5. assistant text above the
   user's question** — transcripts were appended in event-arrival order.
   Fixed: voice messages keyed by Realtime `item_id` (`voiceUpsert`
   reducer action); user placeholder created on `conversation.item.created`
   so it lands before the assistant reply; late async transcript fills it.
6. **"IAM issues" returned Key Vault findings** — `query_findings` had no
   domain filter. Fixed: added a `domain` enum param (`findings.domain`:
   iam/storage/encryption/…) so the model can scope queries.
Commits `0b92f4b`, `a0aa068`, `d034e80`, `f93d593`, `4fa55cd`. The earlier
"deferred 4b/4c polish" items (donut, TOOL_RULES) are now resolved.

**SP4 Phase 4d deployed (2026-05-20) — action approvals. SP4 feature-complete.**
The chat can now propose actions and the user approves them:
- `propose_risk_entry` / `propose_policy_draft` tools return a pending
  `approval_card` (NEVER auto-execute — determinism invariant).
- Clicking **Approve** → `POST /risks` or `/policies`, idempotent on the
  card's `approval_id` (atomic `INSERT ... ON CONFLICT DO NOTHING
  RETURNING` — double-tap safe). Card → `approved` with a link.
- Edit-in-place before approving; Cancel. Card state persists via
  `PATCH /v1/conversations/{id}/messages/{message_id}` — reload shows the
  final state.
- Migration `007_approval_idempotency.sql` — `source_approval_id` on
  `risks` + `policies` with a partial unique index.
- The pre-SP4 voice modal (`web/src/voice/`) retired; `excelHelpers.ts`
  moved to `web/src/lib/`.
- **Phase 4d demo gate — pending KK's test:** in chat, "add X to my risk
  register" → editable approval card → Approve → risk created. Same for a
  policy draft. Double-click Approve = no duplicate.
- **SP4 status:** all 4 phases (4a shell+text · 4b tools+artifacts · 4c
  voice · 4d approvals) built + deployed on `feat/sp4-chat-first` and
  merged via PR #3 (commit `06cb4f6`). Deferred polish (see items above):
  compliance donut visual, TOOL_RULES general-knowledge tuning. iOS = SP4.5.

**SP4 Phase 4c deployed (2026-05-20) — voice.** The chat surface now has
voice via OpenAI Realtime over WebRTC:
- **Model: `gpt-realtime-2`** (OpenAI's newer GPT-5-class realtime model —
  validated against the live API; drop-in over `gpt-realtime`).
- **`voice.py`** mints the Realtime ephemeral key with the full persona
  (`prompts.py` — PERSONA + TOOL_RULES + VOICE_ADDENDUM) + the 12-tool
  catalog (the browser supplies the Realtime-shaped tool defs).
- **`voiceClient.ts` + `turnQueue.ts`** — browser WebRTC client (lifted
  from the proven `web/src/voice/` client). Voice tool calls execute
  browser-side via the TS `executeTool`. Transcripts persist per-turn to
  `conversation_messages` (`modality: "voice"`); `fetch(keepalive:true)`
  flushes the pending turn on page unload.
- **Voice UI** — mic toggle in the composer (off/connecting/on), persimmon
  breathing dot in the header, live transcripts into the stream, barge-in
  (`response.cancel` on user speech), sync-warning banner.
- **Gotcha — OpenAI Realtime rejects `session.metadata`.** The first mic
  test failed: every `/v1/realtime/client_secrets` mint 400'd with
  `"Unknown parameter: 'session.metadata'"`. `voice.py` had bound
  `conversation_id` into `session.metadata` — OpenAI's Realtime API has no
  such field. Removed it (commit `9735d36`); `conversation_id` doesn't need
  to reach OpenAI — the browser owns the conversation binding. Lesson:
  validate the FULL session payload against the live API, not a minimal one.
- **Gotcha 2 — voice mint response field.** After the metadata fix the mint
  200'd but the mic still failed: `voice.py` returned the ephemeral key as
  `client_secret`, the web `voiceClient` reads `.value`. Mismatch → `Bearer
  undefined` to `/v1/realtime/calls` → 401. Fixed (commit `713a315`):
  `voice.py` returns `value`. Lesson: the 4c.1 + 4c.2 reviews each checked
  one side vs the spec; neither cross-checked the two sides of the
  Lambda↔client contract.
- **Phase 4c demo gate — pending KK's mic retest** (after the metadata fix):
  toggle the mic, hold a spoken conversation, verify transcripts stream +
  persist + tools work + barge-in. Spec §15: same questions in text vs
  voice → same tool calls / same results.

**SP4 Phase 4b deployed (2026-05-20) — tools + artifacts.** The chat can
now query real tenant data and render it as cards:
- **`tools.ts`** — 12-tool TS catalog (`web/src/chat/`): 8 data, 2 action
  (`propose_*`), 2 side-effect. Used by the browser for the landing
  briefing + (later) voice.
- **`tools_dispatch.py`** — Python server-side mirror in the chat_session
  Lambda. The text path runs the **Anthropic agentic tool-use loop
  server-side** inside the LWA app (`app.py`): the model calls tools, the
  Lambda executes them against Aurora (tenant-scoped), streams back
  `text-delta` + `tool-result` SSE events. Max 6 tool rounds.
- **8 artifact components** + `Artifact.tsx` renderer (`web/src/chat/
  artifacts/`) — kpi_card, entity_list, finding_card, risk_card,
  chart_bar, chart_donut, severity_breakdown, approval_card. Rendered
  inline in the chat stream; persisted as `tool` messages so they
  reconstitute on reload.
- **SourceSideSheet** — clicking a card's `↗ source` chip opens a
  right-edge panel with the underlying entity/finding.
- **Landing morning briefing** — a fresh conversation auto-runs
  `get_morning_briefing` and shows 2-3 posture cards.
- Determinism invariant intact: the LLM never writes — `propose_*` tools
  return pending approval cards only (the approve→POST is Phase 4d).
- **Known 4b limitation:** persisted `tool` messages aren't replayed into
  the Anthropic history across turns (they lack the tool_use/tool_result
  block IDs), so the model re-derives tool calls each turn rather than
  "seeing" prior tool outputs. Cards still reconstitute on reload. Fine
  for 4b; revisit if multi-turn tool memory is needed.
- **Phase 4b demo gate — PASSED** 2026-05-20 (KK: "works like a charm",
  findings + AI inventory render with real data).
- **Deferred 4b polish:** the `chart_donut` for compliance posture renders
  but is visually ineffective — revisit the donut component (sizing /
  legend / segment clarity). KK flagged, agreed to improve later.
- **Deferred 4c polish (prompt tuning):** asked "details for CC 2.1" the
  assistant declined ("not available from findings"). `TOOL_RULES`
  over-constrains — it should let the model answer GENERAL compliance/
  security knowledge (what a control like SOC 2 CC2.1 requires) from its
  own knowledge, while keeping CUSTOMER-SPECIFIC data (the tenant's status
  for that control) gated behind tools. Refine the `prompts.py` TOOL_RULES
  to draw that line. (Bigger future option: a compliance-control reference
  tool/KB.) KK flagged 2026-05-20, agreed to iron out later.

**Post-4a-demo-gate additions (2026-05-20, KK feedback during testing):**
- **Rename + Delete on conversations** (`ConversationRail` hover → ⋯ menu,
  inline rename, delete-with-confirm; backend `PATCH`/`DELETE` already
  existed). Commit `35d6801`.
- **Legacy screens re-themed to Quiet Paper.** Tailwind `blue`/`slate`/
  `white` scales remapped in `web/tailwind.config.js` + `index.css` body —
  flips all ~17 route files to the warm cream/persimmon palette without a
  per-file sweep. Commit `836c256`. Chat surface (`web/src/chat/*`) stays
  on its own inline-hex Quiet Paper styling — two styling systems, same
  palette; a future pass could unify them.
- **Voice mic** is NOT in Phase 4a — it's Phase 4c by design. The 4a
  composer is text-only.

**Gotcha — Python Lambda native deps:** ANY Python Lambda bundling a
package with a compiled extension (`cryptography`, `pydantic-core`, etc.)
MUST bundle with `platform: 'linux/amd64'` + `pip install --platform
manylinux2014_x86_64 --implementation cp --python-version 3.12
--only-binary=:all:`. Otherwise pip on an Apple-Silicon Mac installs the
wrong-arch wheel and the Lambda fails at import. `AiGithubFn` and now
`ChatStreamFn` do this. Pure-Python deps (starlette, uvicorn, PyJWT
itself, boto3) don't need it — but `PyJWT[crypto]` pulls `cryptography`,
which does.

## 🚀 Slice 1b shipped — what's new since the last update

End-to-end on 2026-05-19 against `kkmookhey/ciso-copilot`:
KK clicks **Scan** on a repo → 3 real AI assets (framework `langchain`,
models `openai/gpt-realtime` + `openai/whisper-1`) discovered, evidence
packet visible on web + iOS.

**What landed:**

- **ai_scanner container Lambda** (`ciso-copilot-ai-scanner`). x86_64, 2048
  MB, 600 s, 4 GB ephemeral. Triggered by SQS `ai-scan-queue` (DLQ:
  `ai-scan-dlq`, maxReceiveCount=3, batchSize=1, maxConcurrency=5).
  Clones repo via GitHub App installation token; runs 8 deterministic
  detectors + a cross-detector correlator; writes assets/relationships/
  findings transactionally to Aurora.
- **8 detectors** (`detectors/{framework, model_usage, mcp_server,
  agentic_workflow, vector_db, embedding, prompt, secrets_in_ai_code}.py`)
  plus `correlator.py`. All deterministic. Each emission carries a Trust
  Evidence Packet per the §7 spec.
- **`ai_scan_api` Lambda** with 5 routes wired to API Gateway:
  `POST /v1/ai/scans`, `GET /v1/ai/scans`, `GET /v1/ai/scans/{id}`,
  `GET /v1/ai/assets`, `GET /v1/ai/assets/{id}`.
- **Web**: `/ai/inventory` (grouped-by-repo asset table with type filter
  chips), `/ai/inventory/:asset_id` (detail + collapsible evidence packet
  + GitHub deep-link), RepoPicker now has working Scan button with 3s
  status polling, sidebar has an **AI inventory** link, Connect page
  shows existing GitHub installations with "Manage repos →" so customers
  don't have to remember connection-id URLs.
- **iOS**: 5th-→6th tab **AI** (`brain.head.profile` icon) between
  Register and Connect. `AIInventoryView` (List grouped by repo, pull-to-
  refresh) + `AIAssetDetailView` (Form with attributes + DisclosureGroup
  for the raw evidence packet).
- **DB**: `ai_scans`, `ai_assets`, `ai_relationships` populated by the
  scanner. Repository nodes upserted by the API on scan trigger.

**Gotchas paid in debugging time today** (real ones — read these before
touching the scanner Lambda):

1. **`logging.basicConfig` is a no-op inside Lambda.** AWS Lambda's
   Python runtime sets up the root logger BEFORE user code runs, so
   `basicConfig` silently doesn't change levels. Use `basicConfig(...,
   force=True)` or `log.setLevel(...)` directly on the named logger. The
   scanner ended up using `print()` for the per-detector counts because
   that bypasses the logger entirely and always lands in CloudWatch.

2. **`'*'` is invalid in a Secrets Manager `SecretId`.** IAM resource
   ARNs use `*` as a wildcard, but `secretsmanager:GetSecretValue` rejects
   it with `ValidationException`. Drop the `*` in the env var (`scan-
   stack.ts` line setting `GITHUB_APP_SECRET_ARN`). The IAM policy may
   still use `secret:.../credentials*` (resource-level glob is fine).

3. **`--no-color` is not a real ripgrep flag.** Original `_walk.py` used
   it; ripgrep rejects with "unrecognized flag" and every detector test
   would have failed silently. The correct flag is `--color=never`.
   Fixed before the demo.

4. **`model_usage` was too SDK-centric in v0.1.0.** Originally only
   detected files with `from openai`/`from anthropic`/`bedrock-runtime`
   imports + kwarg-style `model="..."`. Missed the raw-HTTPS-to-API
   pattern that's actually common (our own `anthropic_call.py` uses
   `urllib.request` + `json.dumps({"model": MODEL, ...})`). Broadened
   in v0.2.0 to also accept API URL substrings (`api.openai.com`,
   `api.anthropic.com`) as provider signals AND match JSON-style
   `"model": "..."` literals in addition to kwargs.

5. **Container Lambdas can't hotswap.** `cdk deploy --hotswap` swaps
   env vars but won't redeploy a new image. After `./build.sh` (which
   pushes to ECR), call `aws lambda update-function-code --image-uri
   ...:latest` + `aws lambda wait function-updated`.

6. **GitHub mirrors only have what's pushed.** Local main was 39 commits
   ahead of `origin/main` (`419c7cc..b226821`) — meaning Slice 1a, the
   whole F-phase work, and the Slice 1b platform commits weren't visible
   to the scanner. The scanner only sees what's on GitHub. Pushed on
   2026-05-19; the demo only worked after that.

7. **Connection URLs are fragile.** A revoked/replaced GitHub install
   leaves a stale connection_id in the user's browser bookmark/URL bar
   and `/ai/connections/{stale-id}/repos` returns 404. Fixed by listing
   active installations on `/connect` with "Manage repos →" links so
   users always reach a live ID.

## 🆕 Expanding scope: Cloud → Cloud + AI Security platform (2026-05-18)

CISO Copilot is expanding to absorb **AI-security capabilities** (originally
prototyped in `~/Projects/Denali`). Denali's vision/MVP docs describe
the work; the actual implementation lives **here**, in this codebase, on
this stack. The Denali folder is reference material — not a separate repo
we maintain.

**What "AI Security" means inside CISO Copilot:**

- New connector types alongside AWS/Azure/GCP/Entra: GitHub, OpenAI,
  Anthropic (and limited AWS-AI: Bedrock, Lambda, IAM-as-it-applies-to-AI).
- New entity types in our trust model: agents, models, prompts, vector DBs,
  MCP servers, tools, datasets.
- New finding/event types: "MCP server with prod GitHub creds", "agent
  with autonomous loop", "unapproved model provider", "prompt mutated this
  week", etc.
- New scanner: a `shasta_runner_ai` (or equivalent) Lambda that ingests a
  GitHub repo + an OpenAI/Anthropic API and produces AI-specific findings,
  alongside the existing cloud scanners.
- New produced artefacts: **AIBOM** (AI Bill of Materials), **Trust Evidence
  Packets**, **Blast Radius** traces.
- New surface: an **MCP server** so Claude Desktop / MCP-compatible clients
  can query CISO Copilot conversationally. This sits alongside our existing
  web + iOS surfaces (and is distinct from the OpenAI Realtime voice surface
  we already have).

**Load-bearing invariants from the Denali vision (must respect, per `~/Projects/Denali/denali-vision.md` §II):**

1. **Determinism is the spine. AI is the surface.** LLMs never write to the
   graph, never declare a violation, never take an action. Detectors are
   deterministic. Our existing pattern of "scanner produces findings; AI
   enrichment is contextual" is already consistent with this — keep it.
2. **Every conclusion carries evidence.** Every output (risk score, blast
   radius, recommendation) ships with a signed, replayable Trust Evidence
   Packet (graph trace + source events + reasoning chain + model+version
   used + confidence + timestamp). This is new — our findings don't yet
   ship with evidence packets.
3. **MCP-first.** Building features queryable via MCP from day one.
4. **Reversibility non-negotiable** for any action against customer envs.
   We don't take actions today — keep it that way until evidence-packet +
   policy framework is in.
5. **Open by default** for schemas (AIBOM, evidence packets, graph model).
   The schemas should be designed as if they'd be extracted to public
   standards — even if we never extract them.
6. **Quality before reach.** First AI connector (GitHub) genuinely excellent
   before second (OpenAI) gets attention.

**What from `~/Projects/Denali` is worth porting:**

- ✅ **The design docs** (`denali-vision.md`, `denali-mvp.md`, `docs/superpowers/specs/`).
  Bring these into `~/Projects/CISOBrief/docs/` as the AI-security PRD.
- ✅ **The MVP scope discipline** (Denali CLAUDE.md §5 — out-of-scope list).
- ✅ **The protobuf event schemas** (`spec/events/envelope.proto`) — if we
  want to standardize event payloads. *Decision pending.* Our current events
  are JSONB in Postgres; protobuf would be over-engineering unless we open
  the event format as a public standard.
- ❌ **The Go platform skeleton** — not porting. Our stack is Python Lambdas
  + CDK; adding Go is a stack change with no payoff at MVP scale.
- ❌ **Neo4j / Redpanda / OpenSearch** — same reason. Aurora Postgres can do
  graph queries via JSONB + recursive CTEs at our scale; EventBridge is our
  bus; we don't need OpenSearch yet. Revisit only if Postgres genuinely
  doesn't scale.
- ⚖️ **Next.js UI scaffold** — we already have a Vite+React SPA. Keep ours;
  do not migrate frameworks.
- ⚖️ **MCP server** — yes, but in Python on Lambda, not Go. New
  `lambda/mcp_server/` that speaks MCP protocol over either WebSocket or
  HTTP, authenticated by Cognito JWT.

**Architecture decisions — LOCKED 2026-05-18 (KK approved):**

1. **Graph storage = Aurora Postgres.** Reuses the existing `cisocopilotdata`
   cluster. Recursive CTEs over `ai_relationships` for traversal (blast
   radius, reachability). JSONB columns on entity tables for type-specific
   attributes. Neptune Serverless was the alternative — it has a minimum
   baseline of ~$87/mo *empty* (1.0 NCU × 730hrs) climbing to $175–350/mo
   under modest load — pay-from-day-one cost we'd absorb before any revenue.
   Postgres handles graph workloads at our scale (target: hundreds of
   thousands of nodes, millions of edges) without breaking a sweat;
   migration to Neptune is a focused per-table move if/when a specific
   query genuinely doesn't perform.
2. **AIBOM schema = dedicated tables.** New `ai_assets` (entities — agents,
   models, prompts, vector DBs, MCP servers, tools, datasets, credentials)
   and `ai_relationships` (calls/accesses/deploys/retrieves/invokes/
   generates/orchestrates/trusts edges). Separate from `findings` because
   the AIBOM is shape-different (inventory + graph, not pass/fail).
   Schemas designed to be portable as an open spec — column names and
   shapes should make sense outside CISOBrief.
3. **GitHub connector = GitHub App.** Per-tenant install via one-click
   onboarding flow mirroring the existing AWS CFN pattern. Webhook +
   installation token model; no PATs. Required permissions: Contents (R),
   Metadata (R), Actions (R), Pull requests (R), Webhooks (R/W on install).
4. **MCP server deferred** (revised 2026-05-18, brainstorm). Original lock
   was HTTP-SSE on API Gateway with Cognito JWT, shipped from day one.
   Revised: MCP is **not in Slice 1**. Slice 1 ships web + iOS only. MCP
   becomes its own slice after the cloud+AI inventory loop is solid.
   Rationale: forcing MCP into the first AI slice would add 2–3 days of
   OAuth-2.1-PKCE + SSE plumbing to the front of every demo path, while
   the user-visible surfaces for CISO Copilot today are web + iOS, not
   Claude Desktop. The Denali vision treats MCP as primary; inside CISO
   Copilot it is "later." When MCP does ship, target the SSE-on-API-
   Gateway + Cognito-as-OAuth pattern.
5. **Naming = "CISO Copilot" everywhere.** No "Denali" sub-brand inside
   the product UI. AI-security surfaces are tabs/sections (e.g. "AI
   Inventory", "Trust Graph", "AI Risks") that sit beside cloud surfaces.
   "Denali" survives only as a reference name in the design docs.
6. **Slice 1 = three vertical mini-slices.** 1a (GitHub App + repo
   picker, ≈5d) → 1b (scanner + 8 detectors + AI Inventory on web +
   iOS read-only, ≈8d) → 1c (relationships + cytoscape.js trust graph
   + AI Risks tab, ≈5d). Total ≈18 days. Each mini-slice ends with a
   working demo. Full spec at
   `docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md`.
7. **Detection scope = wide.** All 8 detectors from Denali MVP §6.1 ship
   in Slice 1: frameworks, model usage, MCP servers, agentic workflows,
   vector DBs, embeddings, prompts, secrets-in-AI-code. Detector 4
   (agentic_workflow) is the fuzziest and ships with `confidence='medium'`
   pending real-world tuning.
8. **Onboarding = install then user-triggered scans.** Not auto-scan-all
   on install. Customer installs the GitHub App, picks repos in a web UI
   picker, clicks Scan per repo. Avoids the "why is it scanning my
   dotfiles" problem at install time.
9. **Evidence packets = format-only, no crypto in Slice 1.** Designed as
   an open spec (versioned JSON), stored inline as JSONB on each
   emitting row. KMS asymmetric signing deferred. AI-side only —
   cloud-finding backfill is a future slice.

These are enterprise-grade choices for a pre-scale product: minimize fixed
infrastructure cost, maximize reuse of the production patterns we've already
hardened (Cognito, CDK, Lambda Proxy, Aurora Data API, our CORS+gateway-
response setup), keep onboarding parity with the cloud connectors.

---

## 🚀 Next session — start here

Slice 1 design landed 2026-05-18. Full spec at
`docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md`. The
spec supersedes the bullet list that previously lived here.

**Order of work for Slice 1 (≈18 days, three vertical mini-slices):**

1. **Mini-slice 1a — GitHub App + repo picker** (≈5d). Register the
   CISO Copilot GitHub App. Build install URL + callback endpoints.
   Build `RepoPicker.tsx` and `ConnectClouds.tsx` "Connect GitHub" card.
   Demo at end of 1a: KK installs on his real GitHub, sees his repos
   listed in the web UI.

2. **Mini-slice 1b — Scanner + AI Inventory** (≈8d). SQL migration
   (`004_phase_ai.sql`): `ai_connections`, `ai_assets`,
   `ai_relationships`, `ai_scans` tables + `findings.evidence_packet`
   column. New `lambda/ai_scanner/` container Lambda with 8 deterministic
   detectors (framework, model_usage, mcp_server, agentic_workflow,
   vector_db, embedding, prompt, secrets_in_ai_code). SQS queue for
   throttled fan-out. New `/v1/ai/scans` and `/v1/ai/assets` endpoints.
   AI Inventory tab on web + read-only AI tab on iOS. Demo: KK scans 3
   real repos, sees real AI assets with evidence packets.

3. **Mini-slice 1c — Relationships + Trust Graph + AI Risks** (≈5d).
   Recursive CTE for `GET /v1/ai/repos/:id/graph`. Cytoscape.js
   per-repo trust graph view on web. AI Risks tab on web; segmented
   control on iOS Risks tab. Per-asset relationships in AssetDetail.
   Demo: per-repo trust graph + AI-typed findings separately surfaced.

**Out of Slice 1 (deferred to future slices):** MCP server, limited
AWS-AI (Bedrock) connector, blast radius, KMS-signed evidence packets,
cloud-finding evidence-packet backfill, push-webhook rescan-on-commit,
sparse checkout for monorepos, all-repos aggregate trust graph.

Do not start before reading `~/Projects/Denali/denali-vision.md` (§II,
§III, §IV) — the 8 invariants and the production sequence are
load-bearing. The vision is more important than any of the
implementation choices above. The Slice 1 spec respects invariants
1, 2, 5, and 6; invariants 3 (MCP-first) and 4 (reversibility) are
out of scope for this slice and remain commitments for later slices.

The rest of this file (live URLs, what works, gotchas, etc.) describes the
cloud-security half of the platform and remains current.

## Status, at a glance

Phases 0 + A + B + C + D + E are deployed. End-to-end sign-in (Google) +
AWS onboarding + scan + findings + **voice via OpenAI Realtime over WebRTC**
all confirmed working on KK's iPhone 16 Pro Max against AWS account
`470226123496`. **Web sign-in via Google verified end-to-end on 2026-05-18**
after recreating the Cognito user pool with `email: mutable: true` and
patching all Lambdas to emit CORS headers (iOS had hidden the missing
`access-control-allow-origin` because `URLSession` doesn't enforce CORS).
Real-time alert pipeline and brief generation are wired but not end-to-end
tested. Microsoft sign-in is unblocked for any *consenting* customer tenant
via lazy per-tenant Cognito IdP provisioning, but KK's own Transilience
tenant blocks user sign-in pending an "Assignment required = No" flip by
his admin.

## Live URLs + AWS account

| Surface | URL / ARN |
|---|---|
| AWS account | `470226123496` (us-east-1) |
| API base | `https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1/` |
| Web SPA | `https://shasta.transilience.cloud/` (custom domain live 2026-05-18; backed by CloudFront `shasta.transilience.cloud` which still works) |
| Asset CDN | `https://d2pvi2ahuyphb0.cloudfront.net/` |
| Cognito User Pool | `us-east-1_jOC1znCSS` (recreated 2026-05-18; old `us-east-1_ePRQ2iwZT` retained, awaiting cleanup) |
| Cognito iOS client | `2r71e13kahf79bvb9stuehm3il` |
| Cognito Web client | `5vroudnp54n7fdqvjj49ff53br` |
| Event bus | `arn:aws:events:us-east-1:470226123496:event-bus/ciso-copilot-events` |
| Aurora cluster | `cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh` (db: `ciso_copilot`) |
| iOS bundle | `ai.transilience.cisocopilot` |

## What works (verified end-to-end on 2026-05-18)

- **Google sign-in (iOS + web Cognito hosted UI)** with email-first home-realm discovery (iOS). Web still on the legacy generic-IdP-picker. Web sign-in + sign-out + sign-in-again all verified on 2026-05-18 (this was the test case that drove the pool recreate).
- **Tenant approval gate**: post-confirmation Lambda creates a `tenants` row in `pending` and emails `APPROVAL_RECIPIENT` (currently KK's Gmail; SES sender flipped to `kkmookhey@gmail.com` because `no-reply@settlingforless.com` isn't DKIM-verified yet).
- **AWS onboarding**: CFN one-click deep link → `CISOCopilotReader` IAM role + EventBridge forwarder created in customer account → `/onboarding/aws/complete` webhook flips `cloud_connections.status` to `active` and enqueues an initial scan.
- **AWS scanner**: 270+ findings produced against KK's own account across IAM, Organizations, CloudFront, Logging, Compute, Storage, Networking, Encryption modules. Visible in iOS Risks tab after pull-to-refresh.
- **Voice (OpenAI Realtime GA via WebRTC)**: tap mic on Overview tab → backend mints ephemeral `ek_...` via `POST /v1/realtime/client_secrets` → iOS WebRTC peer connection + data channel → full-duplex audio with Google AEC3 → tool calls (`get_top_risks`, `list_connected_clouds`) dispatch through our authenticated API and feed results back. Voice quality clean (no echo). **iOS only — web voice still to be lifted from Shasta.**
- **Real-time alert pipeline (AWS)**: synthetic GuardDuty finding routed via `event_router` Lambda → `events` table → surfaced via new `GET /events` endpoint → iOS Overview "Recent activity" + "Alerts" stat + web Welcome "Recent activity" + "Critical alerts" stat. Verified end-to-end on 2026-05-18 by direct Lambda invoke (PutEvents from same account is blocked by AWS for `aws.*` source prefixes).
- **Compliance posture per framework**: new `/compliance/summary` Lambda aggregates findings by framework + control_id, returns {passing, failing, total, score_pct} per framework. iOS Overview shows ComplianceRow per framework; web Welcome shows FrameworkCard tiles. Logic modelled on Shasta's `compliance/scorer.py`.
- **Multi-tenant data isolation**: personal-email-domain users (gmail.com/outlook.com/yahoo.com/icloud.com/etc.) now get a per-user tenant. Corporate domains keep shared-tenant behavior. Prior bug: KK's wife `randevak@gmail.com` was auto-joined to KK's `gmail.com` tenant and could see his clouds + scans. Fixed; she's now in her own pending tenant `693cffb6-...`.
- **iOS Share on finding detail**: `ShareLink` toolbar item, formats finding into shareable text (Slack/Teams/Mail/Messages/Jira via app via iOS share sheet).

## What's wired but not end-to-end tested
- **Daily brief / push notifications**: APNs setup exists from v1 era; APNs FROM v2 backend is via SNS Mobile Push but no test push has been fired end-to-end since the v2 cutover.
- **Azure onboarding (`/onboarding/azure/{initiate,complete}` + `cron-azure` scanner)**: code complete, no real subscription onboarded yet.
- **Entra onboarding**: code complete, scanner image works; KK's own dev tenant `017c6f31-...` already admin-consented to the app reg. **Sign-in** for company-tenant users blocked on his admin (separate from scanner).
- **GCP onboarding**: code complete, no real project onboarded. WIF binding hardcoded to fixed-name role `ciso-copilot-gcp-scanner` — *do not* let CDK auto-name this role; existing customer WIF bindings would break.
- **Web app sign-in / Microsoft multi-tenant**: web still uses generic `startSignIn` (Cognito Hosted UI picker). Google works; Microsoft would hit the same iss-mismatch we already fixed for iOS. Fix is to port iOS's email-first `/auth/discover-tenant` flow to web.

## Known gotchas (paid in debugging time today)

1. **API Gateway claims dict shape**: Cognito `identities` claim arrives as a *single dict*, not a list, in some Lambda invocation paths. Nine Lambdas patched to normalize: `if isinstance(ids, dict): ids = [ids]`. If you ever see `KeyError: 0` on `ids[0]`, this is the cause.

2. **Cognito IdP names**: max 32 chars, regex `[^_\p{Z}][\p{L}\p{M}\p{S}\p{N}\p{P}][^_\p{Z}]+`. **Underscores forbidden.** Use dashes. Our per-tenant Microsoft IdPs are named `MS-<first-29-hex-chars-of-tenant-id-without-dashes>`.

3. **Cognito multi-tenant Microsoft**: cannot federate via a single IdP — the id_token's `iss` claim is per-tenant. The discover-tenant Lambda lazily creates one Cognito OIDC IdP per customer tenant (`oidc_issuer: login.microsoftonline.com/<tenant>/v2.0`) and attaches it to the user-pool client's `SupportedIdentityProviders`. First user from a new tenant pays a ~1s provisioning hit; subsequent users hit a cached IdP.

4. **Email attribute mutability** *(FIXED 2026-05-18)*: pool `email` is now `Mutable: true`. Originally `Mutable: false`, which caused `user.email: Attribute cannot be updated` on *every* fresh federated re-sign-in (Cognito syncs email from the id_token on each sign-in; iOS hides this with refresh tokens, web hits it directly). Cognito's `UpdateUserPool` API refuses to flip mutability on a *standard* attribute in place — the pool had to be replaced. Done via construct-ID rename `UserPool` → `UserPoolV2`. Old pool `us-east-1_ePRQ2iwZT` retained (RETAIN was in effect on the old logical ID); manual delete pending.

5. **SES sandbox + Gmail spoof-drop** *(FIXED 2026-05-18 later)*: domain `settlingforless.com` now verified in SES (TXT + 3 DKIM CNAMEs + SPF TXT at apex, all published via Google Cloud DNS console). post_confirmation Lambda + `scripts/send_approval_email.py` now both `Source=CISO Copilot <no-reply@settlingforless.com>`. Earlier symptom: when sending FROM kkmookhey@gmail.com via AWS SES (because Gmail is the only verified-sender identity), Gmail silently spam-foldered or dropped them — Gmail From: arriving from non-Google IPs without Google DKIM signature looks like spoofing. Account still in SES sandbox (200/day, 1/s); sufficient since approval emails only go TO the verified `APPROVAL_RECIPIENT` (kkmookhey@gmail.com). Request prod access only when we want to send notifications/digests to other users.

6. **CFN templateURL must be S3, not CloudFront**: CloudFormation Console hard-rejects non-S3 URLs. We presign a 1-hour S3 GET URL on every `/onboarding/aws/initiate` call. The Lambda role has `s3:GetObject` on `arn:aws:s3:::ciso-copilot-cdn-470226123496/cfn/aws-onboard.yaml`. IAM perm propagation can take ~1 min after a fresh deploy; if the first presigned URL 403s, wait and retry.

7. **AWS Config `DeliveryChannel` limit = 1 per account/region**: CFN template defaults `EnableAwsConfig=false`. Customer can flip to `true` in the CFN review step if their account has no Config recorder. We still ingest Config item changes via the EventBridge forwarder, which is always created.

8. **Hotswap doesn't update IAM policies**: when a Lambda gains a new IAM permission, `cdk deploy --hotswap` will skip the policy update. Use a full `cdk deploy` for IAM changes.

9. **Voice over WebSocket vs WebRTC**: WebSocket + AVAudioEngine has no native AEC on iOS. At speakerphone volume the speaker bleeds into the mic, fires server-VAD, and produces "jumbled, repeating" output. **Use WebRTC.** The `stasel/WebRTC` SPM package supplies Google's AEC3. See `ios/CISOCopilot/Services/VoiceClient.swift` and `Projects/shasta-ios-poc/ios/ShastaPOC/Voice/RealtimeClient.swift` for the working pattern.

   **Web voice — use headphones.** Browser built-in AEC (`echoCancellation: true` in `getUserMedia`) is heuristic and falls apart on laptop speakers at full duplex. Without headphones, the model's own audio loops into the mic → Whisper transcribes the garbled output as random phonemes (often non-English) → model thinks user spoke and responds in whatever language it "heard" → spiral. The system prompt now hard-codes "respond in English only" to cap the drift, but the real fix is closed-cup headphones. iOS doesn't have this issue because `stasel/WebRTC` ships Google's AEC3 which is dramatically better than browser AEC.

10. **OpenAI Realtime GA event names**: `response.audio.delta` → `response.output_audio.delta`. Same for `_transcript`. Function-call event names unchanged. GA endpoint: `POST /v1/realtime/client_secrets` (was `/v1/realtime/sessions` in Beta). Ephemeral key arrives in `body.value` (was `body.client_secret.value`). Beta `OpenAI-Beta: realtime=v1` header must be *removed*.

11. **Lambda Proxy responses must emit `access-control-allow-origin`**: `apigw.Cors.ALL_ORIGINS` in CDK only auto-handles the OPTIONS preflight; the actual response body comes through Lambda Proxy unchanged. iOS didn't surface this (no CORS enforcement in `URLSession`); the browser silently rejected every authenticated response → Shell's `.catch(() => signOut())` triggered an infinite bounce to /signin. All 14 Lambdas patched to include `"access-control-allow-origin": "*"` in their `_resp` headers. Gateway-level rejections (401 from the Cognito authorizer, 5xx) still don't emit CORS — `gatewayResponses` config in api-stack.ts is a follow-up.

12. **Web logout requires trailing slash on `logout_uri`**: `window.location.origin` returns no trailing slash. Cognito does exact-match against the client's registered LogoutURLs (CDK registers them as `https://.../` with trailing slash). Mismatch → Cognito's `/logout` redirects to `/error?...` with a misleading "Required String parameter 'redirect_uri' is not present" message. Fix in `web/src/lib/cognito.ts` logoutUrl getter.

13. **Cognito standard-attribute mutability is set at pool creation, period**: there is no `UpdateUserPool` path that flips `Mutable` on an *existing* standard attribute. Attempting it returns "Invalid AttributeDataType input" from CFN. To change it: replace the resource (CDK construct-ID rename) which triggers CFN to create a new pool and (depending on DeletionPolicy) destroy or orphan the old. Cross-stack exports from the old pool are *imported* by api-stack and can't be deleted while the import exists — migrate by either pre-replacing the import with a literal in api-stack (one deploy) or by doing a two-pass deploy. We took the literal route on 2026-05-18.

14. **CloudFront-fronted `auth.<cognito>.amazoncognito.com` domains use the pool's domain prefix as global key**: the prefix `ciso-copilot` is unique. If the old pool still owns it when CFN tries to create the new pool's domain, the deploy fails. Pre-delete the old domain manually with `aws cognito-idp delete-user-pool-domain --user-pool-id ... --domain ciso-copilot` *before* deploying the replacement.

## Architecture (only the parts that bit us)

### Sign-in (multi-tenant Microsoft)

```
iOS / Web (email entry)
    ↓ POST /auth/discover-tenant {email}
backend (UNAUTHED)
    ├─ Gmail/Googlemail → return {idp_name: "Google", authorize_url: ...}
    └─ else → Microsoft .well-known/openid-configuration on user's domain
              ├─ idempotently CreateIdentityProvider "MS-<tenant29>"
              ├─ idempotently attach to UserPool client's SupportedIdentityProviders
              └─ return {idp_name: "MS-<tenant29>", tenant_id, authorize_url}
    ↓
iOS/Web opens Cognito authorize URL with identity_provider hint baked in
    → Microsoft → MFA → /oauth2/idpresponse → Cognito → cisocopilot://auth/callback?code=...
```

### Voice (WebRTC GA)

```
iOS taps mic
    ↓ POST /voice/session (JWT-authed)
backend mints via OpenAI POST /v1/realtime/client_secrets
    {session: {type:"realtime", model:"gpt-realtime", instructions, audio, tools, ...}}
    → {value: "ek_...", expires_at, session: {...}}
iOS creates RTCPeerConnection (empty ICE), local audio track, "oai-events" data channel
    → POST https://api.openai.com/v1/realtime/calls
       Authorization: Bearer ek_...
       Content-Type: application/sdp
       <offer SDP>
    ← <answer SDP>
audio flows full-duplex over RTP; events flow over data channel:
   "input_audio_buffer.speech_started/stopped"
   "conversation.item.input_audio_transcription.delta/done"
   "response.output_audio_transcript.delta/done"
   "response.output_audio.delta"  (audio chunks if model can speak)
   "response.function_call_arguments.delta/done"  → iOS dispatches → conversation.item.create + response.create
   "response.done"
```

### Scanner (AWS)

```
/onboarding/aws/complete (custom resource webhook from customer's CFN stack)
    → Secrets Manager put: ciso-copilot/connections/<conn_id> = {role_arn, external_id}
    → cloud_connections.status = 'active'
    → EventBridge.PutPermission grants customer account PutEvents on our bus
    → invoke shasta-runner Lambda async with {scan_id, conn_id, account_id, regions}

shasta-runner (Lambda container image, ECR ciso-copilot-shasta-runner:latest)
    → STS AssumeRole arn:aws:iam::<customer-account>:role/CISOCopilotReader (external_id)
    → run Shasta global modules (iam, organizations, cloudfront, logging)
    → run per-region modules (compute, storage, networking, encryption,
       database, monitoring, secrets, governance)
    → batch INSERT into findings
    → scans.status = 'completed', finished_at = now()
```

## Important code locations

- `platform/lib/*.ts` — CDK stacks (network, data, auth, ecr, static, events, scan, api)
- `platform/lambda/*/main.py` — all backend Lambdas; each has its own dir + handler
- `platform/lambda/auth_discover/main.py` — multi-tenant Microsoft routing (lazy IdP provisioning)
- `platform/lambda/voice_session/main.py` — OpenAI Realtime ephemeral key mint
- `platform/lambda/post_confirmation/main.py` — tenant creation + approval email
- `platform/lambda/shasta_runner*/` — 4 scanner Docker images, one per cloud
- `platform/cfn/aws-onboard.yaml` — customer-side CFN template (presigned at runtime)
- `platform/cfn/{azure,gcp}/onboard.sh` — Cloud-Shell-pasteable bootstrap scripts
- `ios/CISOCopilot/Services/VoiceClient.swift` — WebRTC realtime client (~340 lines)
- `ios/CISOCopilot/Services/AuthManager.swift` — Cognito OAuth (uses /auth/discover-tenant)
- `ios/CISOCopilot/Views/SignIn/SignInView.swift` — email-first sign-in
- `web/src/routes/SignIn.tsx` — **still on legacy Cognito picker** (not email-first)
- `web/src/lib/cognito.ts` — Cognito OAuth helpers (web)

## Open items (in priority order for "ready for self-service")

1. ~~**Web sign-in parity**~~: ✅ DONE 2026-05-18 (later). SignIn.tsx now uses email-first /auth/discover-tenant; auth_discover Lambda attaches per-tenant IdPs to BOTH iOS + web clients and uses the right client_id per platform.
2. **APNs push end-to-end test**: trigger a synthetic "act now" finding, confirm push lands on KK iPhone.
3. **Daily brief generation**: per v2 spec §X, build the nightly cron Lambda that calls Anthropic to produce why-it-matters / board-paragraph / team-questions prose. Not started.
4. **DNS for `settlingforless.com`**: enables custom domain on web + SES domain DKIM verification + nicer onboarding URLs.
5. **Entra company admin "Assignment required = No"**: unblocks KK signing in with his Transilience account. Independent of code.
6. **Apply SES production access**: needed before inviting any external user (sandbox blocks send to unverified addresses).

## Shasta lift — backlog status

1. ~~**CISO dashboards**~~ ✅ DONE (web + iOS) 2026-05-18.
2. ~~**Risk register**~~ ✅ DONE (backend + web + iOS) 2026-05-18.
3. ~~**Voice on web**~~ ✅ DONE 2026-05-18 (later). WebRTC client at `web/src/voice/`, hits `/voice/session`. Tools: get_top_risks, list_connected_clouds, get_compliance_summary, list_recent_alerts, list_risks, **add_risk (voice-driven risk creation)**. Voice button on Welcome opens modal with mic + transcript. The "voice changes dashboards" trick from Shasta is deferred — tools mutate the DB right now, not yet front-end state.
4. ~~**Policy creation**~~ ✅ DONE (backend + web) 2026-05-18. 5 starter templates lifted from Shasta `policies/` (access_control, incident_response, data_classification, vendor_management, change_management). Web /policies route with template picker, render, markdown editor + preview, status workflow (draft → approved → retired). AI enrichment deferred.
5. ~~**Questionnaire-from-evidence**~~ ✅ DONE (backend + web) 2026-05-18. SIG Lite (17 q) + CAIQ Lite (9 q) banks lifted from Shasta. Auto-fill engine maps check_ids → findings → yes/no/partial/manual with confidence. Web /questionnaires route with progress bar + drill-in.
6. ~~**Trust center**~~ ✅ DONE 2026-05-18 (later). `trust_pages` table; trust Lambda with UNAUTHED GET `/public/trust/{slug}` + authed GET/PUT `/trust`. Per-section toggles (compliance / finding counts / clouds / last scan). Web admin at `/trust`, public page at `/public/trust/{slug}` (no auth) with framework tiles + severity bars. Redacted: no ARNs, account IDs, finding titles, IPs.

## Deferred follow-ups (next sessions)

- ~~**Voice "changes dashboards"**~~ ✅ DONE. Added `navigate_to(view)` and `filter_findings_view(severity?, cloud?, framework?)` tools to voice_session; VoiceChat now threads a `ViewActions` callback into `executeTool`, navigates via react-router, auto-closes the modal so the destination is visible.
- ~~**Anthropic API integration**~~ ✅ DONE. Both policies + questionnaires Lambdas now call Claude (model: `claude-sonnet-4-6`) via stdlib urllib. Policy `POST /policies/{id}/enrich` rewrites the doc grounded on tenant context (clouds + open findings); web has a purple "✨ Enrich with AI" button in the editor. Questionnaire `POST /questionnaires/{id}/items/{iid}` drafts yes/no/partial + justification per item; web has a ✨ button per item that fills the answer + shows confidence='ai-suggested'. Lambda timeouts bumped (60s policies, 45s questionnaires) for the model round-trip. Helper at `lambda/{policies,questionnaires}/anthropic_call.py` (duplicated, not a layer — keeps deploys self-contained).
- **iOS Policies + Questionnaires + Trust views**: backend ready; iOS UI mirrors the web routes.
- **Daily brief generation** (from earlier open items).
- **APNs push end-to-end test**.

## Admin tooling

- `scripts/send_approval_email.py <tenant_id>` — re-send the access-approval email for any pending tenant. Uses the same JWT signing + HTML format as the post_confirmation Lambda. Built 2026-05-18.
- Future: lightweight web admin UI (list pending tenants, click Approve/Reject in-app) to remove dependency on email delivery.

## Cleanup state in DB (end of 2026-05-18 testing session)

- `users` table: 1 Google user (`kkmookhey@gmail.com`), 1 Microsoft user (`kkmookhey@transilience.ai`), 1 Google user (`randevak@gmail.com` — KK's wife) all linked to their own admin-role tenant rows. Same rows survived the pool recreate because `users.sso_subject` is keyed on the IdP `sub`, not the Cognito sub.
- `tenants` table: `gmail.com` (approved, KK only), `transilience.ai` (approved, KK only), `randevak@gmail.com` (pending — full email used as tenant key for personal-domain isolation), `Dev Test Tenant` (long-lived scaffold).
- `cloud_connections` table: 3 **active** connections — AWS (`26e97477-...`, account `470226123496`), Azure (`79964b99-...`, Entra tenant `017c6f31-...`, 2 subscriptions), GCP (`219f41eb-...`, project `gen-lang-client-0693606939`). All orphan `pending` rows from re-clicked Connect tiles deleted.
- `scans` table: one `completed` scan per active connection (AWS, Azure sub `cb0d6ed4-...`, GCP), plus a manual rescan for Azure sub `8cd2b4cc-...` triggered after the multi-sub fix landed.
- `findings` table: ~480 across the 3 clouds (270 AWS + 108 Azure + 102 GCP). Will grow by ~100 once the second Azure sub completes.

## Features shipped 2026-05-18 (final stretch — iOS UX polish + AI enrichment + new surfaces)

- **AI enrichment via Claude (`claude-sonnet-4-6`) on policies + questionnaires.**
  Backend uses stdlib `urllib.request` (no SDK dep) against `https://api.anthropic.com/v1/messages`. Secret `ciso-copilot/anthropic-api-key` provisioned. Helper at `lambda/{policies,questionnaires}/anthropic_call.py` (duplicated, no Lambda layer). Lambda timeouts bumped (policies 5min, questionnaires 45s) for model round-trips.
- **Policies — Bulk "Generate all" + 3 new templates.** Total templates: 8 (access_control, incident_response, data_classification, vendor_management, change_management, security_awareness, bcp_dr, vulnerability_mgmt). `POST /policies/generate-all` renders all + parallel-enriches via `ThreadPoolExecutor(8)` → ~30–90s wall, all 8 personalized to tenant clouds + open finding counts in one click. Web button "✨ Generate all" (purple) on `/policies`.
- **Policies — per-policy "✨ Enrich with AI"** button on the editor (existing `/policies/{id}/enrich` endpoint, AI-personalized rewrite).
- **Questionnaires — Excel upload + AI-fill + round-trip export.** SheetJS (xlsx 0.18.5) added to web. `web/src/voice/excelHelpers.ts` auto-detects question/category columns via question-shape heuristics; `writeBackAndDownload` writes answers + notes back into the source workbook at the original row positions and triggers download. Schema gained `questionnaires.source_filename` + `questionnaire_items.source_row_idx`. Backend `POST /questionnaires/from-excel` accepts parsed rows. Web modal previews first 50 detected rows; questionnaire detail has "✨ Suggest all" (parallel Claude, 4-way concurrency) and "⬇️ Export filled .xlsx" buttons.
- **Risks page redesign (web + iOS).** Default view: domain sections (collapsible on web, native `Section` on iOS) → rolled-up rows by check_id with affected-resource count + framework refs → drill in to see ARNs. Web: search box (`/findings/rollup?q=`), flat-vs-grouped toggle, clearable filter chips. iOS: native `.searchable` with 350ms debounce. Backend: new `/findings/rollup` Lambda (Python aggregation over ~500 findings into ~30 groups), `check_id` filter added to `/findings`.
- **Voice changes dashboards** (web). Two new tools in `voice_session`: `navigate_to(view)` and `filter_findings_view(severity?, cloud?, framework?)`. `VoiceChat` threads a `ViewActions` callback into `executeTool`, navigates via react-router, auto-closes the modal ~400ms after a navigation so the destination view shows. Defensive `responseActive` ref queues `response.create` until `response.done` to avoid OpenAI "active response in progress" 400s when tools return instantly.
- **Trust center.** `trust_pages` table; trust Lambda with **UNAUTHED** `GET /public/trust/{slug}` + authed `GET/PUT /trust`. Per-section toggles (compliance / finding counts / clouds / last scan). Web admin at `/trust`, public read-only page at `/public/trust/{slug}` with framework tiles + severity bars + cloud chips. Redaction enforced: no ARNs, account IDs, finding titles, IPs ever leave the public page.
- **Clickable everything on iOS Overview.** New `AppState @Observable` lifted into `MainTabView` exposes `selectedTab`; any descendant view can switch tabs via `Environment(AppState.self)`. Stat cards (Clouds → Connect tab, Findings → Risks tab, Alerts → full-list sheet that drills into per-alert detail), compliance rows (push `TopRisksView(initialFramework:)` in current nav stack with a clearable filter chip), connection rows (jump to Connect tab), Recent activity rows (open `AlertDetailSheet`). Chart segments (donut, bars) still passive — Swift Charts gesture work deferred.
- **Clickable alerts on Welcome (web)** — modal with title, severity pill, kind, source, full description, resource ARN (text-selectable), actor, fired/ingested timestamps, event_id.
- **Web sign-in: SES sandbox lifted.** Production access granted; `Source=no-reply@settlingforless.com` (DKIM + SPF in Google Cloud DNS verified). `admin_decision` Lambda hardened: SES failure to the requester no longer 500s the approve link (best-effort `try/except`).
- **iOS risk register tab** (added earlier in the day) — 5th "Register" tab with status filter, inline status menu, "+ New" sheet.

## Features shipped 2026-05-18 (autonomous push #2 — Shasta full lift)

- **sso_provider normalization**: per-tenant Microsoft IdPs (`MS-<hex>`) now resolve to `sso_provider='microsoft'` in users table. Backfilled 2 KK rows.
- **iOS risk register tab** ("Register"): 5th tab; list filtered by status, inline status menu, "+ New" sheet with severity/owner/due-date.
- **Voice on web** at `web/src/voice/`: WebRTC peer connection to OpenAI Realtime via our `/voice/session` ephemeral key; expanded tool set (get_top_risks, list_connected_clouds, get_compliance_summary, list_recent_alerts, list_risks, **add_risk** — voice can create risk register entries); VoiceChat modal opened by "Voice" button on Welcome.
- **Policies module**: `policies` table; `/policies` API (list/get/create-from-template/patch); 5 starter templates (access_control, incident_response, data_classification, vendor_management, change_management) condensed from Shasta `policies/generator.py`. Web `/policies` route: list, "+ From template" modal with company_name/effective_date/approver vars, edit modal with markdown source + live preview + status dropdown.
- **Questionnaires module**: `questionnaires` + `questionnaire_items` tables; `/questionnaires` API (list/get/create/patch-item/templates); 2 banks — SIG Lite (17 q) + CAIQ Lite (9 q) — lifted from Shasta `questionnaire/questions.py`. Deterministic auto-fill: maps each question's `check_ids` → findings → all-pass=`yes` (auto-high), all-fail=`no` (auto-high), mixed=`partial` (auto-medium), no-mapping=`manual`. Web `/questionnaires` route: list with progress bar, "+ Start" modal, detail view grouped by category with per-item answer dropdown + evidence callout + confidence badge.

## Features shipped 2026-05-18 (autonomous push #1 — while KK was on errands)

- **Tenant isolation for personal-email domains**: `post_confirmation` now segregates `gmail.com` / `outlook.com` / `yahoo.com` / `icloud.com` / etc. into per-user tenants. KK's wife migrated to her own pending tenant — fixed the data-leakage bug where she could see KK's clouds.
- **`scripts/send_approval_email.py`**: reusable admin tool to re-fire approval emails for any pending tenant. Built when the email path was being debugged; useful when SES delivery is flaky or for manual ops.
- **SES from-domain fix**: switched `Source=` to `no-reply@settlingforless.com` (DKIM + SPF verified via Google Cloud DNS). Previously sent from `kkmookhey@gmail.com` which Gmail silently spam-foldered (Gmail-from-AWS-IP looks like spoofing).
- **SES production access granted**: form submitted by KK; AWS approved. Sending TO unverified recipients now works → user-side approval notifications deliver.
- **admin_decision Lambda error handling**: wraps `_send_user_email` in try/except so SES sandbox failure no longer 500s the approve link (was misleading "Internal Server Error" while the tenant flip had already succeeded).
- **`shasta.transilience.cloud` custom domain**: ACM cert issued, Cloud DNS records (CNAME + DKIM validation) added, CloudFront alternate domain attached, Cognito callback URLs include the new domain. SPA reachable at `https://shasta.transilience.cloud/` end-to-end.
- **Dashboards on web home**: PieChart (severity), BarChart (by-cloud) using Recharts; clickable → drill down to `/findings?severity=X` / `/findings?cloud=Y`. TopRisks reads URL params + shows clearable filter chips. Compliance posture cards now clickable too (filter by framework).
- **`/findings/summary` endpoint**: aggregates by severity + cloud for dashboard tiles without paging through findings.
- **`/events` endpoint + UI surfacing**: real-time alerts now reachable. iOS Overview shows live "Recent activity" + Alerts count; web Welcome same.
- **`/compliance/summary` endpoint**: per-framework score% aggregation (Shasta scorer lift).
- **Email-first sign-in on web**: SignIn.tsx now takes email → `/auth/discover-tenant` → redirect to per-tenant Microsoft IdP authorize URL. Mirrors iOS. `auth_discover` Lambda updated to attach per-tenant IdPs to BOTH iOS + web clients + use the right `client_id` per platform.
- **iOS dashboards**: same charts ported via Swift Charts. SeverityDonut + CloudBars + ComplianceRow. Alerts count + recent activity already there.
- **iOS Share on finding detail**: `ShareLink` toolbar item formats title + remediation + frameworks into shareable text; iOS share sheet picks up Slack/Teams/Mail/Messages/Jira automatically.
- **Admin web UI (`/admin`)**: list tenants by status (default pending), Approve/Reject buttons. Gated to ADMIN_EMAILS allowlist on both backend and nav. Removes dependency on email delivery for inviting testers.
- **Risk register (`/risks`)**: schema (`risks` table with severity + status enums), `/risks` Lambda (GET/POST/PATCH), web route with filters + status dropdown + New Risk modal, "Add to risk register" button on finding detail (one-click convert).
- **CORS hardening**: 14 API Lambdas emit `access-control-allow-origin: *`; gateway-level rejections (DEFAULT_4XX/5XX, UNAUTHORIZED, ACCESS_DENIED) emit CORS via `addGatewayResponse` so browser sees real errors instead of dying on preflight.
- **`findings_list` `total`**: separate COUNT query, iOS + web wired to use it for stats.
- **Multi-sub Azure scanning**: `onboarding_azure_complete` enqueues one scan per subscription.
- **Azure scanner image**: `msgraph-sdk` baked in so IAM module (Conditional Access / MFA) actually runs.
- **`findings_summary` Lambda → `/findings/summary`**: severity + cloud aggregations used by the dashboard.

## Cleanup done in the 2026-05-18 testing session

- **Cognito pool migration**: old `us-east-1_ePRQ2iwZT` deleted; only `us-east-1_jOC1znCSS` remains.
- **Lambda CORS headers**: all 14 API Lambdas emit `access-control-allow-origin: *`.
- **API Gateway CORS**: gateway-level rejections (`DEFAULT_4XX`, `DEFAULT_5XX`, `UNAUTHORIZED`, `ACCESS_DENIED`) emit CORS via `addGatewayResponse` in api-stack.ts.
- **Findings response**: `findings_list` Lambda now returns a real `total` field (separate COUNT query) in addition to page `count`.
- **iOS Overview stats**: `Clouds` filters to `status == "active"`; `Findings` uses new `findingsTotal()` API method.
- **Web Welcome stats**: same fix — active conns count + total findings.
- **Multi-sub Azure scanning**: `onboarding_azure_complete` now enqueues one scan per subscription.
- **Azure scanner image**: `msgraph-sdk` added so the IAM module (Conditional Access / MFA checks) runs instead of throwing.
- **Web logout**: `logout_uri` now has trailing slash to match the client's registered LogoutURLs.
- **Web callback**: Callback.tsx surfaces `?error=...&error_description=...` instead of swallowing them as "cancelled or no code."
- **Tenant isolation for personal-email domains**: `post_confirmation` now segregates gmail.com/outlook.com/yahoo.com/icloud.com/etc. users into per-user tenants (keyed on full email). Schema unchanged — `tenants.email_domain` now stores either the corp domain or the full personal email. KK's wife migrated to her own pending tenant.
- **Approval email link target**: `_decision_url` now uses `API_BASE_URL` env var (currently the API Gateway invoke URL) instead of the unresolved `api.settlingforless.com`.
- **`/events` endpoint + surfacing**: real-time alerts now reachable via `GET /events` (paginated, filters by kind/severity/source). iOS Overview shows live "Recent activity" + Alerts count; web Welcome shows recent activity + critical alerts stat.
- **`/compliance/summary` endpoint + surfacing**: per-framework {passing, failing, total, score_pct} aggregation. iOS Overview shows ComplianceRow per framework; web Welcome shows FrameworkCard tiles.
- **iOS Share button on findings**: `ShareLink` in `FindingDetailView` toolbar.

## How to /clear and resume

Memory under `~/.claude/projects/-Users-kkmookhey-Projects-CISOBrief/memory/` survives `/clear`. After clearing:

1. Read this `HANDOFF.md` first.
2. Read `CISOBrief-v2.md` (the PRD) if working on a new feature.
3. Skim `~/.claude/projects/-Users-kkmookhey-Projects-CISOBrief/memory/MEMORY.md` for collaboration norms (`feedback_momentum_style.md`, `feedback_testing_first.md`) and the project memory `project_ciso_copilot.md`.

For today's session, also read `TEST_PLAN.md` (the comprehensive web-app test script KK will be running).
