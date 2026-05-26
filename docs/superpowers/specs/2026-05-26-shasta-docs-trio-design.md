# Shasta docs trio + branding pass — design

> Brainstorm checkpoint, 2026-05-26. The detour-before-SOC-Slice-2: produce
> a production-grade docs trio for "Shasta by Transilience" and apply a
> "Shasta by Transilience" brand pass across the platform. Goal: invite the
> Transilience team and the larger services team to play with the product
> as a cohesive lead magnet, not as an unfinished side project.

## Status

Brainstorm done. Awaiting KK approval on this spec before producing the
three doc files. The branding pass is captured here at architecture level
only — its own brainstorm + spec lands after docs ship.

## Strategic frame

**Product positioning**: "Shasta by Transilience" is the canonical brand
pairing. Transilience's umbrella tagline is **"Full Stack Security OS"** —
one platform that eventually sees every security, privacy, and safety
signal a CISO cares about. Shasta is the first manifestation of that OS,
shipped today as cloud + AI + SOC + compliance. The OS extends later into
DSPM, CTEM, Cloud MDR, compliance wizards, privacy posture, and safety
posture.

**Audience hierarchy for the docs**:
1. Transilience commercial-product team (today) — sophisticated, will
   scrutinise the depth, needs to see this is cohesive product strategy
2. KK's larger services team (this week) — security practitioners, broader
   range of technical depth
3. Friends-and-family design-partner pilots (post-billing) — CISO-level
   evaluators
4. OSS readers + customers (post-secrets-audit + license decision) —
   variable backgrounds

The docs target audience #1 + #2 today. They are written to be useful for
#3 + #4 once the OSS / GA milestones land, but no extra effort is spent
chasing those audiences in this pass.

## The docs trio

Three files, each with one job. Living under the repo root so they're the
first thing a reader sees on GitHub.

### 1. `README.md` — the lead magnet (target ~400–600 lines)

| Section | Job | Notes |
|---------|-----|-------|
| Hero | Hook + live link | Logo, "Shasta by Transilience — the Full Stack Security OS", 90-char one-liner, link to `shasta.transilience.cloud` |
| The thesis | Why one OS | ~150 words on the fragmentation problem (Wiz + Sentinel + Defender + Drata + Snyk + …) and what "one OS" actually means: unified findings model, shared compliance crosswalk, shared identity graph, shared evidence packets, shared front door |
| What Shasta covers today | Four-quadrant feature map | Cloud security / AI security / SOC / Compliance — with sub-bullets per quadrant pointing at the actual shipped scanners + sub-projects |
| The four surfaces | Web / iOS / voice / chat | Table with "what each surface is best at" — e.g. iOS is the alerting+handoff companion, voice is the hands-free walk-through, web is the analyst console, chat is the question-answer surface |
| Sub-projects shipped | Timeline table | Subtle date-stamped table of shipped modules. Lets velocity speak without saying it. ~10–12 rows. |
| How this was built | Light principles section | ~150 words. Acknowledges AI-augmented development (Claude Code + Anthropic SDK) without dwelling. Key principles: plan-first / spec-first workflow, vertical slices over horizontal phases, test-driven where possible, evidence-before-completion, subagent-driven implementation for parallel work, OSS leverage (Shasta + Whitney + Trivy + Semgrep) over reinvention. Sells *how* we ship velocity responsibly, not *that* we shipped fast. |
| Live URLs + try it | Where to actually look | `shasta.transilience.cloud` + Cognito sign-in (Google / Microsoft federation) |
| Tech stack | One-line-per-layer summary | AWS CDK + Lambda Python + Aurora Postgres + Cognito + EventBridge + SQS + ECR + Vite/React/TS + SwiftUI + WebRTC + LiteLLM |
| Run it locally | Honest pointer | "This is a multi-tenant SaaS-shaped codebase; full self-host guide is roadmap. Today's path: live URL above, or for the curious — point at HANDOFF.md + CLAUDE.md for the deployment commands. Full self-host docs land when there's time." |
| Links | Cross-refs | ARCHITECTURE.md, ROADMAP.md, HANDOFF.md (state), BACKLOG.md (open items) |
| License + contact | Boilerplate | Proprietary today; OSS license decision in flight |

