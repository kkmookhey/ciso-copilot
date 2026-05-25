# AI Discovery — Connectors · Design Spec

> The sub-project that completes **Layer 1 (Discover)** of the AI security
> system inside CISO Copilot. Companion to `CISOBrief-v2.md` (PRD),
> `HANDOFF.md` (state), `docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md`
> (the unified entity model this writes into), and
> `docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md`
> (the GitHub code scanner this sits beside).
>
> Date: 2026-05-20
> Status (updated 2026-05-25):
>   - **Plan 1 — Cloud-AI connector (Bedrock / SageMaker / Comprehend):
>     SHIPPED.** Folded into every AWS scan via `shasta_runner/app/ai_pass.py`.
>     See HANDOFF.md "AI Discovery — cloud-AI connector + findings overhaul"
>     block.
>   - **Plan 2 — OpenAI / Anthropic provider connectors: DROPPED (2026-05-25).**
>     Not pursuing. §7 below + the §3 in-scope mention + the §6/§8 plan-2
>     references are kept as paper trail only; do not start work against them.
>     Reason: admin-API access path is not viable for the product shape we
>     want. Discovery of provider-side AI usage falls back to (a) the code
>     scanner (Slice 1a/1b — shipped) for `from openai`/`from anthropic`
>     imports and (b) the Entra sign-in pass (Slice 2 — shipped) for
>     SaaS-tier ChatGPT/Claude logins.

---

## 1. What we are building

CISO Copilot's AI security system is built in five layers — Discover →
Model → Assess → Reason → Govern. Layer 1 (Discover) currently has only
**one source**: the GitHub code scanner (Slice 1a/1b — shipped). You can
only see AI that lives in source code.

This sub-project adds the two remaining discovery sources:

1. **Cloud-AI** — the AI services running in a customer's cloud
   (Bedrock, SageMaker, Comprehend, AI-carrying Lambda functions).
2. **Provider connectors** — the customer's AI usage as seen from the
   *provider org* (OpenAI, Anthropic): projects, members, API keys, and
   the models actually provisioned.

After this lands, the AI estate is visible from all three angles —
code, cloud, and provider — and they converge on one model.

## 2. Guiding principle — AI is a lens, not a silo

AI security is **one more source of intelligence feeding the unified
security + compliance model** — not a separate product vertical.

- A Bedrock model is a *cloud* resource that happens to be AI. It is
  discovered by the cloud scan and stored with `domain='cloud'`.
- A finding carries *every* framework it maps to at once — SOC 2, CIS,
  NIST AI RMF, ISO 42001 — with no "AI findings" bucket.
- The compliance view shows AI and non-AI frameworks side by side
  because they are all just keys in `findings.frameworks`.

The unified `entities` / `edges` / `findings` model already expresses
this. This sub-project uses it as-is; it does not re-wall AI into a
parallel surface.

## 3. Scope and non-goals

**In scope:** the cloud-AI connector and the OpenAI + Anthropic provider
connectors, including key/access hygiene findings and NIST AI RMF /
ISO 42001 posture surfaced through the *existing* compliance view.

**Explicitly out of scope** (named so the boundary is unambiguous):

- Whitney prompt-injection / enhanced Semgrep rules — belongs to the
  Assess module.
- Trivy / Syft container + dependency scanning — Assess module.
- Provider **usage / cost analytics** — a separable feature; the
  connectors pull inventory + hygiene only.
- Azure-AI / GCP-AI cloud scanning — Shasta has Azure AI support, but
  this sub-project wires **AWS only**; other clouds follow once AWS
  proves the pattern.
- Any new inventory page, trust-graph visualization, or AI-governance
  page (a separate, dropped sub-project).
- Scheduled / continuous re-scans — scans run on connect and on demand.

## 4. Decisions log

Settled in the 2026-05-20 brainstorm:

| # | Decision | Rationale |
|---|---|---|
| D1 | Cloud-AI runs **folded into every AWS scan** — no separate connection, no separate trigger | Shasta's AWS-AI checks reuse the AWS reader role the cloud scan already assumes; "cloud includes AI components" |
| D2 | Cloud-AI service entities use **`domain='cloud'`**, AI-ness expressed in `kind` | The "AI is a lens" principle — a Bedrock model is a cloud resource |
| D3 | Provider connectors discover **inventory + key/access hygiene findings** (not usage analytics) | Hygiene is connector-native and high value; usage analytics is separable |
| D4 | NIST AI RMF / ISO 42001 posture is **scored into the existing compliance view**, no new page | `compliance_summary` already rolls up any framework key from `findings.frameworks` |
| D5 | Provider scan runs **on connect and is re-runnable on demand** | No scheduler needed at current scale |
| D6 | Scope is **connectors only** | Whitney + Trivy are the next module (Assess) |
| D7 | No code edits to `~/Projects/Shasta` | Shasta is a frozen input building block (CLAUDE.md); all wiring lives in CISO Copilot |

