# AI-Powered SOC (incl. Configuration Drift Monitoring) — Design

> **Status:** Design (brainstorming complete 2026-05-25). Implementation
> plan to be written next via `superpowers:writing-plans`.
> **Author:** KK + Claude (brainstorming session 2026-05-25).
> **Scope:** A new sub-project of CISO Copilot v2 that turns the v2 PRD's
> spec'd-but-unbuilt real-time event pipeline (CISOBrief-v2.md §86-87,
> §132, §264, §370-389) into a shipped capability, layered with AI
> enrichment, threat-intel matching, and anomaly classification.

---

## 1. Problem & wedge

**Problem.** v2 ships scheduled posture scans, AI-Visibility, and the
chat-first surface — but the CISO has no real-time signal for "what
changed in my clouds in the last 60s?". The v2 PRD §86-87 promised
real-time alert + drift ingestion with sub-60s push to iPhone; the
plumbing was spec'd (router Lambda, EventBridge bus, `events` +
`drift_events` tables) but never built.

**Wedge.** This sub-project ships three demo moments end-to-end:

1. **Drift-with-explanation** — someone widens a security group → within
   60s the iPhone vibrates with a deterministic push, and tapping
   through shows an AI-written narrative explaining the change, who did
   it, recent history of the same actor on the same resource, and
   suggested next steps.
2. **Investigation copilot** — every drift event lands in `/soc` with
   AI-enriched context (related findings on the resource, statistical
   features, TI matches), not just a raw event payload.
3. **Baseline + anomaly classification** — after ~7 days of observation
   per tenant, AI classifies each event against learned baseline
   (expected / unusual / suspicious) using cheap statistical features
   fed to the LLM.

Voice-driven hunt is explicitly deferred to a future sub-project.

**Non-wedge (out of scope).** Attack graph, blast radius, AI-exposure
Sankey, kill-chain incident replay, remediation automation, Microsoft
Sentinel integration. See §11.

---

## 2. Scope captured during brainstorm

| Decision | Chosen path | Rationale |
|---|---|---|
| Demo wedge | Drift-explanation + investigation copilot + baselining | Three reinforcing moments; voice hunt deferred |
| Drift substrate | Native cloud drift services | Lean on AWS Config / Entra audit log / Azure native / GCP Asset Inventory rather than rebuild detection |
| Drift surface | Infra config + identity | Both substrates; identity drift is the highest-leverage signal for a CISO |
| AI architecture | Async enrichment Lambda | Push fires on deterministic rules first; AI narrative + anomaly score written back asynchronously. Predictable cost, simple failure modes |
| Anomaly engine | Hybrid: stats → LLM | Cheap statistical features computed in Python, fed to Claude as features for narrative + classification |
| Cloud scope v1 | AWS + Entra | Highest-leverage demo, reuses Entra connection from AI-visibility |
| Delivery surface | New dedicated `/soc` web page (+ iOS push) | Stronger positioning than reusing Alerts tab; iOS stays companion-only per `project_ios_companion_vision` |
| TI integration | Folded in (Slice 1c). Free feeds only v1. | abuse.ch + CISA KEV + GreyNoise Community + Tor exit list. Pluggable adapters. Paid feeds (GreyNoise Enterprise) post-launch with telemetry-backed biz case |
| LLM swappability | LiteLLM from day one | One env var swaps provider/model; defaults to `claude-sonnet-4-6`; prompt caching preserved |
| Kill-chain pre-commitments | `mitre_technique` + nullable `incident_id` columns | Both columns added to schema in Slice 1; no view painted in this sub-project |
| Azure substrate (Slice 4) | Activity Log + Policy + Resource Graph + Defender-if-on | **NEVER Sentinel** — prohibitive customer cost ([[feedback_no_azure_sentinel]]) |
| AWS Config recording | "Essentials" profile (~25 resource types), not all-resources | Keeps customer-side cost in $30-80/mo range |

