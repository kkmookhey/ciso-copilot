# AI Visibility v2 — Design Spec

> Companion to `CISOBrief-v2.md` (PRD), `HANDOFF.md` (state),
> `docs/superpowers/specs/2026-05-22-ai-security-strategy.md` (strategy
> frame), `docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md`
> (the unified entity model this writes into), and
> `docs/superpowers/specs/2026-05-20-ai-discovery-connectors-design.md`
> (the cloud-AI connector spec that shipped — AWS leg only).
>
> Date: 2026-05-22
> Status: brainstorm-approved by KK on 2026-05-22; awaiting written-spec
> review before the implementation plan is written.

---

## 1. What we are building

The visibility-side wedge of the AI security story. After this lands,
when a CISO clicks "AI" in CISO Copilot they see a single Fail / Partial
/ Pass score across four sources — code (already shipped), AWS cloud
(already shipped), **Azure cloud (new), and Entra sign-ins (new)** —
with per-person, per-source, per-framework drill-downs and a compliance
rollup against NIST AI RMF, ISO 42001, SOC 2 AI, and EU AI Act in the
same screen. **GCP-AI is promoted to its own sub-project** because
Shasta has no `gcp/ai_*` module today (see §13); it joins the demo arc
once that work lands.

The strategy doc frames why this is the wedge. This spec is the
build contract for the four slices that get us there.

## 2. Guiding principle — AI is a lens, not a silo

Reaffirmed from prior specs:

- A Vertex AI endpoint is a *cloud* resource that happens to be AI. It
  is discovered by the GCP scan and stored with `domain='cloud'`.
- A finding carries *every* framework it maps to at once — SOC 2, CIS,
  NIST AI RMF, ISO 42001, EU AI Act — with no "AI findings" silo.
- The compliance view shows AI and non-AI frameworks side by side
  because they are all just keys in `findings.frameworks`.

The unified `entities` / `edges` / `findings` model already expresses
this. This sub-project extends the source set; it does not re-wall AI
into a parallel surface.

## 3. Scope and non-goals

**In scope:**

1. **Cloud-AI on Azure** — wire Shasta's existing Azure-AI checks into
   `shasta_runner_azure`. Same pattern as the AWS-AI pass already
   shipped (`shasta_runner/app/ai_pass.py`). GCP-AI is **out of
   scope** for this sub-project (see §3 out-of-scope and §13 risks).
2. **Entra sign-in connector** — new Lambda + connector type. Reads
   Entra sign-in logs (Microsoft Graph `auditLogs/signIns`), detects
   sign-ins to known AI SaaS apps (ChatGPT, Claude, Cursor, Copilot,
   Perplexity, etc.), emits AI-user-signin entities and findings.
   Requires `AuditLog.Read.All` + `Directory.Read.All` Graph
   application scopes.
3. **Unified AI view** — new `/ai` route on the web app: Fail / Partial
   / Pass top tile, by-source tiles (cloud/code/identity), per-person
   ranked drill-down, per-framework rollup. Built off the findings-overhaul
   UI primitives.
4. **Per-person grouping** — view-layer SQL `GROUP BY LOWER(email)` on
   findings + entities. No new schema. No graph resolver.
5. **Compliance mapping sweep** — every new finding kind from S1/S2
   carries the right `nist_ai_rmf:<control>`, `iso_42001:<clause>`,
   `soc2_ai:<control_id>` tags. EU AI Act keys added to the registry,
   not mapped.

**Explicitly out of scope** (named so the boundary is unambiguous):

- ❌ **GCP-AI cloud pass** — separate sub-project ("GCP-AI Discovery").
  Shasta has no `gcp/ai_discovery.py` / `gcp/ai_checks.py` today; the
  detection logic must be built (either in CISO Copilot or contributed
  upstream to Shasta). Brainstormed in a follow-on session.
- ❌ **OpenAI / Anthropic admin-API connectors** — admin-key blocker.
  Schema already permits them; route lands when access lands.
- ❌ **MCP discovery** (any surface) — separate sub-project (`MCP
  Risk`). Reserved in the architecture; not built here.
- ❌ **Inline traffic** — browser extension / network proxy / endpoint
  agent. Wrong product shape. Promised when asked.