## 5. Architecture overview

Both connectors write into the unified `entities` / `edges` / `findings`
tables via the existing `unified_writer` (`commit_scan(...)`). Nothing
else in the stack changes.

```
Cloud-AI (new — extends an existing Lambda):
  AWS scan → shasta_runner (container Lambda)
    → existing cloud scan + SP1 enum passes
    → NEW: Shasta AI pass (ai_discovery + ai_checks + ai_sbom + compliance/ai)
    → entities(domain=cloud) + edges + findings(frameworks: nist_ai_rmf, iso_42001, …)
    → unified_writer.commit_scan

Provider (new — net-new connection + Lambda):
  "Connect OpenAI/Anthropic" card → paste admin API key
    → POST /v1/ai/connections/provider/connect
    → verify key, store in Secrets Manager, insert ai_connections row
    → enqueue provider-scan-queue
    → provider_scanner Lambda → provider admin API enumeration
    → entities(domain=ai) + edges + hygiene findings
    → unified_writer.commit_scan
```

**Reused unchanged:** `unified_writer`, `entities`/`edges`/`findings`
tables, `ai_connections` table, `compliance_summary` Lambda, the AI
Inventory web + iOS views (they read `entities`), Secrets Manager,
API Gateway, Cognito.

## 6. Cloud-AI connector

### 6.1 Where it runs

The AWS scanner is the existing `platform/lambda/shasta_runner/`
container Lambda (`app/`, `Dockerfile`, `build.sh`). It already wraps
Shasta and, since SP1, emits entities + edges + findings. This
sub-project adds an **AI pass** to `shasta_runner/app/` that runs after
the existing cloud scan and SP1 enumeration passes, inside the same
`commit_scan` transaction.

### 6.2 What the AI pass calls (all existing Shasta functions)

| Shasta entry point | Produces |
|---|---|
| `aws.ai_discovery.discover_aws_ai_services(client)` | SageMaker endpoints / models / training jobs, Bedrock foundation models + guardrails, Comprehend endpoints, Lambda functions carrying AI API-key env vars |
| `aws.ai_checks.run_full_aws_ai_scan(client)` | 15 AWS-AI security checks (Bedrock + SageMaker + Lambda + S3 training data + CloudTrail) → `Finding` objects |
| `aws.ai_sbom` (cloud mode) | AI components inventory (CycloneDX shape) — used to enrich entity attributes |
| `compliance/ai/mapper.py` + `scorer.py` | Maps each finding to NIST AI RMF / ISO 42001 (and OWASP LLM Top 10 / MITRE ATLAS) control IDs |

Shasta is installed into the image with `pip install --no-deps`
(per CLAUDE.md). The AI modules introduce no new heavy dependencies —
`boto3` is already present.

### 6.3 Entities emitted (`domain='cloud'`)

| `kind` | `natural_key` | Source |
|---|---|---|
| `bedrock_model` | resource ARN | `discover_aws_ai_services` |
| `bedrock_guardrail` | resource ARN | `discover_aws_ai_services` |
| `sagemaker_endpoint` | resource ARN | `discover_aws_ai_services` |
| `sagemaker_model` | resource ARN | `discover_aws_ai_services` |
| `sagemaker_training_job` | resource ARN | `discover_aws_ai_services` |
| `comprehend_endpoint` | resource ARN | `discover_aws_ai_services` |

