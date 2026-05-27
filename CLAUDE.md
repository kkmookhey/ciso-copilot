# Shasta by Transilience

**The Full Stack Security OS** — one platform for cloud security, AI
security, SOC, and compliance, accessible from web, iOS, voice, and
chat. Multi-tenant. Connects to AWS, Azure, Entra, GCP. Runs posture
scans, ingests real-time alerts and config drift, AI-enriches
everything.

Repo internal name is still `CISOBrief` (rename pending). Product
brand on every UI surface is **"Shasta by Transilience"**.

## Documents — read in this order at session start

These six documents survive `/clear` and carry the load-bearing
context. Read them at the top of every session so you and the user
have aligned mental models before touching code.

1. **`HANDOFF.md`** — current build state, live URLs, what works,
   what's wired but untested, known gotchas paid in debugging time.
   Source of truth for *what exists right now*. **Read first every
   session.**
2. **`README.md`** — what Shasta is, the four surfaces, the shipped
   capabilities, the shipped-modules timeline, the product story.
   The lead magnet.
3. **`ARCHITECTURE.md`** — load-bearing design decisions and ADRs.
   *Why* we built it the way we did. Read before touching a load-bearing
   subsystem.
4. **`ROADMAP.md`** — where the OS extends next, the M1–M7 heavy
   lifts, future arenas (DSPM / CTEM / MDR / privacy / safety), and
   the anti-roadmap (what we deliberately won't build). Read before
   proposing new sub-projects.
5. **`CISOBrief-v2.md`** — the v2 PRD / executable spec. The build
   contract for v2 work. Read when starting feature work that maps
   back to the PRD.
6. **`CISOBrief.md`** — v1 spec (Cloudflare-only KEV brief). v1 was
   deployed at `ciso-copilot.kkmookhey.workers.dev` and is *sunset*.
   Reference only; do not build against this.

Where any two disagree: **HANDOFF wins for current state**,
**ARCHITECTURE wins for design rationale**, **ROADMAP wins for
sequencing and anti-roadmap**, **README wins for the product story**,
**v2 wins for the spec contract**. v1 / this file lose.

Also useful: **`BACKLOG.md`** for triaged open items and decisions
pending.

## Status (2026-05-26)

- **v1** — sunset (Cloudflare Workers; the `workers/` directory was
  deleted in Phase 2 Slice A).
- **v2** — live. CME-v2 + AI Visibility v2 (Slices 1+2+2.1) + SOC
  Slices 1+1c all shipped. **Phase 2 Slice A** (secrets / hardcoded-
  identifier extraction) shipped 2026-05-26 — every per-deployment
  identifier now reads from env-var configuration (`platform/.env`,
  `web/.env.production`, `ios/Local.xcconfig`). MIT-public **code-side**
  gate is closed; only Tier 2 (doc sanitization in `HANDOFF.md`,
  `TEST_PLAN.md`, `docs/superpowers/specs/*.md`,
  `docs/superpowers/plans/*.md`) remains before the repo can flip
  public.
- **Next** — Phase 2 commerce-ready: capability gating, then the
  billing module sub-phases, then SOC Slice 2 (identity drift). See
  **ROADMAP.md** for the full phase plan. **iOS device install of the
  Slice A5 build is pending KK-manual** — see HANDOFF for the unlock-
  the-phone + one-command step.

## Repo layout

```
platform/        AWS CDK (TypeScript) + Lambda Python + Docker scanner images
  bin/           CDK app entry
  lib/           one stack per file (network, data, auth, ecr, static, events, scan, api)
  lambda/        one Lambda per directory; each has main.py + (optional) build.sh
  cfn/           customer-facing artefacts (aws-onboard.yaml, azure/onboard.sh, gcp/onboard.sh)
  sql/           Aurora schema (001_phase0.sql, 002_phase_a.sql)
  .env           ENTRA_*, GOOGLE_*, DOMAIN, APPROVAL_RECIPIENT (not checked in)

ios/             SwiftUI app, iOS 17+, WebRTC SPM dep, Cognito OAuth via ASWebAuthSession
  CISOCopilot/   Services, Views, RootView, App entry
  project.yml    xcodegen source — regenerate xcodeproj from this

web/             Vite + React + TS + Tailwind; deployed to S3 + CloudFront
  src/routes/    SignIn, Callback, PendingApproval, Welcome, ConnectClouds, TopRisks
  src/lib/       cognito.ts (OAuth) + api.ts (HTTP)

HANDOFF.md       state of the build
TEST_PLAN.md     today's web-app test script
CISOBrief-v2.md  PRD / spec (v2)
CISOBrief.md     PRD (v1, retained for reference)
```

## Common commands

All from `platform/` unless stated.

```bash
# CDK deploy (full — for IAM/infra changes)
npx cdk deploy <StackName> --require-approval never

# CDK hotswap (Lambda code + env vars only, much faster; SKIPS IAM updates)
npx cdk deploy <StackName> --require-approval never --hotswap

# All stacks:
#   CisoCopilotNetwork, CisoCopilotData, CisoCopilotAuth, CisoCopilotEcr,
#   CisoCopilotStatic, CisoCopilotEvents, CisoCopilotScan, CisoCopilotApi

# Aurora query via Data API (cluster + secret ARNs in HANDOFF.md)
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot --sql "SELECT ..."

# Tail a Lambda's logs
aws logs tail "/aws/lambda/<FunctionName>" --since 5m

# Scanner image rebuild + push (run from platform/lambda/shasta_runner_<cloud>/)
./build.sh
```

```bash
# Web deploy
cd web
pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
```

```bash
# iOS build (device — KK iPhone 16 Pro Max)
cd ios
xcodegen generate
xcodebuild build \
  -project CISOCopilot.xcodeproj -scheme CISOCopilot \
  -destination "id=00008140-001E104E3A9B001C" \
  -derivedDataPath build-device \
  -allowProvisioningUpdates
xcrun devicectl device install app --device 00008140-001E104E3A9B001C \
  build-device/Build/Products/Debug-iphoneos/CISOCopilot.app
```

## Working principles for this build

- **Read `HANDOFF.md` and `TEST_PLAN.md` at session start.** They survive `/clear`.
- **Today's mode**: testing + bugfix only. Do not add new features until
  the existing flows are self-service-ready from the web app.
- **Single CDK app, all in `platform/`.** Don't fragment.
- **Shasta is a read-only reference — never edit it.** Shasta lives at
  `~/Projects/Shasta`, ships as a sub-package, and is installed into
  scanner images via `pip install --no-deps` (avoids the xhtml2pdf →
  pycairo build chain). Do NOT modify Shasta — not the local checkout,
  not its GitHub repo. If a Shasta function is wrong, work around it in
  *this* repo (see the scanner's `ai_pass.py`). Don't rewrite Shasta
  checks to TypeScript either.
- **WebRTC for voice on iOS, not WebSocket.** The platform AEC is what
  prevents the speakerphone echo loop. Reference working code at
  `~/Projects/shasta-ios-poc/ios/ShastaPOC/Voice/RealtimeClient.swift`.
- **One model version, pinned, in one config value** — currently
  `gpt-realtime` and `claude-sonnet-4-6`.
- **The iOS / web apps never call upstream sources.** Only the API
  Gateway. This is the rate-limit and key-protection boundary.
- **Cognito subject extraction always uses `identities[0].userId` first,
  then falls back to `sub`.** For federated logins (Microsoft/Google),
  `claims.sub` is the Cognito-user-pool sub — NOT the upstream IdP sub.
  `users.sso_subject` stores the upstream value (from the `identities`
  claim). Any handler that JOINs `users.sso_subject` must follow this
  pattern; see `voice_session._subject_from_claims` and
  `events_list._resolve_tenant_id` for the canonical impl. Reaching for
  `claims.get("sub")` directly silently 401s every federated user.
- **EventBridge rule patterns for "AWS API Call via CloudTrail" events
  must NOT filter on `source`.** Real management API events arrive with
  `source: aws.<service>` (aws.ec2, aws.iam, aws.s3, etc.) — never
  `aws.cloudtrail`. Filter on `detail-type` + `detail.eventName` only.
  Same gotcha lives in the router's `_normalize` / `_classify_kind` /
  `_source_event_id` / `_extract_states` — they all key on `detail-type`,
  not `source`, for this exact reason.

## Things you must NOT do

- ❌ Commit secrets, API keys, tokens, `.env`, `.info`, `*.p8`. The
  `.gitignore` covers these; verify before any `git add`.
- ❌ Modify `.env`, `.env.*`, `package-lock.json`, `uv.lock`,
  `pnpm-lock.yaml`, or anything in `.git/` directly. Use the proper tools.
- ❌ Run destructive commands without confirming: `rm -rf`, `DROP TABLE`,
  `git push --force`, `git reset --hard`, `git commit --no-verify`.
- ❌ Skip tests or disable hooks. Fix the underlying issue.
- ❌ Invent function names, library APIs, or version numbers. Check or say
  "I don't know — let me look it up."
- ❌ Edit Shasta. `~/Projects/Shasta` and the Shasta GitHub repo are
  read-only references. Work around Shasta bugs in *this* repo.

---

*Update this file when something consistently bites — that's the signal a
rule is missing. Detailed state belongs in `HANDOFF.md`, not here.*