---

## 3. Slice plan (vertical, demo-able per slice)

| Slice | Substrate | Demo moment | Est. effort |
|---|---|---|---|
| **1** | AWS Config infra drift | "Open SG to world → iPhone vibrates with AI narrative in 60s" | ~3 weeks |
| **1c** | Threat-intel substrate | Same SG event but with TI badges: "Source IP is Tor exit + abuse.ch botnet C2 (conf 85)" | ~2 weeks |
| **2** | Identity drift (AWS IAM via CloudTrail + Entra audit log) | "New admin role assigned at 3am → push with AI explanation" | ~3 weeks |
| **3** | Baseline activation | "First time this actor has done this in your tenant" callout | ~2 weeks |
| **4** | Azure (Activity Log + Policy + Resource Graph; **no Sentinel**) + GCP (Asset Inventory + Audit Logs) | Same Slice 1+1c+2 patterns on Azure and GCP | ~4 weeks (parallel) |

Each slice ships end-to-end (substrate → router → enrichment → API → UI
→ push). No "phase 1 = all DB work" horizontal slicing.

---

## 4. Architecture

### 4.1 High-level data flow

```
Customer AWS account                          Our AWS account (us-east-1)
┌──────────────────────────┐                  ┌──────────────────────────────────────┐
│  AWS Config recorder     │                  │  ciso-copilot-events                  │
│   (essentials profile)   │──EventBridge────▶│  (central EventBridge bus)            │
│                          │   cross-account  │           │                           │
│  CloudTrail mgmt events  │   PutEvents      │           ▼                           │
│   (IAM mutations, S2)    │                  │   ┌─────────────────────┐             │
└──────────────────────────┘                  │   │  Router Lambda      │             │
                                              │   │  - normalize        │             │
Customer Entra tenant (S2)                    │   │  - dedupe           │             │
┌──────────────────────────┐  Microsoft Graph │   │  - severity         │             │
│  Entra audit log         │──poll every 5min▶│   │  - push rules       │             │
└──────────────────────────┘                  │   └──────────┬──────────┘             │
                                              │              │                        │
                                              │              ▼                        │
                                              │   ┌─────────────────────┐             │
                                              │   │  events +           │             │
                                              │   │  drift_events       │             │
                                              │   │  (Aurora)           │             │
                                              │   └─────┬───────────┬───┘             │
                                              │         │           │                 │
                                       push fires immediately       │                 │
                                       (deterministic rules)        │                 │
                                              │         │           ▼                 │
                                              │         ▼   ┌─────────────────┐       │
                                              │    SNS APNs │ Enrichment Lambda│      │
                                              │             │ - stat features │       │
                                              │             │ - TI lookup     │       │
                                              │             │ - LiteLLM call  │       │
                                              │             │ - write back    │       │
                                              │             └────────┬────────┘       │
                                              │                      │                 │
                                              │                      ▼                 │
                                              │      events.ai_* fields populated      │
                                              │                      │                 │
                                              │                      ▼                 │
                                              │    GET /v1/soc/drift → /soc renders    │
                                              └──────────────────────────────────────┘
```

### 4.2 Latency budget (t = seconds from drift event)

| t (sec) | What happens |
|---|---|
| 0 | Customer drift occurs |
| 2-5 | CloudTrail / Config emits to customer event bus |
| 5-10 | EventBridge cross-account PutEvents to our bus |
| 10-12 | Router Lambda normalizes, inserts, enqueues, fires push |
| 12 | **iPhone vibrates** (templated deterministic copy) |
| 12-20 | Enrichment Lambda picks up SQS message, computes features, calls LiteLLM, writes back |
| 20 | User taps push → `/soc/drift/{id}` loads with full AI narrative |

**Demo line is "60s" for headroom.** Target p95 enrichment <30s.

### 4.3 Operational guarantees

