# Architecture

> The load-bearing engineering decisions behind Shasta by Transilience.
> Written for the engineer who joins the team next week and needs to
> understand why the codebase looks the way it does.
>
> For *what is shipped*, see [HANDOFF.md](HANDOFF.md). For *what's
> coming*, see [ROADMAP.md](ROADMAP.md). For *open items*, see
> [BACKLOG.md](BACKLOG.md). This doc is for *why we built it this way*.

## Contents

1. [System overview](#system-overview)
2. [The unified findings model](#the-unified-findings-model)
3. [The four cloud connectors](#the-four-cloud-connectors)
4. [The CME-v2 compliance mapping pipeline](#the-cme-v2-compliance-mapping-pipeline)
5. [The SOC pipeline](#the-soc-pipeline)
6. [Identity and auth](#identity-and-auth)
7. [The four surfaces](#the-four-surfaces)
8. [Design decisions (ADRs)](#design-decisions-adrs)
9. [Operational concerns](#operational-concerns)

---

## System overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Customer's Cloud                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐ │
│  │   AWS      │  │   Azure    │  │    GCP     │  │    Entra     │ │
│  │ (CFN role) │  │ (az SP)    │  │ (IAM bind) │  │ (admin cons.)│ │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └──────┬───────┘ │
│        │ STS           │ AAD           │ SA              │ Graph   │
└────────┼───────────────┼───────────────┼─────────────────┼─────────┘
         │               │               │                 │
         ▼               ▼               ▼                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Scanner Lambdas (containerised, ECR)                   │
│   Shasta scanner sub-package + custom checks + AI repo detectors    │
│        (one Lambda per cloud, :latest tag, hot-swappable)           │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Unified Writer (single Aurora path)                │
│   entities + edges + findings + frameworks + provenance + scans     │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                Aurora PostgreSQL (Data API)                         │
│  scans, entities, edges, findings, ai_features,                     │
│  threat_indicators, events, tenants, users, connections, …          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌──────────────┐       ┌──────────────┐       ┌────────────────┐
│  API Gateway │       │ EventBridge  │       │  SOC Pipeline  │
│  (Cognito    │       │  + SQS       │       │  (router →     │
│   authorizer)│       │  (real-time  │       │   classify →   │
│              │       │  cloud drift)│       │   enrich)      │
└──────┬───────┘       └──────┬───────┘       └────────┬───────┘
       │                      │                        │
       ▼                      ▼                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            Surfaces                                  │
│   Web (S3+CF)    iOS (APNs)    Voice (WebRTC)    Chat (LWA stream)  │
└─────────────────────────────────────────────────────────────────────┘
```

Five layers, top to bottom: connector → scanner → writer → store →
surfaces. Every scanner writes into the same unified model. Every
surface reads from the same store. The store is the only point of
authority; everything else is stateless.

The diagram shows the steady-state read/write paths. There are two
secondary paths worth naming:
- The **SOC pipeline** (right column) is fed by customer EventBridge
  rules that forward AWS Config + CloudTrail management events into
  Shasta's central event bus. The router classifies, enriches via LLM,
  and writes back into the same `events` table the surfaces read from.
- The **chat surface** runs through a Lambda Web Adapter (LWA) bridge
  so we can stream LLM responses through managed Python Lambda — see
  [ADR-009](#adr-009-lambda-web-adapter-for-streaming).

---

## The unified findings model

Every signal in the platform — a cloud misconfiguration, an AI workload
discovery, a SOC event, an Entra sign-in risk — is written through the
same data model. The model is deliberately graph-shaped:

- **`entities`** — anything we care about. A cloud resource (S3 bucket,
  IAM user, Bedrock deployment), a person (Entra user), a piece of code
  (a repo, a commit). Primary key is `id` (UUIDv7), `tenant_id`
  partitions every query, `domain` (cloud / identity / code / ai) and
  `kind` (s3_bucket / entra_user / repo / bedrock_deployment / …)
  categorise.
- **`edges`** — relationships between entities. "User X has policy Y."
  "Bedrock deployment Z runs in account A." "Commit C touched file F."
  Same `tenant_id` partition, source + target entity IDs, edge `kind`.
- **`findings`** — assertions about an entity (or pair of entities).
  Carries `check_id` (the rule that fired), `status` (pass / partial /
  fail), `severity` (info / low / medium / high / critical), `frameworks`
  (JSONB list of compliance framework IDs that this finding applies to),
  `evidence_packet` (JSONB blob of context: raw cloud API responses, AI
  narrative, source IPs, anything that explains the finding),
  `mitre_technique` (where applicable), and the standard timestamps.
- **`scans`** — every scanner run is a row. Findings reference their
  emitting scan via `scan_id`. We use `ON CONFLICT DO UPDATE` on
  finding upsert so re-scans don't churn IDs — see the HANDOFF gotcha
  about `scan_id` tracking "most recent touch" rather than discovery.
- **`ai_features`** — for SOC enrichment: a free-form JSONB blob written
  by the enrichment Lambda. Contains the LLM's narrative + anomaly
  score + suggested next-step commands + the matched TI indicators +
  the statistical features that fired.

Three things this model does for us:

1. **Cross-domain queries are trivial.** "Show me all AI-touching
   findings from AWS where the affected entity has an Entra owner who
   logged into ChatGPT this week" is a JOIN, not a federation problem.
2. **Framework tagging is data, not code.** When CME-v2 runs the
   normalize → augment pipeline, it writes the resulting framework IDs
   into the `frameworks` JSONB column. The UI filters on that column
   directly. No special-case logic per framework.
3. **AI is a lens, not a silo.** See [ADR-006](#adr-006-ai-is-a-lens-not-a-silo).
   AI-security findings live in the same `findings` table as AWS
   misconfigurations. The `/ai` view is a *filter* over the same data,
   not a separate pipeline.

---

## The four cloud connectors

Each cloud has its own onboarding flow + scanner image + Lambda. They
all converge into the unified writer.

### AWS

**Onboarding:** Customer launches a CloudFormation stack
(`platform/cfn/aws-onboard.yaml`) which creates a cross-account IAM role
trusted by Shasta's account, an EventBridge rule that forwards
CloudTrail management events + AWS Config configuration items to
Shasta's central bus, and a cross-region trail.

**Scanner:** `platform/lambda/shasta_runner_aws/` — a containerised
Lambda (ECR-hosted, `:latest` tag) that uses the Shasta sub-package's
discovery + check logic across Quick / Medium / Deep tiers. Medium adds
the AI-resource pass (Bedrock, SageMaker, Cognitive Services); Deep
adds deeper-scoped checks at higher cost.

**Onboarding artifact** is plain CloudFormation YAML rather than CDK so
that customers can read it themselves before deploying. Defensive
copy-paste is the norm in security; we don't ask them to run arbitrary
scripts.

### Azure

**Onboarding:** A shell script that creates an AAD service principal
with Reader + Security Reader roles scoped to a subscription (or
management group), then registers the SP credentials with Shasta. No
Sentinel dependency — see [ADR-015](#adr-015-dont-lean-on-azure-sentinel).

**Scanner:** `platform/lambda/shasta_runner_azure/` — pulls from
Activity Log + Resource Graph + Microsoft Cloud Security Benchmark +
Defender (when on). The AI pass (Bedrock equivalents: Azure OpenAI,
Azure ML, Cognitive Services minus OpenAI-kind dups) lives in
`ai_pass.py` as a workaround for a Shasta upstream issue we couldn't
fix in-place. See [ADR-001](#adr-001-shasta-as-a-sub-package-not-a-fork).

### GCP

**Onboarding:** A shell script that grants a Shasta-controlled service
account `roles/iam.securityReviewer` + `roles/cloudasset.viewer` at the
organisation level (or project level for narrow trials).

**Scanner:** `platform/lambda/shasta_runner_gcp/` — pulls from Cloud
Asset Inventory + IAM + Cloud Audit Logs + Security Command Center
(when on). GCP AI coverage is still pending — see ROADMAP §M3.

### Entra (Microsoft 365 identity)

**Onboarding:** Customer admin clicks the Microsoft consent URL with
Shasta's AAD app ID. Scopes requested: `Policy.Read.All`,
`AuditLog.Read.All`, `Directory.Read.All`, `IdentityRiskEvent.Read.All`,
`Reports.Read.All`.

**Scanner:** `platform/lambda/shasta_runner_entra/` — runs Shasta's
existing Entra compliance checks, plus the **AI sign-in pass**
(`ai_signin_pass.py`) that matches user `signInActivity` against a
30-app catalog (`ai_saas_catalog.json`) and emits per-tier findings
(`ai_signin_personal_tier`, `ai_signin_corp_tier`,
`ai_signin_unknown_tier`). Sign-in detail requires Entra ID P1 or P2 —
the Free-tier banner on `/connect` surfaces this constraint when the
Graph 403 fires.

---

## The CME-v2 compliance mapping pipeline

CME-v2 (Compliance Mapping Engine v2) is the binding crosswalk between
scanner-emitted control IDs and canonical published compliance
frameworks. It runs on every finding write.

### Two stages

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  Scanner emits   │    │   normalize      │    │    augment       │
│  check_id +      │ →  │  (rewrite_rules) │ →  │ (canonical adds) │ → findings table
│  framework hints │    │  ~65 rules       │    │  13 canonical    │
└──────────────────┘    └──────────────────┘    └──────────────────┘
```

**Stage 1 — normalize.** Scanner-emitted IDs are rewritten to canonical
published-format IDs via per-framework `rewrite_rules` in
`ai_framework_registry.json`. Example: Shasta's shorthand `GAI-5` (a
NIST AI 600-1 risk) becomes the canonical `NIST.AI.600-1:2.5`.
~65 rewrite rules across 8 frameworks today.

**Stage 2 — augment.** Where a scanner check_id implies additional
canonical IDs that the scanner doesn't emit directly, augment rules
attach them. Example: a Bedrock model-access finding (`baseline_bedrock_*`)
augments to NIST AI RMF MAP 4.1 + ISO 42001 Annex A.7.4 + EU AI Act
Art. 14. 13 canonical augment rules today, all in
`framework_registry.py`.

### Why two stages

The original CME-v1 was a single-pass rewrite. It hit two limits: (a)
no way to *add* a canonical ID that the scanner hadn't emitted (e.g.
the scanner emits SOC2 but the canonical mapping also implies ISO27001
Annex A.X.Y); (b) ambiguous rewrites (Shasta's `GAI-5` mapping to two
NIST 600-1 sections — the rewrite needed to emit two IDs from one
source). The two-stage model splits "translate" from "enrich" cleanly,
each stage has unambiguous semantics, and observability is per-stage
via the `registry_apply_summary` CloudWatch metric.

### Provenance

Every finding records `evidence_packet._registry_rule_ids` listing the
IDs of every rule that fired during normalize + augment. An auditor (or
support engineer) can ask "why is this finding tagged with PCI 8.3?"
and get a real answer. This is forward-compatible with the Findings
History sub-project (spec §17.1) where we'll keep historical snapshots
of which rules applied at scan time, even if the registry has since
changed.

### Disclaimer surfacing

Every framework tile and chip in the UI carries the disclaimer:
> Mapping only — not a compliance attestation. Verify with your
> auditor.

This is non-negotiable. We are a control-coverage tool, not an
attestation tool. The compliance wizard sub-project (M6) is where we'll
extend toward attestation depth.

---

## The SOC pipeline

The SOC sub-project turns AWS Config drift + CloudTrail management
events into AI-enriched, prioritized, push-notified signals.

### Flow

```
Customer's EventBridge rule
        │
        ▼
Shasta central event bus (ciso-copilot-events)
        │
        ▼
event_router Lambda
  ├─ source_event_id dedupe (partial unique index)
  ├─ Config severity rule table (predicate over before/after state)
  ├─ push rule (per-tenant rate limit, criticals bypass)
  └─ SQS enqueue
        │
        ▼
soc-enrichment-queue (SQS)
        │
        ▼
soc_enrichment Lambda
  ├─ statistical features (rolling-window per-actor, per-resource)
  ├─ threat-intel matches (DB lookup + GreyNoise on-demand)
  ├─ LiteLLM → claude-sonnet-4-6 (narrative + score + next-steps)
  ├─ per-tenant daily spend cap (DynamoDB)
  └─ UPDATE events row with ai_*  columns + ai_features JSONB
        │
        ▼
/soc web page + APNs push
```

### Key design choices

- **EventBridge → SQS → Lambda** (not direct EventBridge → Lambda)
  because the enrichment step has bounded concurrency (we don't want
  to fire 1,000 simultaneous LLM calls) and we want backpressure when
  Anthropic rate-limits us.
- **Severity rule table over hard-coded rules.** The rules table
  (`severity_rules.py`) has predicates evaluated against
  `before_state` / `after_state` from CloudTrail or Config. New rules
  are data, not code.
- **`source_event_id` partial unique index** for dedupe. CloudTrail and
  EventBridge both retry events; without dedupe we'd double-enrich.
- **Per-tenant daily LLM spend cap** in DynamoDB
  (`soc_llm_spend_daily`). $10/day default; read-then-add with bounded
  race window. Bigger caps require explicit per-tenant override.
- **Per-tenant push rate limit** (10/hr default); critical-severity
  events bypass. The point is to stop noisy-tenants from DDoSing their
  own iPhones.

### The threat-intel substrate (Slice 1c)

5,726 IOCs across 4 cached feeds + GreyNoise on-demand:

| Source | Updates | Volume | Kinds |
|--------|---------|--------|-------|
| AbuseCH Feodo | Hourly | 5 | IPs |
| AbuseCH ThreatFox | Hourly | 2,842 | IPs (dedup at PK level) |
| CISA KEV | Daily | 1,602 | CVE IDs |
| Tor exit nodes | Hourly | 1,277 | IPs |
| GreyNoise Community | On-demand | — | IP context (per-tenant cap 30/day) |

Indicators land in a global `threat_indicators` table (not per-tenant —
the IOCs are public) with composite PK `(value, kind, source)`.
Enrichment does an IP/domain/sha256 lookup on every event's
`source_ip`, the LLM names matched sources/tags in the narrative, and
the `/soc` detail pane renders "Threat intel" badges from
`ai_features.ti_matches`.

GreyNoise on-demand is the fallback for IPs that don't match the
cached feeds. Disabled until a key lands in
`ciso-copilot/greynoise-api-key` Secrets Manager — see HANDOFF for the
provisioning gate.

---

## Identity and auth

### Cognito federation

Shasta uses Amazon Cognito as the user pool. Customers sign in via:
- **Google** (single Google IdP for all Google users)
- **Microsoft** (per-tenant Cognito OIDC IdP — see
  [`project_multitenant_ms_idp`](docs/superpowers/specs/) — set up lazily
  by `/auth/discover-tenant` when a new Entra tenant first signs in)

### The subject-extraction gotcha

This bit us hard once and now lives in the canonical
`voice_session._subject_from_claims` helper. The rule:

```python
def _subject_from_claims(claims: dict) -> str:
    # Always prefer identities[0].userId for federated logins;
    # fall back to sub only for non-federated.
    identities = json.loads(claims.get("identities", "[]"))
    if identities:
        return identities[0]["userId"]
    return claims["sub"]
```

For federated logins (Microsoft / Google), the Cognito user pool's
`sub` is the *Cognito-pool sub*, not the upstream IdP's sub. Our
`users.sso_subject` stores the upstream value (the value the customer's
IdP knows the user as). Any handler that JOINs
`users.sso_subject = <subject>` must extract the subject via
`_subject_from_claims`, or it will silently 401 every federated user.

Pattern repeats in `events_list._resolve_tenant_id`,
`voice_session._subject_from_claims`, and now standardised.

### The EventBridge `source` filter gotcha

This is the second gotcha-as-pattern. EventBridge rules for "AWS API
Call via CloudTrail" events must **NOT** filter on `source`. Real
management API events arrive with `source: aws.<service>` (aws.ec2,
aws.iam, aws.s3, etc.) — *never* `aws.cloudtrail`. The original
`aws-onboard.yaml` had `source: [aws.cloudtrail, …]` which silently
dropped every customer's management event.

The fix: filter on `detail-type` + `detail.eventName` only. Same gotcha
lives in the router's `_normalize` / `_classify_kind` /
`_source_event_id` / `_extract_states` — they all key on `detail-type`,
not `source`, for this exact reason.

---

## The four surfaces

### Web

`web/` — Vite + React 18 + TypeScript + Tailwind. Built and synced to
S3, served via CloudFront. Routes include `/connect` (cloud onboarding),
`/findings` (the analyst console with framework + category + cloud
filter chips), `/ai` (the AI Visibility view with family-grouped tiles),
`/soc` (the SOC event timeline + detail pane), `/compliance` (the
framework rollup), `/settings`.

Authentication is Cognito OAuth (Implicit + PKCE) via the `cognito.ts`
helper. API calls go through `api.ts` which automatically attaches the
ID token. Every API route is Cognito-authorised at the gateway.

### iOS

`ios/CISOCopilot/` — SwiftUI app, iOS 17+, WebRTC via Swift Package
Manager, Cognito OAuth via `ASWebAuthSession`. iOS is positioned as a
companion app — alerting + handoff + voice — not a feature-parity port
of the web. See [`project_ios_companion_vision`](docs/superpowers/specs/)
for the rationale.

APNs push delivers SOC enrichments and the daily brief. Voice runs
through the same WebRTC pipeline as the web app — see below.

### Voice (WebRTC)

Voice on iOS uses **WebRTC**, not WebSocket. WebRTC carries the audio
through the platform's native audio stack, which is what enables
hardware-accelerated AEC (acoustic echo cancellation) on the iOS
speakerphone. A WebSocket-based pipeline that bounces 16kHz PCM
through a Python Lambda would loop the speakerphone audio back into
itself within seconds.

Reference implementation:
`~/Projects/shasta-ios-poc/ios/ShastaPOC/Voice/RealtimeClient.swift`.
The model layer is OpenAI Realtime + Gemini Realtime; the LiteLLM
abstraction means we can swap providers per-call.

### Chat

The chat surface uses **Lambda Web Adapter (LWA)** to stream LLM
responses through managed Python Lambda. Managed Python Lambda can't
do native `RESPONSE_STREAM`; LWA wraps the Lambda runtime so a
FastAPI-style streaming handler bridges to Lambda's response stream.

Without LWA we'd have to (a) move to containerised Lambda (lose hot
start), or (b) move to Fargate (lose the Lambda-shaped operational
model), or (c) give up streaming (lose the UX). LWA is the
right-shaped abstraction for our scale.

---

## Design decisions (ADRs)

15 load-bearing decisions. Each is a discrete choice with discrete
consequences; we put them on record so the next engineer doesn't have
to discover them the hard way.

### ADR-001: Shasta as a sub-package, not a fork

**Context.** The Shasta open-source cloud + AI scanner already exists
at `~/Projects/Shasta` and ships as a Python package. We need its
discovery logic, compliance check definitions, and AI-control
enrichment inside our scanner Lambdas. Two options: fork it into this
repo, or consume it as a sub-package.

**Decision.** Consume Shasta as a Python sub-package, installed into
each scanner image via `pip install --no-deps`. The `--no-deps` skips
the xhtml2pdf → pycairo native build chain that wouldn't survive
Lambda. Shasta lives at `~/Projects/Shasta` as a read-only reference.
Never edit Shasta — if a Shasta function is wrong, work around it in
this repo (see `shasta_runner_azure/ai_pass.py` for the canonical
example).

**Consequences.** (a) Shasta upgrades land as a dependency bump, not
merge conflicts. (b) The OSS Shasta scanner becomes the same engine
our hosted product runs — unified product strategy. (c) A Shasta bug
forces a workaround instead of an upstream fix. Hit this once; handled
it cleanly. (d) Don't try to rewrite Shasta checks to TypeScript or
fold them into this repo — the abstraction is the value.

### ADR-002: Single AWS account, multi-tenant

**Context.** SaaS platforms have two basic isolation models:
per-tenant AWS account (clean blast-radius separation, expensive to
operate), or single-AWS-account multi-tenant (single deploy,
`tenant_id` partitioning everywhere).

**Decision.** Single AWS account. Tenant isolation lives in the data
model (`tenant_id` column on every table, enforced at query time) and
in IAM (every Lambda runs as a single role; data-API queries are
tenant-scoped).

**Consequences.** (a) Operations scale: one CDK deploy, one log stream
per Lambda, one Aurora cluster. (b) Cost attribution is harder — see
the billing module roadmap item. (c) A bug that skips the `tenant_id`
filter is a multi-tenant disclosure. We mitigate via repository-layer
helpers that take a `tenant_id` argument explicitly; no raw SQL in
service/API code. (d) If a customer ever requires single-tenant
isolation for compliance reasons, that's a separate CDK app, not a
refactor.

### ADR-003: Cognito federation + the subject-extraction pattern

**Context.** We need Google + Microsoft sign-in. Cognito supports
federation through IdP plugins. For federated logins, the Cognito
user pool's `sub` claim is the *Cognito-pool sub*, not the upstream
IdP's sub.

**Decision.** Every handler that needs the upstream identity
(`users.sso_subject`) extracts the subject via
`_subject_from_claims(claims)`: prefer `identities[0].userId` (the
upstream IdP sub), fall back to `claims["sub"]` only for
non-federated logins.

**Consequences.** (a) Federated users get linked to the right
`users` row. (b) Reaching for `claims.get("sub")` directly silently
401s every federated user. We hit this twice before it became a
pattern. (c) New handlers that touch identity must use the canonical
helper; code review catches this.

### ADR-004: EventBridge management-event filter pattern

**Context.** EventBridge rules for "AWS API Call via CloudTrail" events
need to filter by service and action. The natural-looking filter is
`source: aws.cloudtrail`.

**Decision.** Filter on `detail-type: "AWS API Call via CloudTrail"`
+ `detail.eventName: [list]` only. Do **NOT** filter on `source`.

**Consequences.** Real management API events arrive with
`source: aws.<service>` (aws.ec2, aws.iam, aws.s3, etc.) — never
`aws.cloudtrail`. The original `aws-onboard.yaml` had
`source: [aws.cloudtrail, …]` which silently dropped every customer's
management event. The router's `_normalize`, `_classify_kind`,
`_source_event_id`, `_extract_states` all key on `detail-type`, never
`source`. Any new EventBridge rule that touches CloudTrail must follow
this pattern.

### ADR-005: Two-stage CME pipeline (normalize → augment)

**Context.** Compliance framework mapping had two failure modes in v1:
(a) ambiguous rewrites where one source ID maps to multiple canonical
IDs (Shasta's `GAI-5` maps to two NIST AI 600-1 sections); (b) the
need to *add* a canonical ID that the scanner doesn't emit directly
(e.g. scanner emits `soc2_5.1`, augment adds `iso27001_A.5.1`).

**Decision.** Two stages. `normalize` rewrites scanner IDs to
canonical IDs via per-framework `rewrite_rules`. `augment` attaches
additional canonical IDs that the scanner doesn't emit, via
`augment_rules`. Each stage has clean semantics.

**Consequences.** (a) Unambiguous translate vs enrich separation.
(b) Per-stage observability: `registry_apply_summary`,
`normalize_rewrote_count`, `normalize_passthrough_count` CloudWatch
metrics. (c) Per-finding provenance via
`evidence_packet._registry_rule_ids` listing every rule that fired.
(d) Forward-compatible with Findings History (snapshot which rules
applied at scan time, even if the registry has since changed).

### ADR-006: AI is a lens, not a silo

**Context.** AI-security is hot. The natural temptation is to build a
parallel pipeline: AI findings table, AI scanners, AI-specific UI,
AI-specific compliance crosswalk.

**Decision.** No parallel AI pipeline. AI-security findings live in
the same `findings` table as cloud misconfigurations. The `/ai` view
is a *filter* over the `findings` table where
`is_ai_touching = true`. AI scanners write through the same unified
writer. AI frameworks (NIST AI RMF, ISO 42001, EU AI Act) live in the
same `ai_framework_registry.json` as SOC 2 and CIS.

**Consequences.** (a) Cross-domain queries (e.g. "AI-touching findings
on AWS owned by an Entra user who used ChatGPT") are JOINs, not
federation. (b) When a customer asks "is my AI deployment
SOC 2 compliant?", we already have the cross-framework mapping. (c)
Every check_id can carry every applicable framework, including AI ones,
without a special-case code path. (d) Future arenas (DSPM, privacy,
safety) inherit the same architecture: they're lenses, not silos.

### ADR-007: WebRTC for voice, not WebSocket

**Context.** Voice on iOS needs sub-200ms round-trip latency and
clean speakerphone audio (no feedback loop, no double-talk).

**Decision.** WebRTC end-to-end. The audio path stays inside the
platform's native audio stack from microphone → AEC → encode → network
→ decode → speakers. We do not bounce PCM through a Python Lambda.

**Consequences.** (a) Hardware-accelerated AEC on iOS prevents the
speakerphone echo loop. (b) Network is direct: client → realtime
model → client, with our Lambda only handing out ephemeral tokens.
(c) We lose the ability to "log all audio" centrally, by design.
Voice transcripts (not audio) are persisted post-call. (d) Reference
implementation:
`~/Projects/shasta-ios-poc/ios/ShastaPOC/Voice/RealtimeClient.swift`.

### ADR-008: LiteLLM abstraction for model swap

**Context.** We use Anthropic Claude as the primary model. We
occasionally use OpenAI realtime + Gemini realtime for voice. We
expect to swap providers per-call (cheaper model for low-stakes
enrichment, frontier model for the SOC narrative).

**Decision.** All LLM calls route through LiteLLM. Default model is
`claude-sonnet-4-6`. Per-call overrides via env var
(`SOC_ENRICHMENT_LLM_MODEL`) or function parameter.
`litellm.drop_params = True` at module load to handle provider quirks
(Anthropic rejects OpenAI's `response_format`).

**Consequences.** (a) Provider swap is a config change. (b) We can
A/B test models on the same prompt without code changes. (c) Provider
quirks (markdown-fenced JSON from Claude, parameter rejection from
Anthropic, etc.) get handled at the LiteLLM boundary, not scattered
across handlers. (d) Cost attribution gets the model name as a tag,
useful for the billing module.

### ADR-009: Lambda Web Adapter for streaming

**Context.** The chat surface needs to stream LLM responses to the
browser. Managed Python Lambda can't do native `RESPONSE_STREAM` (only
container Lambda or Lambda function URLs with the right invocation
mode).

**Decision.** Use Lambda Web Adapter (LWA) to wrap the Python Lambda
runtime. LWA bridges between Lambda's response stream and a
FastAPI-style streaming handler. The handler emits SSE chunks; LWA
forwards them to API Gateway, which streams to the browser.

**Consequences.** (a) Keep managed Python Lambda (fast cold starts,
no container build). (b) Pay the LWA wrapper cost (~10ms cold-start
overhead, negligible warm). (c) Don't try to do response streaming
from container Lambda — different shape, different ops model. (d)
This abstraction is load-bearing for any future streaming endpoint
(streaming SOC narrative, streaming compliance audit, …).

### ADR-010: AWS Config essentials profile, not all-resources

**Context.** AWS Config is the source of cloud-drift events for the
SOC pipeline. Configuration is per-customer: which resource types
get recorded.

**Decision.** Default onboarding profile is "Config essentials" — a
curated list of ~30 resource types (EC2, IAM, S3, RDS, VPC, ELB,
Lambda, KMS, …) that cover 95% of meaningful drift. All-resources
recording is opt-in.

**Consequences.** (a) Customer cost is ~$30-80/mo for essentials vs
$200+ for all-resources. (b) We can scale to early-stage customers
without scaring them on the AWS bill line. (c) Customers with deeper
needs can opt in to all-resources on a per-stack basis. (d)
Application-level resources (CloudFront, CloudFormation, …) not
covered in essentials may surface drift via CloudTrail management
events instead.

### ADR-011: Per-tenant rate limit + spend cap from day 1

**Context.** Multi-tenant SaaS has two anti-noisy-neighbour patterns:
rate limits (concurrent ops per tenant) and spend caps (cost per
tenant per period).

**Decision.** Every LLM-touching pipeline has both. SOC enrichment
has a $10/day default spend cap (DynamoDB `soc_llm_spend_daily`) and
10/hr push rate limit (criticals bypass). Future LLM calls (chat,
voice, daily brief) inherit the pattern.

**Consequences.** (a) A runaway tenant can't blow up the AWS bill.
(b) A noisy tenant can't DDoS their own iPhone. (c) Cap-near-exhaustion
race window is bounded (~5-10× per-call cost — acceptable). (d) Need
customer-facing visibility into their own spend cap status (billing
module).

### ADR-012: ECR-stored scanner images with `:latest` tag

**Context.** Scanner Lambdas are containerised (Shasta sub-package +
custom checks + AI repo detectors). We need a fast hot-swap deploy
path for scanner-only changes.

**Decision.** Each scanner image lives in ECR with a `:latest` tag.
Build script (`platform/lambda/shasta_runner_<cloud>/build.sh`) builds
the image, tags as `:latest`, pushes. To deploy a scanner change:
push new image, then `aws lambda update-function-code` to re-resolve
the tag. No CDK deploy needed.

**Consequences.** (a) Scanner code changes deploy in ~60s, not 5-10min.
(b) Same `:latest` tag across environments — risky if we ever fork
production from staging. (c) Cleanup of old tags is manual today (will
need an ECR lifecycle policy). (d) Don't use `:latest` for application
Lambdas — those go through CDK with content-hash addresses.

### ADR-013: Wrap OSS, don't reinvent

**Context.** Multiple components of Shasta could be built from scratch
or wrapped from existing OSS. We're shipping with the Shasta cloud +
AI scanner sub-package today. The threat-intel substrate and
vulnerability scanning roadmap (M1) will pull in Trivy / Syft for SBOM,
NVD + GHSA + OSV.dev for CVE feeds, CISA KEV + EPSS for prioritisation.
Future code-security work (Whitney Semgrep prompt-injection rules,
gitleaks for secrets, deeper SAST) will follow the same wrap-don't-build
pattern.

**Decision.** Wrap. Always. The platform layer (multi-tenant unified
findings, compliance crosswalk, surfaces, identity, billing) is where
we add value; the per-finding detection logic is commoditised and we
should consume the best OSS available.

**Consequences.** (a) Faster shipping, less maintenance burden on
detection logic. (b) Upstream OSS bugs are not our bugs — we file
them and work around them where needed (ADR-001 pattern). (c) When
we *do* build custom detection (e.g. the AI SaaS sign-in catalog or
the 9 AI repo detectors in `ai_scanner/`), it's narrow and
well-justified — usually because no upstream tool covers the AI-shaped
problem yet. (d) Don't fork upstream tools — that gives us all the
maintenance burden with none of the upstream support.

### ADR-014: Integrations via MCP, not bespoke API clients

**Context.** Shasta needs outbound integrations: Slack, JIRA, Gmail,
M365, GitHub, Linear, Notion. The traditional approach is per-service
API clients with OAuth flows + token storage + rate-limit retry.

**Decision.** All outbound SaaS integrations use the Model Context
Protocol (MCP). Customers consent once per service; an MCP client
harness handles OAuth + retries + schema discovery. Action proposals
go through an approval gate (drafted, not auto-executed).

**Consequences.** (a) Adding a new integration is wiring MCP, not
writing a client. (b) Customers see the same consent UI per service.
(c) The MCP layer is reusable for the M2 sub-project (auditing the
customer's MCP risk surface). (d) Risk: MCP server quality varies
widely; we'll need a curated set rather than ad-hoc adoption.

### ADR-015: Don't lean on Azure Sentinel

**Context.** Azure offers Sentinel as the canonical SIEM. It would be
"natural" for our SOC to ingest from Sentinel.

**Decision.** No Sentinel dependency. Azure SOC ingestion uses
Activity Log → Diagnostic Settings → Event Hub → Lambda consumer +
Azure Policy state + Resource Graph change feed + Defender (when on).

**Consequences.** (a) Prohibitive customer cost avoided (Sentinel is
$2-10/GB ingested; mid-tier customers see $10K+/mo). (b) We're
narrower in coverage than Sentinel — we get config + audit drift, not
arbitrary logs. (c) Customers on Sentinel can continue to use it
alongside us; we don't lock them in. (d) GCP follows the same pattern
— no Chronicle dependency.

---

### ADR-016: Cross-stack RestApi extension via `fromRestApiAttributes`

**Context.** `CisoCopilotApi` was approaching the CloudFormation 500-resource hard cap (494/500 as of 2026-06-08). AI Security Sub-slice 1.4 alone added ~12-16 resources, which would exceed the cap. Continued route-deletion to free space was unsustainable.

**Decision.** New AI-domain Lambdas land in a separate `CisoCopilotAi` stack that imports the existing `RestApi` + Cognito authorizer via named CFN exports (`Fn.importValue`). One API Gateway, one `/v1` stage, one CORS config, one authorizer. The Workspace OAuth Lambdas (Sub-slice 1.4) and all subsequent AI features go here by default.

**Mitigation of known limitations:**
- API Gateway stages don't auto-redeploy when routes are added from a different stack. Mitigated by an `AwsCustomResource` in `CisoCopilotAi` that calls `apigateway:UpdateStage` on every deploy where the AI `Deployment`'s logicalId changes.
- `CisoCopilotApi`-only redeploys would otherwise overwrite the served deployment and drop AI routes. Mitigated by a `latestDeployment?.addToLogicalId({ aiStackExtensionVersion: 'v1' })` pin in `CisoCopilotApi` that keeps its deployment logicalId stable across non-AI changes.
- CDK v2 doesn't expose a `fromAttributes` factory for `Authorizer`. The cross-stack authorizer is constructed as an inline `IAuthorizer` object literal (just `authorizerId` + `authorizationType`), which adds zero CFN resources and consumes the `CisoCopilotApi-CognitoAuthorizerId` CFN export.

**Rejected alternatives:**
- Separate RestApi at `ai-api.shasta.io` — split CORS, split rate-limit, web/iOS clients need to know which host to call per route.
- CloudFront path-routing to two API Gateways — extra failure mode, doubles monitoring surface.
- Move all existing AI Lambdas now — bigger blast radius for a deploy that was primarily about unblocking Sub-slice 1.4.

**Boundary discipline:** "New work only." Existing AI Lambdas stay in `CisoCopilotApi`. Opportunistic migration is allowed when `CisoCopilotApi` hits the cap again, not before.

**Spec:** `docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md`.

---

## Operational concerns

### Cost attribution

Today we track LLM spend per tenant only for SOC enrichment
(`soc_llm_spend_daily` in DynamoDB). Compute, storage, Aurora, NAT
egress, ECR are aggregate-cost only.

The billing module sub-project (see ROADMAP near-term) will close this
gap: parallel tracking for AI Visibility scanner runs, voice/realtime
tokens, MCP-driven LLM calls, AWS Config bytes ingested, scanner Lambda
compute time, Aurora storage proportional to findings volume.

### Observability

CloudWatch is the only sink today. Per-Lambda log groups, structured
JSON logs from `src/logging.py`, custom metrics via PutMetricData. Key
metrics tracked:

- `registry_apply_summary` (per-scan compliance mapping)
- `normalize_rewrote_count`, `normalize_passthrough_count`
- `soc_enrichment_latency`, `soc_enrichment_cost_usd`
- `scan_duration_seconds`, `scan_findings_count`

We don't have customer-facing error surfaces yet. A 500 today shows
the customer `{"message":"Internal server error"}` and that's it.
Pre-GA we need request-ID surfacing + a customer-facing error page.

### Security boundaries

The API Gateway is the rate-limit and key-protection boundary. The iOS
and web apps **never** call upstream sources directly — they call the
Shasta API, which mediates. This protects the OpenAI/Anthropic/Cognito
API keys and centralises rate limiting.

Customer-data isolation:
- Aurora rows: every table has `tenant_id`; repository-layer helpers
  enforce filtering.
- ECR scanner images: shared across tenants. The images contain code
  only, no data.
- SQS queues: shared. Messages carry `tenant_id` and the consumer
  Lambda passes it through.
- DynamoDB spend cap: keyed by `tenant_id`.
- CloudWatch logs: shared log groups per Lambda. Tenant ID appears in
  every log line for filtering.

Cross-tenant data leak would be a code bug, not an infrastructure
mistake. Audit checklist for new handlers: tenant_id source, tenant_id
flow through the call stack, tenant_id in the SQL WHERE clause.

### Disaster recovery

Aurora is the only stateful tier. Default backup retention is 7 days
(point-in-time recovery). Cross-region replica is on the roadmap (not
shipped). For a region-down event today, we'd restore from snapshot
into a new region, point CDK at the new ARN, and redeploy. RTO
~2-4 hours. Pre-GA we need cross-region replica + a written runbook.

Scanner images in ECR replicate across regions automatically (ECR
replication enabled). CDK code is in git. Cognito user pool is the
nontrivial restore — pool migration would require user re-sign-in.

### Secrets management

All secrets in AWS Secrets Manager. `.env` and `.env.*` are gitignored
and not present in CI. The OAuth app credentials (Google client secret,
Microsoft client secret, APNs auth key) live in Secrets Manager and
are read by Lambdas at cold start. Database credentials use Aurora
Data API + an attached secret ARN — no connection strings flow through
code.

---

*This document is the source of truth for "why we built it this way."
Updates land via PR; significant new ADRs warrant a discussion entry
in `docs/superpowers/specs/` first.*