Lambda functions that carry AI API-key env vars are **not** new
entities — they are the existing `aws_lambda_function` entity with an
added `attributes.ai_usage` block (the detected provider + env-var
names). Edges: `aws_account → contains → <service entity>` (edge kind
`contains`, matching SP1's cloud topology edges).

### 6.4 Findings

The 15 checks return Shasta `Finding` objects. The `shasta_runner`
already maps Shasta findings into CISO Copilot's `findings` rows; the AI
pass adds nothing new to that mapping except populating
`findings.frameworks` from the `compliance/ai` mapper output. Because a
finding carries all applicable frameworks, an AI check that is also a
SOC 2 control simply carries both keys.

### 6.5 IAM

**No change.** The CFN onboarding role grants
`arn:aws:iam::aws:policy/ReadOnlyAccess`, which already covers
`bedrock:List*/Get*`, `sagemaker:List*/Describe*`, and
`comprehend:List*/Describe*`. No customer re-onboarding is required.

## 7. Provider connectors (OpenAI, Anthropic)

### 7.1 Connection flow

1. `ConnectClouds.tsx` gains "Connect OpenAI" and "Connect Anthropic"
   cards beside the cloud + GitHub connectors.
2. Clicking a card opens a paste-key modal (the customer supplies an
   **organization admin API key**).
3. Web posts `{provider, admin_api_key}` to
   `POST /v1/ai/connections/provider/connect` (a new route on the Lambda
   that already serves `/v1/ai/connections/*`).
4. The Lambda makes one probe call against the provider admin API to
   verify the key, stores it in a new Secrets Manager secret, and
   inserts an `ai_connections` row (`provider='openai'|'anthropic'`,
   `secret_arn=…`, `status='active'`). The `ai_connections` schema
   already permits these providers and carries `secret_arn`.
5. The connect handler enqueues a provider scan; the modal closes onto a
   "scanning…" state.

A bad key → `400`, the modal shows "key rejected". The admin key is
never returned to the client after storage.

### 7.2 Provider scanner

A new **`platform/lambda/provider_scanner/`** Lambda — plain Python
(HTTPS + `psycopg2`), no container image needed (it makes API calls, it
does not clone repos). Triggered by a new `provider-scan-queue` SQS
queue (DLQ, `maxReceiveCount=3`), mirroring the `ai-scan-queue` /
`ai_scanner` pattern. Re-runnable via
`POST /v1/ai/connections/{id}/scan`.

*(Considered and rejected: a synchronous async-invoke with no queue.
A queue is kept for DLQ + retry consistency with the other scanners.)*

It enumerates the org through the provider admin API:

- **OpenAI** — organization → projects → users + service accounts →
  API keys → models available to the org.
- **Anthropic** — organization → workspaces → members → API keys →
  models.

(Exact admin-API endpoint paths are verified during implementation.)

### 7.3 Entities emitted (`domain='ai'`)

| `kind` | `natural_key` |
|---|---|
| `ai_provider_org` | `{provider}:{org_id}` (e.g. `openai:org-abc`) |
| `ai_project` | `{provider}:{org_id}/{project_or_workspace_id}` |
| `ai_api_key` | `{provider}:{org_id}/{key_id}` |
| `ai_org_member` | `{provider}:{org_id}/{user_id}` |

Discovered models are **not** new entities — they upsert onto the
existing deduped `ai_model` entity (`natural_key = {provider}/{model_id}`,
e.g. `openai/gpt-4o`). A model the code scanner already found is
*enriched*, not duplicated — provider discovery and code discovery
converge on one node.

Edges: `ai_provider_org → contains → ai_project`,
`ai_project → has_key → ai_api_key` (new edge kind `has_key`),
`ai_project → uses → ai_model`,
`ai_org_member → member_of → ai_provider_org` (edge kind `member_of`,
already in SP1's set).

### 7.4 Key / access hygiene findings

Deterministic rules over the enumerated inventory. Each carries
`frameworks` (NIST AI RMF GOVERN/MANAGE controls + ISO 42001), so they
score into the compliance view alongside everything else.

| `check_id` | Fires when | Severity |
|---|---|---|
| `provider-stale-api-key` | API key unused > 90 days | medium |
| `provider-api-key-no-expiry` | API key has no expiry set | low |
| `provider-overbroad-api-key` | org-scoped key where a project-scoped key would suffice | medium |
| `provider-excess-org-admins` | org has more owners/admins than a configurable threshold | medium |

The finding `subject_entity_id` points at the relevant `ai_api_key` or
`ai_provider_org` entity.

## 8. AI-framework posture (NIST AI RMF / ISO 42001)

No new Lambda, no new endpoint, no new page. `compliance_summary`
already unnests `findings.frameworks` (`jsonb_each`) and rolls up a
`{passing, failing, total, score_pct}` per framework key. As soon as
cloud-AI findings and provider hygiene findings populate `frameworks`
with `nist_ai_rmf` / `iso_42001` controls, those frameworks appear in:

- the compliance summary API,
- the compliance donut + per-framework tiles in chat
  (`get_compliance_summary`),
- any existing compliance UI that reads that endpoint.

The only requirement on this sub-project is that findings are
**emitted with correct `frameworks` JSONB** — guaranteed for cloud-AI by
Shasta's `compliance/ai` mapper, and authored explicitly for the four
provider hygiene findings.

## 9. Data model changes

The unified model already absorbs almost everything:

- **Entities** — `kind` is free-text; the new kinds need no migration.
- **`ai_connections`** — already permits `provider IN ('github',
  'openai','anthropic')` and carries `secret_arn`; no change.
- **`findings.frameworks`** — already exists; no change.

One **minor migration — `platform/sql/008_provider_scans.sql`**:

```sql
-- provider scans have no repository
ALTER TABLE ai_scans ALTER COLUMN repo_asset_id DROP NOT NULL;
ALTER TABLE ai_scans ADD COLUMN scan_kind TEXT NOT NULL DEFAULT 'repo'
  CHECK (scan_kind IN ('repo', 'provider'));
```

`scan_kind` lets the scans list distinguish code scans from provider
scans; `repo_asset_id` becomes nullable because a provider scan has no
repo entity.

## 10. Surfaces (web + iOS)

- **Web** — the only new UI is the two Connect cards + the paste-key
  modal in `ConnectClouds.tsx`. New cloud-AI and provider entities flow
  into the existing AI Inventory automatically (it lists `entities`).
  Findings flow into the existing findings/Risks surface. Compliance
  picks up the new frameworks automatically.
- **iOS** — no code change required for data: the AI tab reads the same
  `entities` endpoint. New entity `kind`s may want icons/labels; that is
  a cosmetic follow-up, not a blocker. No onboarding from iOS (provider
  connect is web-only, consistent with cloud + GitHub onboarding).

## 11. Error handling

| Condition | Behavior |
|---|---|
| Provider admin key invalid at connect | `400`; modal shows "key rejected"; no `ai_connections` row created |
| Provider API `429` during scan | exponential backoff; if still failing, scan ends `failed` with `error_message` |
| Provider key revoked after connect | next scan gets `401`; scan flips `ai_connections.status='failed'`; UI shows a reconnect CTA |
| Provider scan partial failure (one endpoint of several fails) | emit what succeeded, record a `provider_scan_partial` finding noting the gap; scan status `success` |
| Bedrock/SageMaker/Comprehend not enabled in a region | Shasta already catches `ClientError` per service; that sub-scan is skipped, the AWS scan continues |
| Cloud-AI pass raises | the whole `commit_scan` transaction rolls back (existing SP1 behavior); SQS retries to DLQ |

## 12. Testing

- **Cloud-AI** — unit-test the AI-pass emission mapping (Shasta result →
  `EntityEmission` / `FindingEmission`, including `frameworks`
  population) with a stubbed Shasta result object. Shasta's own test
  suite already covers the 15 checks and the `compliance/ai` mappers, so
  this side does not re-test detection.
- **Provider** — unit-test entity/edge/finding emission against stubbed
  OpenAI and Anthropic admin-API responses (recorded fixtures). The four
  hygiene rules each get a fixture pair (firing / not-firing) with a
  golden assertion.
- **Connect flow** — integration test: `connect` with a stubbed probe
  call → asserts the Secrets Manager write + `ai_connections` row +
  enqueue.
- **`compliance_summary`** — a regression test that a finding carrying
  `nist_ai_rmf` / `iso_42001` keys rolls those frameworks into the
  summary (proves D4 with no code change to that Lambda).
- **E2E (manual)** — KK connects his real OpenAI and Anthropic orgs,
  runs an AWS scan, and confirms: Bedrock/SageMaker entities appear,
  provider org/projects/keys appear, hygiene findings fire, and
  NIST AI RMF + ISO 42001 show up in the compliance view.

## 13. Sequencing (vertical slices)

| Slice | Deliverable | Demo |
|---|---|---|
| 1 — Cloud-AI | AI pass added to `shasta_runner`; entities + findings + framework scores | An AWS scan now also surfaces Bedrock/SageMaker entities and NIST AI RMF / ISO 42001 in the compliance view |
| 2 — OpenAI connector | Connect card + flow, `provider_scanner`, `008` migration, OpenAI enumeration, entities + hygiene findings | Connect OpenAI → org / projects / keys / members visible, hygiene findings fire |
| 3 — Anthropic connector | Anthropic enumeration in `provider_scanner` + Connect card | Connect Anthropic → same shape, second provider |

Each slice ends with a working demo. The `008` migration lands with
Slice 2 (the first slice that needs it).

## 14. Out of scope (explicit)

- Whitney prompt-injection / Semgrep, Trivy/Syft container scanning
  (Assess module)
- Provider usage / cost analytics
- Azure-AI / GCP-AI cloud scanning
- Scheduled / continuous re-scans, webhook-driven rescans
- New inventory page, trust-graph visualization, AI-governance page
- KMS-signed evidence packets
- Runtime AI discovery (live agents, MCP servers in the wild)

## 15. References

- v2 PRD — `CISOBrief-v2.md`
- Current state — `HANDOFF.md`
- Unified entity model — `docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md`
- GitHub code scanner — `docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md`
- AI-security capability roadmap — `docs/future_todos.md`
- Building blocks — `~/Projects/Shasta` (`github.com/transilienceai/shasta`)

---

*Spec ends here. The implementation plan is written separately via the
writing-plans skill once this spec is approved.*