1. Push fires within 60s of event entering our bus (deterministic path is load-bearing; AI never gates push)
2. Drift event durably stored within 5s of router Lambda invocation
3. AI enrichment p95 <30s, or marks failed/pending — never blocks event from being readable
4. Idempotent ingestion via `(tenant_id, source_event_id)` unique constraint
5. Per-tenant push budget (default 10/hr) so floods don't = phone-buzz-fest

### 4.4 Non-guarantees (explicit)

- Sub-10s end-to-end latency (we target 12-20s; demo line "60s")
- Push when Anthropic / LiteLLM provider is down (deterministic copy still fires; AI narrative arrives when API recovers)
- Event delivery if customer side stops forwarding (we detect gap and warn)

---

## 5. Components

| Component | Path | What it does | Slice |
|---|---|---|---|
| AWS Config CFN extension | `platform/cfn/aws-onboard.yaml` (extend) | Adds Config recorder (essentials profile) + delivery channel + EventBridge rule forwarding `aws.config` events to our bus | 1 |
| CloudTrail-IAM EventBridge rule | `platform/cfn/aws-onboard.yaml` (extend) | Rule selecting CloudTrail mgmt events where eventSource ∈ {iam, sso, organizations, sts} | 2 |
| Router Lambda | `platform/lambda/soc_router/` | Per-source normalize → dedupe → severity → INSERT → push rules → SQS enqueue | 1 (+ 2, 4 extend) |
| Enrichment Lambda | `platform/lambda/soc_enrichment/` | SQS consumer: features → TI lookup → LiteLLM call → UPDATE events row | 1 (+ 1c, 2, 3 extend) |
| TI feed adapters | `platform/lambda/ti_feed_{abusech,kev,tor,greynoise_community}/` | Cron-driven ETL; upsert to `threat_indicators` | 1c |
| TI base | `platform/lambda/ti_feed_base/` | Abstract `TIFeed` class with `fetch / parse / upsert` | 1c |
| Entra audit poller | `platform/lambda/soc_entra_poller/` | 5-min cron; Graph API → `drift_events` shape | 2 |
| `/v1/soc/drift` API | `platform/lambda/soc_api/` | List + detail + feedback endpoints | 1 |
| `/soc` web page | `web/src/routes/Soc.tsx` | Timeline + filter chips + detail pane (AI narrative + TI badges + related findings) | 1 (+ extend per slice) |
| Customer docs | `docs/customer/drift-detection-{aws,entra,azure,gcp}.md` | Per-cloud onboarding + cost disclosure + opt-out | 1 (AWS), 2 (Entra), 4 (Azure+GCP) |

### 5.1 LLM abstraction

- **Library:** LiteLLM (`pip install litellm`).
- **Default model:** `claude-sonnet-4-6` (via Anthropic provider).
- **Swap mechanism:** Env var `SOC_ENRICHMENT_LLM_MODEL` controls the
  `model=` parameter in `litellm.completion(...)`.
- **Audit:** `events.ai_model_version` records the actual model used
  per event (provenance under audit).
- **Prompt caching:** preserved when provider is Anthropic; transparent
  to the abstraction.
- **Future:** per-tenant override (e.g., residency-constrained customer
  forces Bedrock-only) is a Slice 5+ extension; v1 is one model per
  deployment.

### 5.2 TI feed pluggability

```python
# ti_feed_base/main.py
class TIFeed(ABC):
    name: str
    cron_schedule: str  # cron expression

    @abstractmethod
    def fetch(self) -> Iterable[RawIOC]: ...

    @abstractmethod
    def parse(self, raw: RawIOC) -> Iterable[Indicator]: ...

    def upsert(self, indicators: Iterable[Indicator]) -> None:
        # shared: writes to threat_indicators with ON CONFLICT (indicator_value, kind)
        # DO UPDATE SET last_seen = EXCLUDED.last_seen, ...
```

