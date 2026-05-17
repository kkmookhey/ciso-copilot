# CISO Copilot v2 — Cloud Posture & Real-Time Alerts

**A multi-tenant SaaS that connects to a CISO's clouds and answers, in real time, what's at risk, what changed, and what to do about it.**

| | |
|---|---|
| **Document type** | Product Requirements Document — build-from spec for v2 |
| **Audience** | Claude Code (executor) + engineering team |
| **Version** | 2.0 — Draft for cloud-native enterprise build |
| **Author** | KK Mookhey |
| **Date** | May 16, 2026 |
| **Platform** | iOS (SwiftUI) + AWS-native backend (Lambda, Aurora, EventBridge) |
| **Primary persona** | CISO at a multi-cloud enterprise (5k–50k employees) |
| **Builds on** | Existing iOS app (re-pointed from Cloudflare v1) + Shasta scanner engine |
| **Predecessor** | `CISOBrief.md` v1 (public-feed brief). v1 is sunset when v2 Phase A ships. |

## How to read this document

Sections 1–4 explain the product and what's in/out. Sections 5–14 are the build spec. Sections 15–16 are NFRs and success criteria. Sections 17–18 are scope-out and open questions.

`[BUILD]`-tagged sections are executable detail; everything else is context.

Where this document and the older `CISOBrief.md` (v1) conflict, **v2 wins on architecture, v1 may inform UI patterns we already validated.**

---

## 1. The product in one paragraph

CISO Copilot v2 is a multi-tenant SaaS that an enterprise CISO connects to their AWS, Azure, Entra ID, and GCP environments. Once connected, it (a) runs Shasta's deterministic scanner library against all regions and subscriptions on schedule, (b) receives real-time alert and config-change events from the customer's clouds via cross-account EventBridge / Event Hubs / Pub/Sub, and (c) exposes everything as a mobile-first iOS app plus a voice interface. The CISO can ask, out loud or in the app, "what are my top risks?", "what changed in IAM this week?", "did anyone open a security group to the world today?" — and get scan-grounded answers in seconds.

### 1.1 Why this is worth building

v1 answered: "what's in the threat firehose that might apply to my stack?" v2 answers: "given my actual cloud environment right now, what should I be looking at?" That's the unsolved wedge — every other posture tool is a dashboard built for the SOC analyst. We build for the CISO who has 90 seconds before a meeting and needs the right specific answer, not a Kanban board.

The defensible moat is the combination: (1) Shasta's 221-check coverage that took months to build, (2) the real-time event pipeline that distinguishes us from nightly-scan posture tools, and (3) the iOS + voice surface that makes it usable by a CISO who is not at their desk.

### 1.2 The positioning, stated bluntly

**Not** "a cloud security posture management tool" — there are dozens.
**Yes** "a phone-first cloud security copilot for the CISO who already has a posture tool and never opens it."

The success metric is *not* "how many findings did we surface." It is "how often did the CISO take an action this morning that they would not have taken without us."

---

## 2. Target user

Enterprise CISO. ~5,000–50,000 employees. Hybrid AWS + Azure, often with GCP for data. Reports to CEO, CIO, or COO. Has a security team of 10–100. Buys software via security or engineering budget. Cares about: SOC 2 / ISO 27001 / FedRAMP compliance, board reporting, ransomware exposure, vendor risk, identity hygiene. Lives on iPhone between meetings. Will delete the app after one false-positive critical alert.

### 2.1 The eight questions she needs answered, in priority order

1. What are my top security risks **right now**, ranked by what actually matters to me?
2. What changed in my environment in the last 24h / 7d / period I choose?
3. Given what's in my environment, what threats and vulnerabilities should I care about today?
4. What critical security alerts have fired in my clouds (GuardDuty, Defender, Inspector, Entra Risk)?
5. What's my compliance score across SOC 2 / ISO 27001 / CIS AWS / CIS Azure / MCSB, and how is it trending?
6. If someone exposed something they shouldn't have in the last hour, did we catch it?
7. If the board asks about a headline cloud breach, what's my one-paragraph "are we exposed" answer?
8. What should I tell my Infra / SOC / IAM teams to do today?

The product is judged on how well a five-minute interaction (scroll, or 30 seconds of voice) answers these eight.

---

## 3. Product principles

| Principle | What it means in practice |
|---|---|
| **Their environment is the relevance.** | We don't show generic CVE feeds. Every item references a specific resource (`arn:aws:s3:::...`, `subscription/.../resourceGroups/...`) or specific finding from their cloud. |
| **Real-time is a feature, not a buzzword.** | Alert-style events surface within 60 seconds of detection in the source cloud. Drift events surface within 30 seconds of the CloudTrail / Activity Log write. UI shows the latency honestly. |
| **Determinism first, LLM second.** | Shasta is deterministic — no LLM in detection. LLM (Sonnet) is used only for: prose explanations, board paragraphs, team-question generation, voice tool routing. |
| **Quiet by default.** | Push budget per user (default: 3 critical/day, plus immediate for `act_now`). Above that, the rules are wrong, not the world. |
| **Cloud trust is sacred.** | We hold customer cloud credentials. Per-tenant KMS keys, encryption at rest, no credential ever in logs, audit-log every credential access, support read-only connections by default. |
| **One scan engine, all clouds.** | Shasta's check library is the only source of findings. Customer's view is unified across AWS, Azure, Entra, GCP. |
| **Iterate per cloud, not per feature.** | We ship cloud-by-cloud (AWS → Azure → Entra → GCP). All features (Top Risks, Drift, Alerts, Voice) work on whichever cloud is currently online. |

---

## 4. `[BUILD]` Scope for v2

### 4.1 In scope

- **Auth & onboarding** — AWS Cognito User Pool with Microsoft + Google OIDC federation (corporate accounts only — verify `hd` / tenant), per-user device registration, multi-device sync.
- **Cloud onboarding flows** — one-click CloudFormation for AWS, scripted SP creation for Azure, admin-consent OAuth for Entra, Workload Identity Federation for GCP.
- **Scheduled pull scans** — Shasta runs daily (configurable per-tenant) across all enabled regions / subscriptions / projects. Full Shasta check coverage (~221 checks).
- **Real-time alert ingestion** — GuardDuty, Inspector, Security Hub, Defender for Cloud, Entra Identity Protection events arrive within 60s.
- **Real-time drift ingestion** — CloudTrail write events + AWS Config items, Azure Activity Log, GCP Audit Logs.
- **Push rules engine** — deterministic rules decide which events fire APNs immediately, per-tenant configurable thresholds.
- **iOS app** — re-pointed from Cloudflare, new auth flow, screens listed in §5.
- **Web app** — Vite + React + TypeScript SPA on S3 + CloudFront at `app.settlingforless.com`. Same Cognito user pool as iOS (separate web client), same API endpoints, screens parallel to iOS (Top Risks, Posture, Alerts, Connect Clouds wizard, Resource detail).
- **Voice interface** — OpenAI Realtime, tool calls into our API, hands-free Q&A.
- **Compliance scoring** — Shasta's existing scoring layer across SOC 2, ISO 27001, CIS AWS, CIS Azure, MCSB, HIPAA. Trend over time.
- **Multi-tenant data model** — tenants, users, cloud_connections, scans, findings, assets, events, drift_events, alerts, scores, audit_events. Aurora Serverless v2 PostgreSQL with row-level tenant isolation.
- **Invite-only signup with manual approval** — new tenants land in `pending` until an admin (initially `kkmookhey@gmail.com`) clicks Approve in an email; user then gets a "you're approved" email and is unlocked. See §10.0.

