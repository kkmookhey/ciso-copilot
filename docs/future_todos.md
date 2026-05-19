# Future TODOs — CISO Copilot

> Parking lot for capabilities to brainstorm into the v2+ roadmap.
> Not specced, not committed. Each item lives here until we sit down
> together and decide scope, slice, and order.
>
> Created: 2026-05-19. Companion to `CISOBrief-v2.md` (current PRD) and
> `HANDOFF.md` (current state). iOS items are stubbed at the bottom —
> KK and Claude will brainstorm those separately.

---

## 0. Foundational shift — interface

The web app's primary interface should become **chat + voice**, with
**dynamic dashboards** rendered in response to the conversation. Static
nav + fixed pages become the fallback, not the default.

- **Chat as the front door.** Sign-in lands on a chat surface, not a
  static dashboard. The user's first sentence routes them.
- **Voice on web.** Today voice lives on iOS via WebRTC. Extend the same
  Realtime client to the web surface. Reuse `~/Projects/shasta-ios-poc`
  reference, but in the browser (WebRTC is native there).
- **Dynamic dashboards.** Dashboards are *generated* per request, not
  pre-built. "Show me my top exposed S3 buckets in prod" returns a
  dashboard composed on the fly — filters, chart, table, action — and
  that dashboard is pinnable. Think Vercel v0 / Claude Artifacts, but
  for security telemetry.
- **"Vibe-chatted" reports.** "Make me a board-deck slide on this
  quarter's posture" → renders a slide-shaped artifact with charts and
  bullets sourced from the graph + findings + evidence packets.
- Open questions for brainstorm:
  - Where does the dashboard renderer live — server-rendered React, or
    client-side from a structured spec the LLM emits?
  - How do we keep dynamic dashboards consistent across sessions / users
    in the same tenant?
  - How do we cite (graph node IDs, evidence packet IDs) inside every
    rendered chart/number so the CISO can drill down to source?

---

## 1. Automated pen-testing

Wire **`github.com/transilienceai/communitytools`** in as a new scanner
type alongside the cloud + AI scanners.

- Reuse the Shasta sub-package pattern from `platform/lambda/shasta_runner_*`.
- New Lambda: `shasta_runner_pentest` (or equivalent), Docker image, ECR.
- Customer-scoped: pen tests run only against scopes the customer has
  explicitly authorised — onboarding flow needs an explicit
  authorisation artefact (signed scope-of-work).
- Findings flow through the same finding pipeline (deterministic
  detector → evidence packet → graph node → UI).
- Open questions:
  - Continuous vs scheduled vs on-demand?
  - Where does the human-in-the-loop sit for destructive / intrusive
    checks?
  - Rate-limit / blast-radius controls (reversibility invariant —
    HANDOFF.md §"Architecture decisions LOCKED").

## 2. Attack surface management (ASM)

Continuous discovery of the customer's external footprint — domains,
subdomains, IPs, certs, exposed services, leaked creds, shadow SaaS.

- New connector type: external-recon (no customer creds needed for the
  public-surface portion; needs creds for SaaS APIs).
- Feeds the graph as a new entity type: `external_asset`.
- Integrates with item §3 (threat intel) to flag assets that match
  active campaigns.
- Candidate open-source building blocks to evaluate (do NOT commit to
  any yet): Amass, Subfinder, httpx, naabu, nuclei. License + supply
  chain review required before adoption.

## 3. Threat intel feeds — tailored to tech stack

Today we have KEV. Expand to a curated feed pipeline that is
**customer-tech-stack-aware**.

- Stack inventory comes from the existing cloud scanners + AIBOM + ASM
  (§2). The customer's stack is already in the graph.
- Feed sources to brainstorm: KEV (have), EPSS, NVD, vendor advisories
  (AWS/Azure/GCP/GitHub/OpenAI/Anthropic), industry-specific ISACs,
  paid feeds (later).
- Output: per-tenant filtered stream — "this CVE matters to *you*
  because you run X version of Y in production accounts A, B."
- Open questions:
  - Update cadence per source (KEV daily, NVD continuous, vendor
    advisories webhook-driven).
  - Dedup + correlation across sources.
  - How this feeds the prioritisation engine (§5).