**Tone**: confident product voice, evidence-first (shipped dates +
scan-count claims), no marketing hyperbole. The shipped-modules table
conveys velocity implicitly. The "How this was built" section
acknowledges AI-augmented development briefly and shifts focus to the
*principles* that made the velocity responsible (plan-first, vertical
slices, test-driven, OSS leverage) — not boastful, not breathless,
just owning the narrative.

### 2. `ARCHITECTURE.md` — the engineering depth (target ~800–1200 lines)

| Section | Job |
|---------|-----|
| System overview | ASCII / mermaid diagram of the layered architecture (data → service → API → UI, plus the four surfaces) |
| The unified findings model | Schema sketch + the "AI is a lens, not a silo" decision. Why every finding carries `evidence_packet` + `frameworks` JSONB + ATT&CK technique + check_id |
| The four cloud connectors | AWS / Azure / GCP / Entra. Per-cloud onboarding flow (CloudFormation / `az` / `gcloud` / Microsoft consent). Scanner image pattern (ECR `:latest`, hot-swap deploys) |
| The CME-v2 two-stage pipeline | Normalize → Augment, registry schema, ~65 rewrite rules + 13 augment rules. Why this beats the old single-pass model |
| The SOC pipeline | EventBridge → bus → router (dedupe + classify) → SQS → enrichment Lambda (LiteLLM → Sonnet) → AI fields written back. TI substrate (5,726 IOCs across 4 sources). Per-tenant rate limit + spend cap |
| Identity + auth | Cognito federation (Google + Microsoft). Subject-extraction gotcha. Multi-tenant IdP routing via `/auth/discover-tenant` |
| The four surfaces — implementation notes | Web (Vite/React/TS, S3+CloudFront), iOS (SwiftUI + WebRTC), voice (WebRTC not WebSocket — AEC story), chat (Lambda Web Adapter for streaming) |
| Design Decisions (ADRs) | 15 numbered ADRs (see list below) |
| Operational concerns | Cost attribution (today's gaps), observability, security boundaries (API Gateway as rate-limit + key-protection boundary) |

**ADRs to capture** (15):

1. Shasta as a sub-package (read-only), not a fork
2. Single AWS account multi-tenant (not per-tenant accounts)
3. Cognito federation + subject extraction (the gotcha)
4. EventBridge mgmt-event filter pattern (never on `source`)
5. Two-stage CME pipeline (normalize → augment)
6. AI is a lens, not a silo
7. WebRTC for voice (not WebSocket)
8. LiteLLM abstraction for model swap
9. Lambda Web Adapter for streaming
10. AWS Config essentials (not all-resources)
11. Per-tenant rate limit + spend cap from day 1
12. ECR-stored scanner images with `:latest` tag
13. Wrap OSS (Shasta / Whitney / Trivy / Semgrep), don't reinvent
14. Integrations via MCP, not bespoke API clients
15. Don't lean on Azure Sentinel

### 3. `ROADMAP.md` — where the OS extends (target ~300–500 lines)

| Section | Job |
|---------|-----|
| The vision | Full Stack Security OS. One platform, every signal. Today's surface is cloud + AI + SOC + compliance. Tomorrow's is DSPM + CTEM + MDR + compliance wizards + privacy + safety. |
| Near-term (next 6 weeks) | Branding + capability gating + billing module (this detour). SOC Slice 2 (identity drift). UX polish (framework drill-down, Entra hint cleanup) |
| Q3 2026 | SOC Slice 3 (anomaly baseline), AWS uplift v2 verification, Azure scanner uplift completion |
| Q4 2026 | GCP scanner uplift, Azure no-Sentinel SOC, GCP SOC |
| Heavy-lift projects (M1–M7) | Lifted forward-looking from BACKLOG.md §M. Each gets a one-paragraph section: shape, what it unlocks, sequencing. |
| Future arenas (OS extension) | DSPM, CTEM, Cloud MDR, Compliance Wizard (M6), Privacy posture, Safety posture (AI red-teaming hooks?) |
| What we won't build | Anti-roadmap. Inline traffic / browser extension / endpoint agent (wrong product shape). Sentinel ingestion (customer cost). Per-person policy enforcement (we report, don't enforce). |

