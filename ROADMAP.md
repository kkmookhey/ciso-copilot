# Roadmap

> Where Shasta by Transilience extends next. This is forward-looking;
> see [HANDOFF.md](HANDOFF.md) for what's shipped and
> [BACKLOG.md](BACKLOG.md) for open items + triage.
>
> Last updated: 2026-06-03.

## The vision

**Shasta is the Full Stack Security OS.** Today's surface is cloud
security + AI security + SOC + compliance. Tomorrow's surface extends
into DSPM, CTEM, Cloud MDR, compliance wizards, privacy posture, and
safety posture — all on the same unified platform, the same findings
model, the same compliance crosswalk, the same identity graph, the
same front door.

The strategic order is set:

1. **Today** — rock-solid cloud + AI + SOC + compliance.
2. **Next** — internal team beta, then friends-and-family pilots with
   billing live.
3. **Then** — public GA with a documented self-host path, OSS license
   decision, and the full set of customer-facing legal artefacts.
4. **From there** — extend the OS arena by arena, one heavy-lift
   sub-project at a time.

We do not chase breadth before the core is rock-solid. Every new arena
that lands must inherit the unified findings model, the compliance
crosswalk, and the surface portability that makes Shasta worth
choosing over fragmented point tools.

> Phases are velocity-driven, not calendar-driven. We will deliver
> faster than a calendar would predict. Phase order is what's locked;
> the dates land when they land.

---

## Phase 1 — Team-ready

**End-state:** Transilience team and the larger services team can
clone the repo, read the docs, and use the platform end-to-end as a
cohesive product.

- Docs trio: README + ARCHITECTURE + ROADMAP (this session)
- "Shasta by Transilience" branding pass — logo + header bar across
  every web route + login hero + Tailwind brand tokens + iOS app icon
  + splash screen
- Screenshots of the branded UI added to the README
- Internal beta announcement to the Transilience team

---

## Phase 2 — Commerce-ready

**End-state:** Friends-and-family design-partner pilots can run with
billing live. Sponsored credit covers the wow moment; customers pay
for usage beyond it.

- Capability gating (free vs paid tiers, server-side enforcement,
  per-tenant tier in the `tenants` table, feature flag map, graceful
  upgrade UX)
- Billing module — sub-phases:
  1. Internal usage tracking (tokens per tenant, compute per tenant,
     Aurora storage proportional)
  2. Customer-visible usage dashboard (no charging yet)
  3. Hard caps + warnings (soft 80%, hard 100% of $50 sponsored credit)
  4. Stripe + credit top-ups (charging live; credits priced at 3× COGS)
- UX polish: framework drill-down on `/ai` tiles + Entra licensing
  hint cleanup