- ❌ **M365 Copilot activity ingest** — Microsoft surfaces this
  natively in the M365 admin centre; revisit if a prospect specifically
  asks.
- ❌ **Google Workspace audit logs** — same audit-log pattern as M365;
  deferred until the M365-equivalent (in this case, Entra) proves out.
- ❌ **Graph-style identity resolver** — `human` entity kind + new edge
  kinds + merge logic in `unified_writer`. Defer until per-person view
  needs cross-source navigation, not just grouping.
- ❌ **Identity stitching UX** (manual merge / confirm-split) — not in
  this sub-project.
- ❌ **Per-person policy enforcement** (block ChatGPT, force Teams,
  etc.) — the product reports, it does not enforce.
- ❌ **iOS AI screens** — iOS is companion-only. Push notifications for
  new high-severity AI findings inherit the existing notification path;
  no new iOS surfaces in this sub-project.

## 4. Decisions log

Settled in the 2026-05-22 brainstorm:

| # | Decision | Rationale |
|---|---|---|
| D0 | S1 ships Azure-only; GCP-AI is promoted to its own sub-project | Shasta has no `gcp/ai_*` module; building it is a slice's worth of work — should be brainstormed separately rather than absorbed into S1 |
| D1 | Visibility + Compliance is the wedge | Existing product shape; entrenched competitors elsewhere |
| D2 | Per-person view = SQL `GROUP BY email`, not a graph resolver | "Email is good enough for now"; saves a schema migration |
| D3 | AI Risk Score = mirror of findings overhaul (Fail/Partial/Pass) | Don't introduce a second score concept; "AI is a lens" principle |
| D4 | A finding is "AI-touching" if framework key starts `nist_ai_rmf` / `iso_42001` / `soc2_ai` / `eu_ai_act`, OR entity has `domain='ai'` / AI-resource `kind`, OR attribute `is_ai=true` | Multi-axis test, covers escape hatches |
| D5 | Entra sign-in connector is a **separate connection** from the existing Entra OIDC IdP routing | Different Graph scopes; different admin-consent flow |
| D6 | Catalog-based AI-SaaS detection (curated JSON in repo) — not ML / regex inference | 50-100 well-known apps; deploy-cycle updates fine for v2 |
| D7 | M365 Copilot ingest is out — Microsoft surfaces it natively | Avoid reinventing the M365 admin center |
| D8 | Compliance includes SOC 2 AI controls (`soc2_ai:<control_id>`) alongside NIST AI RMF + ISO 42001 + EU AI Act | KK ask; customer-relevant |
| D9 | iOS gets push notifications for new high-severity AI findings; no new iOS screens | Companion-only iOS direction per memory |

## 5. Architecture overview

All slices write into the unified `entities` / `edges` / `findings`
tables via the existing `unified_writer.commit_scan(...)`. Nothing else
in the stack changes. Components by surface (slice mapping in §6):

```
Azure cloud-AI (S1 — extends shasta_runner_azure):
  Azure scan → shasta_runner_azure
    → existing cloud + SP1 enum passes
    → NEW: AI pass (Shasta discover_azure_ai_services + run_full_azure_ai_scan
            + enrich_findings_with_ai_controls)
    → entities(domain=cloud, kind=azure_openai_deployment | cognitive_service
                | azure_ml_workspace | …)
    → findings(frameworks: nist_ai_rmf, iso_42001, soc2_ai)
    → unified_writer.commit_scan

  (GCP-AI follows the same shape in a separate sub-project — see §3
  out-of-scope.)

/ai view + per-person grouping (S1 — view layer only):
  Web /ai route → GET /v1/ai/summary (new endpoint)
    → SQL aggregation over findings + entities filtered by is_ai_touching()
    → per-person GROUP BY LOWER(email) over whatever email fields exist
    → returns { fpp_tiles, by_source, by_framework, top_people, recent_findings }

Entra sign-in connector (S2 — new connection + new Lambda):
  "Connect Entra Sign-ins" card → admin-consent OAuth (separate from existing IdP routing)
    → POST /v1/connections/entra-signin/connect
    → store cert in Secrets Manager, insert connections row
    → enqueue entra-signin-scan-queue
    → entra_signin_scanner Lambda → Graph /auditLogs/signIns paginated read
    → entities(domain=identity, kind=ai_user_signin)
    → findings(frameworks: nist_ai_rmf, iso_42001, soc2_ai)
    → unified_writer.commit_scan
  S2 also lights up the email-merge dimension of the /ai per-person view
  (no new schema — Entra UPN is just another email-bearing attribute).

Compliance mapping sweep + framework registry (S3 — data + code):
  SQL migration adding eu_ai_act + soc2_ai keys to framework registry allowlist
  Code update tagging every new finding kind from S1/S2 with applicable frameworks
  Compliance view re-renders with AI-specific frameworks pinned to top under the /ai lens

iOS push notifications + polish (S4 — existing notification path):
  AI finding kinds added to APNS-eligible set in the existing notifier
  Deep link to existing finding-detail screen (no new iOS surfaces)
  Playwright smoke test for /ai route
```

