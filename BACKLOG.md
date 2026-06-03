# Backlog

> Open work items, sized small to medium. For the live state of every
> shipped surface see [HANDOFF.md](HANDOFF.md); for forward-looking
> phases + heavy lifts see [ROADMAP.md](ROADMAP.md); for engineering
> conventions see [CLAUDE.md](CLAUDE.md).
>
> **How to read.** Each item carries a triage code: **[NOW]** ready to
> pick up today, **[SOON]** queued behind a clear blocker or sequencing
> call, **[DECIDE]** needs a 1-line call from KK, **[ARCHIVE?]** doc
> rot — verify it's still alive before re-listing.
>
> Last swept 2026-06-03 after the Azure + GCP broadcast pipeline shipped
> (PR #43). The pre-2026-05-25 paper trail (CME-v2 / SOC Slice 1 done
> calls, NVD-key false alarm, etc.) is no longer reproduced here — it
> lives in git history and HANDOFF entries from those dates.

---

## A. Broadcast pipeline follow-ups (post PR #43)

These all surfaced during the 2026-06-02/03 Azure + GCP smoke. Two are
infrastructure gaps; one is the operational hygiene note that should
become a hook.

- **[NOW] Wire JIT bot-token refresh in `findings_subscriber`.** Surfaced
  live 2026-06-03: the admin Slack bot token expired, subscriber threw
  `slack chat.postMessage: token_expired`, 2 messages went to the DLQ.
  Subscriber already `SELECT`s `access_expires_at` (main.py:66) but never
  uses it. Mirror the per-user OAuth pattern: import
  `mcp_oauth.refresh_if_near_expiry` and call it before
  `chat.postMessage`. Schema already has `refresh_token_enc` +
  `refresh_data_key_ct` on `tenant_bot_connectors`. ~1 session.
- **[NOW] Wire `broadcast_fanout` into `shasta_runner_entra`.** Different
  writer path (`_insert_findings` in `main.py`, not `unified_writer.py`).
  Currently the only scanner that doesn't broadcast on critical-fail.
  `_shared/broadcast_fanout.py` is already bundled in the Entra image
  via build.sh — just needs the call site at the writer + IAM grant +
  env var. ~1 session.
- **[SOON] `unified_writer` consolidation across the 4 scanner copies.**
  The 3 cloud-scanner files are gitignored runtime stagings (`build.sh`
  copies from `ai_scanner/unified_writer.py`), but drift exists:
  per-scanner detector emission types, CME-v2 normalize counters in
  `ai_scanner` only. Spec §5.D of the MCP Slice 2 design called for
  hoisting into `_shared/`. Worth doing after the Entra writer is
  on-pattern so all 4 scanners stop diverging. Medium size.
- **[NOW] Operational hook: post-image-push warning.** After today's
  twice-bitten "stale ECR image" lesson, worth a Claude Code hook (or
  Makefile target) that reminds anyone bumping `unified_writer.py` or
  `_shared/` to rebuild all 4 images + run `update-function-code` for
  the 3 Image-Lambdas. ~30 min, drop into `.claude/hooks/`.
- **[SOON] Authored regression test for the bot-token refresh path.**
  Pair with the first item — once refresh is wired, add a test that
  simulates `chat.postMessage → 401 token_expired → refresh → retry →
  200`. Reuses the per-user OAuth test pattern.

## B. Live bugs / mysteries

- **[NOW] Quick tier silently skips AI pass.** *(Hit twice in one day —
  AWS + Azure)*. UI shows `/ai = 0` with no explanation. Either (a) slim
  AI pass on quick tier or (b) UI hint when latest scan is quick. ~1 hr.
- **[SOON] STS credentials expire mid-scan on medium-tier multi-region.**
  Scan `f749cb31` failed 2026-05-21. Bump role's `max-session-duration`
  + re-assume with `--duration-seconds`, or split medium into per-region
  chunks under a fresh session each. Blocking AWS scanner uplift v2 E2E
  verification per ROADMAP Phase 3.

## C. UX gaps & papercuts

- **[NOW] Slack DM-via-Risks table-cell relocation.** The MCP Slice 1
  "DM via Slack" button mounts inside the Risk Register's title cell;
  KK flagged it as potentially cramped. ~15 min to relocate to a row
  action menu or icon column. HANDOFF Slice 1 §plan-vs-codebase drift.
- **[DECIDE] Server-derived `cloud` field on `Finding` API responses.**
  Today the SPA infers cloud from ARN substrings (`cloudOf()` in
  TopRisks); misses Azure findings whose ARN doesn't carry
  "azure"/"microsoft." Move to a server-side computed column or
  derived response field. Blocked on schema decision: column on
  `findings` or compute in API? ~2 hr.

## D. Doc + ops hygiene

