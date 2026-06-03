# Shasta by Transilience

> **The Full Stack Security OS** — one platform for cloud security, AI
> security, SOC, and compliance, accessible from web, iOS, voice, and
> chat. Built so a CISO eventually sees every security, privacy, and
> safety signal across the company in one product.

**Live:** [shasta.transilience.cloud](https://shasta.transilience.cloud) ·
**Status:** v2 shipping daily · **Owner:** [Transilience.ai](https://www.transilience.ai)

---

## The thesis

A modern CISO juggles fragmented tools: Wiz for cloud, Sentinel for SIEM,
Defender for endpoints, Drata for compliance, Snyk for code, plus a
growing pile of AI-specific scanners that don't talk to any of them. Each
tool has its own findings model, its own identity graph, its own
compliance crosswalk, its own UI. The CISO becomes the integration
layer — which doesn't scale, and breaks every time something changes.

**Shasta is the opposite bet.** One unified findings model. One identity
graph. One compliance crosswalk that maps to NIST AI RMF, ISO 42001, EU
AI Act, SOC 2, ISO 27001, PCI, FedRAMP, and CIS — automatically. One
front door, accessible from a web console, an iOS app, a real-time voice
interface, and an MCP-driven chat surface. Every signal flows through
the same pipeline, carries the same metadata, and surfaces in the same
place.

The thesis: **Full Stack Security OS**. Start with cloud + AI + SOC +
compliance because that's where the unfair advantage is today — the
Shasta open-source scanner gives us a head start on cloud + AI
detection, and the 10 AI-specific repo detectors in `ai_scanner/` cover
ground no upstream tool addresses cleanly. Extend the OS into DSPM,
CTEM, Cloud MDR, compliance wizards, privacy posture, and safety
posture once the foundation is rock-solid.

One platform. Every signal a CISO needs.

---

## What Shasta covers today

### Cloud security

- **AWS scanner** — CloudFormation-onboarded, ECR-hosted scanner images,
  findings tagged across 8 frameworks. Quick / Medium / Deep tiers.
- **Azure scanner** — Activity Log + Resource Graph + Microsoft Cloud
  Security Benchmark + Defender (when on). No Sentinel dependency.
- **GCP scanner** — Cloud Asset Inventory + IAM + Cloud Audit Logs +
  Security Command Center (when on).
- **Entra scanner** — Microsoft Graph: conditional access, MFA
  enforcement, identity governance, sign-in risk events.

### AI security

- **AI workload discovery** — Bedrock, SageMaker, Cognitive Services,
  OpenAI / Anthropic-bound resources surfaced as first-class entities.
- **AI SaaS visibility** — Entra sign-in pass against a 30-app catalog
  (ChatGPT, Claude, Cursor, Copilot, Perplexity, Gemini, Mistral, …)
  with per-tier classification.
- **AI code scanner** — GitHub App connector + 9 detectors walking
  connected repos: model usage (OpenAI / Anthropic / Bedrock calls),
  embeddings, frameworks (LangChain / LlamaIndex), vector DBs, MCP
  servers, prompt files, secrets-in-AI-code, agentic workflows,
  cross-domain (AI code touching cloud OIDC).
- **AI-specific frameworks** — NIST AI RMF, ISO 42001, EU AI Act, MITRE
  ATLAS, OWASP LLM, NIST AI 600-1 mapped automatically.
- **Unified `/ai` view** — family-grouped tiles, drill-down to findings,
  top-AI-users table.

### SOC

- **AWS Config drift events** in real time — every config change against
  a customer-onboarded account flows through.
- **EventBridge → router → SQS → enrichment Lambda** pipeline with
  per-tenant rate limit + per-tenant daily LLM spend cap.
- **AI-enriched events** — LiteLLM → Claude Sonnet writes a narrative,
  an anomaly score, three suggested next-step CLI commands, MITRE
  technique, and a confidence rating per event.
- **Threat-intel substrate** — 5,726 IOCs across AbuseCH Feodo +
  ThreatFox, CISA KEV, and Tor exit nodes. GreyNoise on-demand fallback
  for unmatched IPs.
- **Per-event provenance** — every enrichment records the rule IDs that
  fired and the TI sources that matched.
- **`/soc` console** with timeline + filter chips (severity / source) +
  detail pane (narrative + anomaly score + next-step commands + features
  disclosure + related findings + 👍/👎 feedback).

### Compliance

- **Compliance Mapping Engine v2** — two-stage normalize → augment
  pipeline. ~65 rewrite rules + 13 canonical augment rules.
- **8 frameworks tagged automatically**: NIST AI RMF, ISO 42001, EU AI
  Act, SOC 2, ISO 27001, PCI DSS, FedRAMP, CIS Benchmarks, NIST AI 600-1.
- **Family-grouped tiles** on `/ai` and the dashboard (security / AI /
  industry).
- **Per-finding provenance** — every finding records the rule IDs that
  applied (`_registry_rule_ids`), so an auditor can ask "why is this
  finding tagged with PCI 8.3?" and get a real answer.
- **`/findings?framework=<key>` filter** with the "Mapping only — not a
  compliance attestation" disclaimer on every tile + chip.

---

## The four surfaces

Shasta is built so a CISO can choose the surface that matches the moment.

| Surface | Best at | Tech |
|---------|---------|------|
| **Web** | The analyst console — findings, `/ai`, `/soc`, `/compliance`, `/connect`. Dense, keyboard-driven. | Vite + React + TypeScript + Tailwind, served from S3 + CloudFront |
| **iOS** | The alerting + handoff companion. Push notifications when something demands attention, a quick read on the go, a one-tap handoff to a teammate. | SwiftUI (iOS 17+), Cognito OAuth via `ASWebAuthSession`, APNs push |
| **Voice** | Hands-free walk-through. "What changed in AWS yesterday?" "Show me Entra users who logged into ChatGPT this week." | WebRTC real-time (not WebSocket — the platform AEC prevents the speakerphone echo loop), OpenAI / Gemini realtime models |
| **Chat** | The question-answer surface. Streaming responses with tool calls into the underlying data. Replaces the home-page dashboard for many users. | Lambda Web Adapter for streaming on managed Python Lambda, LiteLLM for model abstraction |
| **MCP** | (Future) Outbound action layer — Shasta drafts the Slack message, the JIRA ticket, the M365 share, all approval-gated. | MCP servers per integration, see [ROADMAP.md](ROADMAP.md) §M5 |

---

## Sub-projects shipped

Each row is a sub-project: brainstorm → design spec → implementation
plan → vertical-slice execution → review → deploy → verify. Specs live
in `docs/superpowers/specs/`; plans in `docs/superpowers/plans/`.

| Date | Module | Status |
|------|--------|--------|
| 2026-05-16 | v1 KEV Brief (Cloudflare Worker — pivoted in week 1) | Sunset |
| 2026-05-18 | v2 platform foundation — AWS CDK, Aurora Postgres, Cognito, scanner pipeline | Shipped |
| 2026-05-19 | SP4 chat-first front door — streaming chat with tool calls | Shipped |
| 2026-05-20 | AI Discovery cloud-AI connector + Findings overhaul (Fail / Partial / Pass tiles + grouping) | Shipped |
| 2026-05-22 | Azure scanner uplift v2 + GCP scanner uplift Slices 1–2 | Shipped |
| 2026-05-22 | AI Visibility v2 Slice 1 — Azure-AI cloud pass + unified `/ai` view | Shipped |
| 2026-05-23 | AI Visibility v2 Slice 2 — Entra AI sign-in pass (30-app catalog) | Shipped |
| 2026-05-24 | AI Visibility v2 Slice 2.1 — Entra Free-tier licensing banner | Shipped |
| 2026-05-24 | Compliance Mapping Engine v2 — 8 frameworks, two-stage normalize → augment | Shipped |
| 2026-05-25 | SOC Slice 1 — AWS Config drift + AI enrichment + `/soc` console | Shipped |
| 2026-05-26 | SOC Slice 1c — Threat-intel substrate (5,726 IOCs) | Shipped |
| 2026-05-26 | "Shasta by Transilience" branding + docs trio + MIT license | Shipped |
| 2026-05-27 | Phase 2 Tier 2/3 — secrets/identifier sweep + repo MIT-public | Shipped |
| 2026-05-28 | Wow demo — voice-first agentic investigation (Demo A + B) | Shipped |
| 2026-05-30 | MCP Connectors Slice 1 — per-user OAuth + Slack-as-MCP + KMS-envelope tokens | Shipped |
| 2026-06-02 | MCP Connectors Slice 2 — autonomous CRITICAL → Slack broadcast (admin bot + channel picker + subscriber Lambda + deep-link gate + drift alarm) | Shipped |
| 2026-06-03 | Azure + GCP autonomous broadcast — env-var injection + post-commit race fix; all 4 scanners broadcasting end-to-end | Shipped |
| 2026-06-03 | Broadcast `RETURNING finding_id` — repeat-scan ON CONFLICT findings now carry the persisted id, so Slack cards land on every critical-fail | Shipped |
| Next | Capability gating + billing module sub-phases (usage → dashboard → caps → Stripe) | Planned |
| Next | SOC Slice 2 — Identity drift (AWS IAM + Entra audit logs) | Planned |

---

## How this was built

Shasta is built AI-natively. We use [Claude Code](https://claude.com/claude-code)
and the Anthropic SDK as a daily development surface, the same way an
earlier generation of engineers used `vim` + `git` + a REPL. That's
worth naming explicitly because the velocity above isn't an accident, and
it isn't magic either. It's the product of a few principles we hold to
hard:

- **Plan-first, spec-first.** Every sub-project starts as a brainstorm,
  becomes a design spec under `docs/superpowers/specs/`, becomes an
  implementation plan under `docs/superpowers/plans/`, and only then
  becomes code. The specs are committed before the implementation. If
  it isn't worth writing down, it isn't worth building.
- **Vertical slices, not horizontal phases.** Each slice crosses every
  layer (DB + service + API + UI) and ships end-to-end. Slice 1
  vibrating an iPhone is shippable; "phase 1 = all DB work" is not.
- **Evidence before assertions.** Nothing is "shipped" until it has been
  manually run end-to-end against a real cloud account and a screenshot
  or log line backs it up. The HANDOFF.md "verified" tags are not
  performative.
- **Wrap OSS, don't reinvent.** The Shasta open-source cloud + AI
  scanner sub-package is consumed today. The threat-intel and SBOM
  roadmap will pull in Trivy / Syft / OSV.dev / NVD when we ship M1.
  Where we *do* build custom detection (the 10 AI repo detectors,
  the AI SaaS sign-in catalog), it's because no upstream tool covers
  the AI-shaped problem cleanly. We add the platform layer
  (multi-tenant, unified findings, compliance crosswalk, surfaces) on
  top — that's the value.
- **Honest gotchas.** Every load-bearing decision that bit us in
  production is captured in [ARCHITECTURE.md](ARCHITECTURE.md) as an ADR.
  The Cognito federation subject-extraction bug, the EventBridge
  `source` filter that silently dropped management events, the LiteLLM
  Anthropic `response_format` quirk — all on record so the next
  engineer (human or AI) doesn't pay the same cost.

The codebase looks the way it does because we treated AI-augmented
development as engineering discipline, not as a shortcut.

---

## Try it

**The fastest path:** sign in at
[shasta.transilience.cloud](https://shasta.transilience.cloud) with a
Google or Microsoft account. You'll land on the onboarding flow and can
connect an AWS, Azure, GCP, or Entra tenant in a few minutes. Approval
is manual for now (the platform is in friends-and-family mode).

**For Transilience team members:** ping Team Transilience directly for
the test-tenant shortcut.

---

## Tech stack

| Layer | Stack |
|-------|-------|
| Infrastructure | AWS CDK (TypeScript), single-AWS-account multi-tenant |
| Backend | Python Lambda (managed + containerised), Aurora PostgreSQL 16 (Data API), EventBridge, SQS, ECR scanner images |
| Identity | Amazon Cognito with Google + per-tenant Microsoft federation |
| Frontend | Vite + React 18 + TypeScript + Tailwind CSS, S3 + CloudFront |
| iOS | SwiftUI (iOS 17+), WebRTC SPM, Cognito OAuth via ASWebAuthSession, APNs |
| Voice | WebRTC real-time data + audio, OpenAI / Gemini realtime models |
| LLM abstraction | LiteLLM (default: `claude-sonnet-4-6`; swappable per call) |
| OSS leverage | Shasta scanner sub-package (cloud + AI), with Trivy / Syft / OSV.dev / Semgrep / gitleaks landing via the M1 threat-intel roadmap |

---

## Run it locally

Shasta is a multi-tenant SaaS-shaped codebase. There is no
`docker-compose up` path today — running it end-to-end requires an AWS
account, a Cognito user pool, an Aurora Postgres cluster, an ECR
repository for scanner images, and Microsoft + Google OAuth app
registrations.

**If you want to try Shasta the easy way:** sign in at
[shasta.transilience.cloud](https://shasta.transilience.cloud).

**If you want to deploy your own copy:** the deployment commands live in
[CLAUDE.md §"Common commands"](CLAUDE.md). The current state of every
deployed surface lives in [HANDOFF.md](HANDOFF.md). A proper
"deploy your own Shasta" guide is on the [ROADMAP.md](ROADMAP.md) — not
today. You'll need to read the CDK stacks in `platform/lib/` and the
scanner images in `platform/lambda/shasta_runner_*/` to fill in the
gaps. Pull requests welcome once OSS opens up; until then, ping
Team Transilience.

---

## Repository layout

```
platform/        AWS CDK (TypeScript) + Lambda Python + Docker scanner images
  bin/           CDK app entry
  lib/           one stack per file (network, data, auth, ecr, static, events, scan, api)
  lambda/        one Lambda per directory; each has main.py + (optional) build.sh
  cfn/           customer-facing artifacts (aws-onboard.yaml, azure/onboard.sh, gcp/onboard.sh)
  sql/           Aurora schema migrations
  .env           ENTRA_*, GOOGLE_*, DOMAIN, APPROVAL_RECIPIENT (not checked in)

ios/             SwiftUI app, iOS 17+, WebRTC SPM dep, Cognito OAuth via ASWebAuthSession
  CISOCopilot/   Services, Views, RootView, App entry
  project.yml    xcodegen source — regenerate xcodeproj from this

web/             Vite + React + TS + Tailwind; deployed to S3 + CloudFront
  src/routes/    SignIn, Callback, PendingApproval, Welcome, ConnectClouds, Findings, AISummary, SOC, …
  src/lib/       cognito.ts (OAuth) + api.ts (HTTP)

docs/            Brainstorm specs + implementation plans (all sub-projects)
  superpowers/specs/    one spec per sub-project, YYYY-MM-DD-<topic>-design.md
  superpowers/plans/    one plan per sub-project, YYYY-MM-DD-<topic>-plan.md

HANDOFF.md       Current state of every deployed surface — read first
ARCHITECTURE.md  Design decisions and ADRs
ROADMAP.md       Where the OS extends next
BACKLOG.md       Open items, triage codes, decisions pending
TEST_PLAN.md     Current end-to-end manual test script
CLAUDE.md        Instructions for AI-augmented development in this repo
CISOBrief-v2.md  v2 PRD (the executable spec we build against)
CISOBrief.md     v1 PRD (Cloudflare-only KEV brief, retained for reference)
```

---

## Documentation index

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the load-bearing design
  decisions, ADRs, system overview.
- **[ROADMAP.md](ROADMAP.md)** — where Shasta extends next: SOC slices,
  M1–M7 heavy lifts, the OS-extension arenas (DSPM, CTEM, MDR, …), and
  the anti-roadmap (what we explicitly won't build).
- **[HANDOFF.md](HANDOFF.md)** — source of truth for what's live right
  now. Read this first every session.
- **[BACKLOG.md](BACKLOG.md)** — open items, triage codes, decisions
  pending.
- **[CISOBrief-v2.md](CISOBrief-v2.md)** — the v2 PRD / spec we build
  against. The contract.
- **[CLAUDE.md](CLAUDE.md)** — engineering conventions, common commands,
  things you must not do.

---

## License

[MIT](LICENSE) — software should be free. The repository is private
today; it goes public once the secrets audit completes (post-billing
module, see [ROADMAP.md](ROADMAP.md)). The MIT terms apply from day
one for any code Transilience or its team members run on their own.

## Contact

- **Team Transilience:** hello@transilience.ai — for general inquiries,
  test-tenant access, design-partner pilots, and commercial questions
- **Founder:** KK Mookhey · kkmookhey@transilience.ai ·
  [Transilience.ai](https://www.transilience.ai)