**Reused unchanged:** `unified_writer`, `entities`/`edges`/`findings`
schema, `compliance_summary` Lambda, AI Inventory web + iOS views (read
`entities`), Secrets Manager, API Gateway, Cognito.

## 6. Slice plan

Four vertical slices, each ships end-to-end:

| # | Slice | What ships | New connector? | Build size |
|---|---|---|---|---|
| **S1** | Cloud-AI Azure + Unified AI View | Azure scanner gets an AI pass mirroring AWS; `/ai` route lands; per-person view stubbed (AWS IAM principals + GitHub authors only). | No | Medium |
| **S2** | Entra sign-in connector + per-person grouping | New `entra_signin` connector type. Reads Graph `auditLogs/signIns`. Per-person view in `/ai` now collapses GitHub + AWS + Entra by email (SQL `GROUP BY LOWER(email)` — no schema merge). | Yes | Medium-large |
| **S3** | Compliance mapping sweep + SOC 2 AI + EU AI Act registry | All new finding kinds tagged with applicable NIST AI RMF + ISO 42001 + SOC 2 AI tags. EU AI Act keys added to registry (named, not mapped). | No | Small |
| **S4** | Polish + iOS push notifications | High-severity AI-finding notifications use the existing iOS push path. `/ai` view polish + Playwright smoke. | No | Small |

### S1 dependencies
- Azure scanner on parity with AWS for non-AI work (the existing
  `shasta_runner_azure` is the host; audit in S1 plan if any gaps
  surface).
- Shasta's `azure/ai_discovery.py` + `azure/ai_checks.py` ship in the
  Shasta package installed into the scanner image (already a
  dependency of the AWS runner).

### Demo arc by slice
- After S1 → "See all the AI in your AWS + Azure cloud and your code."
- After S2 → "...and who's using ChatGPT / Claude / Cursor in your
  org, mapped to your people."
- After S3 → "...all scored against NIST AI RMF, ISO 42001, SOC 2 AI,
  EU AI Act in the same view."
- After S4 → "...and the iOS app pings you when a new high-severity
  AI finding lands."
- After GCP-AI Discovery (separate sub-project) → "...plus your GCP
  cloud."

## 7. Per-person view (the simplified identity model)

**Goal:** show a ranked "Top AI users" list inside the `/ai` view
without introducing a new entity kind or a graph resolver.

**Approach:** SQL `GROUP BY LOWER(email)` on whatever email-bearing
attribute the source provides:

- GitHub code findings carry `commit_author_email`.
- AWS cloud entities optionally carry `iam_owner_tag` if the customer
  tags resources with an owner email.
- Entra sign-ins carry `entra_upn` (which is generally the user's
  email).
- Future sources can add their own `email` attribute and inherit the
  grouping for free.

**Query shape** (in the new `/v1/ai/summary` endpoint's per-person
panel):

```sql
SELECT
  LOWER(COALESCE(commit_author_email, iam_owner_tag, entra_upn)) AS person,
  COUNT(*) FILTER (WHERE status = 'fail') AS fail_count,
  COUNT(*) FILTER (WHERE status = 'partial') AS partial_count,
  ARRAY_AGG(DISTINCT source) AS sources
FROM findings_view  -- includes is_ai_touching=true
WHERE tenant_id = $1
  AND COALESCE(commit_author_email, iam_owner_tag, entra_upn) IS NOT NULL
GROUP BY 1
ORDER BY fail_count DESC, partial_count DESC
LIMIT 25;
```

