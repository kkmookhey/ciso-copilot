# Codebase Findings — Dead Code, Optimizations, Correctness Risks

> Surfaced by the full-codebase documentation sweep, **2026-06-05**. Every dead-code
> claim was grep-verified against the whole repo by the documenting agent. This is a
> *report*, not a change set — nothing here has been fixed. Triage before acting.
> Severity/confidence are the agents' calls.

---

## A. Correctness risks & latent bugs (triage first)

These are not style nits — they are things that are wired-but-broken, or that will
break on a clean rebuild. Ordered by impact.

1. ~~**`questionnaires`: two shipped features are silently unreachable.**~~ `HIGH` —
   **FIXED 2026-06-05.** `questionnaires/main.py` dispatched
   `if method == "POST": return _create(...)` *before* the `from-excel` and
   `{id}/items/{iid}` AI-suggest checks, so `_from_excel` and `_suggest_item` never
   executed. Reordered the POST branches specific-first; regression guard added in
   `questionnaires/tests/test_routing.py` (3 tests, green).

2. ~~**`scan_reaper` source is not committed.**~~ **FALSE ALARM — corrected 2026-06-08.**
   The source IS committed: `scan_reaper/main.py` + tests + CDK wiring (`scan-stack.ts`)
   were added in commit `8a9db88 feat(scan): add stuck-scan reaper` and merged to
   `origin/main` via **PR #42**. The Lambda is deployed (`ciso-copilot-scan-reaper`,
   python3.12) with an enabled `rate(10 minutes)` EventBridge schedule.
   **Root cause of the false alarm:** the sweep ran `git ls-files` against the *current
   branch* (`feat/ai-security-slice-1`), which was cut from `origin/main` one merge before
   the reaper landed, so the file is absent *on this branch only*. Local `main` is also
   stale (6 commits behind `origin/main`). The lone `.pyc` in the tree was a local
   Python-3.14 compile artifact (Lambda runs 3.12) left over from a previous checkout of a
   branch that has the source — git leaves untracked `__pycache__` files when switching
   branches. **Lesson: verify "is it committed?" with `git log --all`, never `git ls-files`
   on one branch.**

3. **`policies` table has no `CREATE TABLE` in any migration.** `HIGH`
   `policies/main.py` reads/writes it and migration `007` *alters* it, but no migration
   creates it — the DDL was applied out-of-band. A fresh-cluster restore from `sql/` alone
   would 500 the policies Lambda. Backfill the `CREATE TABLE` into a migration.

4. **`ai_supply_chain_matcher` writes `frameworks` as an array `[]`, not the object `{}`.** `MED`
   The canonical contract (and the iOS `Finding` decoder, `APIClient.swift:794`) requires a
   JSON **object**. A matcher-emitted CRITICAL finding with `frameworks: []` will make the
   **entire** iOS `FindingsResponse` fail to decode. Normalize to `{}`.

5. **Two voice brokers disagree on model + interrupt settings.** `MED`
   iOS/`voice_session` uses `gpt-realtime` with `turn_detection.interrupt_response: True`;
   the web broker `chat_session/voice.py` uses `gpt-realtime-2` with `interrupt_response: False`.
   This violates the "one model version, pinned, in one config value" rule and means web voice
   can 400 on user interrupts. Pick one.

6. **`filter_findings_view` chat tool is a stub.** `MED`
   Client-side it only `console.log`s (`web/src/chat/Shell.tsx:208`); chat-driven findings
   filtering does not actually work. Only `navigate_to` is wired. Either implement or remove
   from the advertised tool set.

7. **`sync_framework_map.py` referenced in 4 docstrings but does not exist.** `MED`
   The four hand-mirrored `framework_map.py` copies (one per cloud runner) cite a sync script
   that has 0 grep hits — the "keep these in sync" invariant has no enforcement. Drift risk.

8. **`me`/list endpoints with finding-by-id needs:** there is **no `GET /findings/{id}`**.
   Web `get_finding` + `SourceSideSheet` and iOS both `listFindings({limit:100})` then
   `.find()` client-side — a finding past the first 100 is silently invisible. `MED`

9. **iOS `VoiceClient` observer leak.** `LOW`
   `start()` adds a `routeChangeNotification` observer every call; `teardown()` never removes
   it. Observers accumulate across start/stop cycles.

---

## B. Dead code (grep-verified unused)

### Database tables — 0 references anywhere (`platform/lambda` + `web/src` + `ios`)
`HIGH` confidence, from [02-database.md](02-database.md):
- `llm_cache` — specced LLM cache, never wired.
- `audit_events` — audit log table, never written.
- `assets` — superseded by `entities`.
- `scores` — superseded by on-the-fly compliance scoring.
- `ai_assets`, `ai_relationships` — retired in favor of `entities` / `edges` (migration 005).

> These six are safe-drop candidates *after* confirming no out-of-band consumer. Dropping
> them shrinks the "what exists" surface and removes the temptation to write to the wrong table.

### Empty / orphaned directories & modules
- **`ai_scan_api/`** — empty, untracked dir; replaced by `entities_api`. `HIGH`
- **`_shared/speakable.py`** — 0 importers repo-wide. `HIGH`
- **`anthropic_call.call()`** (non-streaming) — only `stream_messages` is imported. `HIGH`

### Dead functions — onboarding auto-scan vestiges (`HIGH`, removed by Slice 2b)
All defined, never called; each handler returns `initial_scan_id: None`:
- `onboarding_aws_complete.py:108` `_enqueue_initial_scan`
- `onboarding_azure_complete.py:146` `_run_initial_scan`
- `onboarding_gcp_complete.py:161` `_run_initial_scan`
- `onboarding_entra_callback.py:103` `_enqueue_initial_scan`
- The `ENTRA_RUNNER_FN` / `*_SCAN_TASK_DEF` env vars CDK injects are consumed *only* by
  these dead functions — effectively dead too.