### 4.2 Out of scope for v2

- **SAML / SCIM** — Cognito OIDC federation only. Migrate to WorkOS when an enterprise customer demands SAML or directory sync.
- **Slack / Teams / email notifications** — APNs only in v2. Phase F.
- **Remediation automation** — read-only platform. Remediation guidance is rendered as text only; we never modify customer clouds.
- **Custom checks / rule editor** — Shasta's check set is the surface. Custom rules in v3.
- **Trust center / customer-facing security pages** — separate product surface, not part of v2.
- **Compliance report PDF generation** — Shasta has it; add via web in v2.5 if customers ask.
- **API for third-party integrations** — internal use only in v2.
- **Multi-region backend** — us-east-1 only. EU residency in v2.5 if a customer requires it.
- **AWS Organizations org-wide onboarding** — per-account in v2. Org-wide enrollment in v2.5.
- **iPad / web** — iPhone only.
- **Self-serve billing** — beta is invite-only with manual contracts. Phase F.

### 4.3 The Phase A demo contract

By end of Phase A (AWS only, real-time pipeline live), the following must work end-to-end:

1. New customer signs up with Microsoft Entra corporate account; account is auto-provisioned a tenant.
2. Customer launches the AWS onboarding CFN from inside the iOS app; CFN creates the cross-account role + EventBridge forwarder + AWS Config recorder.
3. Within 10 minutes of connection, **Top Risks** screen shows the first 10 ranked findings from the initial scan.
4. Within 60 seconds of a `AuthorizeSecurityGroupIngress` allowing `0.0.0.0/0` on port 22 in the customer's AWS account, the customer's iPhone vibrates with an APNs push naming the security group.
5. Customer says "what's my biggest risk right now?" to the voice interface; receives a 2-sentence answer referencing a specific finding and a specific resource ARN.
6. Compliance score is visible for SOC 2 + CIS AWS, with trend line covering the days since onboarding.

If any of those six don't work on the demo day, Phase A is not done.

---

## 5. `[BUILD]` Screens & information architecture

Tab bar: **Brief** · **Alerts** · **Posture** · **Profile**. Voice is a floating button on every screen.

| Screen | Purpose & contents |
|---|---|
| **Brief** (default home) | Three sections, in order: **Top Risks** (top 10 across clouds, severity-sorted), **Posture Diff** (default 24h, toggle to 7d/30d/custom — shows new findings, resolved findings, new resources, IAM/SG/policy widening), **Threat Exposure** (CVEs and threat-actor activity matched to actual assets). Pull-to-refresh. Voice button bottom-right. |
| **Alerts** | Live inbox of real-time events. Filter chips: cloud (AWS/Azure/Entra/GCP), severity, source (GuardDuty / Inspector / Defender / Entra Risk / CloudTrail drift / Config change). Each row: timestamp, source, severity badge, one-line summary, resource ARN/ID. Tap → detail. Critical events show at top, color-coded red. |
| **Posture** | Compliance scoring. Top: composite score across all connected clouds. Middle: per-framework scores — **all frameworks Shasta supports are visible by default** (SOC 2, ISO 27001, HIPAA, CIS AWS, CIS Azure, CIS M365, MCSB, AWS Foundational Security, ISO 42001, EU AI Act, NIST AI RMF) — with trend lines. Bottom: findings grouped by domain (IAM, Network, Data, Identity, AI, ...) and by framework section. |
| **Profile** | **Connected clouds** (list with last-scan timestamp + status), **Add a cloud** (launches per-cloud wizard), **Notifications** (push budget, severity threshold, quiet hours), **Voice** (enable/disable, language), **Account** (SSO identity, sign out). |

### 5.1 Detail screens

- **Finding detail** — header (severity + status), description, affected resource (clickable to Resource view), framework mappings (chips), remediation prose (LLM-generated), "Ask my team" questions, "Board paragraph", related findings on same resource.
- **Drift event detail** — what changed, before vs after JSON diff, actor principal (IAM user / service account / OAuth app), timestamp, related findings on the changed resource, "Was this expected?" thumbs.
- **Alert detail** — full event payload prettified, severity, source-specific links (GuardDuty finding URL, etc.), related findings, related drift events on the resource.
- **Resource detail** — for any ARN/ID: type, region, properties, all findings (open + resolved), all drift events touching it in the last 30d, related real-time alerts.
- **Connected cloud detail** — connection status, regions/subs/projects in scope, last scan, scheduled-next, signals connected (pull-scan ✓ / real-time alerts ✓ / config drift ✓), disconnect button, re-scan now button.

### 5.2 Severity classification (unchanged from v1 spirit, refined)

| Label | Color | Criteria |
|---|---|---|
| **Act Now** | Red | Real-time event with critical+impact (root login, public S3 with regulated tag, GuardDuty `Backdoor:EC2` etc.), OR finding marked critical in Shasta with KEV-class active exploitation |
| **Check Today** | Orange | Finding marked high in Shasta on internet-facing or identity surface, OR Defender/GuardDuty high-severity alert |
| **Watch** | Yellow | Finding marked medium, OR drift event on sensitive resource type that's not yet critical |
| **FYI** | Grey | Low-severity finding or informational events |

### 5.3 Navigation

- Tab bar: Brief (default) · Alerts · Posture · Profile.
- Voice button: floating circle, bottom-right, every screen.
- Onboarding (first launch only): SSO sign-in → "Connect your first cloud" → AWS CFN flow → return to Brief.
- Subsequent cloud additions: Profile → Add a cloud.

---

## 6. `[BUILD]` Data sources

### 6.1 Customer cloud APIs (pull scanner — Shasta)