Adding GreyNoise Enterprise later = one new subclass + one env var for
the API key. No schema migration.

---

## 6. Data model

Builds on v2 PRD §370-389 (`events` + `drift_events`). Adds AI fields,
TI table, and the two kill-chain pre-commitments.

```sql
-- AI enrichment fields on events (post-router-INSERT, populated async)
ALTER TABLE events ADD COLUMN ai_narrative           TEXT;
ALTER TABLE events ADD COLUMN ai_anomaly_class       TEXT;        -- 'expected' | 'unusual' | 'suspicious' | NULL
ALTER TABLE events ADD COLUMN ai_anomaly_score       INTEGER;     -- 0-100
ALTER TABLE events ADD COLUMN ai_next_steps          JSONB;       -- [{"step":"...", "command":"..."}, ...]
ALTER TABLE events ADD COLUMN ai_features            JSONB;       -- structured features fed to LLM (auditability)
ALTER TABLE events ADD COLUMN ai_model_version       TEXT;        -- e.g. 'claude-sonnet-4-6'
ALTER TABLE events ADD COLUMN ai_enriched_at         TIMESTAMPTZ;

-- Kill-chain pre-commitments (nullable; populated by future correlator)
ALTER TABLE events ADD COLUMN mitre_technique        TEXT;        -- e.g. 'T1098', 'T1548.005'
ALTER TABLE events ADD COLUMN incident_id            UUID;

-- Drift-event graph-shape pre-commitment
ALTER TABLE drift_events ADD COLUMN target_resource_arn TEXT;     -- redundant with events.resource_arn; explicit for future entity graph

-- Idempotency column + unique constraint for dedupe contract
-- (existing schema has no equivalent; add source_event_id)
ALTER TABLE events ADD COLUMN source_event_id TEXT;               -- provider-native event ID (CloudTrail eventID, Config configurationItemCaptureTime+resourceId, etc.)
ALTER TABLE events ADD CONSTRAINT events_tenant_source_event_id_unique
  UNIQUE (tenant_id, source, source_event_id);

-- New indices for /soc query patterns (idx_events_tenant_kind_fired already exists in 002_phase_a.sql)
CREATE INDEX idx_events_tenant_anomaly    ON events (tenant_id, ai_anomaly_class, fired_at DESC)
                                          WHERE ai_anomaly_class IN ('unusual','suspicious');
CREATE INDEX idx_events_incident          ON events (incident_id) WHERE incident_id IS NOT NULL;

-- Threat intelligence indicators (tenant-independent, global table)
CREATE TABLE threat_indicators (
  indicator_value   TEXT NOT NULL,
  kind              TEXT NOT NULL,            -- 'ip' | 'domain' | 'url' | 'sha256' | 'cve'
  source            TEXT NOT NULL,            -- 'abusech' | 'kev' | 'tor' | 'greynoise_community' | future
  first_seen        TIMESTAMPTZ NOT NULL,
  last_seen         TIMESTAMPTZ NOT NULL,
  confidence        INTEGER,                  -- 0-100, source-dependent
  tags              JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw               JSONB,                    -- source-specific extras
  PRIMARY KEY (indicator_value, kind, source)
);
CREATE INDEX idx_threat_indicators_value ON threat_indicators (indicator_value, kind);

-- Per-tenant LLM spend cap counter (DynamoDB, not Aurora)
-- Table: soc_llm_spend_daily
-- PK: tenant_id (string)
-- SK: yyyy-mm-dd (string)
-- Attrs: cents_spent (number), cap_cents (number), model_version (string)
```

**Notes:**
- AI fields all on `events`, not a separate `event_enrichments` table —
  1:1 relationship, one UPDATE per enrichment.
- `ai_features` is the auditability hook. When a customer or auditor
  asks "why did this fire suspicious?", we can show the structured
  signals the LLM saw, not just the narrative.