- ✅ Secrets / hardcoded-ARN audit + repo MIT-public (shipped 2026-05-27,
  PRs #26 + #30 + #31)
- ✅ MCP Connectors — per-user OAuth (Slack-as-MCP) + autonomous
  CRITICAL → Slack broadcast across every scanner (shipped 2026-05-30 →
  2026-06-03, PRs #33 + #35–#39 + #41 + #43). Foundation for the
  M5 action-layer roadmap.

---

## Phase 3 — SOC depth + cross-cloud parity

**End-state:** Shasta's SOC story matches the cloud-posture story in
depth, across all three clouds. The unified-OS claim ("AWS-equivalent
on Azure and GCP") is true everywhere it's claimed.

### SOC Slice 2 — Identity drift

AWS IAM CloudTrail events (`AttachUserPolicy`, `CreateAccessKey`,
`UpdateAssumeRolePolicy`, OAuth consent, conditional access changes)
flow through the existing event router with identity-specific severity
classification. New `soc_entra_poller` Lambda polls Microsoft Graph
`auditLogs/directoryAudits` + `auditLogs/signIns` on a 5-min cadence.
TI enrichment (from Slice 1c) fires automatically on identity events.

**Product wedge:** "New admin role assigned at 3am" demo moment.

**Spec section:** `2026-05-25-ai-powered-soc-design.md` §3 (Slice 2),
§10.2.

### SOC Slice 3 — Anomaly baseline activation

Statistical features fire on a 30d rolling window from `events`. After
~7d of per-tenant observation, the AI enrichment prompt gets richer
baseline summaries (per-actor typical hours, typical resources
touched, typical action set). Enables the "first time anyone has
touched IAM at 3am" narrative.

**Why after Slice 2:** needs Slice 1c + Slice 2 worth of event volume
in `events` to actually have a baseline.

### AWS scanner uplift v2 — final E2E verification

The AWS uplift work shipped through Slices 0+1+Scan Execution v2 on
the `feat/aws-scanner-uplift` branch. This phase closes the loop on
E2E verification (Quick / Medium / Deep tier comparison against a
known ground-truth account), promotes the branch to `main`, and
unblocks the Azure and GCP mirror work.

### Azure scanner uplift — completion

Three parallel uplift slices mirroring AWS. The shape is already
documented at `docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md`.

### GCP scanner uplift

Same pattern as AWS / Azure. Plus the GCP-AI cloud pass (currently
out-of-scope from AI Visibility v2 §3 since Shasta has no `gcp/ai_*`
upstream today — we build it in-tree, see ADR-001 caveats).

### Daily brief generator

Nightly Anthropic-driven prose Lambda. Emits a 200-word prose summary
per tenant: what changed today across cloud + AI + SOC + compliance,
what shifted in their risk posture, what the next action should be.
Delivered via email + APNs push to the iOS companion app.

**Why here:** natural precursor to the M7 board deck generator (same
architecture, smaller scale).

---

## Phase 4 — GA-ready

**End-state:** Shasta is open to anyone with a Google or Microsoft
account. Public website, App Store iOS app, MIT-licensed repo public,
status page, on-call rotation, customer-facing error surfaces.

### Cross-cloud SOC pipelines

- **Azure SOC (no Sentinel)** — Activity Log → Diagnostic Settings →
  Event Hub → Lambda consumer + Azure Policy state + Resource Graph
  change feed + Defender (when on). **NEVER Sentinel.** Spec section:
  `2026-05-25-ai-powered-soc-design.md` §10.3.
- **GCP SOC** — Cloud Asset Inventory feed → Pub/Sub + Cloud Audit
  Logs → log sink + SCC findings (when on). Spec section:
  `2026-05-25-ai-powered-soc-design.md` §10.4.

### iOS App Store submission

Move from dev provisioning to App Store. ~1-3 week App Review cycle.
Needs marketing screenshots, app icon (post-Phase 1 branding),
privacy nutrition labels, demo account. Customer-installable from any
iPhone.

### Public GA milestones (parallel)

- TOS / Privacy Policy / DPA template
- Customer-facing error surfaces (request-ID propagation, friendly
  error pages)
- Status page (statuspage.io or equivalent)
- On-call rotation (PagerDuty)
- "Deploy your own Shasta" guide (the self-host story for MIT users)
- MIT-licensed repo flips public

---

## Heavy-lift projects (M1–M7)

Seven project-shaped capabilities. Each needs its own brainstorm → spec
→ plan → code → review → test cycle. Loose sequencing follows; exact
order shifts with customer feedback.

### M1. Threat intel — cloud + code context × SBOM × NVD

**Shape (v1).** Reuse a battle-tested OSS SBOM generator (Syft,
cdxgen, or Trivy SBOM); subscribe to NVD CVE + GHSA + OSV.dev;
prioritise with CISA KEV + EPSS. Emit findings via `vuln-*` check_id
family so they ride the unified writer + framework registry.

**What it unlocks.** Reachability-aware vulnerability prioritisation
across customer code + container images + cloud resources. The
"is CVE-X exploitable in *our* environment" answer.

**Sequencing.** Builds on the OSS-leverage thesis (ADR-013). Distinct
from AI-security scanning. Lands after Phase 4 (GA-ready).

**Open questions.** SBOM scope (code only, or images / runtimes /
Kubernetes too)? Per-tenant private feeds or free-tier? Reachability
analysis depth?

### M2. MCP Security — auditing AI-agent attack surface

**Shape (v1).** Two layers: (a) MCP discovery via CloudTrail + traffic
logs + the M5 connectors; (b) risk scoring against an OWASP Agentic
Top 10-derived rubric. Emit `mcp-*` check_ids.

**What it unlocks.** A separate sales motion: "we audit your AI agent
risk surface." Distinct from M5 (M5 = we consume MCP; M2 = we audit
the customer's MCP surface).

**Sequencing.** Reuses M5's MCP harness. Build M5 first; M2 reuses
the substrate.

**Open questions.** Customer-operated vs customer-consumed MCP
servers? Detection telemetry source (no inline agent)? Catalog of
known-risky MCP servers.

### M3. Azure / GCP scanner parity with AWS (incl. AI coverage)

**Shape (v1).** Three parallel uplift slices mirroring the AWS uplift
pattern. Already partially in flight per the AWS uplift branch.

**What it unlocks.** Cross-cloud parity — load-bearing for the
"unified Full Stack Security OS" claim.

**Sequencing.** Lands inside Phase 3 (SOC depth + cross-cloud parity).
AWS uplift verification first, Azure and GCP follow the same pattern.

### M4. Dynamic chat-driven dashboards

**Shape (v1).** Pattern A: catalog of ~15-20 pre-templated dashboard
intents; LLM picks intent + params, backend returns typed chart spec.
Pattern B (later): freeform text-to-SQL-to-chart, gated by read-only
role + denylist.

**What it unlocks.** "Show me Bedrock spend by team this month" works
inline in chat. Extends SP4 voice tools (`navigate_to`,
`filter_findings_view`). Lives in the chat-first surface.

**Sequencing.** Build daily brief (Phase 3) first to validate the
chat-tool pattern. M4 lands after Phase 4.

**Open questions.** Inline-in-chat / new tab / saved dashboard?
Recharts vs Tremor / Visx?

### M5. MCP-based action layer (M365 / Slack / JIRA integrations + intelligence)

**Shape (v1).** Three parts: (a) MCP client harness over
customer-approved OAuth; (b) action proposer (drafts Slack messages,
JIRA tickets from findings + scan deltas); (c) approval gate —
drafted, not auto-executed.

**What it unlocks.** "We don't just tell you — we draft the JIRA
ticket and the Slack message; you click approve." High-leverage
sales-engineering signal.

**Sequencing.** Lands after Phase 4 — alongside M2 since they share
the MCP substrate. The "intelligence" half (deciding what to draft)
is where differentiation lives.

**Open questions.** Slack + JIRA first? Always-draft or opt-in
auto-send? Audit-trail table.

### M6. Compliance Wizard for non-security users

**Shape (v1).** Guided journey UI (linear or branching) per framework
— coverage → gaps → policy generation → evidence-collection plan →
readiness %. New `compliance_journeys` table per tenant + framework.

**What it unlocks.** A direct shot at Drata / Vanta territory. Sits on
findings + policies + questionnaires + trust center. CME-v2 is the
data substrate.

**Sequencing.** Strategy doc first, not a brainstorm. Positioning vs
Drata / Vanta needs to settle before architecture. Lands after
Phase 4.

**Open questions.** Launch order (SOC 2 first)? Auditor-in-the-loop
partnership or pure self-serve? Differentiation pitch.

### M7. Board deck generator

**Shape (v1).** Tenant-context → structured-content (Anthropic API
with `tool_use` for JSON-schema-conformant slide content) →
template-render (`python-pptx`).

**What it unlocks.** Quarterly board deck written in 5 minutes by
the platform, reviewed in 30 minutes by the CISO, instead of 3 days
in Keynote. Daily brief is the same architecture at smaller scale.

**Sequencing.** Ship daily brief first (Phase 3); M7 extends
linearly. Lands after M4 (shared prose-to-structured-render pattern).

**Open questions.** PPTX first, then Google Slides / Notion? Generic
template or customer-uploaded? One-shot or monthly cron?

### Cross-project observations

- **M2 + M5 share the MCP substrate.** Build M5's client harness
  first; M2 reuses it for discovery telemetry.
- **M4 + M7 share the prose-to-structured-render pattern.** M4 first
  → faster feedback signal.
- **M1 + M6 are both compliance-adjacent moats.** M1 unlocks M6's
  depth.
- **M3 unblocks cross-cloud parity** — load-bearing for the
  unified-OS claim.

---

## Future arenas — the OS extension

Once the cloud + AI + SOC + compliance core is rock-solid (and M1-M7
have shipped), Shasta extends the OS into adjacent security domains.
Loose order; customer demand sets the actual sequence.

### DSPM — Data Security Posture Management

Discover data stores across cloud + SaaS, classify the data (PII /
PHI / PCI / IP), map data flow, alert on misconfiguration. Inherits
the unified findings model, the compliance crosswalk, the identity
graph (data → owner).

### CTEM — Continuous Threat Exposure Management

Per-asset, per-finding, exploitability + reachability + business
context → continuously-prioritised risk score. Sits on M1 (threat
intel) and M3 (cross-cloud parity).

### Cloud MDR — Managed Detection and Response

Shasta-managed 24/7 SOC service layer on top of the AI-powered SOC
pipeline. Customer pays for response time + escalation; we provide
the human-in-the-loop layer. Requires the on-call rotation + runbooks
from Phase 4 (GA-ready).

### Compliance Wizard (M6, listed above)

Repeated here as an OS-arena because it crosses into "compliance as a
product line" once M6 ships. The wizard generates policies, evidence
plans, and audit-ready exports.

### Privacy posture

Data subject rights workflows (DSAR / RoPA / DPIA). Inherits the
identity graph and data classification from DSPM. GDPR / CPRA /
state-level US privacy laws.

### Safety posture

AI red-teaming hooks, prompt-injection telemetry, hallucination
monitoring, jailbreak detection. Differentiated by integration with
the AI workload discovery + AI code scanner already shipped.

---

## What we won't build (anti-roadmap)

This section is as important as what we will build. We're explicit
about no's so the team doesn't relitigate them.

### Inline traffic / browser extension / endpoint agent

Wrong product shape. Shasta is a control-plane product: we read from
cloud APIs and identity providers, we don't terminate user traffic or
sit on the endpoint. An endpoint agent is a fundamentally different
operations, distribution, and trust story.

### Azure Sentinel ingestion

Prohibitive customer cost ($2-10/GB ingested; mid-tier customers see
$10K+/mo). Customers on Sentinel can continue to use it alongside us;
we don't replace it, we don't depend on it. Same logic applies to
Google Chronicle.

### Per-person policy enforcement

Shasta reports — does not enforce. "Block ChatGPT for user X" is the
job of an identity provider's conditional access or a CASB; not us.
This is a deliberate scope boundary, not a capability gap.

### M365 Copilot ingest

Deferred unless a prospect explicitly asks. The Entra sign-in pass
already surfaces who is using which AI services; the deeper Copilot
telemetry adds complexity disproportionate to the differentiation gain
at our current scale.

### Per-tenant AWS account isolation

Single-account multi-tenant is the right tradeoff for our customer
size and operations team (ADR-002). If a customer requires
single-tenant isolation for compliance reasons in the future, that's a
separate CDK app, not a refactor of the main platform.

### Forking Shasta or any upstream OSS

We wrap, we don't fork. See ADR-013. A fork gives us all the
maintenance burden with none of the upstream support. Where we need
fixes, we file upstream and work around in-place.

### Native vulnerability / SAST engines

We will wrap Trivy / Semgrep / gitleaks / OSV.dev (M1 threat-intel
roadmap). We do not build our own detection engines for commoditised
problems. Detection-engine work is a 10-engineer team in someone
else's company; we are not that company. Where we *do* build custom
detection (the 9 AI repo detectors in `ai_scanner/`), it's because
no upstream tool covers the AI-shaped problem yet.

### Mobile-first feature parity for iOS

iOS is a companion app (alerting + handoff + voice), not a full
analyst console. See `project_ios_companion_vision` memory. We will
not port every web feature to iOS.

---

*Roadmap evolves with customer feedback. Update this file when
sequencing changes, but the vision section is stable.*