| Cloud | Library | Auth |
|---|---|---|
| AWS | `boto3` (Shasta `aws/*` modules) | STS AssumeRole to customer's `CISOCopilot-Reader` role using external ID; ReadOnlyAccess + SecurityAudit + ViewOnlyAccess managed policies |
| Azure | `azure-mgmt-*` (Shasta `azure/*` modules) | Service Principal with Reader + Security Reader on subscriptions; secret stored in Secrets Manager per-tenant |
| Entra ID | `msgraph-sdk` (Shasta `azure/entra.py`, `azure/iam.py`) | OAuth 2.0 admin consent flow; refresh token + client credentials; scopes: `Policy.Read.All`, `Directory.Read.All`, `IdentityProtection.Read.All` |
| GCP | `google-api-python-client` (Shasta `gcp/*` modules) | Workload Identity Federation; STS exchange to service account with Security Reviewer role |

### 6.2 Real-time event streams (push pipeline)

| Cloud | Source | Transport into our backend |
|---|---|---|
| AWS | GuardDuty findings, Inspector findings, Security Hub aggregated, CloudTrail management write events, AWS Config item changes | Customer EventBridge rule → cross-account `PutEvents` into our central `ciso-copilot-events` event bus |
| Azure | Activity Log, Defender for Cloud alerts, Microsoft Sentinel incidents (if connected) | Diagnostic settings → Event Hub in our subscription → Lambda consumer |
| Entra | Sign-in logs, Audit logs, Identity Protection risk events | Diagnostic settings → Event Hub; Identity Protection via Graph subscriptions where push-eligible |
| GCP | Cloud Audit Logs (Admin Activity + Data Access subset), Security Command Center findings | Log sink → Pub/Sub topic → our subscription |

### 6.3 What we do NOT pull

- CloudTrail **data events** (S3 object-level, Lambda invoke) — too high volume and cost in v2. Management events only.
- VPC Flow Logs — out of scope; volume too high.
- Customer application logs — never.

---

## 7. `[BUILD]` Architecture

Single-cloud on AWS, region `us-east-1`. The iOS app is the only client; everything else is server-side.

### 7.1 High-level

```
                                    Customer clouds
                            ┌─────────────────────────────┐
                            │  AWS · Azure · Entra · GCP   │
                            └──────────┬───────────┬───────┘
                                       │           │
                              pull     │           │   push (real-time)
                           (AssumeRole │           │  (EventBridge,
                            SP, WIF,   │           │   Event Hubs,
                            OAuth)     ▼           ▼   Pub/Sub)
                            ┌──────────────────────────────┐
                            │     Our AWS  (us-east-1)     │
                            ├──────────────────────────────┤
iOS  ─CloudFront─ API GW ───►  Lambda (Python)             │
                            │   • API handlers              │
                            │   • Onboarding orchestrator   │
                            │   • Voice session minter      │
                            │                              │
                            │  Step Functions  ──► Lambda  │
                            │   "scan workflow"     (Shasta)│
                            │      └─► ECS Fargate          │
                            │         (long scans >15 min)  │
                            │                              │
                            │  Central EventBridge bus     │
                            │      └─► Kinesis Firehose ──►│
                            │             • S3 raw         │
                            │             • Lambda router  │
                            │                              │
                            │  ┌─────────────┐  ┌────────┐ │
                            │  │  Aurora     │  │   S3   │ │
                            │  │  Serverless │  │raw blobs│ │
                            │  │  v2 Postgres│  └────────┘ │
                            │  │  + per-tenant KMS         │
                            │  │  + row-level security     │
                            │  └─────────────┘             │
                            │                              │
                            │  SNS Mobile Push ──► APNs    │
                            │                              │
                            │  Cognito User Pool           │
                            │   + Microsoft & Google OIDC  │
                            │                              │
                            │  Secrets Manager             │
                            │   (customer cloud creds,     │
                            │    per-tenant KMS-encrypted) │
                            │                              │
                            │  EventBridge cron schedules  │
                            │   (daily scans, alert polls) │
                            └──────────────────────────────┘
                                       │
                            voice transport (WebRTC)
                                       │
                            ┌──────────▼──────────┐
                            │   OpenAI Realtime   │
                            │   (model + voice;   │
                            │    tools call back  │
                            │    to our API)      │
                            └─────────────────────┘
```

### 7.2 Component responsibilities

- **API Gateway + Lambda (Python 3.12)** — single API for the iOS app: auth, scan triggers, finding/event reads, voice session minting. All handlers thin; business logic in a shared `app/` package.
- **Cognito User Pool** — federates Microsoft (Entra) and Google (Workspace) via OIDC. Hosted UI for sign-in. On first sign-in, Lambda post-confirmation trigger auto-provisions a tenant if the user's email domain hasn't been seen.
- **Aurora Serverless v2 PostgreSQL** — primary store. Auto-pauses to 0.5 ACU when idle. Schema in §8. Row-level security via `tenant_id` set per-connection from Lambda's JWT-validated identity.
- **Step Functions + Lambda** — scan orchestrator. Per-region or per-subscription fan-out, each Lambda runs Shasta's `client.for_region(r)` / `for_subscription(s)` pattern. Each Lambda writes findings to Aurora directly.
- **ECS Fargate (Shasta image)** — fallback for full-account scans that exceed Lambda's 15-min cap (rare; required for accounts >5k resources in a single region).
- **Central EventBridge bus (`ciso-copilot-events`)** — receives cross-account events from all customer AWS accounts. Each customer's CFN creates a rule in their EventBridge that targets our bus. Resource-based policy allows `PutEvents` from any account in our trusted-tenant set.
- **Kinesis Firehose** — buffers event traffic. Two delivery streams: one to S3 (raw archive), one to a router Lambda.
- **Router Lambda** — normalizes by source (GuardDuty / Inspector / Security Hub / CloudTrail / Config), writes to `events` (and `drift_events` when applicable) in Aurora, evaluates push rules (§13), enqueues SNS Mobile Push if matched.
- **SNS Mobile Push** — replaces our v1 hand-rolled JWT signing. Apple platform application configured once with the `.p8` key.
- **Secrets Manager + per-tenant KMS keys** — customer cloud creds. Each tenant gets a CMK; secret encrypted with envelope encryption using the tenant CMK. Lambda role allows `kms:Decrypt` only for the tenant context derived from the request's JWT.
- **OpenAI Realtime** — voice transport. iOS opens WebRTC directly to OpenAI using an ephemeral session token minted by our Lambda; the session is configured with our tool definitions (§12) that call back into our API as the user speaks.
- **CloudFront** — fronts API Gateway for global edge caching of public assets (CFN templates, marketing pages, app icons). Not used for authed API calls.

### 7.3 Why these choices