- `threat_indicators` is global (not per-tenant) — IOCs are public
  knowledge by nature; no tenant isolation needed.
- LLM spend cap lives in DynamoDB for write performance; we update on
  every enrichment call.

---

## 7. Data flow lifecycle (happy path, fully detailed)

Already covered in §4.2 as latency budget. Expanded narrative:

1. **t=0:** Drift occurs (e.g., `AuthorizeSecurityGroupIngress :22
   0.0.0.0/0` on `sg-abc` in customer AWS account).
2. **t=2-5:** AWS Config detects the SG state change → emits
   `ConfigurationItemChangeNotification` to customer's default
   event bus. (CloudTrail also emits the API event; we deduplicate on
   our side via `source_event_id`.)
3. **t=5-10:** Customer EventBridge rule (installed by our CFN)
   forwards via cross-account `PutEvents` to our central
   `ciso-copilot-events` bus in `us-east-1`.
4. **t=10-12:** Router Lambda fires:
   - normalize → `{kind:'drift', source:'config', action:'AuthorizeSecurityGroupIngress', principal:'arn:aws:iam::...:user/x', target_arn:'sg-abc', before_state:{...}, after_state:{...ingress:[0.0.0.0/0:22]...}}`
   - dedupe on `(tenant_id, source_event_id)` — INSERT ... ON CONFLICT DO NOTHING
   - deterministic severity from rule table → `'high'` (SG-open-to-world is in the high-severity rule set)
   - INSERT into `events` + `drift_events` within one tx
   - severity ≥ tenant push threshold → SNS Mobile Push (APNs) fires
   - SQS enqueue → enrichment queue
5. **t=12:** iPhone vibrates with templated push body: *"drift · prod-frontend opened :22 to internet · by user:x"*. AI not present yet.
6. **t=12-20:** Enrichment Lambda picks up SQS message:
   - **Features (cheap):** first-time-actor-on-resource bool, off-hours flag (tenant tz-aware), action-rarity from 30d history, blast-radius proxy
   - **TI lookup (Slice 1c):** extract IPs/domains/hashes from event payload → `SELECT * FROM threat_indicators WHERE indicator_value = ANY($1)`. Add matches to features.
   - **Context (cheap):** related findings on `target_arn`, recent drift on same `principal`, baseline summary
   - **LiteLLM call:** `litellm.completion(model=$SOC_ENRICHMENT_LLM_MODEL, messages=[...], response_format={"type":"json_object"})`. Spend cap check first.
   - **Parse response:** `{narrative, anomaly_class, anomaly_score, next_steps, mitre_technique}` (mitre_technique only if model is confident).
   - **UPDATE `events`** with `ai_*` fields. Single round trip.
7. **t=20:** User taps the push → `/v1/soc/drift/{event_id}` returns full row with AI fields populated.

---

## 8. Error handling

| Failure mode | Detection | Response |
|---|---|---|
| LiteLLM provider outage / 5xx | Enrichment Lambda exception | `ai_narrative = NULL`, `ai_model_version = 'unavailable'`. SQS DLQ after 3 retries. Nightly retry-from-DLQ Lambda. Push already fired. |
| Per-tenant spend cap reached | DynamoDB counter check pre-call | Short-circuit; `ai_model_version = 'cap_reached'`. CloudWatch metric. Email at 80% of cap. Default cap $10/day v1. |
| Customer EventBridge perm lost | CloudWatch alarm on event-volume drop per `conn_id` (>2σ below 7d baseline) | Mark `cloud_connections.signals.drift = false`. `/soc` shows banner. Page our on-call; email customer for theirs. |
| AWS Config not enabled | First scan pings Config recorder status | Wizard shows "AWS Config not yet recording — enable here". `signals.drift = false`. UI degrades gracefully. |
| CloudTrail trail missing | Same check at scan time | Same pattern. |
| Aurora connection pool exhausted | RDS Data API throttles | Router uses RDS Data API (SDK retry). Enrichment SQS visibility timeout = 60s; failed messages naturally retry. Aurora Serverless v2 scales ACU. |
| Event flood (Config bulk eval on big account) | Router concurrency cap | Concurrency = 50. SQS absorbs burst. Push rate-limit = 10/hr/tenant default; floods digest to "tap to see N more". |
| Duplicate events | `(tenant_id, source_event_id)` unique constraint | ON CONFLICT DO NOTHING. |
| Slow LLM (p99 > 30s) | Enrichment Lambda timeout = 90s | UI shows "AI analysis pending" not "missing". Beyond 90s → SQS retry. |
| TI feed adapter failure | CloudWatch alarm on adapter execution failure | Other feeds keep running. Stale indicators have `last_seen` timestamp so consumers can detect freshness. |