Rows where the person field is null show up under an "Unattributed"
sub-grouping in the UI, broken out per source (so you can see "5
unattributed GitHub findings" vs "12 unattributed Bedrock invocations").

**Accepted limits:**
- Personal-Gmail ChatGPT sign-ins (`bob.personal@gmail.com`) do not
  merge with corp identity — surfaces as a separate person row, which
  is actually useful signal.
- Service accounts merge across sources like humans — can be flagged
  later with an `is_service_account` attribute if needed.
- GitHub `noreply` emails (`123456+user@users.noreply.github.com`)
  never merge — accepted limit.

**What we do NOT do in this sub-project:**
- No `human` entity kind, no new edge kinds, no merge logic in
  `unified_writer`. Defer.
- No manual stitching UX (confirm/split merges). Defer.
- No fuzzy matching on display name. Defer.

## 8. AI Risk Score (the headline number)

**Definition:** the existing Fail / Partial / Pass tile from the
findings overhaul, filtered to AI-touching rows.

**`is_ai_touching(finding)` test** (new helper in the API layer, ~10
lines, used by the score endpoint and the `/ai` list view):

```python
def is_ai_touching(finding) -> bool:
    if any(f.startswith(("nist_ai_rmf:", "iso_42001:", "soc2_ai:", "eu_ai_act:"))
           for f in finding.frameworks):
        return True
    if finding.entity and finding.entity.domain == "ai":
        return True
    if finding.entity and finding.entity.kind in AI_RESOURCE_KINDS:
        return True
    if finding.attributes.get("is_ai") is True:
        return True
    return False

AI_RESOURCE_KINDS = {
    "bedrock_model", "sagemaker_endpoint", "vertex_endpoint",
    "azure_openai_deployment", "ai_saas_app", "ai_code_finding",
    "ai_user_signin", "ai_api_key", "ai_org_member", "ai_project",
    "ai_provider_org",
}
```

**Score endpoint:** `GET /v1/ai/score` → `{ fail: <n>, partial: <n>,
pass: <n>, sample_findings: [...] }`. Cached at request time (no
materialized view in v2 — Aurora handles the row counts comfortably at
expected scale; revisit if scan volume crosses ~1M findings/tenant).

**Why not a separate "AI maturity tier" or 0-100 number:** "AI is a
lens" — introducing a second score concept just for AI fights the
unified model. The four AI frameworks already provide maturity
signal through the existing compliance view's per-framework tiles.

## 9. Entra sign-in connector (S2 detailed)

### 9.1 Connection flow

1. New "Connect Entra Sign-ins" card on `/connect` (distinct from the
   existing Entra IdP routing card — different consent, different
   purpose).
2. Click → Microsoft admin-consent URL with `AuditLog.Read.All` +
   `Directory.Read.All` application-permission scopes.
3. Admin grants; redirect to webhook
   `POST /v1/connections/entra-signin/complete`.
4. Webhook stores a client secret in Secrets Manager, inserts the
   `connections` row (`provider='entra_signin'`, `secret_arn=…`,
   `status='active'`), enqueues a first scan. (Client-certificate
   auth is the Microsoft-recommended best practice; pin v2 on client
   secret for simplicity, revisit cert rotation as a hardening
   follow-on.)
5. Connector is re-runnable via the existing
   `POST /v1/connections/{id}/rescan` route.

### 9.2 Scanner Lambda

`platform/lambda/entra_signin_scanner/` — plain Python (HTTPS +
`psycopg2`), no container image (mirrors the deferred `provider_scanner`
shape). Triggered by `entra-signin-scan-queue` SQS + DLQ
(`maxReceiveCount=3`).

**Scan logic:**

1. Auth to Graph via client credentials (client secret in Secrets
   Manager — see §9.1 on cert hardening as a follow-on).
2. Page through
   `https://graph.microsoft.com/v1.0/auditLogs/signIns?$filter=createdDateTime ge {last_scan_at}`.
3. For each event, match `appDisplayName` (and fallback `appId`)
   against the curated AI-SaaS catalog (see §9.3).
4. On match: emit one `entities` row
   (`domain='identity'`, `kind='ai_user_signin'`,
   `natural_key={tenant_id}:{user_principal_name}:{app_id}`) and one
   `findings` row with severity from the catalog's per-app risk
   policy.
5. `commit_scan(...)` writes the batch; idempotent on natural key.

### 9.3 AI-SaaS catalog

JSON file in `platform/lambda/entra_signin_scanner/ai_saas_catalog.json`,
shipped with the Lambda. ~50-100 entries. Updated by editing + redeploy
(deploy cycle is fast enough for v2 — versioning to a separate config
service is a follow-on if entries churn faster).

```json
{
  "OpenAI": {
    "kind": "ai_saas_app",
    "match": { "appDisplayName": ["OpenAI", "ChatGPT"], "appId": ["..."] },
    "default_severity": "medium",
    "tier_inference": ["personal", "teams", "enterprise"]
  },
  "Anthropic": { ... },
  "Cursor": { ... },
  "GitHub Copilot": { ... },
  "Perplexity": { ... },
  "Gemini": { ... }
}
```

### 9.4 Findings emitted

| Finding kind | Trigger | Default severity |
|---|---|---|
| `ai_signin_personal_tier` | Sign-in to a personal-tier AI SaaS app (ChatGPT free/plus) | High |
| `ai_signin_corp_tier` | Sign-in to a corporate-tier AI SaaS app (ChatGPT Teams, Enterprise) | Low |
| `ai_signin_unknown_tier` | Sign-in matched the app but tier couldn't be inferred | Medium |
| `ai_signin_unsanctioned_app` | Sign-in to an AI app not in the customer's sanctioned-app list | High (if list exists; downgraded to Medium otherwise) |

Sanctioned-app list is a per-tenant config (no UI in v2 — set via DB
seed or admin-only API).

### 9.5 Failure modes

- **Permissions revoked at Microsoft** → Graph returns 403; connector
  status flips to `degraded`; `/connect` shows a re-consent prompt.
- **Rate limited** → exponential backoff per Graph headers; DLQ on
  `maxReceiveCount`; surface DLQ depth in the existing scan-health
  dashboard.
- **Customer on Entra Free tier** (no sign-in log retention beyond 7
  days) → connector still works on the 7-day window; surface a "limited
  retention" banner in `/connect`.
- **App ID changes upstream** → catalog falls back to `appDisplayName`;
  unmatched events drop silently (logged for telemetry).

### 9.6 What we explicitly do NOT do

- No prompt content inspection (don't have access).
- No data-flow attribution ("did Bob paste IP into ChatGPT") — invisible
  from sign-in logs.
- No automatic policy enforcement — report-only.
- No SCIM / user-provisioning sync (out of shape).

## 10. Compliance updates (S3 detailed)

**What already exists:** NIST AI RMF + ISO 42001 are accepted framework
keys in `findings.frameworks`; the existing compliance view rolls them
up like any other framework.

**What S3 adds:**

1. **Framework registry additions:**
   - `soc2_ai:<control_id>` — accepted prefix added to the registry
     allowlist + control catalogue seeded from the AICPA SOC 2 AI
     considerations.
   - `eu_ai_act:<article>` — accepted prefix; **registry-only**, no
     checks mapped yet. Surfaces as an "in progress" tile in the
     compliance view.

2. **Mapping sweep:** every new finding kind from S1 + S2 carries the
   right `nist_ai_rmf:<control>` + `iso_42001:<clause>` +
   `soc2_ai:<control_id>` tags. Tags live alongside the check
   definitions (Shasta runner checks + Entra catalog + view-layer rule
   tables). Lint test in CI asserts no new AI-touching finding kind
   ships without at least one AI-framework tag.

3. **`/ai` lens sort:** the existing compliance view's per-framework
   tiles re-sort to pin AI-specific frameworks to the top when the user
   navigated in from `/ai`. One CSS-driven sort change, no schema
   change.

**Out of scope:** new compliance UI surface, evidence-collection
automation for the new frameworks, automated control-effectiveness
testing. The existing compliance UI handles all four frameworks
correctly once the registry accepts them.

## 11. iOS scope (S4 detailed)

iOS remains companion-only per `project_ios_companion_vision` memory. No
new screens.

- High-severity AI findings (`fail` status + severity `high|critical`)
  trigger an APNS push via the existing notification path. Tap deep-links
  to the existing finding-detail screen (the AI-ness of the finding is
  visible via framework tags already rendered).
- No new "AI" tab on iOS; no per-person view on iOS; no `/ai` mirror.

## 12. Testing strategy

**Unit tests (per slice):**
- S1: Azure-AI + GCP-AI check assertions in `shasta_runner_azure/tests/`,
  `shasta_runner_gcp/tests/`. At least one happy-path + one
  missing-permission case per cloud-AI check.
- S2: Entra connector — mock Graph responses, assert correct entity +
  finding emission for each catalog entry. `signIns` paginator test.
  Per-app severity policy test.
- S3: framework-registry test asserts SOC 2 AI + EU AI Act keys are
  accepted. Mapping-sweep test confirms every new finding kind carries
  ≥1 AI-framework tag (lint-style).
- S4: notification dispatch test mocks APNS, asserts payload shape for
  new AI finding kinds.

**Integration tests:**
- S1: synthetic API Gateway event → scanner Lambda → real Aurora →
  assert findings + entities landed. Same pattern as the AWS scanner
  uplift verification.
- S2: Graph mock server + real scanner Lambda → real Aurora; assert
  per-person view query returns the right rows.
- S3: integration test loads the registry, seeds findings with new
  framework tags, asserts compliance view rollup includes all four AI
  frameworks.

**Manual verification (human-gated):**
- Each slice ends with a 5-line checklist in `HANDOFF.md` mirroring the
  existing GCP-uplift / scan-screen verification blocks — a real
  tenant, the Google-OAuth-gated demo path.

**Smoke / regression:**
- Playwright headless test on the deployed `/ai` route: asserts the
  Fail/Partial/Pass tile + the per-person table render. Slots into the
  existing CI test harness.

## 13. Risks & open questions

| Risk | Mitigation |
|---|---|
| Azure scanner not on parity with AWS for non-AI work — AI pass exposes gaps | Audit Azure scanner in S1 plan; spec a small uplift if gaps found |
| Shasta has no `gcp/ai_*` module today | GCP-AI promoted to its own sub-project; not blocking S1; brainstormed separately to decide build-in-CISO-Copilot vs contribute-to-Shasta-upstream |
| Shasta's compliance/ai mapper produces `iso42001_controls` + `eu_ai_act` but not `soc2_ai` | SOC 2 AI control catalogue + per-check mapping built in CISO Copilot (see §10); does not block S1 |
| Entra sign-in volumes are large for big tenants → Graph rate limits + scan duration | Incremental scans (`createdDateTime ge last_scan_at`); paginate; backoff per Graph headers |
| Catalog rot — AI SaaS apps appear faster than redeploys | Tolerable in v2; spec a config-service hand-off if churn proves painful |
| Email-only grouping under-merges (engineers using personal vs corp emails) | Documented limit; surfaces as separate person rows which is itself useful signal |
| Microsoft changes Graph audit-log API shape | Standard upstream risk; integration tests catch breakages |

**Open questions** (revisit before writing the S2 plan):
- Does the existing multi-tenant Entra OIDC routing share infra with
  the new connector, or are they fully independent? — affects whether
  cert storage is one secret or two.
- Per-tenant sanctioned-app list seed format — do we ship a "blessed"
  default list per industry, or start blank?

## 14. Done definition

This sub-project is done when:

1. A CISO can sign into the deployed app, click `/ai`, and see a Fail /
   Partial / Pass top tile that reflects AI findings across code + AWS
   + Azure + Entra sign-ins. (GCP joins after its own sub-project lands.)
2. The per-person drill-down shows a ranked list of identifiable people
   (by email), each with their AI-touching finding counts and source
   list.
3. The compliance view rolls up findings under NIST AI RMF, ISO 42001,
   SOC 2 AI, and (named-only) EU AI Act tiles.
4. iOS pushes a notification when a new `fail` + `high|critical` AI
   finding lands.
5. Each slice has its `HANDOFF.md` verification checklist passing on a
   real tenant.

---

*Update this spec only if a structural decision changes. Slice-level
detail belongs in the per-slice implementation plans (written next).*