- **Aurora over DynamoDB** — findings × frameworks × time-series scans want joins. Shasta's local SQLite has the same shape; Aurora is the natural cloud equivalent. Per-tenant RLS in Postgres is a well-trodden pattern.
- **Step Functions + Lambda fan-out** — Shasta's `for_region` / `for_subscription` pattern is already a fan-out. Step Functions makes that visible, retryable, monitorable.
- **Fargate as fallback only** — Lambda is cheaper at low scale; Fargate adds image-build complexity. Only invoke when Lambda's 15-min cap is the bottleneck.
- **EventBridge over webhook** — cross-account `PutEvents` is more secure than exposing a public webhook with shared secrets. AWS handles the auth.
- **SNS Mobile Push over hand-rolled JWT** — the v1 Worker code path was educational but for a SaaS, the managed thing is correct.
- **Cognito + OIDC** — cheap, AWS-native, federates Microsoft and Google out of the box. Trade-off: SAML/SCIM is rough; we'll migrate to WorkOS in v2.5 when a paying customer demands it.

---

## 8. `[BUILD]` Data model

Aurora PostgreSQL. All tables include `tenant_id UUID NOT NULL` with RLS policy `tenant_id = current_setting('app.tenant_id')::uuid`. Indexes elided for brevity but `tenant_id` is always part of the primary lookup index.

```sql
-- Tenancy & identity
CREATE TABLE tenants (
  tenant_id     UUID PRIMARY KEY,
  display_name  TEXT NOT NULL,
  email_domain  TEXT NOT NULL UNIQUE,        -- auto-provisioned from SSO
  plan          TEXT NOT NULL DEFAULT 'beta',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  user_id       UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  email         TEXT NOT NULL,
  sso_provider  TEXT NOT NULL,               -- 'microsoft' | 'google'
  sso_subject   TEXT NOT NULL,               -- 'sub' claim from IdP
  role          TEXT NOT NULL DEFAULT 'member',  -- 'admin' | 'member'
  device_token  TEXT,                        -- APNs token, multi-device support via separate table later
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (sso_provider, sso_subject)
);

-- Cloud connections
CREATE TABLE cloud_connections (
  conn_id       UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  cloud_type    TEXT NOT NULL,               -- 'aws' | 'azure' | 'entra' | 'gcp'
  display_name  TEXT NOT NULL,
  status        TEXT NOT NULL,               -- 'pending' | 'active' | 'error'
  signals       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {pull_scan:true, alerts:true, drift:true}
  credentials_secret_arn  TEXT NOT NULL,     -- ref into Secrets Manager
  scope         JSONB NOT NULL,              -- {regions:[...], subscriptions:[...], projects:[...]}
  last_scan_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Scans
CREATE TABLE scans (
  scan_id       UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id       UUID NOT NULL REFERENCES cloud_connections(conn_id),
  trigger       TEXT NOT NULL,               -- 'scheduled' | 'manual' | 'onboarding'
  status        TEXT NOT NULL,               -- 'running' | 'completed' | 'failed'
  scope         JSONB NOT NULL,              -- regions/subs scanned this run
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  error         TEXT
);

-- Findings (Shasta output)
CREATE TABLE findings (
  finding_id    UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id       UUID NOT NULL REFERENCES cloud_connections(conn_id),
  scan_id       UUID NOT NULL REFERENCES scans(scan_id),
  check_id      TEXT NOT NULL,               -- Shasta's check identifier
  title         TEXT NOT NULL,
  description   TEXT NOT NULL,
  severity      TEXT NOT NULL,               -- 'critical' | 'high' | 'medium' | 'low' | 'info'
  status        TEXT NOT NULL,               -- 'fail' | 'pass' | 'not_assessed' | 'not_applicable'
  resource_arn  TEXT,                        -- nullable for tenant-level findings
  resource_type TEXT,
  region        TEXT,
  domain        TEXT NOT NULL,               -- Shasta CheckDomain
  frameworks    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {soc2:[...], cis_aws:[...], ...}
  remediation   TEXT,
  first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at   TIMESTAMPTZ
);

-- Asset inventory (snapshot per scan, deduped across scans)
CREATE TABLE assets (
  asset_id      UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id       UUID NOT NULL REFERENCES cloud_connections(conn_id),
  identifier    TEXT NOT NULL,               -- ARN or cloud-native ID
  type          TEXT NOT NULL,               -- 'aws_ec2', 'azure_storage_account', ...
  region        TEXT,
  properties    JSONB NOT NULL DEFAULT '{}'::jsonb,
  first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, conn_id, identifier)
);

-- Real-time events (alerts + drift, distinguished by `kind`)
CREATE TABLE events (
  event_id      UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id       UUID NOT NULL REFERENCES cloud_connections(conn_id),
  kind          TEXT NOT NULL,               -- 'alert' | 'drift'
  source        TEXT NOT NULL,               -- 'guardduty' | 'inspector' | 'cloudtrail' | 'config' | ...
  severity      TEXT NOT NULL,               -- normalized to our scale
  title         TEXT NOT NULL,
  description   TEXT,
  resource_arn  TEXT,
  actor         TEXT,                        -- IAM user / role / OAuth app for drift
  raw_s3_key    TEXT NOT NULL,               -- pointer to full payload in S3
  normalized    JSONB NOT NULL,
  fired_at      TIMESTAMPTZ NOT NULL,
  ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  push_sent     BOOLEAN NOT NULL DEFAULT false
);

-- Drift event specifics (1:1 extension of events.kind='drift')
CREATE TABLE drift_events (
  event_id      UUID PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
  action        TEXT NOT NULL,               -- 'AuthorizeSecurityGroupIngress', 'PutBucketPolicy', ...
  before_state  JSONB,                       -- nullable (CloudTrail-only events lack before/after)
  after_state   JSONB
);

-- Compliance scores
CREATE TABLE scores (
  score_id      UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  conn_id       UUID,                        -- nullable for tenant-wide composite
  framework     TEXT NOT NULL,               -- 'soc2' | 'iso27001' | 'cis_aws' | ...
  score         INTEGER NOT NULL,            -- 0-100
  scan_id       UUID REFERENCES scans(scan_id),
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- User feedback (kept from v1)
CREATE TABLE feedback (
  feedback_id   UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id       UUID NOT NULL REFERENCES users(user_id),
  target_kind   TEXT NOT NULL,               -- 'finding' | 'event'
  target_id     UUID NOT NULL,
  sentiment     TEXT NOT NULL,               -- 'up' | 'down'
  reason        TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit log (our own — for SOC 2)
CREATE TABLE audit_events (
  event_id      UUID PRIMARY KEY,
  tenant_id     UUID,                        -- nullable for cross-tenant admin actions
  user_id       UUID,
  action        TEXT NOT NULL,
  target        TEXT,
  payload       JSONB,
  ip            INET,
  user_agent    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- LLM response cache (kept from v1, generalized)
CREATE TABLE llm_cache (
  cache_key             TEXT PRIMARY KEY,
  tenant_id             UUID,                -- nullable for tenant-independent prompts
  prompt_type           TEXT NOT NULL,
  response              TEXT NOT NULL,
  model_version         TEXT NOT NULL,
  generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_last_modified  TEXT
);
```