### Dead UI
- **Web:** `chart_bar` artifact kind + `ChartBar.tsx` renderer (no tool emits it, `MED-HIGH`);
  unused `api.ts` fns `findingsRollup`, `listAIScans`, `getEntityGraph`, `getEntityRelationships`,
  `revokeAIConnection` ("API ahead of UI", backend exists, `MED`).
- **iOS:** `FindingRow` + `FindingDetailView` (`TopRisksView.swift:320,373`) — definitions only,
  no call site; `FindingDetailView` contains the app's *only* `ShareLink`, built but unwired
  (`HIGH`). APIClient `policies` / `questionnaires` / `trust` methods + `initiateGcpOnboarding`
  — defined, never called by any View (`HIGH`).

### Suspected-dead but KEEP (defensive / intentional seams)
- `ai_scanner/unified_writer.py:249` `:stub` SQL param — bound but never referenced in any SQL
  text; silently ignored (`HIGH` dead, but harmless).
- `EntityEmission.connection_id` — set by every detector, read nowhere (`MED`).
- `coverage/engine.run_coverage` serial variant, `RegistryApplyError` handler, `gen_scorecard.py`
  dev CLI — intentional, leave.
- **Entra's `AUTONOMOUS_BROADCAST_QUEUE_URL` env + SQS grant are inert** — Entra never
  broadcasts (pushes APNs instead). Either wire broadcast or drop the grant. `HIGH`

---

## C. Optimization opportunities

Concrete, non-speculative. Grouped by theme.

### Redundant DB round-trips (the most common pattern)
- **`COUNT(*)` re-scan per page** in `findings_list`, `events_list`, and others — replace with
  `COUNT(*) OVER ()` windowed count in the same query.
- **`soc_enrichment.compute_features`** runs 4 separate Aurora queries over the same 30-day
  `events` window — collapse 3 into one CTE.
- **`ai_summary`** runs 4 full-table aggregates per request — one CTE or a rollup.
- **`questionnaires._list`** — 2 correlated `COUNT(*)` subqueries per row → one `LEFT JOIN … GROUP BY`.
- **`admin_tenants._list_tenants`** — correlated per-row subquery for first-user email → `DISTINCT ON`.
- **`compliance_summary` and `trust._aggregate_compliance`** duplicate the entire
  framework-scoring query in separate zips — extract a shared module.

### N+1 / serial-in-loop in scanners
- `enumerate_storage` — serial `get_bucket_location` per bucket.
- `ai_pass.discover_bedrock_and_ai_lambdas` — serial region loop (everything else parallel).
- `event_router.send_push` — serial `create_platform_endpoint`+`publish` per device token in the
  hot path.
- `ai_supply_chain_matcher._MATCH_SQL` re-scans *all* of a tenant's `sca_vuln` rows (filters by
  tenant, not `scan_id`) — cost grows with cumulative findings; scope to the triggering scan.
- `ai_scanner` — 6 detectors each independently `rglob`+read every `.py` (3 also re-parse AST);
  a shared single-read/single-parse pre-pass cuts repo I/O ~6×.
- `unified_writer` — one RDS Data API round-trip per row; `batchExecuteStatement` could batch inserts.

### Cross-runner duplication (biggest structural win)
- The three cloud runners (Azure/GCP/Entra) carry ~250 lines each of line-for-line duplicated
  `_to_emission` / `convert_*_findings` / `*_id_to_entity` / 3-stage handler scaffold. Collapse
  into one `scanner_core` engine + thin per-cloud adapters. (The docstrings already gesture at this.)
- Make `framework_map.py` a build-time copy from one source (kill the 4-copy hand-sync drift).

### Client
- Web `call<T>` refreshes the token per request with no in-flight de-dup — concurrent calls each
  trigger a refresh on a stale token; share one refresh promise.
- Web Dashboard + iOS OverviewView each fire redundant calls on mount (Dashboard: 2× `listEvents`;
  iOS: 7 calls, 3 redundant — `findingsSummary.total` makes `findingsTotal` unnecessary).

---

## D. Notable architecture observations (not defects — know these)

- **`event_router/push.py` is a deliberate copy of `_shared/push.py`**, and
  `soc_enrichment/spend_cap.py` is **symlinked** into the router (the router is flat-zipped with
  no `build.sh`). One DynamoDB table multiplexes the push-rate counter and the LLM-spend counter
  via sort-key prefix.
- **The text agentic loop never persists/replays `tool_use`/`tool_result` blocks** — the model
  re-derives tool calls fresh every turn.
- **Slack's MCP server rejects bot tokens (401)** — all admin/bot Slack ops bypass MCP for the
  direct Web API (this is why `findings_subscriber` and `tools.slack_dm` call `chat.postMessage`
  directly).
- **Auth gate is triplicated** in the web app (`routes/Shell`, `chat/Shell`, `DeepLinkGate`) —
  change one, change all three.
- **`registry.rules[]` is non-empty (13 rules) but the `ai_touching` selector never gates anything**
  because stub entities aren't backfilled into `entity_index` (`unified_writer.py:60-63`) — know this
  before authoring an `ai_touching`-dependent rule.
- **6 API routes are deliberately UNAUTHED** with their own gates (onboarding completes via one-time
  secret, admin email-decision via token, public trust page, Entra/MS redirects, `discover-tenant`
  pre-login). Security rests on the per-route gate, not the Cognito authorizer.

---

*Companion to [CODEBASE_MAP.md](CODEBASE_MAP.md). Per-subsystem detail in the numbered docs.*