## Shipped-modules timeline table (draft for the README)

The implicit-velocity story, told in dates:

| Date | Module | Status |
|------|--------|--------|
| 2026-05-16 | v1 KEV Brief (Cloudflare Worker) | Sunset |
| 2026-05-18 | v2 platform foundation (AWS CDK, Aurora, Cognito) | Shipped |
| 2026-05-19 | SP4 chat-first front door | Shipped |
| 2026-05-20 | AI Discovery cloud-AI connector | Shipped |
| 2026-05-21 | Findings overhaul (Fail/Partial/Pass + grouping) | Shipped |
| 2026-05-22 | AI Visibility v2 Slice 1 (Azure-AI + /ai view) | Shipped |
| 2026-05-23 | AI Visibility v2 Slice 2 (Entra AI sign-in) | Shipped |
| 2026-05-24 | AI Visibility v2 Slice 2.1 (Entra licensing banner) | Shipped |
| 2026-05-25 | Compliance Mapping Engine v2 (8 frameworks) | Shipped |
| 2026-05-25 | SOC Slice 1 (AWS Config drift + AI enrichment) | Shipped |
| 2026-05-26 | SOC Slice 1c (TI substrate, 5,726 IOCs) | Shipped |
| Next | "Shasta by Transilience" branding + capability gating + billing | In progress |
| Next | SOC Slice 2 (identity drift) | Planned |

## Branding pass (architecture-level only; own spec to follow)

This is captured here only to make the docs reference it correctly. The
branding pass gets its own brainstorm + spec after the docs ship.

**Scope (what we know today)**:
- Brand pairing: "Shasta by Transilience" on every page header
- Logo asset: TBD (KK to confirm source); placeholder text-mark acceptable
  for the first pass
- Header bar component across all web routes (today only `ConnectClouds`,
  `TopRisks`, `AISummary`, etc. have inconsistent headers)
- Login + Callback + PendingApproval pages get the full hero treatment
- Tailwind brand tokens: needs decision on colors + typography
- iOS app icon + splash screen: SwiftUI asset catalog update

**Open questions for the branding brainstorm**:
- Logo source (does KK have a designed mark or text-only acceptable?)
- Color palette (current Tailwind defaults vs Transilience brand colors)
- Header pattern (top bar with logo + nav + user menu? Sidebar? Both?)
- Do existing route-specific titles (e.g. "AI Exposure") stay or get
  normalised under one branded shell?

## Out of scope for this session

- Capability gating (tier-based feature locks) — depends on branding shell
  + billing
- Billing module (token + cloud cost metering, Stripe integration) — 4–6
  week sub-project
- Secrets audit + OSS license decision — prerequisite for public OSS
  release, not for team share
- iOS App Store submission — prerequisite for GA, not for team share
- Customer-facing error surfaces (today CloudWatch only)
- TOS / Privacy Policy / DPA template — prerequisite for GA

These are all explicitly future work. The session today produces docs +
branding only; everything above lands later.

## Acceptance criteria

- [ ] `README.md`, `ARCHITECTURE.md`, `ROADMAP.md` exist at repo root
- [ ] README links to ARCHITECTURE + ROADMAP + HANDOFF + BACKLOG
- [ ] README leads with "Full Stack Security OS" framing
- [ ] README contains the shipped-modules timeline table
- [ ] README has an honest "Run it locally" pointer (no fake instructions)
- [ ] ARCHITECTURE captures the 15 ADRs
- [ ] ROADMAP captures the M1–M7 heavy-lift projects + the OS-extension
      arenas + the anti-roadmap
- [ ] Tone is evidence-first, no marketing hyperbole; "How this was
      built" section is light (~150 words) and principle-focused
- [ ] All three files are committed to git
- [ ] Screenshots are deferred to after the branding pass (so they show
      the branded UI)

## Sequencing for the rest of the session

1. KK approves this spec
2. Write `README.md` → review → commit
3. Write `ARCHITECTURE.md` → review → commit
4. Write `ROADMAP.md` → review → commit
5. Brainstorm + spec the branding pass (logo, palette, header pattern)
6. Implement branding pass → deploy → verify
7. Capture screenshots from the branded UI → drop into README → commit
8. Tell KK we're ready for the team share
