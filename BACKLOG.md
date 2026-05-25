# Backlog — items to vet

> Synthesized 2026-05-24, pruned 2026-05-25 after CME-v2 shipped (PR #20)
> and the NVD-key concern was dismissed as a false alarm.
>
> **How to read**: each section is grouped by what KK has to *do* with the
> item, not by component. Triage codes: **[KEEP]** = clear must-do,
> **[DECIDE]** = needs a call, **[DROP?]** = candidate to delete, **[DOC]** =
> doc hygiene only.

---

## A. Done since this file was first written (kept here as a paper trail)

- ✅ **`AISummary.tsx` deploy + stale "coming in S2" caption** — shipped in PR #20 (2026-05-25). Family-grouped tiles + mapping disclaimer also landed.
- ✅ **Slice 3 spec blockers** — all four (PCI/Bedrock writer, AML.T0028 taxonomy, Entra commit path, compliance defensibility) folded into CME-v2 D-1..D-10 and shipped in PRs #17–#20.
- ✅ **Slice 3 review concerns** — EU AI Act Art. 4 corrected to Art. 9/26 hooks; firing-rule IDs persisted as `_registry_rule_ids` per finding; per-rule observability via `registry_apply_summary` CloudWatch counter; FrameworkTile + framework chips on `/findings` carry the "Mapping only" tooltip; ISO 42001 / NIST AI RMF IDs source-verified against published standards in `ai_framework_registry.json`.
- ✅ **NVD API key in repo** — false alarm. `workers/src/cron/nvd.ts` reads `env.NVD_API_KEY` via Cloudflare Workers secret binding; `.dev.vars` is gitignored and not present locally; `wrangler.toml` carries no value. v1 Workers stack is sunset; v2 platform doesn't ship NVD yet.

## B. Spec reviews still pending KK sign-off

- **[DECIDE]** Entra Free-Tier Licensing Banner / Slice 2.1 (`docs/superpowers/specs/2026-05-24-entra-licensing-banner-design.md`) — Slice 2.1 banner code shipped 2026-05-23 (commit `890a15f`). Decide whether to retro-mark this spec as shipped or sweep into archive. *(Spec content overtaken by implementation.)*

## C. Slice 3 follow-ups still genuinely open (didn't make CME-v2)

- [ ] **Pre-author the `--backfill-tenant <id>` script** — saves improvising under audit pressure. Aurora storage of historical findings is append-only post-#41 anyway; the script would re-apply the current registry to existing rows.
- [ ] **Pin the `frameworks` JSONB shape contract** in the CME-v2 spec (sorted, deduped, list[str] of canonical IDs) — currently enforced by code but not documented as a guarantee.
- [ ] **Broader "mapping not attestation" product copy** — tooltip lives on tiles + chips; sweep the rest of the product (PDF reports, Trust page, /compliance headers) before customer-facing quotes leak into auditor packets.

## D. Live bugs / mysteries

- ✅ **`findings` table inconsistency** *(shipped 2026-05-25, scans_status hotswap)*: Root cause — `unified_writer._insert_finding`'s `ON CONFLICT ... DO UPDATE SET scan_id=EXCLUDED.scan_id` reassigns each finding row to the most-recent emitting scan, so `count(*) FROM findings WHERE scan_id=X` undercounts every superseded scan. `scans_status` now reads `scans.stats.findings` (the authoritative count the scanner stamps at completion) and falls back to the live count only when stats is unwritten (running scan). Docstring warnings added in all four `unified_writer.py` variants. The §17.1 Findings History sub-project remains the proper full fix for per-scan history. 4 new unit tests / 70 scanner_core tests still pass.
- [ ] **Quick tier silently skips AI pass** *(hit twice in one day — AWS + Azure)*: UI shows `/ai = 0` with no explanation. Fix (a) slim AI pass on quick, or (b) UI hint when latest scan is quick.
- [ ] **STS credentials expire mid-scan on medium tier multi-region** — scan `f749cb31` failed 2026-05-21. Bump role's `max-session-duration` + re-assume with `--duration-seconds`, or split medium into per-region chunks under a fresh session each.

## E. UX gaps logged in this session (2026-05-25)

- ✅ **Drill-down on framework tiles** *(shipped 2026-05-25)*: `/ai` `FrameworkTile` now navigates to `/findings?framework=<key>` on click; source-doc link demoted to a small ↗ icon. Tooltip preserved.
- ✅ **Redundant Entra ID P1/P2 hint** *(shipped 2026-05-25)*: `AISummary.tsx` empty-state trimmed to "No identifiable AI users yet — connect Entra to populate." with a `/connect` cross-link. P1/P2 detail lives only in the Slice 2.1 banner now.

## F. HANDOFF.md hygiene

`HANDOFF.md §"Open items"` (line 1656) and `§"Deferred follow-ups"` (line 1674) haven't been touched since 2026-05-18.

- **[DOC]** Reconcile §"Cleanup state in DB" (line 1687) — snapshot bears no resemblance to today's tenant set (4 tenants now incl. `transilienceai.com`, `rkreddy@gmail.com`, `gmail.com`, `transilience.ai`).
- **[DOC]** Mark SES production access as ✅ done (line 1707, 1723 confirm granted).
- **[DOC]** Verify CORS `gatewayResponses` config shipped in 2026-05-18 push.

## G. Deferred follow-ups still genuinely open

From `HANDOFF.md §"Deferred follow-ups"`:

- **[KEEP]** **iOS Policies + Questionnaires + Trust views** — backend ready since 2026-05-18; iOS UI not built. The `project_ios_companion_vision.md` memory rethinks iOS as a lightweight alerting + team-handoff companion; KK may cut these from the port.
- **[KEEP]** **Daily brief generation** — nightly Anthropic-driven prose Lambda. Not started. Shipping this first is the natural pre-cursor to M7 (board deck generator).
- **[KEEP]** **APNs push end-to-end test** — needed for the iOS companion model.

## H. v2 out-of-scope items still to triage

From `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md §3`:

- **[DECIDE]** **GCP-AI cloud pass** — status of the sub-project?
- **[DECIDE]** **MCP discovery** — see §M.M2 below.
- **[DROP?]** Inline traffic / browser extension / endpoint agent — explicitly "wrong product shape." Confirm dropped.
- **[DECIDE]** **M365 Copilot ingest** — deferred unless prospect asks. Has anyone asked?
- **[DECIDE]** **Google Workspace audit logs** — same pattern as Entra; revisit?
- **[DECIDE]** **Graph-style identity resolver** + **identity stitching UX** — `GROUP BY LOWER(email)` is the current model. Has any scenario hit the limits?
- **[DECIDE]** **Per-person policy enforcement** (block ChatGPT, force Teams) — spec says "reports, not enforces." Keep firm?
- **[DECIDE]** **iOS AI screens** — per the iOS-companion memory, this is staying companion-only with push. Reaffirm.

## I. Spec rot — read once and either revise or archive

The following specs predate CME-v2. 10-min read each to decide current truth vs partially superseded vs archive-only:

- **[DOC]** `2026-05-18-ai-security-slice-1-design.md` — Slice 1 shipped; status unclear.
- **[DOC]** `2026-05-19-sp1-unified-entity-model-design.md` — referenced by many later specs.
- **[DOC]** `2026-05-19-sp4-chat-first-design.md` — SP4 chat-first shipped per memory.
- **[DOC]** `2026-05-20-ai-discovery-connectors-design.md` — "shipped, AWS leg only."
- **[DOC]** `2026-05-20-aws-scanner-uplift-design.md` — "AWS scanner uplift in progress; Azure next."
- **[DOC]** `2026-05-20-check-title-catalog-design.md` — has explicit "out of scope" section.
- **[DOC]** `2026-05-20-fedramp-pci-framework-mappings-design.md` — **superseded by `compliance-mapping-engine-v2.md`**; move to `archive/`.
- **[DOC]** `2026-05-21-azure-scanner-uplift-design.md` — paired with AWS uplift.
- **[DOC]** `2026-05-21-region-discovery-design.md` — shipped or open?
- **[DOC]** `2026-05-21-scan-performance-design.md` — relates to STS-expiry bug (D-3).
- **[DOC]** `2026-05-22-ai-security-strategy.md` — strategy frame; reread alongside CME-v2.
- **[DOC]** `2026-05-22-gcp-scanner-uplift-design.md` — paired with AWS/Azure uplift.
- **[DOC]** `2026-05-22-scan-screen-design.md` — relates to "Run first scan" XML 403 bug.

## J. Plans/ already executed

- **[DOC]** `plans/auth-pool-recreate.md` — Cognito pool migration. Shipped 2026-05-18. Move to `plans/archive/` or delete.

## K. Test plan refresh

`TEST_PLAN.md` was written for 2026-05-18.

- **[DECIDE]** Update in place to reflect the current shipped surface (CME-v2, Slices 1+2, chat-first, /ai with 8 family-grouped tiles + disclaimer).
- **[DECIDE]** OR archive + write a new TEST_PLAN focused on AI-security capabilities.

## L. Memory hygiene

- `project_slice_1b_state.md` — chain via `[[project_cme_v2_shipped]]` (new memory written 2026-05-25) or rename.
- `project_ciso_copilot.md` — next-up framing is stale; refresh after sequencing decision lands.

---

## M. Heavy-lift projects (KK input 2026-05-24)

> Seven project-shaped capabilities. Each needs the full brainstorm → spec
> → plan → code → review → test cycle, **not** single-PR items.

### M1. Threat Intel — cloud + codebase context × SBOM × NVD

- **Shape (v1)**: Reuse a battle-tested OSS SBOM generator (Syft, cdxgen, or Trivy SBOM); subscribe to NVD CVE feed + GHSA + OSV.dev; prioritise with CISA KEV + EPSS. Emit findings via `vuln-*` check_id family so they ride the unified writer + framework registry.
- **Overlap**: Reinforces `feedback_oss_leverage.md`; CME-v2 picks up the framework tagging. Separate from AI-security scanning.
- **Open**: SBOM scope (code only, or also images/runtimes/K8s)? Per-tenant private feeds or free-tier? Reachability analysis?
- **First step**: Brainstorm. *(NVD key concern resolved — see §A.)*

### M2. MCP Security — auditing AI-agent attack surface

- **Shape (v1)**: (a) MCP discovery (CloudTrail + traffic logs + the M5 connectors); (b) risk scoring against OWASP-Agentic-Top-10-derived rubric. Emit `mcp-*` check_ids.
- **Overlap**: v2 §3 placeholder. Distinct from M5 (M5 = we consume MCP; M2 = we audit customers' MCP risk).
- **Open**: customer-OPERATED vs customer-CONSUMED MCP servers? Detection telemetry source (no inline agent)? Catalog of known-risky servers.
- **First step**: Brainstorm. Frame as AI Visibility v3 sub-project.

### M3. Azure / GCP scanner parity with AWS (incl. AI coverage)

- **Shape (v1)**: Three parallel uplift slices mirroring the AWS pattern.
- **Overlap**: In flight per `project_aws_scanner_uplift.md`. Existing specs `2026-05-21-azure-scanner-uplift-design.md` + `2026-05-22-gcp-scanner-uplift-design.md`.
- **Open**: Build GCP-AI in-tree (per CLAUDE.md) or upstream into Shasta (forbidden)? Sequencing — Azure first or GCP?
- **First step**: Finish AWS uplift E2E verification before starting Azure or GCP.

### M4. Dynamic chat-driven dashboards

- **Shape (v1)**: Pattern A — catalog of ~15-20 pre-templated dashboard intents; LLM picks intent + params, backend returns typed chart spec. Pattern B (later) — freeform text-to-SQL-to-chart, gated by read-only role + denylist.
- **Overlap**: Extends SP4 voice tools `navigate_to`, `filter_findings_view`. Lives in the chat-first surface.
- **Open**: Inline-in-chat / new tab / saved dashboard? Recharts vs Tremor/Visx?
- **First step**: 30-min brainstorm with KK to enumerate the top 20 CISO questions.

### M5. MCP-based action layer (M365 / Slack / JIRA integrations + intelligence)

- **Shape (v1)**: (a) MCP client harness over customer-approved OAuth; (b) action proposer (drafts Slack messages, JIRA tickets from findings + scan deltas); (c) approval gate — drafted, not executed.
- **Overlap**: Distinct from M2. Trust-center / risk-register / questionnaires all sink into JIRA. Daily brief (G) could send via this layer.
- **Open**: Slack + JIRA first? Always-draft or opt-in auto-send? Audit-trail table.
- **First step**: Brainstorm. The "intelligence" half is where differentiation lives.

### M6. Compliance Wizard for non-security users

- **Shape (v1)**: Guided journey UI (linear or branching) per framework — coverage → gaps → policy generation → evidence-collection plan → readiness %. New `compliance_journeys` table per tenant + framework.
- **Overlap**: Sits on findings + policies + questionnaires + trust center. CME-v2 is the data substrate. /trust is the output artifact.
- **Open**: Launch order (SOC 2 first)? Auditor-in-the-loop partnership or pure self-serve? Differentiation pitch vs Drata/Vanta.
- **First step**: Strategy doc, not a brainstorm. Positioning needs to settle before architecture.

### M7. Board deck generator

- **Shape (v1)**: Tenant-context → structured-content (Anthropic API with `tool_use` for JSON-schema-conformant slide content) → template-render (`python-pptx`). Daily brief is the same architecture at smaller scale — ship the brief first.
- **Recommendation**: Anthropic API directly, NOT Claude CLI under the hood. `claude-sonnet-4-6`; prompt-caching for tenant context; structured output to prevent prose-as-chart bugs.
- **Open**: PPTX first, then Google Slides/Notion? Generic vs customer-uploaded template? One-shot or monthly cron?
- **First step**: Build daily brief (§G) first; deck extends linearly.

### Cross-project observations

- **M2 + M5 share the MCP substrate.** Build M5's client harness first; M2 reuses it for discovery telemetry.
- **M4 + M7 share the prose-to-structured-render pattern.** M4 first → faster feedback signal.
- **M1 + M6 are both compliance-adjacent moats.** M1 unlocks M6's depth.
- **M3 unblocks cross-cloud parity** — load-bearing for the unified `/ai` claim.

---

## How to use this list

1. Skim top to bottom; mark **DROP** on anything no longer relevant.
2. For each remaining **[DECIDE]**, jot a 1-line answer in place.
3. The **[KEEP]** items left after that are the real backlog — move into a fresh `HANDOFF.md §"Open items"` and let this file go.

**Fastest decisions for next session to act:**
- **§D-1** — `findings` table inconsistency. Load-bearing on every demo. 1h debug.
- **§E** — the two web-UX gaps logged 2026-05-25 (drill-down + redundant Entra hint).
- **#41** Findings History sub-project — KK already chose sequencing; next plan-write target.