## 4. Attack path analysis

Graph-based "from where I am, how do I get to crown jewels" analysis,
across cloud + identity + AI surfaces.

- Reuses the Aurora-Postgres-as-graph decision (HANDOFF.md). Path
  queries via recursive CTEs initially; revisit if scale forces a
  dedicated graph DB.
- Inputs: IAM, network, identity, data-classification, secrets, AI
  blast-radius traces.
- Output: ranked paths to designated crown-jewel assets, with the
  smallest set of cuts that break each path.
- Open questions:
  - How does the customer designate crown jewels? Tagging convention,
    explicit UI step, or inferred (e.g. "anything tagged `pii=true`")?
  - Per-path evidence packet so the CISO can replay the reasoning.

## 5. Vulnerability / risk prioritisation

A single ranked risk register. Combines:

- KEV / EPSS / CVSS (raw severity)
- Exploitability in this customer's environment (reachable from the
  internet? blocked by SG/WAF? mitigated by IAM?)
- Business context (crown-jewel proximity from §4, asset criticality
  tags)
- Threat intel match (§3 — is this CVE being actively exploited
  against our customer's industry?)
- AI-specific risk (model provenance, prompt drift, autonomous-loop
  agents, MCP credential blast radius)

Output: one ranked list, with evidence packets. No black-box scores —
every risk shows the reasoning chain.

## 6. On-the-fly reports / dashboards from chat

Overlaps with §0, called out separately because it's the *killer demo*.

- "Make me a board-ready 1-pager on AWS posture for Q2."
- "Show me every place a developer can push code that lands in prod
  without review."
- "Compare our current MTTR on critical findings vs last quarter."
- The LLM emits a structured **dashboard/report spec** (JSON), the
  renderer turns it into HTML/PDF/PNG. Spec is the artifact — it can
  be pinned, scheduled, shared.
- Every number is clickable → drills into evidence packet / graph
  node / source finding.

## 7. CI/CD and container security

New scanner family targeting the **build & deploy** surface, not the
runtime cloud surface.

- CI/CD checks: GitHub Actions misconfigs, OIDC trust policy mistakes,
  secrets in workflow files, untrusted actions, branch protection gaps,
  PR-without-review merges. (We already have a GitHub connector — extend
  it.)
- Container checks: image vuln scan, SBOM extraction, base-image
  freshness, root-user containers, capability/privilege escalation,
  signed-image enforcement.
- Candidate OSS to evaluate (NOT yet committed): Trivy, Grype, Syft,
  Checkov, Kubescape, OWASP Dependency-Track, Falco (runtime — later).
- Same Shasta sub-package pattern; same finding pipeline.
- Open questions:
  - Scan in-CI (PR-time gate) vs scan-from-registry (continuous)?
    Probably both, different latencies.
  - How do CI/CD findings cross-link with cloud findings? (e.g.
    "this deployed image has CVE-X, and it's running in account Y
    on EC2 i-Z.")

---

## 8. iOS — separate brainstorm

Not in this document. KK + Claude will sit down separately and decide
what's worth shipping on iOS vs web. Heuristic to start that
conversation: **iOS is for "tell me / ask me / approve" moments**;
**web is for "show me / build me / dig in" moments**.

Open questions to seed that session:
- Which of §1–§7 has an iOS surface, vs web-only?
- Voice-first triage on iOS (already partly there) — what does the
  "ask a question, get an answer + action" loop look like for ASM /
  attack paths / pen-test results?
- Push notifications: which finding classes wake the CISO up?

---

## Notes & invariants to respect when we brainstorm

Pulled from the locked architecture decisions in `HANDOFF.md`:

- Determinism is the spine. AI is the surface. LLMs never write to
  the graph or declare violations directly.
- Every conclusion carries an evidence packet.
- Reversibility is non-negotiable for any action against customer
  environments — until evidence-packet + policy framework is in,
  these features are read-only.
- One model version, pinned, in one config value.
- Shasta sub-package pattern for scanners — no rewrites to TS.
- iOS / web never call upstream sources directly. API Gateway only.