Row-level security policy applied uniformly:

```sql
ALTER TABLE findings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON findings
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
-- repeat for every table with tenant_id
```

Lambda handlers set `SET app.tenant_id = '...'` per-request using the tenant resolved from the validated JWT.

### 8.1 Why an `events` + `drift_events` split

Alerts (GuardDuty, Inspector, etc.) and drift events (CloudTrail writes, Config changes) share most fields — source, severity, timestamps, push-eligibility, resource ARN. Putting them in a single `events` table makes the timeline view trivial (one query) and the push rules engine unified. The `drift_events` extension table holds the only thing drift-only events need: before/after state and the action verb.

---

## 9. `[BUILD]` Real-time event pipeline

### 9.1 AWS path (Phase A primary)

```
Customer AWS account
────────────────────
EventBridge default bus
  │
  │  rule "ciso-copilot-forward"
  │  event-pattern matches:
  │    • source = aws.guardduty            (Finding)
  │    • source = aws.inspector2           (Finding)
  │    • source = aws.securityhub          (Findings - Imported)
  │    • source = aws.config               (Configuration Item Change)
  │    • source = aws.cloudtrail           AND
  │      detail-type = "AWS API Call via CloudTrail" AND
  │      detail.eventName IN (
  │        AuthorizeSecurityGroupIngress, AuthorizeSecurityGroupEgress,
  │        RevokeSecurityGroupIngress,    RevokeSecurityGroupEgress,
  │        PutBucketPolicy, PutBucketAcl, PutBucketPublicAccessBlock,
  │        AttachUserPolicy, AttachRolePolicy, PutUserPolicy, PutRolePolicy,
  │        CreateAccessKey, DeactivateMFADevice,
  │        ConsoleLogin (root only),
  │        DisableKey (KMS), ScheduleKeyDeletion (KMS),
  │        ModifyDBInstance (PubliclyAccessible),
  │        UpdateClusterConfig (EKS public endpoint),
  │        ...full list in workers/policies/cloudtrail_watched_events.json
  │      )
  │
  ▼
Cross-account PutEvents to our bus
  arn:aws:events:us-east-1:<our-account>:event-bus/ciso-copilot-events

Our AWS
───────
Central event bus  ciso-copilot-events
  │
  │  resource-policy allows PutEvents from
  │  any account in trusted-tenants list
  │
  │  rule "fanout"  →  Kinesis Firehose delivery stream
  ▼
Firehose  buffers 60s / 1MB
  ├─► S3 raw archive (gzipped, partitioned by date/tenant)
  └─► Lambda router (transformation processor)
        │
        │ - Resolve tenant_id from `account` field in event
        │ - Normalize severity (GuardDuty severity 7+ → 'high', 8+ → 'critical', etc.)
        │ - Categorize kind ('alert' for GD/Inspector/SH, 'drift' for CT/Config)
        │ - Write events row
        │ - If drift: write drift_events row with action + before/after when present
        │ - Evaluate push rules (§13)
        │ - If push: enqueue SNS Mobile Push
        ▼
Aurora `events` + `drift_events` + APNs (for matched rules)
```

### 9.2 Azure path

Azure Activity Log + Defender alerts → Diagnostic Settings → **Event Hub** in our subscription (one Event Hub per tenant or shared with partition key by tenant — start shared, partition).
Lambda consumer (via Event Hub trigger Lambda? Or scheduled poller of Event Hub iterator. AWS does not have a native Event Hub trigger — use Logic Apps in our tenant Azure sub, OR write a Fargate consumer.) → same normalization + push rules.

Decision: **Fargate consumer task** subscribed to the Event Hub via the Azure SDK. Runs continuously, batches 100 events / 30s, writes to Aurora through the same router code path as AWS (router becomes source-agnostic).

### 9.3 GCP path

Cloud Audit Logs + Security Command Center notifications → Log Router sink → **Pub/Sub topic** in customer's project → our service account pulls.

Our side: Fargate task pulls from Pub/Sub (or AWS Lambda with EventBridge schedule pulling messages — Pub/Sub pull is supported by Lambda but with caveats). **Decision: Fargate consumer, same pattern as Azure.**

### 9.4 Entra path