---

## 9. Testing strategy

### 9.1 Unit tests (Python pytest, one suite per Lambda)

- `soc_router/tests/test_normalize.py` — per-source fixtures (AWS Config, CloudTrail-IAM, Entra audit) → normalized shape
- `soc_router/tests/test_dedupe.py` — idempotency contract
- `soc_router/tests/test_severity.py` — rule table coverage
- `soc_router/tests/test_push_rules.py` — threshold + rate limit + digest behavior
- `soc_enrichment/tests/test_features.py` — statistical features from seeded Aurora fixture
- `soc_enrichment/tests/test_ti_match.py` — TI lookup for IP/IPv6/domain/hash (Slice 1c)
- `soc_enrichment/tests/test_litellm_prompt.py` — snapshot test of constructed prompt (no live call)
- `soc_enrichment/tests/test_litellm_parser.py` — canned responses (valid JSON, malformed, partial, rate-limit, 5xx) → correct field state
- `soc_enrichment/tests/test_cost_cap.py` — DynamoDB counter; below cap → call; at cap → short-circuit
- `ti_feed_{abusech,kev,tor,greynoise}/tests/test_parse.py` — one per feed, real sample fixture
- `soc_api/tests/test_list.py` — pagination, filters, tenant isolation (RLS)
- `soc_api/tests/test_detail.py` — full AI fields + related findings; graceful when enrichment pending
- `soc_api/tests/test_feedback.py` — thumbs up/down writes to existing `feedback` table

### 9.2 Integration tests (ephemeral Aurora)

- `test_e2e_drift_flow.py` — SQS → router → events row → enrichment (LLM mocked) → API returns full row
- `test_e2e_push_flow.py` — high-sev drift → SNS mock receives templated push; AI fields populated at later GET
- `test_e2e_ti_match_flow.py` — drift with IP matching seeded indicator → `ai_features.ti_matches` populated (Slice 1c)
- `test_e2e_anomaly_class.py` — 30d seeded baseline + new event from never-seen actor at 3am → mocked LLM gates on features → `anomaly_class = 'unusual'` (Slice 3)
- `test_e2e_failure_recovery.py` — LLM 5xx → `ai_model_version = 'unavailable'` but event queryable; SQS DLQ has message; nightly retry recovers

### 9.3 Frontend tests (Vitest)

- `Soc.test.tsx` — timeline renders, filter chips, severity colors, "AI analysis pending" state
- `SocDetail.test.tsx` — AI narrative + features + next-steps + TI badges + related findings + feedback thumbs

### 9.4 Manual / E2E gates (TEST_PLAN.md)

| Slice | Manual gate |
|---|---|
| 1 | Open SG to 0.0.0.0/0:22 on test AWS account → drift in `/soc` <20s → AI narrative present → push on iPhone <60s |
| 1c | Trigger from a known Tor exit IP → narrative mentions Tor + abuse.ch |
| 2 | Assign new admin role in test Entra → drift appears in `/soc` Identity filter <5min → AI narrative present |
| 3 | First-time-actor event after 7d seeded baseline → `/soc` shows "unusual" badge |
| 4 | Repeat Slice 1+1c+2 patterns on Azure (verify NO Sentinel dependency) and GCP |

