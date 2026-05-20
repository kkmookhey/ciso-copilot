# CISO Copilot v2 — Handoff & State

> Source of truth for the *current* state of the v2 build. Reload this at the
> top of every session. The PRD is `CISOBrief-v2.md`; this document records
> what's actually built, what was broken and fixed, and what still hurts.
>
> Last updated: 2026-05-19 (SP4 Phase 4a deployed — chat-first front door, text path).

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
  token-streamed assistant replies. Deployed to `app.settlingforless.com`.

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

**Phase 4a demo gate — PENDING KK's authenticated verification.** Deploy
verified at the unauthenticated level (`/` → `/signin` redirect works,
sign-in page clean, zero console errors). The full demo (sign in → land on
chat → type a question → watch the reply stream in → refresh → resume)
needs KK's Google sign-in. **Next: KK runs the authed demo; then Phase 4b
(tools + 8 artifact components).**

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

**Out of Slice 1 (deferred to future slices):** MCP server,
OpenAI/Anthropic connectors, limited AWS-AI (Bedrock) connector, blast
radius, KMS-signed evidence packets, cloud-finding evidence-packet
backfill, push-webhook rescan-on-commit, sparse checkout for monorepos,
all-repos aggregate trust graph.

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
| Web SPA | `https://app.settlingforless.com/` (custom domain live 2026-05-18; backed by CloudFront `dil1ztnjosz43.cloudfront.net` which still works) |
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
- **`app.settlingforless.com` custom domain**: ACM cert issued, Cloud DNS records (CNAME + DKIM validation) added, CloudFront alternate domain attached, Cognito callback URLs include the new domain. SPA reachable at `https://app.settlingforless.com/` end-to-end.
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
