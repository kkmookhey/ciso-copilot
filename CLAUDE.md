# CISO Copilot

A multi-tenant CISO platform — connects to AWS, Azure, Entra, GCP, runs
posture scans, ingests real-time alerts and config drift, surfaces it all
through an iOS app, a web app, and a voice interface.

## Three documents — read in this order each session

1. **`HANDOFF.md`** — current build state, live URLs, what works, what's
   wired but untested, known gotchas paid in debugging time. Source of
   truth for *what exists right now*. **Read first every session.**
2. **`CISOBrief-v2.md`** — the executable spec / PRD for v2. The build
   contract. Read when starting new feature work.
3. **`CISOBrief.md`** — v1 spec (Cloudflare-only KEV brief). v1 was
   deployed at `ciso-copilot.kkmookhey.workers.dev` and is *sunset*.
   Reference only; do not build against this.

Where any two disagree: **HANDOFF.md wins for state, v2 wins for spec**,
v1 / this file lose.

## Status (2026-05-20)

- **v1** — deployed at `ciso-copilot.kkmookhey.workers.dev`, sunset.
- **v2** — live. The SP4 chat-first front door, the AI-discovery cloud-AI
  connector, and the findings overhaul (Fail/Partial/Pass tiles + the
  Status/Category/Cloud/Framework grouping) are all deployed. Next:
  AI-discovery plan 2 — OpenAI/Anthropic provider connectors. Read
  **HANDOFF.md** first every session.

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
- **Shasta lives at `~/Projects/Shasta` and ships as a sub-package** — do
  not rewrite checks to TypeScript. Install in scanner images via
  `pip install --no-deps` to avoid the xhtml2pdf → pycairo build chain.
- **WebRTC for voice on iOS, not WebSocket.** The platform AEC is what
  prevents the speakerphone echo loop. Reference working code at
  `~/Projects/shasta-ios-poc/ios/ShastaPOC/Voice/RealtimeClient.swift`.
- **One model version, pinned, in one config value** — currently
  `gpt-realtime` and `claude-sonnet-4-6`.
- **The iOS / web apps never call upstream sources.** Only the API
  Gateway. This is the rate-limit and key-protection boundary.

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

---

*Update this file when something consistently bites — that's the signal a
rule is missing. Detailed state belongs in `HANDOFF.md`, not here.*