### 9.5 Out of test scope

- Live LLM provider API calls in unit/integration (mocked; live only in manual gates)
- Live cloud-provider event delivery in unit (fixtures only; live in manual gates)
- LLM provider spend per test run ($0 — mocked)

---

## 10. Customer onboarding & cost disclosure

Per-cloud wizard toggle "Drift detection" (default ON for AWS+Entra v1)
expands a disclosure card. Per-cloud documentation in
`docs/customer/drift-detection-{cloud}.md` links from the wizard card.

### 10.1 AWS (Slice 1 + 2)

| Resource | Purpose | Customer cost |
|---|---|---|
| `AWS::Config::ConfigurationRecorder` (essentials, ~25 resource types) | Resource state change detection | ~$30-80/mo for typical account |
| `AWS::Config::DeliveryChannel` | Required pair; delivers to customer-owned S3 | <$5/mo S3 |
| `AWS::Events::Rule` (Config events) | Forward to our bus | $0 (cross-account PutEvents free) |
| `AWS::Events::Rule` (CloudTrail IAM events) | Identity drift forwarding (Slice 2) | $0 (assumes existing CloudTrail trail) |

**Disclosure copy:** "Enable real-time drift detection? We'll turn on
AWS Config (essentials profile — 25 security-relevant resource types)
and forward IAM events from your existing CloudTrail. Estimated AWS
cost: **$30-80/mo**. Without this, drift is detected at scan cadence
(daily) instead of within 60 seconds."

**Opt-out:** Toggle off → CFN drops Config recorder + EventBridge
rules. Existing rows stay queryable.

### 10.2 Entra (Slice 2)

| Added OAuth scope | Purpose | Customer cost |
|---|---|---|
| `AuditLog.Read.All` | Directory audit log (roles, OAuth grants, MFA changes) | $0, all Entra tiers |
| `Reports.Read.All` (optional) | Sign-in risk events | $0 Free tier; richer signal with Entra P1/P2 (already disclosed per Slice 2.1 banner) |

**Transport:** 5-min Graph polling v1. Graph webhook subscriptions
deferred to Slice 2.5.

**Disclosure copy:** "Enable Entra identity drift detection? We'll
read your directory audit log every 5 minutes via Microsoft Graph.
No customer cost. Requires admin consent to: `AuditLog.Read.All`,
`Reports.Read.All`."

### 10.3 Azure (Slice 4)

| Substrate | Purpose | Customer cost |
|---|---|---|
| Activity Log → Diagnostic Settings → Event Hub | Subscription-level write events | Diagnostic settings free; Event Hub Basic ~$11/mo |
| Azure Policy compliance state | Resource-level drift | Free |
| Azure Resource Graph change feed | Resource state changes | Free |
| Defender for Cloud alerts (if customer has it on) | Alert ingestion | Customer's existing Defender cost |
| ❌ Microsoft Sentinel | — | **Never. Prohibitive customer cost.** |

### 10.4 GCP (Slice 4)

| Substrate | Purpose | Customer cost |
|---|---|---|
| Cloud Asset Inventory feed → Pub/Sub | Real-time state changes | Asset Inventory free; Pub/Sub ~$0-5/mo |
| Cloud Audit Logs (Admin Activity) → log sink → Pub/Sub | IAM mutations + project policy changes | Admin Activity free; Pub/Sub same |
| Security Command Center findings (if on) | Alert ingestion | Customer's existing SCC cost |

### 10.5 What we explicitly don't enable customer-side

- CloudTrail data events (S3 object-level, Lambda invoke) — too high cost (v2 PRD §184)
- VPC Flow Logs — too high cost (v2 PRD §185)
- Azure Sentinel — prohibitive cost ([[feedback_no_azure_sentinel]])
- AWS Config all-resources recording — only essentials profile (5-10x cost otherwise)
- Inline traffic inspection / endpoint agents / browser extensions — wrong product shape (BACKLOG.md §H)