- **Identity Protection risk events** — Microsoft Graph subscriptions where supported; webhook to API Gateway. Subscriptions require periodic renewal; Lambda cron renews.
- **Sign-in & Audit logs** — Diagnostic settings to the same Event Hub as Azure (uses Entra tenant's diagnostic settings target).

### 9.5 Backpressure & retry

- Firehose absorbs short bursts. If router Lambda fails, Firehose retries; failed records go to a DLQ S3 bucket.
- Fargate consumers checkpoint partition offsets to DynamoDB (small footprint, fast).
- All events arriving at the router with no matching `tenant_id` (e.g., a customer's account that hasn't completed onboarding) are dropped into a "stranded events" S3 prefix for later inspection.

---

## 10. `[BUILD]` Cloud onboarding flows

### 10.0 Account signup & manual approval

We're invite-only in v2 beta. First-time sign-in flows through this state
machine — designed so KK can vet every new tenant before they start handing
us cloud credentials, but doesn't require him to live in a console.

```
1. User taps "Sign in with Microsoft" (or Google) in iOS app.
2. Cognito Hosted UI → IdP OAuth → callback with id_token.
3. Cognito Post-Confirmation Lambda runs (first sign-in only):
     - Extracts email + email_domain + sso_subject.
     - Reject if email is a personal/consumer account
       (Microsoft: tid == 9188040d-... ; Google: hd claim absent).
     - If a tenant for email_domain exists → link user to it,
       inherit tenant.status.
     - Else: create tenant(status='pending'), create user(role='admin').
     - Send approval email to ${APPROVAL_RECIPIENT} via SES with:
         • tenant id, display name, requester email, sector (none yet)
         • Approve link:   GET /admin/tenants/<id>/decision?token=<JWT(approve)>
         • Reject link:    GET /admin/tenants/<id>/decision?token=<JWT(reject)>
       Tokens are HS256-signed with our admin key, single-use, 7-day TTL.
4. iOS shows "Access request pending" screen, polls GET /me every 30s.
5. Admin clicks Approve:
     - Validates JWT (signature, single-use via DB nonce, TTL).
     - Sets tenant.status = 'approved', approved_at = now().
     - Sends "you're in" email to the requesting user via SES with a deep
       link back into the app.
   Reject path mirrors with status='rejected' + rejection email.
6. iOS poll returns status='approved'; transitions to "Add a cloud" wizard.
```

Implementation pieces:

- **SES** — verify `settlingforless.com` as a sending domain in Phase 0. Sender
  `no-reply@settlingforless.com`. Production access (out of sandbox) requested
  before public beta.
- **`tenants` table** — `status TEXT` ∈ {`pending`, `approved`, `rejected`,
  `suspended`}; `approved_at TIMESTAMPTZ`; `approval_token_nonces` JSONB to
  enforce single-use.
- **Env var `APPROVAL_RECIPIENT`** — `kkmookhey@gmail.com` for now; change
  later to a shared inbox or a Slack webhook.
- **API gateway authorizer** — for endpoints other than `/me` and `/voice/session`,
  require `tenant.status == 'approved'`; otherwise return 403. iOS app handles
  403 by routing back to the pending-approval screen.

### 10.1 AWS — one-click CloudFormation

In the iOS app, Profile → "Add AWS account":

1. App generates a one-time external ID (UUID) and includes it in the CFN console URL.
2. URL deep-links to AWS CFN console: `https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?templateURL=https://cdn.ciso-copilot.com/cfn/aws-onboard.yaml&stackName=ciso-copilot-connector&param_OurAccountId=<ours>&param_ExternalId=<one-time>`.
3. Customer reviews; clicks Create.
4. CFN creates:
   - **IAM role `CISOCopilotReader`** — trust policy allows our account to assume with the external ID; managed policies: `arn:aws:iam::aws:policy/ReadOnlyAccess` + `arn:aws:iam::aws:policy/SecurityAudit` + `arn:aws:iam::aws:policy/job-function/ViewOnlyAccess`.
   - **EventBridge rule `ciso-copilot-forward`** — pattern in §9.1; target = our bus ARN with `RoleArn` granting PutEvents.
   - **AWS Config recorder + delivery channel** (optional, prompted on the CFN form) — recorder for all supported resource types in all enabled regions, delivery to S3 bucket in customer's account; EventBridge rule additionally watches `Configuration Item Change Notification` events to forward.
5. CFN custom resource (Lambda) POSTs the role ARN + external ID + Config status back to our `/onboarding/aws/complete` endpoint.
6. Our backend assumes the role to verify access, lists enabled regions, kicks off the initial scan via Step Functions.
7. App polls `/connections/<id>` until status = `active` and `last_scan_at` populated.

CFN template lives in S3 at `cdn.ciso-copilot.com/cfn/aws-onboard.yaml`, versioned (`/v1/`, `/v2/`...) — never modified in place.

### 10.2 Azure — scripted SP creation

In the app, Profile → "Add Azure subscription":

1. App shows: "Run this in Cloud Shell or your terminal with `az` CLI":
   ```bash
   curl -s https://cdn.ciso-copilot.com/azure/onboard.sh | bash -s -- <one-time-token>
   ```
2. Script:
   - Validates user is signed in to the right Azure tenant.
   - Creates app registration `CISOCopilotReader`.
   - Assigns Reader + Security Reader at the subscription scope (or management group if requested).
   - Creates a client secret with 24-month expiry.
   - Creates diagnostic settings on the subscription, Entra tenant, and Defender for Cloud, all targeting our Event Hub namespace via shared access key (passed in to the script).
   - POSTs `tenant_id` (Azure), `subscription_ids`, `client_id`, `client_secret`, and the one-time token to our `/onboarding/azure/complete` endpoint.
3. Backend verifies access, stores secret in Secrets Manager under per-tenant KMS, kicks off initial scan.

### 10.3 Entra — admin consent OAuth

1. App shows "Connect Entra ID" → opens iOS in-app browser (`ASWebAuthenticationSession`) to:
   `https://login.microsoftonline.com/<entra-tenant>/adminconsent?client_id=<ours>&redirect_uri=...&state=<one-time>`.
2. Microsoft prompts a tenant admin to consent to our app's permissions (`Policy.Read.All`, `Directory.Read.All`, `IdentityProtection.Read.All`, `AuditLog.Read.All`, `SecurityEvents.Read.All`).
3. Microsoft redirects to our endpoint; we store the tenant ID + consent grant.
4. We use client credentials flow to call Graph as the app.

### 10.4 GCP — Workload Identity Federation

1. App shows: "Run in Cloud Shell":
   ```bash
   curl -s https://cdn.ciso-copilot.com/gcp/onboard.sh | bash -s -- <one-time-token>
   ```
2. Script:
   - Creates Workload Identity Pool + Provider in the project pointing at our AWS account.
   - Creates service account `ciso-copilot-reader` with `roles/iam.securityReviewer`.
   - Allows our AWS role to impersonate the SA via WIF.
   - Creates log sinks for Admin Activity + SCC findings → Pub/Sub topic.
   - Grants our AWS service account `roles/pubsub.subscriber` on the topic.
   - POSTs project IDs, SA email, WIF provider URI, Pub/Sub topic to our endpoint.
3. Backend stores config, kicks off initial scan.

---

## 11. `[BUILD]` LLM prompts

LLM is Sonnet 4.6 via Anthropic API (kept from v1; Bedrock-native option later). All prompts cached per `CISOBrief.md` §10.4 pattern, generalized to `target_kind` + `target_id` + optional `tenant_stack_hash`.

Five prompt types:

1. **`finding-why-it-matters`** — given a Shasta finding + the resource ARN + the tenant's sector, produce 2–3 sentences. Same rules as v1.
2. **`finding-board-paragraph`** — single paragraph board update referencing the specific resource and risk.
3. **`finding-team-questions`** — 3 questions each for Infra / SOC / IAM teams, JSON.
4. **`drift-narrative`** — given a drift event (before, after, actor, resource), 2 sentences explaining "what changed and is this normal." Cached by `event_id` only — no stack dependency.
5. **`alert-narrative`** — given a GuardDuty / Defender / etc. alert payload, 2 sentences explaining the threat and the action to take.

Cache keys:
- `finding:<finding_id>#<prompt_type>` for finding-* prompts (no stack hash; finding already names the tenant context).
- `event:<event_id>#<prompt_type>` for drift / alert narratives.

Invalidation: when `findings.last_seen` updates or `events.normalized` changes (rare; events are immutable in practice).

Pre-render strategy: when a finding or event is written, fire `ctx.waitUntil(generateNarrative(...))` so the iOS app sees prose on first open. Fall back to "Generating..." text if not yet rendered.

---

## 12. `[BUILD]` Voice (OpenAI Realtime)

### 12.1 Session minting

1. iOS calls `POST /voice/session`. Lambda validates JWT, mints an ephemeral OpenAI Realtime session via `https://api.openai.com/v1/realtime/sessions` with:
   - `model: gpt-4o-realtime-preview-2024-10-01` (or whatever is current at build time).
   - `voice: alloy` (or per-user preference).
   - `instructions`: full system prompt including tenant ID, user name, connected clouds, sector — so the model has context.
   - `tools`: tool definitions in §12.2.
2. Lambda returns the ephemeral session token to iOS.
3. iOS opens WebRTC directly to OpenAI using the session token. Lambda is not on the audio path.

### 12.2 Tool definitions (mirrors Shasta's voice tool schema)

Tools the model can call. Each tool is a Lambda backend endpoint behind API Gateway:

| Tool | Maps to |
|---|---|
| `get_top_risks(limit, severity?)` | `SELECT FROM findings WHERE status='fail' ORDER BY severity, last_seen DESC` |
| `get_finding(finding_id)` | `SELECT FROM findings WHERE finding_id=?` |
| `list_findings(filter)` | parametrized: cloud, severity, framework, resource_type |
| `get_resource_findings(resource_arn)` | findings on a specific ARN |
| `list_recent_alerts(since, severity?)` | `SELECT FROM events WHERE kind='alert'` |
| `list_recent_drift(since, resource_arn?)` | `SELECT FROM events WHERE kind='drift'` |
| `get_compliance_score(framework?)` | `SELECT FROM scores` |
| `get_score_trend(framework, days)` | scores over time |
| `list_connected_clouds()` | cloud_connections summary |
| `get_resource(identifier)` | assets table lookup |
| `whats_new(period)` | aggregated changes across findings + events + scans |
| `summarize_top_3()` | LLM summarizes top 3 findings — called when user says "give me the headlines" |

All tools enforce the calling user's tenant via JWT context. No tenant ID is passed by the LLM — the backend resolves it.

### 12.3 System prompt sketch

```
You are CISO Copilot, the security assistant for {{user_name}}, the CISO at
{{tenant_name}} ({{tenant_sector}}).

Connected clouds: {{connected_clouds}}.
Last full scan: {{last_scan_at}}.

You answer in 2–4 spoken sentences unless asked for more detail. You always
reference specific resources by their identifier (ARN, subscription, etc.)
when relevant. You never speculate about resources that aren't in the data —
if you don't have it, call the appropriate tool to fetch it.

You never make up findings, scores, or alerts. If a tool returns nothing,
say so.

If the user asks for action, recommend a specific next step. Do not lecture.
```

---

## 13. `[BUILD]` Push rules engine

Implemented in the router Lambda. Pure function on a normalized event payload, returns `{ push: boolean, title, body, severity }`.

### 13.1 v2 launch rule set

| Source | Condition | Push severity |
|---|---|---|
| GuardDuty | severity ≥ 7 (high+) | critical |
| Inspector | finding severity = `CRITICAL` AND `network_reachability.openPortRange` includes 22/3389 | critical |
| Security Hub | finding has `Compliance.Status = FAILED` AND `Severity.Label = CRITICAL` | critical |
| CloudTrail | `AuthorizeSecurityGroupIngress` AND new rule has `CidrIp = 0.0.0.0/0` AND `FromPort` in {22, 3389, 1433, 5432, 3306, 6379, 27017} | critical |
| CloudTrail | `PutBucketPolicy` OR `PutBucketAcl` resulting in public-read or public-write | critical |
| CloudTrail | `AttachUserPolicy` / `AttachRolePolicy` attaching `AdministratorAccess` or `arn:aws:iam::aws:policy/PowerUserAccess` | high |
| CloudTrail | `DeactivateMFADevice` on any IAM user | high |
| CloudTrail | root account `ConsoleLogin` | critical |
| CloudTrail | KMS `DisableKey` or `ScheduleKeyDeletion` | high |
| Defender for Cloud | Severity = `High` | high |
| Entra Identity Protection | risk_level = `high` AND event in `riskEventTypes = {atypicalTravel, anonymizedIPAddress, malwareInfectedIPAddress, leakedCredentials}` | high |

### 13.2 Per-user throttling

- `critical` severity: no throttle, always pushed.
- `high` severity: max 3/day per user (configurable per-tenant).
- `medium` / `low`: never pushed; available in the Alerts inbox only.

Stored per user in `users.notification_prefs JSONB`.

---

## 14. `[BUILD]` Phased build order

Each phase ends with a working, deployable, demoable increment.

### Phase 0 — Foundations (week 1)

- CDK / Terraform scaffolding: VPC, Aurora cluster, Cognito, API Gateway, Secrets Manager, KMS, SES domain identity for `settlingforless.com`.
- Aurora schema applied (incl. `tenants.status`).
- Cognito User Pool + Microsoft & Google OIDC IdPs configured (corporate accounts only — reject consumer Microsoft / personal Google).
- Post-confirmation Lambda: auto-provisions tenant in `pending`, emails `APPROVAL_RECIPIENT` via SES with Approve/Reject links.
- `/admin/tenants/<id>/decision` endpoint validates the signed JWT and flips status; sends user notification.
- API Gateway authorizer enforces `tenant.status == 'approved'` on all routes except `/me`.
- iOS auth flow rewired (replaces v1 anonymous deviceId), pending-approval screen, polling.

**Done means:** A new user can sign in with their Microsoft 365 corporate account from the iOS app, see "Access request pending," KK approves via email, user gets a "you're in" email, and lands on "Add a cloud."

### Phase A — AWS (weeks 2–4)

- **A.1 Onboarding** — CFN template; `/onboarding/aws/*` endpoints; iOS wizard.
- **A.2 Pull scanner** — Step Functions workflow; Shasta packaged as a Lambda container image; per-region fan-out; findings written to Aurora; assets table populated.
- **A.3 Real-time pipeline** — Central event bus; Firehose; router Lambda; events + drift_events tables.
- **A.4 Push rules** — engine in router Lambda; SNS Mobile Push integration; APNs.
- **A.5 iOS screens** — Brief (Top Risks + Posture Diff + Threat Exposure), Alerts (live inbox), Posture (scores), Connected Clouds.
- **A.6 Finding / event detail screens** — LLM narratives pre-rendered.
- **A.7 Web SPA** — Vite + React + TypeScript at `app.settlingforless.com`. Same Cognito user pool, separate web client. Screens parallel to iOS: Sign-in, Connect AWS wizard, Top Risks, Posture, Alerts, Resource detail.

**Done means:** Phase A demo contract (§4.3).

### Phase B — Azure (week 5)

- B.1 Onboarding script + endpoint.
- B.2 Pull scanner — Shasta Azure module packaged; subscription fan-out.
- B.3 Real-time — Event Hub consumer (Fargate task); router accepts Azure-shaped events.
- B.4 iOS — Azure shows up alongside AWS in all screens.

### Phase C — Entra (week 6)

- C.1 Admin consent OAuth + endpoint.
- C.2 Pull scanner — Shasta `azure/entra.py` + `azure/iam.py`.
- C.3 Real-time — Graph subscriptions (where push-eligible) + diagnostic settings via Azure Event Hub path.
- C.4 iOS — Entra-specific surfaces: MFA coverage, privileged users count, CA gap analysis, app consents, risky sign-ins.

### Phase D — GCP (week 7)

- D.1 Workload Identity Federation onboarding.
- D.2 Pull scanner — Shasta GCP module.
- D.3 Real-time — Pub/Sub pull consumer (Fargate task).
- D.4 iOS — GCP tabs.

### Phase E — Voice (week 8)

- E.1 `/voice/session` endpoint.
- E.2 Tool definitions implemented as API endpoints.
- E.3 iOS voice UI — floating button, tap-to-talk overlay, transcript display.

### Phase F — Enterprise polish (weeks 9+)

- Audit logs + admin console for tenant admins.
- Slack / Teams / email notifications.
- Multi-device support per user.
- Org-wide AWS Organizations enrollment.
- Compliance report PDF.
- WorkOS migration (if any paying customer demands SAML/SCIM).
- Self-serve billing (Stripe).
- Our own SOC 2 audit readiness.

---

## 15. Non-functional requirements

- **Multi-tenant isolation:** PostgreSQL RLS enforces tenant boundary on every query. Per-tenant KMS keys for customer cloud creds. No tenant identifier ever derived from a client-supplied value — always from the JWT.
- **Event ingestion latency:** p95 < 30s from cloud-source emission to Aurora write; p95 < 60s to APNs delivery for critical events.
- **Scan duration:** p95 < 10 min for an AWS account with up to 1000 resources across enabled regions; failover to Fargate above 15 min.
- **API latency:** p95 < 500ms for read endpoints; p95 < 2s for writes (excluding scan triggers).
- **Voice latency:** p95 < 800ms for tool call round-trip from spoken command to spoken response.
- **Compliance:** SOC 2 Type II audit readiness target end of Phase F. ISO 27001 deferred.
- **Audit logs:** every cloud credential access, every cross-tenant access (none should happen), every sign-in, every cloud onboarding step.
- **Encryption:** at-rest in Aurora (AWS-managed key for app data + per-tenant CMK for secrets); in-transit TLS 1.2+ everywhere.
- **Backup:** Aurora continuous backups, 30-day retention. Per-tenant export on request.
- **Availability:** 99.9% target for the API; voice is best-effort (depends on OpenAI Realtime availability).

---

## 16. Success criteria

### 16.1 Phase A go-live (internal beta)

- 3 internal Transilience users + 2 design-partner customers connected.
- Phase A demo contract passes (§4.3).
- p95 metrics in §15 met for one week of normal traffic.

### 16.2 Phase E go-live (product GA)

- 10 paying customers, each with ≥1 cloud connected.
- D7 retention ≥ 60% for newly connected customers (they open the app every day for the first week).
- Brief precision (% findings marked useful via thumbs feedback) ≥ 75%.
- Voice success rate (user said something → got a useful answer) ≥ 80%.

---

## 17. What v2 sunsets from v1

- `ciso-copilot.kkmookhey.workers.dev` Worker — decommissioned once Phase A is GA.
- D1 database `ciso_copilot` — exported to S3 archive, then deleted.
- R2 bucket `ciso-copilot-raw` — same.
- The Cloudflare-specific iOS code paths (API client base URL, public-feed brief screens) — removed.
- The KEV/NVD/EPSS ingestion code — retired; v2's Threat Exposure feature uses Shasta's `threat_intel` module fed by the same upstream feeds but now joined against real assets.

A `legacy/` folder in the repo keeps the v1 Cloudflare code for reference until Phase E ships; then deleted.

---

## 18. Locked-in decisions

| # | Decision |
|---|---|
| 1 | **Domain:** `settlingforless.com`. API at `api.settlingforless.com`, CFN templates at `cdn.settlingforless.com`, OAuth callbacks under `auth.settlingforless.com`, SES sender `no-reply@settlingforless.com`. Re-brand later by switching DNS + reissuing Cognito callbacks. |
| 2 | **Cognito IdP app registrations:** Microsoft (Entra multi-tenant via `/common`) + Google (Workspace OIDC). Personal-account rejection enforced in Pre-Token-Generation Lambda. |
| 3 | **Anthropic + OpenAI billing:** KK's personal API keys for the beta. Migrate to a Transilience org with usage caps before public GA. |
| 4 | **Shasta packaging:** Lambda container image (`shasta-runner`) in ECR, built from a tagged Shasta release. Fargate uses the same image for long scans. |
| 5 | **Compliance frameworks:** all Shasta-supported frameworks visible by default — no toggle. |
| 6 | **Signup flow:** invite-only beta. Manual approval by admin (`kkmookhey@gmail.com` initially) via email link. See §10.0 for the full state machine. |
| 7 | **Apple Developer / distribution:** TestFlight via proper provisioning, Team `2G875YX5NV`, Bundle `ai.transilience.cisocopilot`. No more direct devicectl installs once Phase A ships. |
| 8 | **Repo + license:** Same repo (`kkmookhey/ciso-copilot`), flipped to **private**, **proprietary license**. v1 code stays in the same repo under `legacy/` until v2 Phase A ships, then deleted. |

---

## Appendix A — Mapping CISO questions to v2 features

| CISO question | v2 surface |
|---|---|
| What are my top security risks? | Brief → Top Risks (Shasta findings ranked) |
| What changed in the last 24h / 1w? | Brief → Posture Diff |
| Given my environment, what threats matter? | Brief → Threat Exposure (Shasta `threat_intel` × assets) |
| What critical alerts have fired? | Alerts tab (real-time event inbox) |
| What's my compliance score? | Posture tab |
| Did anyone expose something they shouldn't have? | Alerts tab + push notification (CloudTrail rules) |
| Board paragraph about a headline breach? | Finding detail → "Board paragraph" |
| What should my teams do today? | Finding detail → "Ask my team" |
| Free-form questions | Voice (floating button anywhere) |

## Appendix B — Glossary

- **CFN** — AWS CloudFormation.
- **CMK** — Customer-managed KMS key.
- **CSPM** — Cloud Security Posture Management (the category we play in).
- **Drift** — A configuration change in the customer's cloud (e.g., a security group opened) detected by CloudTrail or AWS Config.
- **Entra** — Microsoft's identity service (formerly Azure Active Directory).
- **Findings vs Events** — Findings come from pull-based scans (Shasta). Events come from real-time streams (alerts + drift).
- **MCSB** — Microsoft Cloud Security Benchmark.
- **OIDC** — OpenID Connect, the OAuth-on-top-of identity layer used for SSO.
- **RLS** — Row-level security in PostgreSQL.
- **Shasta** — Our deterministic multi-cloud compliance scanner; the engine of v2.
- **WIF** — Workload Identity Federation (GCP's cross-cloud auth model).