- **[NOW] Integration test against a real Postgres** (`docker run`-able
  sqlite/pg compatibility shim, or a small Aurora dev cluster the test
  suite hits). Root cause of all 4 ICICI-demo bugs (PR #29 / 2026-05-27):
  every Python Lambda mocks `_rds.execute_statement` so the test suite
  never validates SQL against real schema. Worth a brainstorm on
  approach (mocked-pg vs real-Aurora-dev) before building.
- **[NOW] `TEST_PLAN.md` refresh.** Last written 2026-05-18. Needs to
  reflect the current shipped surface (CME-v2, SOC Slice 1+1c, MCP
  Connectors Slice 1+2, AI Inventory, AI Exposure Score, broadcast
  pipeline). Either rewrite in place or archive + new file focused on
  the AI-security capabilities.
- **[ARCHIVE?] Spec rot sweep.** ~12 specs in `docs/superpowers/specs/`
  pre-date CME-v2. Each is a 10-min read to decide current truth vs
  partially superseded vs archive-only. Worth a single batch session
  rather than per-spec; output a manifest of (keep / move to archive/ /
  rewrite). Specs known to be superseded:
  `2026-05-20-fedramp-pci-framework-mappings-design.md` → CME-v2.
- **[NOW] AI FinOps Slice 1 plan.** Spec landed 2026-06-01 at
  `docs/superpowers/specs/2026-06-01-ai-finops-design.md`; plan + Slice 1
  not started. Slice 1 = GitHub + Anthropic Admin API → build-cost-per-
  feature dashboard. New customer-facing module sister to `/ai`.

## E. Phase 3 precursors (per ROADMAP)

- **[SOON] Daily brief generator.** Nightly Anthropic-driven prose
  Lambda; emits a 200-word per-tenant summary across cloud + AI + SOC
  + compliance. Natural precursor to M7 (board deck) — same
  architecture at smaller scale. ROADMAP Phase 3.
- **[SOON] iOS Policies + Questionnaires + Trust views.** Backend
  ready since 2026-05-18; iOS UI not built. Cross-reference the
  `project_ios_companion_vision` memory before building — KK may cut
  these in favor of the alerting + handoff companion shape.

## F. Out-of-scope items still needing a call

From `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md §3`:

- **[DECIDE] GCP-AI cloud pass** — status of the sub-project? Probably
  rolls up under M3 (cross-cloud parity) on ROADMAP.
- **[DECIDE] OpenAI / Anthropic admin-API connectors** — blocked on
  admin-key access; OpenAI's admin API is now GA. Worth attempting.
  Pair with the AI FinOps spec (item D above) — Anthropic Admin is
  Slice 1 of FinOps.
- **[DECIDE] M365 Copilot ingest** — deferred unless prospect asks. Has
  anyone asked?
- **[DECIDE] Google Workspace audit logs** — same pattern as Entra;
  revisit?
- **[DECIDE] Graph-style identity resolver + identity stitching UX** —
  `GROUP BY LOWER(email)` is the current model. Has any scenario hit
  the limits?
- **[DROP?] Inline traffic / browser extension / endpoint agent** —
  ROADMAP anti-roadmap explicitly says no. Confirm dropped permanently;
  delete this row from BACKLOG next sweep.
- **[DROP?] Per-person policy enforcement.** ROADMAP anti-roadmap:
  "Shasta reports — does not enforce." Confirm dropped.

## G. Heavy-lift projects (M1–M7)

The 7 project-shaped capabilities (threat-intel × SBOM, MCP security,
cross-cloud scanner parity, dynamic dashboards, MCP action layer,
compliance wizard, board deck) all live on [ROADMAP.md](ROADMAP.md)
with sequencing + open questions per project. Each needs a full
brainstorm → spec → plan → vertical-slice cycle, not single-PR items.

Per the ROADMAP cross-project notes:
- **M2 + M5 share the MCP substrate.** Build M5's client harness first;
  M2 reuses it for discovery telemetry.
- **M4 + M7 share the prose-to-structured-render pattern.** M4 first
  → faster feedback signal.
- **M1 + M6 are both compliance-adjacent moats.** M1 unlocks M6's depth.
- **M3 unblocks cross-cloud parity** — load-bearing for the unified-OS
  claim.

## H. Items resolved since the last sweep (2026-05-25 → 2026-06-03)

Recorded once so future readers know NOT to re-list them.

- ✅ `findings` table "inconsistency" — NOT A BUG. `scan_id` tracks
  most-recent-touch (ON CONFLICT semantic), not discovery. Documented
  as a HANDOFF gotcha.
- ✅ Drill-down on framework tiles — already shipped. FrameworkTile
  (`AISummary.tsx:252-289`) splits label-link (→ /findings?framework=)
  and source-doc icon.
- ✅ Redundant Entra ID P1/P2 hint — already addressed. AISummary now
  cross-links to ConnectClouds where the Slice 2.1 banner lives.
- ✅ APNs push end-to-end test — verified 2026-05-27 on KK iPhone 16 Pro
  Max.
- ✅ Secrets / hardcoded-ARN audit — Phase 2 Slice A + Tier 2 + Tier 3
  gitleaks audit (PRs #26 + #30 + #31).
- ✅ MIT-public flip — done 2026-05-27.
- ✅ Wow demo — PR #32 (43 commits, voice-first agentic investigation).
- ✅ MCP Connectors Slice 1 (per-user OAuth + Slack-as-MCP, PR #33).
- ✅ MCP Connectors Slice 2 (autonomous CRITICAL → Slack broadcast,
  PRs #35–#39 + #41 hotfix).
- ✅ Azure + GCP broadcast wiring + post-commit race fix (PR #43,
  2026-06-03).
- ✅ RETURNING finding_id on `_insert_finding` upsert — closes the
  ON CONFLICT broadcast gap (PR #44 / commit `30c9c6b`, 2026-06-03).
- ✅ Auth pool recreate plan — `plans/auth-pool-recreate.md` executed
  2026-05-18; move to `plans/archive/` next file-hygiene sweep.

---

## How to use this file

1. **Pick from §A or §B for ship-tomorrow size work** — clearly scoped,
   blast radius known, drops straight into a PR.
2. **For [DECIDE] rows in §F**, jot the call inline and demote to
   [NOW] / [SOON] / delete.
3. **At the end of every multi-PR phase, re-sweep this file** — move
   resolved items to §H, prune §H entries older than 30 days, archive
   §C/§D items that have aged out of relevance.

*If an item carries no triage code, treat it as ARCHIVE?.*