---

## 11. Out of scope (explicit non-goals)

| Non-goal | Why deferred | Where it eventually belongs |
|---|---|---|
| Remediation automation | v2 PRD §100 — read-only platform | Future "Automated Response" sub-project |
| Attack Graph 3D visualization | Requires entity-relationships engine + IAM transitive-closure solver (months) | Separate "Entity Graph & Blast Radius" sub-project |
| Blast Radius rings | Same as above | Same |
| AI Exposure Sankey | Requires prompt/response tracing through models | Future "AI Flow Tracing" sub-project |
| Kill-Chain incident view | Requires cross-event correlation engine. `incident_id` + `mitre_technique` columns pre-committed; view not painted. | Future "Incident Correlator" sub-project |
| Voice-driven hunt | Deferred during scoping | Future, extends SP4 chat-first |
| Inline traffic / endpoint agents / browser extensions | Wrong product shape (BACKLOG.md §H) | Never |
| Microsoft Sentinel integration | Prohibitive customer cost ([[feedback_no_azure_sentinel]]) | Never |
| CloudTrail data events / VPC Flow Logs | Volume + cost prohibitive (v2 PRD §184-185) | Never |
| Customer-facing event-export API | Internal API only in v2 (v2 PRD §104) | v2.5+ |
| Multi-step LLM agent investigation | Cost + latency + failure modes; v1 sticks to async one-shot | Future "Investigation Copilot v2" |
| Commercial premium TI feeds (Recorded Future, Mandiant, Crowdstrike) | Pre-launch biz case too weak | Post-launch upgrade with telemetry-backed case |
| GreyNoise Enterprise in v1 | Ship free tier first; upgrade when telemetry shows Community rate limits hit | Post-launch upgrade |
| Custom per-tenant detection rules | v2 PRD §101 — Shasta + our rule table are the surface; custom rules in v3 | v3 |
| Anomaly baseline materialization table | YAGNI for v1 — inline 30d-window query works at expected volumes | Slice 3+ only if Aurora cost demands |
| Slack/Teams/email alerts for drift | APNs only in v2 (v2 PRD §99) | Phase F, via [[project_integrations_mcp]] |
| iOS SOC UI | iOS is companion-only ([[project_ios_companion_vision]]); push only on iOS, deep-view on web | Out — explicitly out, not deferred |
| Per-tenant LLM provider override | One model per deployment in v1 | Slice 5+ |
| Graph webhook subscriptions for Entra | 5-min polling v1; webhooks later | Slice 2.5 |

---

## 12. References

- v2 PRD: `CISOBrief-v2.md` §§4.1, 5, 6.2, 7.2, 13, 370-389
- Existing CFN: `platform/cfn/aws-onboard.yaml`
- Existing Aurora schema: `platform/sql/001_phase0.sql`, `002_phase_a.sql`
- Existing scanner pattern: `platform/lambda/scanner_core/`, `platform/lambda/shasta_runner_entra/`
- Existing API pattern: `platform/lambda/findings_list/`, `platform/lambda/ai_summary/`
- Memories: [[feedback_no_azure_sentinel]], [[feedback_oss_leverage]],
  [[project_ios_companion_vision]], [[project_integrations_mcp]],
  [[project_multitenant_ms_idp]], [[project_ciso_copilot]]

---

## 13. Next steps after this spec

1. KK reviews this spec; revisions inline.
2. Invoke `superpowers:writing-plans` to produce the Slice 1
   implementation plan (TDD-gated, vertical, demo-able).
3. Slice 1 implementation in its own branch
   `feat/ai-powered-soc-slice-1`; PR; merge.
4. Repeat for 1c, 2, 3, 4.
