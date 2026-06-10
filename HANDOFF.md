# Shasta by Transilience — Handoff & State

## 🤖 AI Security Slice 1 — Sub-slices 1.1 + 1.2 + 1.3 shipped (2026-06-08/09, PR #46 open)

**Status:** three of five sub-slices live + deployed + smoke-verified. Two
remain (1.4 Workspace scanner + 1.5 mapping rules + smoke). **Next session
should NOT start Sub-slice 1.4 directly — the AI-stack extraction
brainstorm must land first.** See "Architectural blocker" below.

**Branch:** `feat/ai-security-slice-1` @ commit `1cab7f5`. **PR:** [#46](https://github.com/kkmookhey/ciso-copilot/pull/46) — single PR currently bundling Sub-slices 1.1, 1.2, 1.3 plus interim work (questionnaires FINDINGS A.1 fix + codebase doc sweep). Still open. KK can decide whether to merge as a Slice 1 part-1 PR or split later.

### Sub-slice 1.1 — `framework_meta` consolidation ✅

`framework_meta.py` was duplicated byte-identically in `ai_summary/` and
`compliance_summary/` (drift risk). Now lives canonically at
`scanner_core/framework_meta.py`; both consumer Lambdas pick it up at CDK
synth time via a new bundling helper.

- **New helper `lambdaCodeWithSharedMeta(lambdaDir)` in `platform/lib/api-stack.ts:22-65`** — `lambda.Code.fromAsset` with a `local.tryBundle` that does `fs.cpSync(sourceDir, outputDir, { recursive: true, filter: skipBytecode })` + `fs.cpSync(sharedFile, outputDir/framework_meta.py)`. No shell (no injection surface). Filters `__pycache__/*.pyc` so the asset hash is deterministic across developer machines. Local bundling failure → loud `throw` (the Docker fallback `command` is a stub `exit 1` because Docker can't reach `../scanner_core` from inside the asset-input mount).
- **Pattern is reusable** for any future Lambda that needs to share `scanner_core/` content without a Layer.

### Sub-slice 1.2 — AI-BOM CycloneDX-ML export ✅

New `ai_bom_export/` Lambda at `GET /v1/ai/bom?format=cyclonedx`. Reads
`entities` + `edges` + AI-attached `findings` for the caller's tenant and
emits a CycloneDX-ML 1.6 JSON document via `cyclonedx-python-lib==11.9.0`.
Browser-side blob download from the new **Export AI-BOM** button on `/ai`.

- **Library pinned**: `platform/lambda/ai_bom_export/requirements.txt` → `cyclonedx-python-lib==11.9.0` (verified `ComponentType.MACHINE_LEARNING_MODEL` + `SchemaVersion.V1_6` + `JsonV1Dot6` all present).
- **Schema gotcha caught by subagent**: the plan called for a fictitious `finding_entities` JOIN table. Subagent caught the verify-before-claiming-new violation and substituted the real `findings.subject_entity_id` column (migration `005_unified_entities.sql:48`). Good signal that the new process guards are catching what they're meant to.
- **FINDINGS.md §A.4 defended**: `_select_ai_findings` SQL gates the `?` operator on `jsonb_typeof = 'object'` so legacy `ai_supply_chain_matcher` rows with `frameworks=[]` don't break the export. Helper `_safe_frameworks(raw)` coerces non-dict JSON to `{}`. Tests cover both shapes.
- **7 unit tests** (`platform/lambda/ai_bom_export/tests/test_main.py`), all pass.

### Sub-slice 1.3 — Bedrock InvokeModel runtime detector ✅

`event_router/main.py` extended with a Bedrock branch that fires BEFORE
the existing SOC flow. Per-call upserts `bedrock_model` entity +
`bedrock_invocation` rollup (per principal × model × day × region, atomic
`jsonb_set` counter) + `iam_principal` entity + uses-edge. Per-event
detectors fire `aws_bedrock_invoke_unsanctioned` (when allowed-list set) and
`aws_bedrock_model_inventory` (always, idempotent). Daily 00:05 UTC schedule
fires `aws_bedrock_invoke_high_volume` for rollups over threshold.

- **Customer-side CFN allowlist updated** (`platform/cfn/aws-onboard.yaml:155-163`) — added 7 Bedrock eventNames (`InvokeModel`, `InvokeModelWithResponseStream`, `Converse`, `ConverseStream`, `InvokeAgent`, `Retrieve`, `RetrieveAndGenerate`). **OPERATIONAL: existing tenants need to re-run their onboarding CFN stack** to pick this up. New tenants get it automatically.
- **Synthetic-scan strategy** (`_ensure_bedrock_runtime_scan` in `event_router/main.py:57-105`) — `findings.scan_id` is `NOT NULL FK → scans(scan_id)`, but Bedrock events aren't part of a scan. Solution: deterministic `uuid5(NAMESPACE, "tenant::conn")` scan_id; `INSERT INTO scans … ON CONFLICT (scan_id) DO NOTHING`; `trigger='runtime'`. One sentinel row per (tenant, conn) — every Bedrock finding for that connection FKs cleanly. **This pattern is reusable for any future real-time detector that writes findings.**
- **EventBridge schedule** (`platform/lib/events-stack.ts:228-244`) — `bedrock-daily-rollup` `cron(5 0 * * ? *)`, dispatches `event_router` with synthetic `{detail-type: shasta.scheduled.bedrock_daily_rollup}`. Verified live; direct-invoke smoke returns `{emitted: 0, day: 2026-06-08}`.
- **End-to-end Bedrock smoke deferred** to Sub-slice 1.5 (needs a real Bedrock invocation in a connected account + CloudTrail propagation minutes).
- **16 unit tests** (`platform/lambda/event_router/tests/test_bedrock.py`), all pass.

### Cap relief landed in Sub-slice 1.2 (FYI for future deploys)

`CisoCopilotApi` synth was 666 resources → CFN reported 506 actual (>500 cap). Removed 3 verified-dead routes in `api-stack.ts` to make room: `GET /v1/entities/{id}/graph`, `GET /v1/entities/{id}/relationships`, `DELETE /v1/ai/connections/{id}`. Web grep + iOS grep confirmed zero callers. Lambda handlers in `entities_api` + `ai_github` still support those operations; **re-adding the routes is one line each** if a caller appears. Freed ~6 resources; current actual ~494/500.

### Architectural blocker — DO BEFORE Sub-slice 1.4

**Stand up `CisoCopilotAi` stack.** Sub-slice 1.4 (Workspace OAuth routes + Fargate scanner + ConnectClouds tile) adds ~12-16 CDK resources to `CisoCopilotApi`. Even after today's cap relief, that takes us past 500 again. The fix that scales (per KK's 2026-06-05 criterion) is extracting AI-domain Lambdas into a separate stack with cross-stack `RestApi` reference.

This is a real engineering project, **not a 30-min task**:
- Cross-stack `RestApi.fromRestApiAttributes()` works but has gotchas (stage doesn't auto-redeploy on new routes added from a different stack — need explicit `Deployment` logicalId hash or manual `aws apigateway create-deployment`)
- Must decide what moves: only the NEW Sub-slice 1.4 Lambdas, or also retrofit `aiSummaryFn` + `aiBomExportFn` + `entitiesApiFn`?
- Permissions for cross-stack `LambdaFunction` invocation by API Gateway need care

Should be its own brainstorm → spec → plan cycle. Estimated ~2-3 hours of careful CDK work + thorough testing.

### Process guards landed in this slice (repo-wide, scales for all future specs)

- **`docs/superpowers/SPEC_TEMPLATE.md`** — every new spec under `docs/superpowers/specs/` must include a §0 "Codebase baseline" section filled in via grep/Read against `docs/codebase/CODEBASE_MAP.md` + actual code. Worked example included.
- **CLAUDE.md "Working principles for this build" §1** — "Verify before claiming new" rule. References the specific Slice 1 incident (commit `225e4d8`) where the spec wrongly claimed NIST AI RMF + OWASP LLM Top 10 were new framework packs.
- **Auto-memory entry** `feedback_pre_spec_audit.md` persists the lesson across `/clear`.

The verify-before-claiming rule was tested today by the AI-BOM subagent (caught the fabricated `finding_entities` table in the plan) and is working as intended.

### Open follow-ups carried forward

- **Sub-slice 1.4 — Google Workspace scanner.** **Blocked on AI-stack extraction (above).** Don't start without that.
- **Sub-slice 1.5 — 8 new mapping rules** in `scanner_core/ai_framework_registry.json` + end-to-end smoke (real Workspace tenant + real Bedrock invocation + AI-BOM cyclonedx schema validation).
- **`docs/codebase/FINDINGS.md` §A.4 still open** — `ai_supply_chain_matcher` writes `findings.frameworks` as `[]` not `{}`. AI-BOM export is defended against it, but other consumers (iOS Finding decoder per the FINDINGS note) still crash on `[]`. Worth a tiny fix PR.
- **Tenant operational note** — Slice 1.3 needs existing tenants to re-run `aws-onboard.yaml` to get Bedrock eventNames forwarded. Either a customer-facing one-pager OR a script that auto-re-runs onboard CFN per-tenant.

### Commit trail (Slice 1 work, top to bottom on `feat/ai-security-slice-1`)

```
1cab7f5 feat(cdk): EventBridge daily-rollup schedule for Bedrock high-volume detection
ce3c065 fix(event-router): synthetic-scan strategy for Bedrock runtime findings + CFN allowlist
d5a7d6f feat(event-router): Bedrock event branch + entity upserts (subagent: 1.3.2-1.3.4)
3cb304c feat(web): Export AI-BOM button on /ai page
a515c69 feat(cdk): wire ai_bom_export Lambda + /v1/ai/bom route, free CFN headroom
0ceaeba feat(ai-bom): finding → vulnerability mapping with defensive frameworks parsing
6852168 feat(ai-bom): edge → CycloneDX dependency mapping
52c2797 feat(ai-bom): entity → CycloneDX component mapping
0aff811 feat(ai-bom): handler skeleton with tenant resolution + format validation
ea2a6fd feat(ai-bom): pin cyclonedx-python-lib==11.9.0 for CycloneDX-ML 1.6
09d956f fix(cdk): harden lambdaCodeWithSharedMeta — skip bytecode, loud failure
27aba70 refactor(framework-meta): consolidate to scanner_core canonical home
4edf24a docs(process): require Codebase baseline §0 + Verify-before-claiming-new rule
f3a59fb docs(plans,specs): apply codebase-map findings to Slice 1 plan + spec
777ef03 docs(plans): AI Security Slice 1 implementation plan — 5 sub-slices, 31 tasks
225e4d8 docs(specs): correct AI Security Slice 1 to match shipped baseline
331b714 docs(specs): AI Security Slice 1 — initial draft
```

---

## 🏗️ CisoCopilotAi stack — cross-stack RestApi extension (shipped 2026-06-10)

`CisoCopilotApi` was at 494/500 CFN resources. Sub-slice 1.4 would have
blown the cap. Fixed by extracting NEW AI-domain Lambdas into
`CisoCopilotAi`, which shares the existing `RestApi` + Cognito authorizer
via `Fn.importValue`. The four existing AI Lambdas (AiSummaryFn,
AiBomExportFn, AiGithubFn, EntitiesApiFn) stay in CisoCopilotApi — "new
work only" scope.

**Spec:** `docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md`
**Plan:** `docs/superpowers/plans/2026-06-10-ai-stack-extraction.md`

**Operational rules — read before deploying:**

- **Deploy order, first time:** `npx cdk deploy CisoCopilotApi CisoCopilotAi --require-approval never`. CDK auto-resolves the order from `Fn.importValue` references; the explicit `addDependency` in `bin/platform.ts` is belt-and-suspenders.
- **AI route changes require full deploy.** `cdk deploy CisoCopilotAi --hotswap` works for Lambda code/env-only changes. It does NOT fire the `AwsCustomResource` that re-points the stage, so any route addition/removal/rename needs `cdk deploy CisoCopilotAi` (no `--hotswap`).
- **The `aiStackExtensionVersion: 'v1'` pin in `api-stack.ts` is load-bearing.** It keeps `CisoCopilotApi`'s `latestDeployment` logicalId stable when `CisoCopilotApi` is deployed independently — without it, a `CisoCopilotApi`-only redeploy would silently drop all routes added by `CisoCopilotAi`. Bump `'v1'` → `'v2'` only on intentional major route-layout changes.
- **`/v1/ai/_health` is a stub.** It exists only to prove the wiring. Sub-slice 1.4's first real Workspace OAuth route should delete it.
- **Authorizer is imported as an `IAuthorizer` object literal**, not via a CDK factory (CDK v2 doesn't expose `fromAttributes` for Authorizer). The literal references `CisoCopilotApi-CognitoAuthorizerId` and adds zero CFN resources — see `platform/lib/ai-stack.ts` lines ~37-45 for the pattern when adding new authed AI routes.
- **Both stacks together must stay under 500 resources each.** CisoCopilotApi: ~494. CisoCopilotAi: ~14 after this work. Headroom for Sub-slice 1.4+1.5: ~480 in CisoCopilotAi, ~6 in CisoCopilotApi. Watch the latter — non-AI features will eat that fast.

---

## 🔔 MCP Connectors Slice 2 — autonomous CRITICAL broadcast (shipped in 5 sub-slices)

**Status (2026-06-01):** all 5 sub-slice PRs open, stacked.

| PR | Branch | Scope |
|---|---|---|
| #35 | `feat/mcp-connectors-slice-2.1-broadcast-fanout` | `_shared/broadcast_fanout.py` + scanner wiring (4 scanners) |
| #36 | `feat/mcp-connectors-slice-2.2-admin-bot-install` | Admin Slack workspace bot OAuth (initiate + callback + `_require_admin`) |
| #37 | `feat/mcp-connectors-slice-2.3-channel-picker` | `mcp_oauth.get_admin_session` + 5 admin routes (channel picker, autonomous toggle, revoke, status) + web admin block |
| #38 | `feat/mcp-connectors-slice-2.4-broadcast-plumbing` | CDK queue+DLQ+DDB+findings_subscriber Lambda (in ScanStack — ApiStack would have hit CFN 500-resource limit) |
| (TBD) | `feat/mcp-connectors-slice-2.5-deeplink-gate` | `<DeepLinkGate>` + EMF drift metric + CloudWatch drift alarm |

**Merge order:** #35 → #36 → #37 → #38 → 2.5. Each PR's base auto-rebases to main as predecessors merge.

**Spec:** `docs/superpowers/specs/2026-05-31-mcp-connectors-slice-2-design.md`
**Plan:** `docs/superpowers/plans/2026-05-31-mcp-connectors-slice-2.md`

### What's live after all 5 merge

- Scanner Lambdas publish to `autonomous-broadcast-queue` on every critical-fail finding via `_shared/broadcast_fanout.publish_if_critical`.
- `findings_subscriber/` Lambda consumes the queue, gates on three kill switches (SSM global / per-tenant toggle / channel-not-picked), re-reads the finding, posts a Block Kit card to the configured Slack channel.
- Admin block on `/settings` → Connectors tab: install, channel picker, autonomous toggle, disconnect.
- `<DeepLinkGate>` wraps `/risks/:finding_id` so Slack-card clicks survive unauthenticated tabs.
- CloudWatch: DLQ depth alarm + drift alarm (`CriticalFailWritten` vs `BroadcastQueued`).

### Manual smoke checklist (post-deploy, after all 5 merge)

1. Admin installs Slack workspace bot via Settings → Connectors → admin block.
2. Pick a broadcast channel via the modal.
3. Manually `INSERT` a critical-fail finding into Aurora dev (`severity='critical' AND status='fail'`).
4. Verify Block Kit card lands in the configured channel within 60s.
5. Click "View details" from a fresh Incognito tab → bounces through `/signin?after=...` → lands on `/risks/:id`.
6. Set `aws ssm put-parameter /cisocopilot/autonomous_rule/enabled false` → next insert: no broadcast.
7. Flip the per-tenant toggle (`UPDATE tenant_bot_connectors SET autonomous_rule_enabled=false WHERE tenant_id=...`) → next insert: no broadcast.
8. Set the channel to a deleted channel → DLQ accumulates after 5 retries → alarm fires within 5 min.

### Known limitations / follow-ups

- **Azure + GCP ECS scanners**: IAM granted but `AUTONOMOUS_BROADCAST_QUEUE_URL` needs to be injected at `RunTask` time as a container override (CDK `ContainerDefinition` has no post-construction env mutation API). For v1, autonomous broadcasts fire for AWS Lambda scanner findings (aiScanner, shastaRunner, shastaRunnerEntra) only. Azure + GCP findings won't broadcast until follow-up.
- **`unified_writer` consolidation deferred**: spec §5.D called for hoisting the duplicated writer modules into `_shared/`; investigation found drift across the 4 copies (CME-v2 normalize counters in `ai_scanner` only, per-scanner detector emission types). Slice 2.1 narrowed to fan-out hook only. Full consolidation is a separate cleanup PR.
- **Subscriber tool name is `send_message`** — verify against Slack's MCP server tool catalog during deploy; might be `chat_postMessage` or `slack_send_message` depending on which MCP server the bot connects to. Test accepts all three names; runtime uses `send_message`.

---

> Source of truth for the *current* state of the build. Reload this at the
> top of every session. The PRD is `CISOBrief-v2.md`; this document
> records what's actually built, what was broken and fixed, and what
> still hurts. Product docs sit at the repo root: README → ARCHITECTURE
> → ROADMAP.
>
> Last updated: 2026-05-28 (MCP Connectors Slice 1 implementation
> complete — 18 commits on `feat/mcp-connectors-slice-1`, code-only,
> awaiting KK's E2E smoke test + CDK deploy. Wow-demo PR #32 merged.
> Spec + plan + Slice 1 implementation all on PRs #33 + the upcoming
> Slice 1 PR. Previous: 2026-05-28 wow-demo merged. 2026-05-27 ICICI
> Lombard demo + 4 prod bug fixes (#29). 2026-05-26: Secrets extraction
> Phase 2 Slice A (#26), AI page UX (#27), docs trio + branding + MIT
> license. SOC Slice 1c + manual gate verified. 2026-05-25: SOC Slice 1
> + CME-v2 (#17–#21). iOS device install + Tier 2 doc sanitization +
> Tier 3 gitleaks audit. Repo public on MIT.)

> **Configuration note.** Commands in this doc use `$VAR` references
> for per-deployment identifiers (account ID, ARNs, domains). Source
> `platform/.env` before running them (e.g.
> `set -a && . platform/.env && set +a`). Values not in `.env` use
> `<PLACEHOLDER>` tokens and come from CDK stack outputs — fetch with
> `aws cloudformation describe-stacks --stack-name <Stack> --query
> 'Stacks[0].Outputs'`. See `platform/.env.example` for the full key
> list.

## 🔌 MCP Connectors Slice 1 — security/IDOR fixes landed, re-smoke required (2026-05-30)

Per-user OAuth + remote-MCP integration for productivity tools. Slice 1
of a 5-slice sub-project ships the load-bearing infrastructure plus
Slack end-to-end. Slices 2-5 (admin Slack bot + autonomous broadcast,
Atlassian, Google Workspace, M365 read-only) follow once Slice 1 is
verified in production.

**Branch:** `feat/mcp-connectors-slice-1`. **PR #33** (currently draft).

### 2026-05-30 fixes from pre-merge code review

Five-angle high-effort review surfaced 5 blockers + 3 important issues
after KK's initial smoke (1–5) passed against a single warm Lambda
container. All eight landed on the branch with new tests; the schema
changed, so KK must drop + re-apply migration 015 and reconnect Slack
before re-smoking.

- **B1 — KMS envelope rewrite.** `crypto.py` was caching only `Plaintext`
  from `GenerateDataKey` and discarding `CiphertextBlob`. Every new
  Lambda container generated a fresh random data key, so tokens written
  by container A were undecryptable by container B (silently bricked
  every connector under concurrency). Replaced with per-row data keys:
  `encrypt_token` returns `(fernet_ct, data_key_ciphertext)`; both are
  persisted; `decrypt_token` calls `kms.Decrypt` on the stored
  `CiphertextBlob` (with bounded LRU cache to skip the round-trip on
  hot reads). Schema gained `access_data_key_ct` + `refresh_data_key_ct`
  bytea columns on `user_connectors` AND `tenant_bot_connectors`.
- **B2 — `tools/main.py` tenant_id pattern.** The MCP arm read
  `claims.get("custom:tenant_id")` — a claim this Cognito pool never
  issues. Every federated (Microsoft/Google) MCP tool call 400'd.
  Replaced with the canonical `SELECT tenant_id FROM users WHERE
  sso_subject = :s` join (mirrors `handlers_slack._resolve_user_context`
  and `voice_session._resolve_user_context`).
- **B3 — Advisory lock now real.** `pg_advisory_xact_lock` was a no-op:
  Aurora Data API auto-commits each `execute_statement`, releasing the
  txn-scoped lock instantly. Extended `_DataAPIWrapper` with a
  `transaction()` context manager that does `begin_transaction`/
  `commit_transaction`/`rollback_transaction`. Lock + re-read + provider
  HTTP call + UPDATE now all share one txn, so the lock survives.
- **B4 — DELETE intra-tenant IDOR.** Revoke filtered by `tenant_id`
  only, discarding `user_id` from `_resolve_user_context`. Any tenant
  member could revoke another member's connector by conn_id. SELECT +
  UPDATE both now bind `user_id`.
- **B5 — `list_me` Aurora unwrap.** Bare `next(iter(cell.values()))`
  decoded NULL → Python `True` and `TEXT[]` → raw `{'stringValues':[...]}`
  dict. UI showed "Connected · true" for null workspaces and mangled
  scopes. Now uses the existing `_zip_record` helper.
- **I1 — PKCE one-shot consume.** `fetch_verifier` now uses
  `delete_item(ReturnValues="ALL_OLD")` instead of `get_item`. RFC 7636
  §4.5 compliance — verifier is replay-proof immediately, not just at
  TTL expiry.
- **I2 — CSRF cookie retired.** Cross-origin web→API + wildcard
  `Allow-Origin: *` made the Set-Cookie unusable, so the check was dead
  code. Removed entirely; the signed state JWT (HS256, server-secret,
  5-min exp, with `iss` + `aud` now pinned and verified) is the CSRF
  defense. `verify_state(token, expected_provider=...)` rejects a JWT
  minted for one provider replayed at another.
- **I3 — None-deref guard.** `refresh_if_near_expiry` re-read under the
  lock now raises `ConnectorMissingError` if the row was concurrently
  deleted, so the caller returns 409 "reconnect in Settings" instead of
  a generic 500.

### What KK needs to do before re-smoking

The KMS-envelope rewrite changed `user_connectors` schema. Existing
rows (the Slack OAuth from yesterday's smoke) were encrypted under the
broken single-key scheme and cannot be migrated.

1. **Drop the existing `user_connectors` table and re-apply migration**:
   ```bash
   set -a && . platform/.env && set +a
   aws rds-data execute-statement --resource-arn "$DB_CLUSTER_ARN" \
     --secret-arn "$DB_SECRET_ARN" --database ciso_copilot \
     --sql "DROP TABLE IF EXISTS user_connectors;
            DROP TABLE IF EXISTS tenant_bot_connectors;"
   aws rds-data execute-statement --resource-arn "$DB_CLUSTER_ARN" \
     --secret-arn "$DB_SECRET_ARN" --database ciso_copilot \
     --sql "$(cat platform/sql/015_mcp_connectors.sql)"
   ```
2. **CDK deploy** (api + data — KMS grants unchanged, but code changed):
   ```bash
   cd platform
   npx cdk deploy CisoCopilotApi --require-approval never --hotswap
   ```
3. **Web rebuild** (no code changes here, but rebuild to keep parity):
   ```bash
   cd web && pnpm build
   aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete
   aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
   ```
4. **Reconnect Slack** in `/settings` (the old encrypted tokens are gone).
5. **Re-run smoke 1–5 + 6**:
   - 1: Connect Slack via Settings
   - 2: Voice agent "DM yourself"
   - 3: Risks "DM via Slack" button (this is the path that previously
        400'd silently for non-KK accounts — verify it works now)
   - 4: Disconnect → reconnect
   - 5: Force token expiry, verify JIT refresh works
   - 6: (new) Two concurrent voice tool calls within 60s of expiry —
        confirm both succeed (the advisory-lock fix being exercised).
        Hard to engineer manually; can verify by tailing CloudWatch for
        the connectors Lambda and looking for paired
        `pg_advisory_xact_lock` log lines under one transactionId.

### What's code-complete and tested

| Layer | Status |
|---|---|
| Aurora schema | `015_mcp_connectors.sql` — `user_connectors` (per-analyst tokens) + `tenant_bot_connectors` (admin bot install) with KMS-encrypted bytea token columns + arrayValue-bound scopes. **Migration NOT applied to Aurora yet.** |
| CDK data stack | New `connectorTokensKey` (KMS) + `pkceVerifierTable` (DDB, TTL on `ttl`). **Not deployed yet.** |
| `_shared/mcp_oauth/` Python package | 6 modules + 6 test files: `crypto` (KMS-envelope Fernet), `state` (HS256 with `csrf_token_hash` + caller-provided `nonce`), `pkce` (RFC 7636 + DDB store), `providers/slack` (OAuth v2), `session` (NULL-safe JIT refresh + advisory lock), `discover_tools` (5-min Lambda-memory cache). **14 tests pass.** |
| `connectors/` Lambda | Skeleton + 4 route handlers: `POST /connect/slack`, `GET /callback/slack`, `DELETE /{conn_id}`, `GET /me`. CSRF cookie binding + sha256 verification. **7 tests pass.** |
| `tools/main.py` extension | `kind__tool` MCP route via `mcp_oauth.get_session()`; `ConnectorMissingError` → 409 with friendly message. |
| `voice_session/main.py` extension | Dynamic tool registry via `_build_openai_tools_sync` (with running-loop guard + ThreadPoolExecutor fallback). `_NATIVE_TOOLS = _tools()` aliases the existing static list. |
| CDK API stack | New Lambda + IAM grants (KMS encrypt/decrypt, DDB rw, Aurora Data API via `grantDataApiAccess`) + 4 routes on the existing `apigw.RestApi`. SSM-backed env for Slack OAuth client + state-JWT secret. `cdk synth` passes; **not deployed yet.** |
| Web `/settings` shell | Tabbed nav (Profile / Cloud / Connectors / Team / Billing). Connectors tab live; the others are placeholders. Route added in `App.tsx`. |
| `web/src/lib/api.ts` | Extended with `listConnectors`, `initiateConnectorOAuth`, `revokeConnector`, `callTool`. `useConnectors` hook in `lib/useConnectors.ts`. |
| Web Connectors catalog | 2-col grid. Slack card live (Connect / Disconnect via OAuth redirect). Atlassian / Google / Microsoft show "Coming in a later slice" placeholders to lock the layout. Microsoft has the PREVIEW badge + read-only note per spec. `?ok=slack` → success toast. |
| Web Risks page act buttons | "DM via Slack" button mounted on Risk rows. Disabled + tooltip when Slack isn't connected; live POST to `/v1/tools/slack__send_message` when connected. |

**Aggregate test counts (post-fix, 2026-05-30)** — `_shared/`: **70 passed**.
`connectors/`: **8 passed**. `tools/`: 22 passed (4 pre-existing failures
in `slack_dm` / `create_pr_with_bump` / `run_forensic_scan` unchanged).
`voice_session/`: 7 passed (1 pre-existing system-prompt length assertion
unchanged; conftest sys.path bug fixed so the dynamic-tools tests now
actually collect). Web: `pnpm build` clean.

### Plan-vs-codebase drift adapted by implementers (worth knowing)

- Plan assumed HttpApi for API Gateway; actual stack is `apigw.RestApi`. Implementer used `addResource()/addMethod()` + `LambdaIntegration`.
- Plan assumed `this.dbClusterArn` etc. on api-stack; actual stack uses `props.dbCluster` with `grantDataApiAccess(fn)` covering Aurora secret read + `rds-data:ExecuteStatement` in one call.
- Plan said routes live in `web/src/routes/Shell.tsx`; actual routing is in `web/src/App.tsx`.
- Plan said link to `/connect-clouds`; actual cloud route is `/connect`.
- Risks page is a Risk register **table** (not finding cards as the spec assumed). ActButtons mount in the title cell; finding-id is synthesized from `r.finding_id ?? r.risk_id`. KK may want to relocate visually.

### Initial bring-up steps (still required if Slack app/SSM not done)

1. **Create the Shasta Slack App** at https://api.slack.com/apps:
   - Redirect URI: `https://api.shasta.io/v1/connectors/callback/slack` (exact match — Slack rejects drift)
   - **`token_rotation_enabled=true`** in the app manifest
   - User scopes: `chat:write,im:write,im:history,search:read,users:read`
2. **Put credentials in SSM** (in `us-east-1`, the deploy region):
   ```bash
   aws ssm put-parameter --name /cisocopilot/connectors/slack/client-id \
     --type SecureString --value "<client_id>" --overwrite
   aws ssm put-parameter --name /cisocopilot/connectors/slack/client-secret \
     --type SecureString --value "<client_secret>" --overwrite
   SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
   aws ssm put-parameter --name /cisocopilot/connectors/state-jwt-secret \
     --type SecureString --value "$SECRET" --overwrite
   ```

### Known follow-ups for Slice 2+

- The plan-of-record's autonomous-broadcast queue (SQS + `findings_subscriber`) is **not** in Slice 1. Slice 2 adds it.
- The pre-existing tools/ + voice_session/ test failures (5 total) are unchanged by Slice 1 — separate triage when convenient.
- KK may want to relocate the Risks-page Slack button if the table-cell placement looks cramped.
- Plan's spec mentioned `pgp_sym_encrypt`; implementation used Fernet (simpler, same security posture). Spec note in §5 explicitly approves this.

---

## 🚀 Wow Demo — voice-first agentic investigation shipped (2026-05-28, PR #32)

The 5-day wow-demo plan (`docs/superpowers/specs/2026-05-27-wow-demo-voice-investigation-design.md`)
shipped on `feat/wow-demo` in a single ~24h push. **43 commits.** Both demos
work end-to-end against real backends; recording artifacts are real, not
staged.

### What's live and verified end-to-end

| Layer | Verification |
|---|---|
| `voice_session` Lambda | coral voice + Shasta persona (peer/expert, chill cadence, cloud-correct vocabulary, hardcoded KAN + Ratanshi); `interrupt_response: True`; OpenAI Realtime GA (`gpt-realtime`); `temperature` removed (GA dropped it). 6 wow-demo tools registered. |
| `tools` Lambda (container, ECR `tools-lambda`) | `POST /v1/tools/{tool_name}` dispatcher with canonical `subject_from_claims` + arg-logging. Direct integrations: Slack Web API (email-or-name lookup), GitHub REST API, Microsoft Graph (msal), CloudWatch Logs Insights, EventBridge one-shot. mcp-atlassian for JIRA (the one MCP path that worked). |
| `device_token_register` Lambda | new `POST /me/device-token` — closes the HANDOFF-flagged Slice 1 follow-up. iOS AppDelegate → NotificationCenter → RootView → API. |
| `ai_supply_chain_matcher` Lambda + SQS queue | joins `findings.check_id='sca_vuln:*'` + `entities.kind='ai_framework'` matched by package name + `edges.kind='uses'` (the real graph; the plan's hypothetical `ai_agent` intermediate doesn't exist) + KEV. Emits `ai_supply_chain_active` at CRITICAL. Conn_id is looked up from the triggering scan row. |
| `forensic_callback` Lambda | EventBridge one-shot → push with `finding_id` for iOS deep-link routing. Tenant resolved from caller's JWT at `run_forensic_scan` time. |
| `ai_scanner` image with Trivy v0.70 | v0.55 was delisted from `get.trivy.dev` between plan and build — bumped to v0.70 + `dnf install tar gzip` (AL2023 doesn't ship them). Trivy emits 14 sca_vuln findings per scan on the demo repo; each with a distinct `check_id=sca_vuln:<CVE>` so findings don't dedupe-collapse. |
| `shasta_runner_entra` | personal-tier push trigger live — will fire push on each new `ai_signin_personal_tier` finding once Task 19 stages signins. |
| iOS app on KK iPhone 16 Pro Max | `IncidentRouter` + `BriefingView` + auto-mount `VoiceClient.start(seedDeveloperMessage:)`. APNs registered. `.videoChat` mode + `overrideOutputAudioPort(.speaker)` + route-change observer for speakerphone volume. Generic dispatchTool forwards unknown function names to `POST /v1/tools/{name}` so new server-side tools don't need iOS rebuilds. |
| Web | `FloatingChrome` (refer-a-friend + report-a-bug) on every authed shell. Brand lockup links to transilience.ai. |

### Demo B (AI Supply Chain) — fully shoot-ready

- Repo: github.com/kkmookhey/wow-demo-pricing-system with `langchain==0.0.184` + `services/pricing/agent.py` (LLMChain runtime use)
- `ai_supply_chain_active` finding (CRITICAL) in DB targeting langchain CVE-2026-45134
- KEV row: `threat_indicators` has `CVE-2026-45134` (kind=cve, source=kev, confidence 95)
- Smoke-tested live: real `KAN-5` + `KAN-7` JIRA tickets, real Slack DMs from `shasta_slack` bot to KK + Ratanshi + Venkat, real PR `#1` on the demo repo (closed). Forensic-scan staged result fires push at `~60s` ETA.
- Shooting script lives in this session's chat log (search "Demo B — AI Supply Chain — shooting script").

### Demo A (Shadow AI) — backend ready, data staging pending

Task 19 needs Ratanshi (or any real Entra user KK controls) signing into chatgpt.com via "Sign in with Microsoft" 3x. Then a manual Entra scan triggers the push automatically via Task 15's `_fire_personal_tier_pushes`. The matcher / forensic-callback / tool dispatch all run the same as Demo B from there.

### Production bugs caught + fixed during deploy (the load-bearing ones)

- Trivy v0.55 delisted from get.trivy.dev → v0.70 + AL2023 `tar/gzip` prereq
- Lambda runtime is read-only except `/tmp` → `npx -y` failed with EROFS on `/home/sbx_user*/.npm`. Switched to direct `mcp-server-{slack,github}` binaries + `HOME=/tmp` + `NPM_CONFIG_CACHE=/tmp/.npm`
- voice_session zip-asset bundles flat → `from voice_session.system_prompt import …` broke at cold start. Bare `from system_prompt import …`
- tools container `COPY . LAMBDA_TASK_ROOT/` flattened the `tools/` package → moved files under `LAMBDA_TASK_ROOT/tools/` with `CMD ["tools.main.handler"]`
- OpenAI Realtime GA dropped `session.temperature` (returned 400 invalid_parameter) → removed
- iOS sheet content doesn't inherit parent `@Environment` → BriefingView crashed on push tap. Spread `auth + api` into the sheet
- mcp-atlassian wraps the new issue under `result["issue"]["key"]`, not at top level → handle both shapes
- GitHub MCP `get_file_contents` returned malformed base64 (45 data chars — fundamentally invalid) for short manifests → bypassed MCP, direct REST. Same move worked for Slack (`@modelcontextprotocol/server-slack` doesn't expose `lookupByEmail`)
- Slack MCP needed `SLACK_TEAM_ID` env var alongside the bot token
- `entities.parent_id` doesn't exist — matcher SQL rewritten to use the real `github_repo --uses-> ai_framework` edge
- `findings.kind` doesn't exist — column is `check_id`. Three matcher SQL fixes
- `findings.conn_id` has FK to `cloud_connections` not `ai_connections` — matcher now looks up triggering scan's conn_id; `findings.scan_id` FK is to `scans`, not `ai_scans`
- Trivy findings emitted with same `check_id="sca_vuln"` → unique-key collapsed 14 CVEs to 1 row. Encoded CVE into check_id (`sca_vuln:CVE-...`)
- New `ai_supply_chain_active` findings with `resource_arn=null, frameworks={}` broke iOS Risks tab JSON decode → cleaned up shape (proper framework mapping + real resource_arn)
- Voice: `interrupt_response: False` caused "active response in progress" 400s every time user interrupted Shasta → flipped to True
- iPhone speakerphone too quiet → `.voiceChat` → `.videoChat` + explicit `overrideOutputAudioPort(.speaker)` on `RTCAudioSession` (not `AVAudioSession`) inside the WebRTC config lock + route-change observer
- Model invented `ratanshi@transilience.io` and defaulted JIRA to `ITSEC` (from the spec's example) → KNOWN PEOPLE block in system prompt pins the real values
- iOS APNs registration was a Slice 1 HANDOFF deferred follow-up — implemented end-to-end this session
- run_forensic_scan stored `conversation_id` but OpenAI Realtime doesn't expose its session id to the tool layer → resolve `tenant_id` from caller's claims at `run_forensic_scan` time, pass it through to `forensic_callback`. Push payload now includes `finding_id` (falls back to conversation_id or scan_id) so iOS routes to BriefingView.

### Aurora schema gotchas surfaced (worth folding into CLAUDE.md)

- `findings` PK is `finding_id` (not `id`)
- `findings.check_id` is the rule identifier — there is no `kind` column
- `findings.status` enum: `fail` / `pass` / `partial` / `not_assessed` / `not_applicable` — no `open`
- `findings.conn_id` NOT NULL, FK to `cloud_connections` (not `ai_connections`)
- `findings.scan_id` FK to `scans` (not `ai_scans`). The unified scans table is what AI findings reference
- `findings.frameworks` must be a JSON object (`{}`), not array — iOS decoder rejects arrays
- `edges.source_entity_id` / `target_entity_id` (not `source_id`/`target_id`)
- `entities` has no `parent_id` column — containment is expressed via `edges.kind='uses'/'imports'/etc.`
- `threat_indicators.indicator_value` (not `value`)
- AI graph emits `github_repo --uses-> ai_framework` directly. There is no `ai_agent` intermediate in the actual schema, despite what the plan assumed
- For voice session: `temperature` removed from session config; OpenAI Realtime GA's `interrupt_response: True` is critical for usable interaction

### What remains for the recording

- **Task 19 (Demo A data staging)** — KK manual: stage Ratanshi as a personal-tier ChatGPT user in Entra. ~30-60 min including propagation.
- **Task 21 (voice cadence iteration + record)** — Shoot Demo B now (script ready); shoot Demo A after Task 19; edit + post.

### PR

[#32](https://github.com/kkmookhey/ciso-copilot/pull/32) — 43 commits awaiting merge after recording verifies the demos look right.

### Branch state

`feat/wow-demo` is ahead of `main` by 43 commits. Two unrelated WIP files in `web/src/` from earlier branding sprint were folded into the funnel-chrome commit. Working tree clean.

---

## 🚀 Phase 2 Tier 3 — gitleaks history audit clean (2026-05-27)

Final gate before the MIT-public flip. Ran `gitleaks git --redact`
across 547 commits / 6.23 MB of repo history. Initial scan: 75
findings. After triage: **zero real credential leaks**.

**Triage summary:**
- **73 `generic-api-key` hits** — all match the literal Aurora Secrets
  Manager **resource name** (`AuroraPgSecretF5CEE99C-niqW1iheRsGP-
  BgwkPp`). Gitleaks's entropy heuristic flagged the CDK-generated
  suffix. The name is the resource identifier, not the secret value;
  reading the actual value requires `secretsmanager:GetSecretValue`
  IAM. Accepted per Phase 2 Slice A spec §3 non-goals (AWS resource
  identifiers are information disclosure, not credential leak). Tier 2
  PR #30 replaced these with `$DB_SECRET_ARN` in the current tree;
  historical commits still carry the literal name and that's fine.
- **2 `private-key` hits** — both are synthetic 2048-bit RSA test
  fixtures for the GitHub-App JWT-minting code path. One is the
  inlined `TEST_PRIVATE_KEY` in `platform/lambda/ai_github/tests/
  test_github_app.py` (current tree, with a comment marking it as
  hermetic-test-only); the other is the same key quoted in the
  Slice 1a planning doc as a code example. Generated via `openssl
  genrsa`. Never used for real signing.

**What's live:**
- `.gitleaksignore` allowlists all 75 fingerprints with a header
  documenting the triage rationale for each rule group. Any future
  contributor adding a fingerprint should pair it with a triage note
  in the PR.
- Post-allowlist re-scan: `no leaks found`. Future scans (manual or CI)
  return clean unless a genuinely new credential lands.

**MIT-public flip — DONE (2026-05-27).** Repo visibility flipped to
public by KK. Repository now lives at
`github.com/kkmookhey/ciso-copilot` under MIT license.

**Optional follow-up (deferred):**
- `.github/workflows/gitleaks.yml` CI gate so future PRs can't introduce
  real secrets undetected (Tier 3 "belt-and-suspenders" option,
  deferred per scope decision)

**PR:** [#31](https://github.com/kkmookhey/ciso-copilot/pull/31)

---

## 🚀 ICICI demo prep — 2 features + 4 bugs fixed (2026-05-27, PR #29)

Demo went well. ICICI Lombard's security + risk team. They run Prisma
Cloud as incumbent CSPM and have asked for a product brochure + Prisma
comparison — saved as a project memory under `project_icici_lombard_demo`
for future positioning work.

**Two features shipped:**

1. **`/ai/inventory` reframed around the autonomous-AI narrative.**
   Hero stat strip counts entities by kind: Agents / MCP servers / Tools
   prominent; Models / Vector DBs / Embeddings / Prompts / Frameworks as
   satellites. Two-section split: "Autonomous AI surface" (agents + MCP +
   tools) on top, "Models, data & prompts" below. Per-row "Discovered in"
   badge derived from `detector_id` (Code / AWS / Azure / Entra / GCP /
   Manual). `per_page` bumped to 200 with a truncation banner when capped.
   File: `web/src/routes/AIInventory.tsx`.

2. **AI Exposure Score on `/ai`.** Single 0–100 number above the existing
   Fail/Partial/Pass tiles. Formula: `round((1 - (fail*3 + partial*1) /
   (fail*3 + partial*1 + pass*1)) * 100)`, clamped 0–100. Verdict bands:
   90+ strong, 70–89 healthy, 50–69 needs attention, <50 critical. SVG
   donut ring (rotated -90° so 12 o'clock is the start, going clockwise).
   `computeExposureScore` + `verdictForScore` exported as pure functions
   so the formula is testable in isolation. Six new tests cover boundary
   bands + empty case + worked example matching the existing
   `/ai/summary` mock. File: `web/src/routes/AISummary.tsx`.

**Four production bugs caught & fixed** (this is the load-bearing part —
each had been broken silently for days or weeks):

1. **`entities_api._upsert_repo_entity` INSERTed into a `connection_id`
   column that no longer exists on `entities`.** The SP1 unified-entity
   migration (`005`) replaced `ai_assets` (which had the column) with
   `entities` (which doesn't). Postgres `42703` was caught by the
   top-level `try/except` and returned to the SPA as a generic 500 with
   zero CloudWatch breadcrumb. **Every `POST /ai/scans` had been broken
   since the Lambda redeploy on 2026-05-19 20:06 UTC** — nobody scanned
   a new repo in the eight days since, so nobody noticed. Also added
   `traceback.format_exc()` to the exception fence so future 500s
   actually log a stack trace.

2. **`ai_scans.repo_asset_id` FK still pointed at the retired
   `ai_assets` table.** Fix #1 unblocked the INSERT and immediately hit
   FK `23503`. Migration `014_ai_scans_repo_asset_fkey_to_entities.sql`
   repoints to `entities(id)` using `NOT VALID` so the 13 historical
   `ai_scans` rows are not re-validated. They become orphans referencing
   nothing in `entities`; `repo_asset_id` is NOT NULL so we can't null
   them. Acceptable — `ai_assets` is retired, rows are demo-tier. Applied
   live via the Data API; the migration file is the tracked record.

3. **Entra `ai_signin_pass` crashed silently on `datetime` from
   Microsoft Graph SDK.** SDK returns `auditLogs/signIns`
   `createdDateTime` as a real `datetime` object; Slice 2 tests used
   string fixtures, and Slice 2.1 verification used a Free-tier tenant
   that hits a 403 before reaching `signin_to_params`. **The entire AI
   sign-in pass had been silently emitting zero findings for any P1/P2
   tenant with real activity** — every Entra tenant before KK's
   production tenant either had no AI activity or was Free-tier. Fix:
   ISO-stringify at extraction (defensively via `hasattr(.isoformat)`).
   Regression test uses a real `datetime(..., tzinfo=utc)` matching
   production SDK behaviour.

4. **`/findings?cloud=entra` dropped Entra rows.** Three symptoms
   (`?cloud=entra` returned 0, Group By Cloud showed no Entra bucket,
   filter looked broken) all from one root cause — `TopRisks` fetched
   only top-200 findings sorted severity DESC, then filtered
   client-side. With ~800 findings dominated by AWS critical/high, the
   few low-severity Entra rows fell outside the response window before
   `cloudOf()` could classify them. Three changes: `cloudOf()` returns
   "Entra" for `domain === "identity"`; `listFindings()` receives
   `cloud` + `severity` URL params so the server filters before
   `LIMIT`; new discoverable cloud chip row on `/findings` so the
   filter is reachable without typing the URL.

5. **`ai_github._complete` returned a locally-generated UUID instead of
   the SQL `RETURNING id`.** On re-installs (where `ON CONFLICT` fires),
   the existing row's id stays unchanged but the API returned a new
   UUID — the SPA navigated to `/ai/connections/<unpersisted-uuid>/repos`
   which 404'd. Caught when KK reinstalled the GitHub App after the
   `$LEGACY_APP_DOMAIN` → `$SHASTA_DOMAIN` domain
   migration. Workaround used during the session: query
   `ai_connections` directly for the real id. Fix: read the
   `RETURNING` value into `persisted_id` and return that.

**Common cause across the four bugs:** every Python Lambda mocks
`_rds.execute_statement` in tests, so the test suite never validates SQL
against real Aurora schema. Same root cause for the `connection_id`, FK,
and ON CONFLICT bugs. Fixture-vs-real-SDK gap caused the `datetime` bug.
**Followup worth opening a brainstorm on:** lightweight integration
tests against a local Postgres (or sqlite with pg-compat layer) before
more features land on these surfaces. None of these would have shipped
broken if the test suite had run real SQL against a real schema.

**Live deploy log this session:**
- `entities_api`: hotswap deploy
- `shasta_runner_entra`: Docker image rebuilt + pushed to ECR
  (`sha256:6e0bb390d3...`) → Lambda re-resolved `:latest`
- Migration `014` applied via Data API; tracked in `platform/sql/`
- `ai_github`: hotswap deploy
- Web bundle: 4× S3 sync + CloudFront invalidation rounds during the
  session as each fix landed

**Deferred / followups (none blocking):**
- HANDOFF.md entry → this block (closes the loop)
- `Finding` API response should carry server-derived `cloud` field so
  the SPA stops guessing from ARN substrings (the current heuristic
  also misses Azure findings whose ARN format doesn't include "azure"
  or "microsoft." substring)
- Integration tests against real Postgres (the meta followup — see above)
- ICICI brochure + Prisma comparison deck (KK to draft; positioning
  guidance saved to memory)

**Files:**
- `web/src/routes/AIInventory.tsx` (rework)
- `web/src/routes/AISummary.tsx` + `.test.tsx` (Exposure Score + tests)
- `web/src/routes/TopRisks.tsx` (cloudOf + filter passing + chip row)
- `platform/lambda/entities_api/main.py` (`_upsert_repo_entity` +
  traceback logging)
- `platform/sql/014_ai_scans_repo_asset_fkey_to_entities.sql` (new)
- `platform/lambda/shasta_runner_entra/app/ai_signin_pass.py` + test
- `platform/lambda/ai_github/main.py` + test

**Commits on `feat/ai-demo-polish-and-fixes`:**
- `852a353` feat(ai-inventory): hero strip + autonomous AI section + discovered-in badges
- `2dbbc36` feat(ai-summary): AI Exposure Score headline with weighted formula
- `3b9f361` fix(entities-api): drop bogus connection_id from entities INSERT
- `4f96f31` chore(sql): migration 014 — repoint ai_scans FK to entities
- `e59c2f7` fix(entra-scanner): ISO-stringify Graph SDK datetime in ai_signin evidence
- `0d0b229` fix(findings): make Entra findings actually visible
- `ebf024e` fix(ai-github): return persisted conn_id from RETURNING on ON CONFLICT

**PR:** [#29](https://github.com/kkmookhey/ciso-copilot/pull/29) merged as `fa39589` (2026-05-27 14:05 UTC).

---

## 🚀 AI page UX polish — shipped (2026-05-26, PR #27)

Small UX layer on top of Slice A. Two changes:

1. **`/ai` is now in the left nav** — `ModuleRail.tsx` gets an "AI" item
   pointing at `/ai`, alongside the existing "AI inventory" item.
   (KK's longer-term proposal of folding `/ai/inventory` into `/ai`
   as panels is deferred to a separate brainstorm.)
2. **Score + source tiles on `/ai` are clickable.** The Fail/Partial/Pass
   score tiles drill into `/findings?status=<key>`; the
   AWS/Azure/Code/Entra source tiles drill into `/findings?cloud=<key>`.
   Hover transitions added so the affordance reads correctly.

Files: `web/src/chat/ModuleRail.tsx`, `web/src/routes/AISummary.tsx`.
Commit `d569fc5` (squashed from `c36ddbe` on `feat/ai-nav-quick-win`).
Deployed: S3 sync + CloudFront invalidation in-flight at merge time.

**Deferred** (the bigger move from the same KK ask):
- Fold AIInventory into `/ai` as panels (inventory + findings side-by-side
  with the score/source tiles). Needs its own brainstorm — trade-off is
  one busy page vs two focused pages.

---

## ✅ iOS device install — shipped (2026-05-27)

Slice A5's iOS code (xcconfig wiring + source Info.plist + `APIClient.baseURL`
reads from `Bundle.main`) is installed on KK iPhone 16 Pro Max
(devicectl identifier `<IOS_DEVICE_UDID>`). Build path:

```
/Users/kkmookhey/Projects/CISOBrief/ios/build-device/Build/Products/Debug-iphoneos/CISOCopilot.app
```

`plutil` confirmed `API_BASE_URL` substituted correctly into Info.plist. Build
+ install ran clean (Tier 2 sanitization PR #30 had no iOS source changes; only
the doc tokens shifted). Bundle ID `ai.transilience.cisocopilot`, signed with
provisioning profile `iOS Team Provisioning Profile: ai.transilience.cisocopilot`.

**Smoke test — VERIFIED (2026-05-27, KK):** opened app on phone, Google
sign-in worked, APNs push landed within ~60s after triggering a drift
event with the expected body
(`drift · <severity> · <resource> · <eventName> · by <user>`). End-to-end
device-install + push-delivery path is now confirmed working.

If a future iOS rebuild is needed, the canonical command lives in
`CLAUDE.md` → Common commands → iOS build (device) block.

---

## 🚀 Phase 1 team-ready — shipped (2026-05-26)

Branding + product docs + MIT license all shipped end-to-end in one
session. The repo is now invitation-ready for the Transilience team.

**What's live and deployed to `$SHASTA_DOMAIN`:**
- Brand pass — every web surface now reads "Shasta by Transilience"
  (no mobius mark — text-only lockup); sign-in / callback / pending
  routes use a `<HeroLockup>` with persimmon-underlined chapter
  headings ("Sign in.", "Pending review."); the `ModuleRail` sidebar
  has the brand block at the top
- Browser title flipped from "web" to "Shasta by Transilience"
- Quiet Paper palette retained; no Tailwind token changes; no SVG
  asset work shipped (mid-flight pivot from a hand-rolled mobius —
  see memory `feedback_branding_typography_over_marks`)

**Product docs (repo-root, committed in `a575805`):**
- `README.md` — lead magnet: "Full Stack Security OS" thesis,
  four-quadrant capability map, four surfaces, shipped-modules
  timeline (13 rows back to 2026-05-16), light "how this was built"
  section, honest run-locally pointer
- `ARCHITECTURE.md` — system diagram + unified findings model + CME-v2
  pipeline + SOC pipeline + identity-auth gotchas + 15 ADRs +
  operational concerns
- `ROADMAP.md` — Phase 1–4 plan (team-ready → commerce-ready → SOC
  depth + cross-cloud parity → GA-ready), M1–M7 heavy lifts with
  phase-based sequencing, future arenas (DSPM / CTEM / MDR / privacy
  / safety), and the anti-roadmap

**License + AI memory:**
- `LICENSE` flipped from proprietary to MIT (`Copyright (c) 2026
  Transilience.ai`). Repo stays private until secrets audit (Phase 2);
  MIT terms apply from day one for anyone running their own copy.
- `CLAUDE.md` — project name flipped to "Shasta by Transilience";
  session-start reading list now 6 docs (HANDOFF → README →
  ARCHITECTURE → ROADMAP → CISOBrief-v2 → CISOBrief). Read in that
  order at the top of every session.

**Commits:**
- `a575805` docs(shasta): product docs trio + MIT license + CLAUDE.md
  session-start refs (7 files, +1796 / -38)
- `d25df9d` feat(branding): apply "Shasta by Transilience" lockup
  across web (7 files, +318 / -36)

Both pushed to `origin/main`.

**Specs written this session** (under `docs/superpowers/specs/`):
- `2026-05-26-shasta-docs-trio-design.md` — the brainstorm that locked
  the README + ARCHITECTURE + ROADMAP structure
- `2026-05-26-shasta-branding-design.md` — the branding brainstorm
  (updated post-pivot to reflect text-only lockup as shipped)

**Deferred from this session:**
- Screenshots of the branded UI in the README (was a Phase 1 task;
  punted until KK takes them after team play-through)
- iOS branding (app icon + splash + nav) — its own sprint, post-team
  feedback
- Team email + GitHub `About` update — KK to execute manually with
  drafts handed off in-session

**▶ NEXT** — Phase 2 (commerce-ready): capability gating, the billing
module sub-phases (usage tracking → customer dashboard → caps →
Stripe), and then SOC Slice 2 (identity drift) after billing. See
`ROADMAP.md` for the phase plan.

## 🚀 Phase 2 Slice A — Secrets / hardcoded-identifier extraction (2026-05-26)

Last code-side gate before the MIT-public flip. Every hardcoded AWS account ID,
API Gateway URL, ARN, personal email, and redirect URI in `platform/lib/`,
`platform/bin/`, `platform/lambda/onboarding_*/`, `web/src/`, `ios/`, and
`scripts/` now reads from env-var configuration. A new operator copies
`.env.example` → real, `cdk deploy`, gets a working deployment with their
own AWS account.

**What's live and deployed:**
- `platform/.env` augmented with `AWS_ACCOUNT_ID`, `SHASTA_DOMAIN`,
  `API_BASE_URL`, `WEB_REDIRECT_URI`, `APP_DOMAIN`, `ADMIN_EMAILS`,
  `APNS_PLATFORM_APP_ARN`, `APP_CERT_ARN`, `LEGACY_APP_DOMAIN`,
  `DB_CLUSTER_ARN`, `DB_SECRET_ARN`. `platform/lib/config.ts` exposes
  them via the existing `required()` helper.
- `web/.env.production` (gitignored) + `web/src/lib/env.ts` Vite boundary.
- `ios/Local.xcconfig` (gitignored) + Info.plist substitution; `APIClient.baseURL`
  reads from `Bundle.main.infoDictionary`.
- `/me` now emits `is_admin: bool` computed server-side from `ADMIN_EMAILS`.
  Both `web/src/{chat,routes}/Shell.tsx` drop their local allowlist
  constants and consume `me.user.is_admin`. Personal emails are out of the
  production JS bundle.
- `platform/cfn/aws-onboard.yaml` drops the three hardcoded `Default:`
  values; the onboarding deep-link in `onboarding_aws_initiate` always
  passed them explicitly, so customer onboarding is unchanged.
  Azure + GCP onboard scripts now require `CISO_COMPLETE_URL` / `AWS_ACCOUNT_ID`
  (the initiate Lambdas already inject them).
- Test fixtures use `999999999999` instead of the real account ID across
  `ai_scanner/`, `soc_enrichment/`, `event_router/` test suites.
- Loose `.p8`/`.pem`/`.env` files relocated from repo root to
  `~/.shasta/secrets/`. `workers/` directory deleted (v1 sunset).

**Final grep audit:** zero hits for `$AWS_ACCOUNT_ID`, `<API_GW_ID>`, or
`<admin-email-prefix>` in any source file under `platform/lib/ platform/bin/
platform/lambda/ platform/cfn/ web/src/ ios/CISOCopilot/ scripts/`. The
only remaining account-ID leak is `platform/cdk.context.json` (standard
CDK practice; accepted per spec §3 non-goals) and `docs/superpowers/`
historical specs/plans (Tier 2 doc sanitization is a separate session
before the MIT flip).

**Deferred to a later session:**
- Tier 2 — sanitize `HANDOFF.md`, `TEST_PLAN.md`, `CLAUDE.md`,
  `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md`.
  Line-by-line redaction across ~30 files.

**Spec:** `docs/superpowers/specs/2026-05-26-secrets-extraction-design.md`
**Plan:** `docs/superpowers/plans/2026-05-26-secrets-extraction-plan.md`
**Branch:** `feat/secrets-extraction`
**Verify:** `TEST_PLAN.md` post-A7 grep gate verified

## 🚀 SOC Slice 1c — shipped & manual gate verified (2026-05-26, PR #25)

AI-powered SOC sub-project Slice 1c — threat-intel substrate — is built
end-to-end and deployed to `CisoCopilotEvents` on `feat/ai-powered-soc-slice-1c`.
Every drift event surfaced in `/soc` now does an IP/domain/sha256 lookup
against a global `threat_indicators` table populated by three cron-driven
feed Lambdas, with an on-demand GreyNoise Community fallback (disabled
until a key lands in `ciso-copilot/greynoise-api-key`). Matches feed back
into the Claude prompt and surface as labeled badges in `/soc` DetailPane.

**What's live and deployed:**
- `threat_indicators` global table + `events.source_ip` column (migration
  `013_phase_soc_ti.sql`, idempotent re-runnable)
- Three cron Lambdas: `TiFeedAbusechFn` (hourly Feodo + ThreatFox),
  `TiFeedKevFn` (daily CISA KEV), `TiFeedTorFn` (hourly Tor exits) — all
  use stdlib `urllib.request` (no third-party deps, tiny zips)
- New `platform/lambda/_shared/` directory with `ti_lookup`, `ioc_extract`,
  `greynoise` modules, vendored via `cp` into each consumer's
  `build.sh` — promotes the Slice 1 deferred follow-up
- `event_router` captures CloudTrail `sourceIPAddress` into the new
  `events.source_ip` column
- `soc_enrichment.features` extended with `_ti_matches(row)` — DB
  lookup + GreyNoise fallback for unmatched IPs (cap 5/event)
- LLM SYSTEM prompt updated to use `features.ti_matches` and name
  matched sources/tags in the narrative
- `/soc` DetailPane renders "Threat intel" badges from
  `ai_features.ti_matches` (zero API contract change — `ai_features`
  was already `Record<string, unknown>`)
- Per-tenant GreyNoise rate limit (30/day) wired via the existing
  `soc_llm_spend_daily` DynamoDB table under sort-key prefix
  `greynoise_count:`

**Aurora state after seed (2026-05-26):**
```
abusech_feodo     | 5
abusech_threatfox | 2842
kev               | 1602
tor               | 1277
                  ----
total             | 5726
```

ThreatFox dedup at PK level: Lambda returned 3,235 rows but ON CONFLICT
collapsed to 2,842 unique `(value, kind, source)` tuples — the source
serves multiple rows per IOC with different `threat_type`/`malware`
combinations; we keep the latest.

**Lessons paid in execution (now baked into code/comments):**
1. **Python 3.14 reclassified RFC 5737 doc ranges** (192.0.2.0/24,
   198.51.100.0/24, 203.0.113.0/24) as `is_private=True`. The plan's
   original `ipaddress.IPv4Address(value).is_global` filter would have
   stripped `203.0.113.5` from a positive test. Fixed with an explicit
   `_SKIP_NETS` CIDR list covering RFC1918 + loopback + 0.0.0.0/8 +
   link-local + multicast + reserved + broadcast. **Also added CGNAT
   100.64.0.0/10** per code-quality review (RFC 6598).
2. **CDK hotswap re-bundling:** when `build.sh` changes the contents
   of the asset folder but the path is the same, `npx cdk deploy
   --hotswap` may skip re-uploading the zip. Use `--hotswap --force`
   (HANDOFF lesson #7 from Slice 1 still holds, now with a Slice 1c
   data point).
3. **ThreatFox `ioc_type=ip:port`:** values arrive as `10.0.0.1:443`;
   strip the port before storing as `kind='ip'`. Easy to miss.
4. **Web `getByText` vs `getAllByText`:** the same TI source/tag
   strings appear in BOTH the new "Threat intel" badge block AND the
   existing "Why this fired (features)" JSON dump. `getByText` throws
   on multiple matches — use `getAllByText(...).length >= 1` for those
   duplicated strings; reserve `getByText` for unique-only strings
   like the section header.
5. **`_shared/` promoted to a real directory.** The Slice 1 deferred
   follow-up "lift to `platform/lambda/_shared/`" is done. Each
   consuming Lambda's `build.sh` now does
   `cp ../_shared/*.py build/`. Lift to a Lambda Layer remains
   deferred (separate version-management surface).
6. **AbuseCH ThreatFox dedup at PK level.** `(value, kind, source)`
   composite PK with `ON CONFLICT … DO UPDATE` means repeat sightings
   are non-destructive: `last_seen` refreshes, `confidence` upgrades
   via COALESCE, `first_seen` preserved.

**Deferred follow-ups (logged, not blockers):**
- **Manual gate (KK-pending):** Run the Slice 1c gate in
  `TEST_PLAN.md` from a Tor exit IP. ~30 min including IP-egress setup.
- **GreyNoise key:** Wire a real key into
  `ciso-copilot/greynoise-api-key` Secrets Manager once an account is
  provisioned. Until then `_greynoise_api_key()` returns None and the
  on-demand path silently disables — DB-side feeds (Feodo / ThreatFox
  / KEV / Tor) cover the demo wedge.
- **URLhaus + MalwareBazaar abuse.ch sub-feeds** — not yet ingested.
  Same pattern as ThreatFox; would extend `parse_threatfox`-style
  functions in the existing `ti_feed_abusech` Lambda.
- **`_shared/` → Lambda Layer.** `cp` vendoring works; lifting to a
  Layer would deduplicate ~10 KB across four Lambdas but adds a
  separate version-management surface.
- **`/soc` filter chip "TI hits only"** — defer to first usage signal.
- **TI on Slice 2 (identity drift) events** — falls out for free
  thanks to the kind-agnostic `ioc_extract`; Slice 2 events flow
  through the same `_ti_matches` step automatically.

**Spec:** `docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md` §6 + §4 TI addendum
**Plan:** `docs/superpowers/plans/2026-05-25-ai-powered-soc-slice-1c.md`
**Branch:** `feat/ai-powered-soc-slice-1c` (12 commits + plan + docs + this HANDOFF block)
**PR:** [#25](https://github.com/kkmookhey/ciso-copilot/pull/25) awaiting merge
**Gate:** `TEST_PLAN.md` → "SOC Slice 1c — TI match end-to-end" — **VERIFIED** (2026-05-26)

### Gate execution notes (the substrate edge cases worth remembering)

1. **`torsocks` is broken on modern macOS.** DYLD injection is blocked
   by SIP for system binaries (`/usr/bin/curl`), AND silently no-ops
   on `/opt/homebrew/opt/python@3.14/bin/python3.14` even though
   that binary isn't SIP-protected — `check.torproject.org/api/ip`
   returned `{"IsTor":false}` while torsocks ran. Use `proxychains-ng`
   (`brew install proxychains-ng` → `proxychains4 -q -f <conf>
   python3 ...`) instead. Reaches `{"IsTor":true}` on the same setup.
2. **`proxychains-ng` does NOT wrap AWS CLI v2.** `/usr/local/bin/aws`
   is a Mach-O universal binary with statically-linked Python — DYLD
   injection is bypassed. The egress IP leaked back to the host's real
   IP without warning. The fix: monkey-patch `socket` in a tiny Python
   script (`pip install pysocks` in a venv → `socket.socket =
   socks.socksocket` BEFORE `import boto3`). boto3 then connects via
   the SOCKS proxy on every call. Confirmed working with Tor exit
   `192.42.116.98` showing up as the `sourceIPAddress` in CloudTrail.
3. **`proxies={'https': 'socks5h://...'}` in `botocore.config.Config`
   does NOT work.** botocore's proxy handling expects HTTP proxies; it
   prepends `http://` to the URL, producing
   `http://socks5h://127.0.0.1:9050` and a `ProxyConnectionError`. The
   socket monkey-patch is the right interface boundary for
   SOCKS-via-boto3.
4. **End-to-end latency in the gate:** 17:22:24 UTC (Tor-routed SG
   open) → enrichment complete and narrative naming Tor visible in
   `/soc` by 17:23:00 UTC. ~35s end-to-end, well within the spec's
   p95 <30s target (event_router fires push within ~10s; enrichment
   adds ~20-25s for the Anthropic round trip).
5. **The negative case is also useful telemetry.** Same SG, same actor,
   same time-of-day — only `source_ip` changes (KK's real IP vs Tor).
   Anomaly score moves 72 → 82, narrative shifts from "No TI hits" to
   "matches Tor exit node". Cleanly shows TI is doing real work in the
   classifier output, not just decorating the UI.

### Polish landed during the gate

- **"Threat intel" badge block moved above the features `<details>`**
  in `DetailPane.tsx`. Reading order is now narrative → anomaly →
  next-steps → **TI badges** → features (collapsed by default) →
  related findings. Badges sit above the fold in the common case.

### Deferred follow-ups

- **GreyNoise Community wiring deferred until 100+ customers.** The
  on-demand fallback is code-complete and silently disabled (env var
  `GREYNOISE_API_KEY_SECRET_NAME` points at an absent secret →
  `_greynoise_api_key()` returns None → enrichment skips the
  on-demand path). The four cached feeds (Feodo + ThreatFox + KEV +
  Tor) cover the demo wedge and the realistic-scale wedge for v1
  pilots. Free tier is 50 req/key/day; with a per-tenant 30/day cap
  we can serve ~1-2 tenants per key. Worth provisioning a paid plan
  + the secret once tenant count exceeds ~50 (single Community key
  becomes the binding constraint).

---

## 🚀 SOC Slice 1 — shipped & demo-gate verified (2026-05-25, PR #24)

AI-powered SOC sub-project Slice 1 live end-to-end and **verified** with
a real `AuthorizeSecurityGroupIngress` on the test AWS account. ~25s
from API call → enriched event in `/soc` with AI-written narrative,
anomaly classification (suspicious, score 88 on RDP-open-to-world),
3 suggested next-step CLI commands, and feedback buttons.

**Pipeline:** customer EventBridge rule → central bus
`ciso-copilot-events` → `event_router` (source_event_id dedupe + Config
severity rule table over before/after state + push rule with per-tenant
rate limit + SQS enqueue) → `soc-enrichment-queue` → `soc_enrichment`
Lambda (statistical features + LiteLLM → claude-sonnet-4-6 + UPDATE
events row) → `/soc` web page + APNs push.

**What's live and verified:**
- `/soc` web page — timeline + filter chips (severity/source) + detail
  pane (AI narrative + anomaly score + 3 next-step CLI commands +
  features disclosure + related findings + 👍/👎 feedback)
- `soc_enrichment` Lambda with LiteLLM abstraction
  (`SOC_ENRICHMENT_LLM_MODEL` env, defaults `claude-sonnet-4-6`,
  swappable to any LiteLLM-supported provider)
- Per-tenant daily LLM spend cap ($10 default) in DynamoDB
  `soc_llm_spend_daily`
- Per-tenant push rate limit (10/hr default; criticals bypass)
- Schema migrations 011 (AI fields + source_event_id partial unique
  index + mitre_technique + nullable incident_id + drift target_arn)
  + 012 (users.device_token)
- AWS Config essentials recording profile (~$30-80/mo customer cost
  vs $200+ for all-resources)
- APNs sandbox SNS Platform Application provisioned with the .p8 at
  repo root

**Lessons paid in debugging during the gate** (now baked into
CLAUDE.md as project conventions):
1. **Cognito subject extraction:** always use `identities[0].userId`
   first, fall back to `claims.sub`. For federated logins the Cognito
   pool's `sub` is NOT the upstream IdP sub — JOINing
   `users.sso_subject = sub` silently 401s every federated user.
   Mirror `voice_session._subject_from_claims`.
2. **EventBridge rules for "AWS API Call via CloudTrail" events: never
   filter on `source`.** Real management API events arrive with
   `source: aws.<service>` (aws.ec2, aws.iam, …), never aws.cloudtrail.
   The original `aws-onboard.yaml` had `source: [aws.cloudtrail, …]`
   which silently dropped every customer's management event.
3. **CloudTrail wraps lists as `{"items": [...]}`** while Config gives
   raw lists. Predicates over after_state must unwrap (helper
   `_unwrap_items` in `severity_rules.py`).
4. **LiteLLM's Anthropic provider rejects `response_format` param.**
   Set `litellm.drop_params = True` at module load; rely on the SYSTEM
   prompt to enforce JSON.
5. **Claude wraps JSON in markdown fences** when not given a strict
   response format. Defensive parser strips ` ```json ` ... ` ``` `
   before `json.loads`.
6. **Postgres `ON CONFLICT` against a partial unique index** must
   include the WHERE clause matching the index predicate; without it
   you get `42P10 no matching constraint`.
7. **CDK hotswap "no changes detected" lies** when only the Lambda
   code inside the asset folder changed; use `--force` to re-bundle.

**Deferred follow-ups (logged, not blockers):**
- **iOS device registration end-to-end** (~half day) — closes the
  "iPhone vibrates in 60s" demo line. Needs (a) iOS app calls
  `registerForRemoteNotifications()` + handles
  `didRegisterForRemote...` callback, (b) new `POST /me/device-token`
  endpoint, (c) iOS POSTs the token after capture. Until this ships,
  push code in event_router gracefully no-ops on null
  `users.device_token`.
- LLM spend cap is read-then-add (race window bounded ~5-10× per-call
  cost near cap)
- CDK doesn't auto-bundle `soc_enrichment` zip — manual `./build.sh`
  required before each deploy
- APNs Platform Application ARN hardcoded as literal in
  `events-stack.ts` (should move to env / SSM Parameter)
- `spend_cap.py` symlinked between event_router and soc_enrichment for
  tests; vendored at build time (lift to `platform/lambda/_shared/`)

**Spec:** `docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md`
**Plan:** `docs/superpowers/plans/2026-05-25-ai-powered-soc-slice-1.md`
**PR:** [#24](https://github.com/kkmookhey/ciso-copilot/pull/24) merged as `3ce55b8`
**Gate:** documented in `TEST_PLAN.md` → "SOC Slice 1 — AWS Config drift end-to-end"

---

## ▶ NEXT: SOC sub-project roadmap

Per design spec §3, four remaining slices in priority order. Pick based
on session bandwidth + product wedge wanted.

### Slice 1c — Threat-intel substrate (~2 weeks) — SHIPPED 2026-05-26

See the "🚀 SOC Slice 1c" block at the top of this file. Live in
Aurora with 5,980 IOCs; manual gate verified end-to-end via a real
Tor-routed `AuthorizeSecurityGroupIngress` API call; PR #25 merged.

### Slice 2 — Identity drift (~3 weeks) — RECOMMENDED NEXT

**Spec section:** `2026-05-25-ai-powered-soc-design.md` §3 (Slice 2
row), §10.2 (Entra), §5 components table (Entra audit poller row).

**What it builds:**
- Add AWS IAM CloudTrail events to the severity rule table (already
  half-there — `event_router`'s pattern includes them, just needs
  identity-specific severity classification: `AttachUserPolicy`,
  `CreateAccessKey`, `UpdateAssumeRolePolicy`, `PutUserPolicy`, etc.)
- New `soc_entra_poller` Lambda — 5-min cron polling Microsoft Graph
  `auditLogs/directoryAudits` + `auditLogs/signIns` filtered for risk
  events. Reuses the existing Entra connection (no new admin-consent
  flow — the Graph scope `AuditLog.Read.All` was added for AI
  Visibility S2.1 and is already consented on the test tenant).
- Extend severity rule table with identity-drift actions: role
  assignment, OAuth consent grant, conditional access changes, MFA
  enforcement changes, federation trust changes.
- `/soc` filter chip gains an "Identity" source filter.

**Product wedge:** "New admin role assigned at 3am" demo moment.
Identity drift is arguably the highest-leverage CISO signal — covers
both cloud IAM (AWS) and SaaS-identity (Entra → Microsoft 365).

**Why next (now that 1c shipped):**
1. Slice 1c is in production — identity events flowing through the
   same pipeline will get TI enrichment for free. A new IAM access
   key created from a Tor exit will surface with the same badge UX
   that Slice 1c just demonstrated.
2. `event_router` already normalizes CloudTrail mgmt events; the
   work is mostly (a) the Entra poller Lambda + Graph-API plumbing,
   and (b) extending `severity_rules.py` with identity-action rows.
3. The `mitre_technique` column is pre-committed but unused — Slice 2
   gives the LLM real ATT&CK material to map (T1098 Account
   Manipulation, T1136 Create Account, T1556 Modify Authentication,
   T1078 Valid Accounts).

**Brainstorm needed:** Some — Entra polling vs webhook subscriptions
(spec recommends polling for v1, webhooks later as Slice 2.5);
severity rule expansion for identity actions; deciding whether to
ingest Entra sign-ins (P1/P2-gated per S2.1 lessons) in v1 or punt
to Slice 2.5.

**Pre-req:** none new — Entra connection + AI Visibility S2.1 banner
already shipped + scope already in place.

### Slice 3 — Anomaly baseline activation (~2 weeks)

**Spec section:** `2026-05-25-ai-powered-soc-design.md` §3 (Slice 3
row), §5.1 (baseline computation).

**What it builds:** Activates the statistical features that today fire
on a 30d rolling window from `events`. After ~7d of per-tenant
observation, AI prompt gets richer baseline summaries (per-actor
typical hours, typical resources touched, typical action set). "First
time anyone has touched IAM at 3am" callout becomes possible.

**Why third:** Needs Slice 1c + Slice 2 worth of event volume in
`events` table to actually have a baseline. Building it before then
gives an empty prompt.

### Slice 4 — Azure (no Sentinel) + GCP (~4 weeks parallel)

**Spec section:** `2026-05-25-ai-powered-soc-design.md` §10.3, §10.4.
**NEVER Sentinel** (see `feedback_no_azure_sentinel.md` memory).

**What it builds:** Azure path (Activity Log → Diagnostic Settings →
Event Hub → Lambda consumer + Azure Policy + Resource Graph change
feed + Defender-if-on). GCP path (Cloud Asset Inventory feed → Pub/Sub
+ Cloud Audit Logs → log sink → Pub/Sub + SCC findings-if-on).

**Why last:** Requires Azure + GCP scanner uplift to be solid first
(per `project_aws_scanner_uplift.md` memory — Azure uplift was next
after AWS uplift wrapped). Highest engineering surface, parallel work.

---

## 🚀 Compliance Mapping Engine v2 — shipped end-to-end (2026-05-25)

The architectural reset KK ordered ("Shasta isn't our binding framework.
We are building something phenomenally better… built it once, built it
right, architecturally sound for multi-modal evidence collection.") is
fully live on `main`. CME-v2 is now the binding crosswalk between scanner-
emitted control IDs and canonical published framework formats.

**What's live (PRs #17–#20):**

- **Two-stage pipeline** (`normalize` → `augment`) in `framework_registry.py`
  runs on every finding write. `unified_writer.commit_scan` (AWS/Azure/GCP/
  ai_scanner) + `shasta_runner_entra._enrich_param_lists_with_registry`
  (Entra) both hook the pipeline.
- **`ai_framework_registry.json`** declares 8 frameworks with `family`,
  `source_url`, `version`, `canonical_format`, `rewrite_rules` (~65 entries),
  `control_descriptions` (12 NIST §2 risks, 16 ATLAS techniques, 9 EU AI
  Act articles, 10 OWASP LLM, 2 ISO 42001, plus all NIST AI RMF GOVERN/
  MAP/MEASURE/MANAGE subcategories).
- **13 canonical augment rules**: 3 Slice E ai_signin_* + 10 baseline AWS
  check_id rules. All emit canonical published-format IDs directly so the
  augment stage does not need a second normalize pass.
- **`/ai/summary` + `/compliance/summary`** return `frameworks_meta` carrying
  `{name, family, source_url, version}` per framework.
- **Web `/ai` + Dashboard** render family-grouped tiles (security / ai /
  industry). All framework tiles + the `/findings?framework=` filter chip
  carry the §14.1 disclaimer ("Mapping only — not a compliance attestation.
  Verify with your auditor.") on hover.
- **CloudWatch observability**: `registry_apply_summary`,
  `normalize_rewrote_count`, `normalize_passthrough_count` log per scan.
- **Provenance**: every finding records `evidence_packet._registry_rule_ids`
  with the IDs of every rule that fired.
- **Forward-compat for §17.1 Findings History + §17.2 Evidence Ingestion**
  preserved in the spec — both deferred to future sub-projects.

### Tasks #53 + #54 follow-up (PR #21)

After CME-v2 shipped, the Shasta-shorthand tail still leaked. PR #21 closes
it strictly per D-2 ("canonical published format is binding"):

- **NIST AI 600-1**: all 12 Shasta `GAI-N` IDs now rewrite to canonical
  `NIST.AI.600-1:2.N` section anchors. GAI-5 and GAI-8 are multi-target where
  NIST splits the concern across two sections. GAI-6 (Data Poisoning) and
  GAI-7 (Prompt Injection) are documented as Shasta extensions and mapped
  to the closest NIST anchors (`§2.9` + `§2.12` for the former, `§2.9` for
  the latter).
- **2 NIST risks now in registry that no Shasta check covers**: `§2.1 CBRN
  Information or Capabilities` and `§2.10 Intellectual Property`. They sit
  in `control_descriptions` so a future baseline-rule can attach them
  without retroactively adding registry entries.
- **MITRE ATLAS labels**: `control_descriptions` values converted from
  plain strings to `{name, description}` so the canonical v4 technique
  short-name (e.g. `AML.T0010` = "AI Supply Chain Compromise") is registry-
  canonical instead of trusting potentially-drifted Shasta check titles.
- **Stray `GOVERN 1.6`** removed from `nist_ai_600_1.control_descriptions`
  (it belongs only in `nist_ai_rmf`).

### What you'll see post next rescan

Existing Aurora rows tagged `GAI-N` stay `GAI-N` until next purge — per
KK's "don't backfill; we purge periodically pre-prod" stance from the
CME-v2 spec. After the next AWS Medium rescan:

- New findings land tagged `NIST.AI.600-1:2.X` in `findings.frameworks`
  JSONB instead of `GAI-N`.
- `/findings?framework=nist_ai_600_1` continues to populate (the framework
  KEY didn't change; only the IDs within it did).
- The 10 new baseline rules (`baseline_bedrock_*`, `baseline_sagemaker_*`,
  `baseline_cloudtrail_ai_events`, `baseline_s3_training_data_versioned`)
  fire from the AWS scanner image already in ECR and produce visible
  CloudWatch `registry_apply_summary` deltas.

### Tests (full suite green)

- `pytest platform/lambda/scanner_core/tests/` — **70 passed**
- `pytest platform/lambda/shasta_runner_entra/app/tests/` — 12 passed
- `pytest platform/lambda/ai_summary/tests/` — 2 passed
- `pytest platform/lambda/findings_list/tests/` — 4 passed (needs DB env-var
  stubs; see Open items)

### Known UX gaps logged (not blockers)

- ~~Framework tiles on `/ai` link to source docs but don't drill into
  `/findings?framework=<key>`.~~ **Done in PR #22 (`f62c3f4`)** —
  `AISummary.tsx:157` is the drill-down `<Link>`; the ↗ source-doc `<a>`
  carries `stopPropagation`.
- ~~Redundant Entra ID P1/P2 hint copy in `AISummary.tsx:87` now overlaps
  with the Slice 2.1 connect-page banner~~ **Done in PR #22 (`f62c3f4`)** —
  empty-state copy is now "Connect Entra (see Connect for any licensing
  notes) to populate this", a cross-link rather than a redundant P1/P2
  hint. Test file uses `MemoryRouter` per the `<Link>` switch.

## 🛠 `/ai` endpoint restored (2026-05-24)

Reactive fix during S2.1 verification — `/ai` was returning **500
Internal Server Error**. Root-cause + fix below; the lesson is the
load-bearing part.

**Symptom:** `https://$SHASTA_DOMAIN/ai` → "Failed to load
AI summary: 500 {\"message\": \"Internal server error\"}". `GET
/v1/ai/summary` without auth returned 401 (so the route looked
deployed), but `aws lambda list-functions` had no `AiSummaryFn` and
the deployed CFN template had **zero** `AiSummary*` references.

**Root cause:** `CisoCopilotApi` CFN events showed a deploy at
**2026-05-23 19:28 UTC** that explicitly DELETED `AiSummaryFnFB51D79F`,
`RestApiaisummaryC0CCE8FD` (the `/ai/summary` resource), the GET +
OPTIONS methods, both Lambda permissions, and the service role +
default policy. Timing math:
- `1f2b926` ("feat(api): wire /ai/summary route + AiSummaryFn") on the
  S1 branch — **2026-05-22 19:21 PDT**.
- The 19:28 UTC deploy — **2026-05-23 12:28 PDT**.
- PR #6 (S1) merged to main — **2026-05-23 17:03 PDT**.
A `cdk deploy CisoCopilotApi` ran between the commit and the merge,
from a tree (main, pre-merge) that didn't have the S1 code. CFN
reconciled the deployed stack against that tree and dropped the
"orphan" resources. The 401 from API Gateway after the wipe was a
residual stage-deployment quirk; authed calls hit the missing
integration and got 500.

**Fix:** Single redeploy from current main HEAD (`bdbf7e8`), which has
all three PRs (#6, #7, #8) merged in:
```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never
```
82s, 13/13 resources `UPDATE_COMPLETE` — pure ADD of one Lambda + IAM
role + policy + GET/OPTIONS methods + Lambda permissions + redeployed
the v1 stage. Verified: Lambda live (direct invoke returns structured
401 `no_tenant` on a synthetic event), `/ai/summary` resource back in
API Gateway (id `175ooa`, GET + OPTIONS), no-auth curl returns 401
(Cognito authorizer working).

**▶ Lesson — paid in debugging time:** **Never `cdk deploy <stack>`
from a tree that lacks feature work which has already been deployed
from another branch.** CDK/CFN treats the divergence as drift and
deletes the "extra" resources, silently. The 2026-05-22→2026-05-23
window had a 22-hour gap between S1's deployed code and S1's merge to
main, and a deploy from a different branch in that window wiped the
endpoint. Mitigation in practice: if you're about to deploy from a
non-main branch, `git fetch origin main && git diff origin/main..HEAD
-- platform/lib/` to confirm you're not regressing main's CDK surface.
Or — merge first, then deploy from main.

## 🚀 AI Visibility v2 — Slice 2.1 shipped (2026-05-24)

Follow-on polish to S2. Spec
`docs/superpowers/specs/2026-05-24-entra-licensing-banner-design.md`;
plan `docs/superpowers/plans/2026-05-24-entra-licensing-banner-plan.md`.
Built subagent-driven on branch **`feat/ai-visibility-v2-slice-2.1`**
(6 commits ahead of `main`).

**S2.1 — Entra Free-tier licensing banner — DONE.**

- **`ai_signin_pass.run_ai_signin_pass`** now returns a tuple
  `(param_lists, premium_required)`. The bool fires only on Microsoft's
  specific 403 error code
  `Authentication_RequestFromNonPremiumTenantOrB2CTenant`. Other 403s
  (revoked consent, missing scope) leave it `False`. Module-top
  constant `_LICENSING_ERROR_CODE` is the matched string. 3 new unit
  tests (12/12 total).
- **`shasta_runner_entra/main.py`** unpacks the tuple; calls new
  `_update_connection_premium_flag(conn_id, *, premium_required,
  signin_count)` helper:
  - On 403 (premium_required): `scope = jsonb_set(COALESCE(scope, '{}'::jsonb),
    '{signin_premium_required}', 'true'::jsonb)`.
  - On positive signal (signin_count > 0): `scope = scope #-
    '{signin_premium_required}'` (clear).
  - Else: no-op (ambiguous case — could be Premium tenant with no AI
    users).
  Both writes also bump `updated_at`. A second try/except around the
  helper means a write failure never fails the scan.
- **`web/src/lib/api.ts`** — added `signin_premium_required?: boolean`
  to `Connection.scope` (one-key extension; existing JSONB shape
  preserved).
- **`web/src/routes/ConnectClouds.tsx`** — exported `ConnectionRow`;
  added `LicensingBanner` component (amber-bordered card) with copy
  "Sign-in detection requires Microsoft Entra ID P1 or P2" + Microsoft
  docs link. Rendered inside the `<li>` when `cloud_type === 'entra'
  && scope?.signin_premium_required === true` (strict `=== true`
  check). 3 new vitest cases (122/122 total).
- **Connections endpoint reuse**: zero changes needed to
  `connections_list/main.py` — it already returned `scope` JSONB. Zero
  CDK changes, zero new Lambdas.
- **Deployed:** scanner image `sha256:9eb38f0c…` pushed; Lambda
  re-resolved `:latest`. Web bundle synced to S3; CloudFront
  invalidation `I4H208LEK1MQDTTFZ91YEARAD1` queued. Live at
  `$SHASTA_DOMAIN`.

**Live-verification (Task 5) — VERIFIED (2026-05-24, KK):** Entra
rescan against the Free-tier test tenant set
`scope.signin_premium_required = true`; amber banner renders under the
Entra row on `/connect` with the expected copy + Microsoft docs link.
S2.1 passes end-to-end.

**Execution notes:**
- Task 3 implementer dropped a socket mid-write — re-dispatched cleanly
  on retry. No partial state landed.
- Reviewer caught an unused `Iterable` import after the signature
  change; one-line fix committed (`0a8f721`).
- Plan's `_fetch_signins` body needed an `try/except ImportError`
  wrapper around the kiota SDK imports so the bare test venv could
  run the new tests without msgraph installed; production behavior
  unchanged.

**▶ NEXT** — S3 brainstorm (compliance mapping sweep + EU AI Act +
SOC 2 AI framework registry adds).

## 🚀 AI Visibility v2 — Slice 2 shipped (2026-05-23)

Sub-project **AI Visibility v2**, Slice 2 (S2). Spec
`docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md` (§9
amended 2026-05-23 with decision D-1); plan
`docs/superpowers/plans/2026-05-23-ai-visibility-v2-slice-2.md`. Built
subagent-driven on branch **`feat/ai-visibility-v2-slice-2`** (6
commits ahead of `main`). S1 also shipped same day — see its block
below (PR #6).

**S2 — AI sign-in pass inside the existing Entra runner — DONE.**

- **Piggyback architecture (decision D-1):** no new connector type, no
  new admin-consent flow, no new secret. AI sign-in scanning lands as
  an additional pass inside `shasta_runner_entra` alongside Shasta's
  existing Entra compliance checks. Customers who already have a
  `cloud_type='entra'` connection get S2 instantly on the next scan.
- **Pre-flight (Task 1 — KK-gated, pending):** `AuditLog.Read.All` on
  AAD app `$ENTRA_CLIENT_ID`. The plan path
  assumes scope is present (Shasta already reads
  `user.signInActivity`); the try/except wrapper in the handler
  ensures a Graph 403 is non-fatal regardless. KK to confirm via
  Entra portal at convenience.
- **`ai_signin_pass.py`** (244 lines + 9 unit tests). Pure helpers
  (`load_catalog`, `match_app`, `signin_to_params`) + lazy-imported
  Graph paginator. Reads Graph `/auditLogs/signIns` (incremental by
  `createdDateTime`), matches against catalog, emits per-tier
  findings (`ai_signin_personal_tier` fail/high,
  `ai_signin_corp_tier` pass/low, `ai_signin_unknown_tier`
  fail/catalog-severity). Each carries `evidence_packet.is_ai='true'`
  + `evidence_packet.entra_upn=<user>` so the existing
  `/ai/summary` predicate + per-person query pick them up with zero
  read-side change.
- **`ai_saas_catalog.json`** — 30 curated AI SaaS apps (OpenAI,
  Anthropic, Cursor, GitHub Copilot, Perplexity, Google Gemini,
  Mistral, Cohere, HuggingFace, Midjourney, Notion AI, Otter, DeepL,
  Synthesia, Tabnine, Codeium, Sourcegraph Cody, DeepSeek, xAI Grok,
  Together.ai, Groq, AI21, etc.). Tightened post-implementation
  (`1a55533`) to drop substring-overreaching aliases
  (`Notion`/`Writer`/`Together`/`Bard`/`Cody`/`Runway`).
- **`_FINDING_INSERT_SQL` extended** in `shasta_runner_entra/main.py`
  to carry `evidence_packet`. Existing Shasta findings pass `{}` (no
  behavior change). New `_insert_finding_param_lists` helper handles
  the AI sign-in path.
- **Handler wiring:** `run_ai_signin_pass` runs after Shasta's
  `run_all_azure_entra_checks` and merges into the same batch path.
  A try/except around the AI pass ensures a Graph failure NEVER
  fails the underlying Shasta scan.
- **Deployed:** scanner image rebuilt + pushed (`sha256:9a75aca8…`);
  Lambda `ciso-copilot-shasta-runner-entra` updated via
  `update-function-code` to re-resolve `:latest` to the new digest.
  No CDK deploy needed. No web changes.

**Execution-time decisions:**
- `match_app` uses **case-insensitive substring matching**, not
  exact. Forgiving for Microsoft's renaming patterns
  ("ChatGPT Enterprise", "ChatGPT for Teams") but at false-positive
  risk for generic aliases — addressed via catalog tightening.
- **Entity emission deferred** — entra runner has no `unified_writer`
  today. Findings carry `evidence_packet.is_ai` for the AI-touching
  predicate's escape hatch; proper `ai_user_signin` entities can
  land in a follow-on refactor.

**Live-verification (Task 7) — partial (2026-05-23):**

Scan `b253e078-8db1-47ab-857c-36b6bc47c4ef` ran against KK's
Entra-connected test tenant. Outcome:
- **16 Shasta entra findings written** (existing posture checks
  unchanged — the try/except wrapper protected this path).
- **0 AI sign-in findings.** Graph returned 403
  `Authentication_RequestFromNonPremiumTenantOrB2CTenant`:
  > "Tenant is not a B2C tenant and doesn't have premium license"

  **`auditLogs/signIns` is gated on Entra ID P1 or P2 (Premium)** —
  Microsoft licensing requirement, not something we can work around
  in code. The free-tier tenant gets no sign-in data at all (the
  spec's earlier "7-day window" language was wrong and has been
  patched).
- **Infrastructure validated.** The full code path executed exactly
  as designed: catalog loaded, Graph paginator hit, 403 caught by
  the wrapper, scan continued and committed. To see real `ai_signin_*`
  findings, the test tenant needs an Entra ID P1/P2 license OR run
  against a customer tenant with Premium licensing.

**Follow-on UX work (not blocking S2):**
- Surface "Entra Free tier — sign-in detection requires P1/P2" banner
  on `/connect` so customers understand why Entra source tile reads
  zero AI findings even after a successful scan.

**Bug fix during smoke (`c45977f`):** the post-consent "Run your
first scan →" link was pointing at `${cdnDistribution}` (the GCP
onboarding asset CDN) instead of the canonical app domain. Returned
S3 XML AccessDenied when clicked. Patched to hardcode
`https://$SHASTA_DOMAIN`. `CisoCopilotApi` deployed.

**Deferred from S2 (per plan + spec):**
- Per-tenant sanctioned-app overrides + `ai_signin_unsanctioned_app`
  finding kind.
- Entity emission for `ai_user_signin` (entra runner needs
  `unified_writer` refactor).
- Framework tagging (NIST AI RMF / ISO 42001 / SOC 2 AI on
  `ai_signin_*` check IDs) — S3 work.

**▶ NEXT** — Slice 3 (compliance mapping sweep + EU AI Act + SOC 2 AI
framework registry adds). Brainstorm + plan separately. Also queued:
a `/connect` banner that surfaces the Entra Free tier P1/P2 licensing
constraint when a scan hits the 403 — small UX polish slice ("S2.1").

## 🚀 AI Visibility v2 — Slice 1 shipped (2026-05-22)

Sub-project **AI Visibility v2**, Slice 1 (S1). Strategy
`docs/superpowers/specs/2026-05-22-ai-security-strategy.md`; spec
`docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md`; plan
`docs/superpowers/plans/2026-05-22-ai-visibility-v2-slice-1.md`. Built
subagent-driven on branch **`feat/ai-visibility-v2-slice-1`** (7
commits ahead of `main`).

**S1 — Azure-AI cloud pass + Unified /ai view — DONE.**

- **Azure-AI pass** (`platform/lambda/shasta_runner_azure/app/ai_pass.py`,
  198 lines + 6 unit tests). Wraps Shasta's
  `discover_azure_ai_services` + `run_full_azure_ai_scan` +
  `enrich_findings_with_ai_controls` into the unified
  entity/edge/finding model. Entities emitted with `domain='cloud'`:
  `azure_openai_deployment`, `azure_ml_workspace`, `cognitive_service`
  (OpenAI-kind cognitive services skipped to avoid dup with the OpenAI
  branch). Findings carry NIST AI RMF + ISO 42001 + EU AI Act framework
  tags + Azure standard frameworks (`soc2`, `iso27001`, `mcsb`,
  `cis_azure`).
- **Tier gating**: `"ai"` added to `azure_units._MEDIUM_EXTRA`; Quick
  scans skip it, Medium + Deep run it. Per-(subscription, ai) ScanUnit
  via `_ai_unit` factory in `main.py` (mirrors `_module_unit`'s
  per-unit-fresh-client pattern). Scanner image rebuilt + pushed
  (`sha256:951ae7ba…`); `:latest` tag so no `CisoCopilotScan` redeploy
  was needed.
- **`/ai/summary` Lambda** (`platform/lambda/ai_summary/`) — new
  Cognito-authed endpoint at `GET /v1/ai/summary`. Returns `{ score,
  by_source, by_framework, top_people }`. `is_ai_touching` evaluated
  inline in SQL via JSONB `?|` over four AI framework keys + entity
  domain/kind allowlist + `evidence_packet ->> 'is_ai'` escape hatch.
  `_query_top_people` SQL groups by `LOWER(COALESCE(commit_author_email,
  iam_owner_email, entra_upn))`. **Schema delta from the spec:** the
  plan assumed `findings.attributes` JSONB and `entities.entity_id` —
  actual schema is `findings.evidence_packet` and `entities.id`.
  Fixed in `ai_summary/main.py` via the plan's Step 5 pre-flight check.
- **API stack** — `AiSummaryFn` Lambda + `/ai/summary` GET route in
  `api-stack.ts`; `CisoCopilotApi` deployed (`UPDATE_COMPLETE` —
  82s deploy, 13/13 resources). Note: `--hotswap` won't create new
  Lambdas, so this deploy used a full CFN update.
- **Web `/ai` route** (`web/src/routes/AISummary.tsx`, 207 lines + 2
  vitest cases). Tile layout: 3 score tiles (Fail/Partial/Pass), 4
  source tiles (AWS/Azure/Code/Entra — Entra labelled "coming in S2"),
  4 framework tiles (NIST AI RMF / ISO 42001 / SOC 2 AI / EU AI Act
  each with F/P/Pass mini-rollup), top-people table with empty-state
  copy "No identifiable AI users yet — connect Entra (S2) to populate."
  Added `api.aiSummary()` method to `web/src/lib/api.ts` matching the
  project's existing `call<T>` pattern (the plan's `apiGet` assumption
  was wrong — no such export existed). Built, synced to S3, CloudFront
  invalidation `IC7ZCIJ34431QOW27SRV34X0WR`.
- **Data state at ship:** 7790 findings in Aurora, 102 AI-touching
  (`is_ai_touching=true`). The score + by-source + by-framework all
  populate. **Top-people view is empty** — zero findings carry
  `commit_author_email` / `iam_owner_email` / `entra_upn` in
  `evidence_packet`. Documented limit; populates in S2 (Entra
  sign-ins) or future emitter patches (AI scanner + AWS owner-tag
  enrichment).
- **Reviewer-caught issues fixed during execution:**
  - `cis_aws_controls` was an AWS leftover copy-pasted into the Azure
    `ai_pass._STD_FRAMEWORK_ATTRS` dict — replaced with
    `cis_azure_controls → cis_azure` after the code reviewer caught
    it.
  - `_query_top_people` had non-deterministic `STRING_AGG` order —
    fixed with `ORDER BY` inside the aggregate.
  - `_AI_RESOURCE_KINDS` allowlist was missing the actual AI-scanner
    entity kinds (`ai_agent`, `ai_embedding`, `ai_framework`,
    `ai_mcp_server`, `ai_model`, `ai_prompt`, `ai_tool`,
    `ai_vector_db`) — added.
  - Module docstring claim that the predicate matches keys "starting
    with" the AI framework prefixes was wrong — the SQL uses exact
    equality via `?|`; docstring corrected.

**Deferred (out of S1 scope, per spec):**
- **S2** — Entra sign-in connector + per-person grouping.
- **S3** — Compliance mapping sweep + SOC 2 AI + EU AI Act registry
  (the four framework tiles render; SOC 2 AI and EU AI Act tiles will
  read zero until S3 maps the actual checks).
- **S4** — iOS push notifications + polish.
- **GCP-AI** — its own sub-project (Shasta has no `gcp/ai_*` today).
- **Provider connectors** — OpenAI/Anthropic admin-API blocked.

**Slice 1 live-verification — pending (KK-gated, Google OAuth):**
1. Open `https://$SHASTA_DOMAIN/ai` in an incognito window;
   sign in with Google.
2. Confirm the page renders "AI Exposure" title + three F/P/P tiles
   with non-zero numbers (today's data: 102 AI-touching findings;
   real F/P/Pass split visible in the tiles).
3. Confirm the by-source row shows AWS + Azure with counts; Code
   per-tenant; Entra zero with "coming in S2" label.
4. Confirm the by-framework row shows NIST AI RMF + ISO 42001 with
   counts; SOC 2 AI + EU AI Act both zero (S3 work).
5. Confirm Top AI Users shows the empty-state copy "No identifiable
   AI users yet — connect Entra (S2) to populate."
6. Open browser devtools → Network. Confirm `GET
   /v1/ai/summary` returned 200 with the documented JSON contract.
7. (Optional) Re-run a Medium Azure scan; refresh `/ai`; counts
   should hold steady or grow (never go negative).

**▶ NEXT (post-S1 view, superseded by S2 above)** — Slice 2 has now
shipped; see its block at the top of this file.

## 🚀 Scan Screen — Slice 2b shipped (2026-05-22)

Cross-cloud `/scan` surface. Spec
`docs/superpowers/specs/2026-05-22-scan-screen-design.md`; plan
`docs/superpowers/plans/2026-05-22-scan-screen-slice-2b.md`. Built
subagent-driven on branch **`feat/scan-screen-slice-2b`** (merged to
main 2026-05-22, commit `a57f528`).

- **New `/scan` route** (`web/src/routes/Scan.tsx`) — stacked cards,
  one per active connection. The `ScanCard` shell handles the header
  (cloud name + last-scan pill + "Never scanned" badge for
  `latest_scan === null`) and routes to a per-cloud body:
  - **AWS** — tier picker only.
  - **Azure** — subscription checklist (from `scope.subscription_names`,
    default = all selected) + tier.
  - **GCP** — project mode: tier only. Org mode: project checklist
    (from `scope.projects`, default = all) + tier. If the org
    connection has no projects yet (first scan after onboarding), the
    body shows a "Projects discover on the first scan" banner and the
    scanner enumerates when clicked.
  - **Entra** — no scope, no tier; just a Scan button.
  - A "Launch all scans" button at the page level fires every card in
    parallel at Quick tier (Promise.allSettled; partial failures
    surface in the per-card UI on next reload).
  - Live scan rendering: when a card's `latest_scan.status` is
    `running` (or a scan was just started locally), the body is
    replaced by `<ScanProgress>` until terminal, then it re-renders the
    picker form so the user can re-launch.
- **Onboarding webhooks dropped the auto-scan** — AWS / Azure / GCP /
  Entra. A freshly onboarded connection now lands in `/scan` with
  `latest_scan: null` and a "Never scanned" badge. The Entra HTML
  success page also redirects to `/scan` directly (link "Run your first
  scan →").
- **Connect page retrofit** — the per-row `ScanPicker` and the inline
  `SubscriptionPicker` are deleted (~144 lines net). Each connection
  row now shows only status + last-scan summary + Delete. The page
  polls `GET /connections` every 5s while any connection is non-active;
  a `pending → active` transition surfaces a toast in the top-right
  linking to `/scan` ("Your &lt;CLOUD&gt; connection is ready — Run
  your first scan →").
- **`PATCH /connections/{id}` validates against either subscriptions
  or projects** — `_update_scope` now accepts a non-empty subset of
  `scope.subscriptions` (Azure) OR `scope.projects` (GCP org), so the
  same endpoint supports both pickers without divergence.
- **Deployed:** `CisoCopilotApi` deployed (`UPDATE_COMPLETE`); web built
  (`tsc -b && vite build` clean), synced to S3, CloudFront invalidation
  `IB4TNKV8P0SR5A1FH4ZK2OYS17` queued. Live at
  `$SHASTA_DOMAIN`.
- **Browser-smoke pending** — an agent can't pass Google OAuth.
  Verification checklist:
  1. Open `https://$SHASTA_DOMAIN` in an incognito window.
  2. Sign in with Google.
  3. Click "Scan" in the nav. Confirm the page renders the existing
     GCP project connection as a card (AWS not connected on this
     tenant; Azure has the subscription picker).
  4. Click "Scan" on the GCP card. Confirm the card flips to
     `ScanProgress` and polls to completion.
  5. Visit "Connect clouds" — confirm the per-row pickers are gone;
     only Delete buttons remain.
  6. (Optional) Re-onboard a cloud to confirm the post-onboard toast
     appears on the Connect page.

## 🚀 GCP Scanner Uplift — Slice 2a shipped (code only — 2026-05-22)

Roadmap item #1, GCP leg. Plan
`docs/superpowers/plans/2026-05-22-gcp-scanner-uplift-slice-2a.md`.
Built subagent-driven on branch **`feat/gcp-scanner-slice-2a`** (merged
to main 2026-05-22, commit `5c7125d`).

**Slice 2a — org-level GCP onboarding — CODE DONE.**
- `cfn/gcp/onboard.sh` learned a `--org <ORG_ID>` flag. Without it the
  script runs the unchanged single-project flow; with it, reader roles
  bind at the **Organization** node (`securityReviewer`,
  `cloudasset.viewer`, `logging.viewer`, `browser`) and the POST body
  carries `mode=org`, `org_id`, `host_project_id`, `host_project_number`.
- `onboarding_gcp_complete` webhook branches on `mode`. Org mode stores
  the org scope (`mode=org`, `org_id`, `host_*`, `sa_email`, WIF refs,
  `projects={}`, `selected=[]`) and **does NOT auto-scan**. Project mode
  unchanged (still auto-scans).
- Scanner-side enumeration: `project_discovery.enumerate_projects` added
  (was deferred from Slice 1a, 4 new unit tests). `shasta_runner_gcp/
  main.py` calls it in Stage 1 for org-mode scans, writes the
  `{project_id: display_name}` map back to `cloud_connections.scope.
  projects` via a new `_record_projects` helper, and uses the
  enumerated list as `project_ids` if the trigger passed an empty
  subset.
- `run.py` accepts `MODE` (defaults `project`) and allows empty
  `PROJECT_IDS`; new optional `HOST_PROJECT_ID` for the org bootstrap.
  10 unit tests for `build_event` (43 scanner tests pass).
- `connections_list._rescan_gcp` routes on `scope.mode` — org branch
  reads `host_project_number`/`selected`; project branch unchanged.
  New Fargate env vars passed: `MODE`, `HOST_PROJECT_ID`.
- **Scope cut from spec**: the webhook does NOT pre-enumerate via
  Resource Manager (Approach C of the spec); enumeration happens
  lazily on the first scan. Trade-off: the project picker (Slice 2b)
  will be empty until the first scan completes (~3-5 min). Documented
  in the plan as an explicit, revisitable simplification — avoided
  bundling google-auth + an IAM-trust expansion into the webhook for
  tonight.
- **Deployed**: scanner image rebuilt + pushed
  (`sha256:8648e2e7…`); `CisoCopilotApi` + `CisoCopilotStatic` deployed
  (Static pushes the new `onboard.sh` to the CDN). Deployed webhook
  smoke-confirmed: a `mode=org` body missing the org fields returns
  400 `missing_fields`, proving the new branch is live.

**Slice 2a live-verification — pending (human-gated).** Requires
org-admin on a real GCP Organization. Procedure when ready:

1. In Cloud Shell of the customer's host project (org-admin signed in):
   ```bash
   curl -fsSL https://cdn.settlingforless.com/gcp/onboard.sh \
     | bash -s -- <EXTERNAL_ID> --org <ORG_ID>
   ```
   `<EXTERNAL_ID>` comes from the web app's "Add GCP" flow (which
   writes a pending row — for now, the web UI doesn't yet expose an
   "org" toggle; a tester can generate an external_id manually or wait
   for Slice 2b's web changes). `<ORG_ID>` is the numeric organisation
   id (`gcloud organizations list`).
2. Verify the connection lands `active`, `scope.mode='org'`,
   `projects={}`, `selected=[]`:
   ```sql
   SELECT scope FROM cloud_connections WHERE external_id='<EXTERNAL_ID>';
   ```
3. From the Connect page, click rescan on the new GCP row (or invoke
   `ConnectionsListFn` with the synthetic event pattern from the
   Slice 1b verification, swapping the conn_id).
4. Watch the scan: `region_discovery → first_signal → crown_jewel →
   done`. First scan performs the enumeration — confirm
   `scope.projects` is now populated and the scanner used the
   discovered list as `project_ids`.
5. Confirm findings landed: `SELECT count(*) FROM findings WHERE
   scan_id=...`.

## 🚀 GCP Scanner Uplift — Slice 1b shipped (2026-05-22)

Roadmap item #1, GCP leg. Plan
`docs/superpowers/plans/2026-05-22-gcp-scanner-uplift-slice-1b.md`.
Built subagent-driven on branch **`feat/gcp-scanner-slice-1b`** (merged
to main 2026-05-22, commit `e36e2e1`).

**Slice 1b — production Fargate triggers + legacy Lambda retired — DONE.**
- `onboarding_gcp_complete` and `connections_list._rescan_gcp` now start
  one `ciso-copilot-gcp-scan` Fargate task per connection via
  `ecs:RunTask` — no more `lambda.invoke` of the legacy scanner. The
  rescan path is tier-aware.
- Cross-stack export hygiene preserved: literal task-def family
  (`'ciso-copilot-gcp-scan'`), `iam:PassRole` covers the literal
  `ciso-copilot-gcp-scanner` task role + the `CisoCopilotScan-GcpScanTaskDef*`
  exec-role name-pattern — zero new cross-stack exports.
- **Live-verified:** a rescan through the real `ConnectionsListFn`
  Lambda (synthetic API Gateway event, real deployed function) ran scan
  `5f322c7a-…` `manual`/`completed`/`phase=done`, **102 findings, 46
  entities, 45 edges** — same shape as Slice 1a's manual verification,
  confirming the entire `POST /connections/{id}/rescan` → `_rescan_gcp`
  → `ecs.run_task` → Fargate path works end-to-end on production.
- **Legacy GCP Lambda retired** — `ciso-copilot-shasta-runner-gcp` is
  gone (`get-function` → `ResourceNotFoundException`). The Data API grant
  that lived on the Lambda was relocated onto `gcpScannerRole` so the
  Fargate task (which shares that role) keeps Aurora access.
- **Deploy gotcha paid in debugging time:** `npx cdk deploy
  CisoCopilotApi` (without `--exclusively`) pulled `CisoCopilotScan` in
  as a dependency and deployed Scan FIRST, hitting the export-still-in-use
  deadlock and rolling back cleanly. Resolved by deploying with
  `--exclusively`: Api first (drops the imports), then Scan (drops the
  Lambda + orphaned exports). Both stacks `UPDATE_COMPLETE`. The Azure
  1b deploy used the same `--exclusively` flag — the plan should always
  spell it out explicitly for two-phase deploys.

**▶ NEXT** (no slice in flight, 2026-05-22): the Scan-screen Slice 2b
and GCP Slice 2a (code) both shipped today. Open verification + dev work
in rough priority order:

1. **Browser-smoke verify Slice 2b** on `$SHASTA_DOMAIN` —
   checklist at the top of the 2b section above; KK-gated (Google OAuth).
2. **Expose the GCP "org" toggle** in the web onboarding flow so the
   `--org <ORG_ID>` path in `cfn/gcp/onboard.sh` is reachable from the
   UI. Unblocks Slice 2a's human verification (requires GCP org-admin).
3. **AI-discovery plan 2** — OpenAI / Anthropic provider connectors
   (per `project_ciso_copilot` memory). Bigger lift; brainstorm first.

## 🚀 GCP Scanner Uplift — Slice 1a shipped (2026-05-22)

Roadmap item #1, GCP leg. Spec
`docs/superpowers/specs/2026-05-22-gcp-scanner-uplift-design.md`; plan
`docs/superpowers/plans/2026-05-22-gcp-scanner-uplift-slice-1a.md`.
Built subagent-driven on branch **`feat/gcp-scanner-slice-1a`** (merged
to main 2026-05-22, commit `d87a839`).

**Slice 1a — v2 GCP scanner backend — DONE.** The GCP scanner
(`platform/lambda/shasta_runner_gcp/`) is now the v2 three-stage
pipeline, mirroring the Azure scanner:
- Six pure adapter modules (`gcp_credential`, `gcp_units`,
  `gcp_id_to_entity`, `gcp_findings`, `project_discovery`, `run.py`) —
  32 unit tests pass.
- `main.py` rewritten as the orchestrator: project discovery →
  tier-aware parallel project×Shasta-module `ScanUnit`s through
  `scanner_core.run_units` → `unified_writer.commit_scan`. Two-phase
  Quick. Legacy direct-`findings` writes gone.
- New `ciso-copilot-gcp-scan` Fargate task def (CDK); `build.sh` copies
  the shared `scanner_core`/`ai_scanner` modules.
- **Live-verified:** Quick scan `6977db63-…` on the GCP connection
  `219f41eb-…` (project `gen-lang-client-0693606939`) ran
  `completed`/`phase=done`, **102 findings (97 fail / 4 pass /
  1 partial), 46 entities, 45 edges**; all 5 Quick modules ran, zero
  errors; project-keyed `scope` coverage map written.
- **Bug paid in debugging time — WIF on Fargate.** First scan came back
  `partial` with every module failing "Unable to determine the AWS
  metadata server security credentials endpoint". google-auth's AWS
  external-account credential source reads AWS creds from env vars / EC2
  IMDS — neither is populated for an ECS Fargate task role (Fargate
  serves them via the container credentials endpoint). The legacy GCP
  scanner was a Lambda, where the env vars *are* set, so WIF worked
  there. Fixed (`683c90b`): `main.py` resolves creds via
  `boto3.Session().get_credentials()` (container-provider aware) and
  `gcp_credential.export_aws_credentials_to_env` exports them before the
  WIF credential is built.
- **Entity-coverage note:** 44 of 46 entities are `gcp_subnetwork` (the
  networking module's resource IDs parse cleanly); iam/storage/compute/
  encryption finding `resource_id`s mostly don't match
  `gcp_id_to_entity._KIND_MAP`, so those findings land with no subject
  entity (the intended graceful contract). Widening `_KIND_MAP` once the
  real per-module resource_id formats are sampled is a follow-up.
- **Not yet wired to production triggers** — invoked manually via
  `ecs run-task`. The legacy GCP Lambda still exists. Slice 1b wires the
  Fargate triggers + retires the legacy Lambda; Slice 2a adds org-level
  onboarding; Slice 2b is the project picker.

**▶ Sequencing decision (2026-05-22) — resolved:** before the Slice 2b
picker, we ran a short cross-cloud brainstorm for a unified **"Scan"
screen** (post-onboard landing where the user picks scope + tier per
cloud, replacing the silent auto-scan-on-onboard). It superseded the
Connect-page per-row picker Azure shipped, so 2b built the Scan screen
rather than a GCP-only Connect-page picker (now live — see top section).
Slices 1a/1b were unaffected.

## 🚀 Azure Scanner Uplift — Slice 0 shipped (2026-05-22)

Roadmap item #1, Azure leg. Spec
`docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md`; plan
`docs/superpowers/plans/2026-05-21-azure-scanner-uplift-slice-0.md`.
Built subagent-driven on branch **`feat/azure-scanner-uplift`** (landed
on main 2026-05-21, commit `1f52337`).

**Slice 0 — shared scanner core — DONE.** New package
`platform/lambda/scanner_core/` holds the cloud-agnostic pieces:
`scan_pipeline.py` (moved from `shasta_runner`) and a new `scan_state.py`
(`update_scan` + `record_scan_scope` — the `scans`-table writes,
extracted from AWS `main.py`; `record_scan_scope` takes a pre-shaped
`scope` dict so a region-keyed or subscription-keyed map both work).
`shasta_runner/build.sh` copies `scanner_core/` modules into `app/` at
image build (same mechanism as the `ai_scanner` copies). AWS `main.py`
now imports from `scan_state`; its inline `_update_scan` + DB-config
constants are gone. `scan_policy.py` and `unified_writer.py`
deliberately did NOT move (AWS-region-shaped / multi-consumer — see spec
§3-§4). **No Azure change yet — that's Slice 1.**

- Tests: AWS scanner suite 98 pass + `scanner_core/tests/` 11 pass
  (= the prior 102 baseline, `scan_pipeline`'s 4 now under
  `scanner_core/`).
- Deployed: `shasta-runner:latest` rebuilt + pushed
  (`sha256:a74c6af…`); `CisoCopilotScan` deployed.
- **Live-verified:** Quick scan `4b6d3b61-87dd-4663-bff7-4753ea809022`
  on conn `26e97477…` ran `completed`/`phase=done`/`tier=quick`, 61
  findings, 17-region scope object, ~3 min — confirming the refactored
  scanner runs end-to-end with zero behaviour regression.

**Slice 1a — v2 Azure scanner backend — DONE (2026-05-22).** Plan
`docs/superpowers/plans/2026-05-22-azure-scanner-uplift-slice-1a.md`;
built subagent-driven on branch **`feat/azure-scanner-slice-1a`** (landed
on main 2026-05-22, commit `aad5548`). The Azure scanner
(`platform/lambda/shasta_runner_azure/`)
is now the v2 three-stage pipeline:
- Five pure adapter modules (`azure_id_to_entity`, `azure_findings`,
  `subscription_discovery`, `azure_units`, `azure_credential`) + `run.py`
  — 26 unit tests pass.
- `main.py` rewritten as the orchestrator: subscription discovery →
  tier-aware parallel subscription×Shasta-module `ScanUnit`s through
  `scanner_core.run_units` → `unified_writer.commit_scan`. Two-phase
  Quick. Legacy direct-`findings` writes gone.
- New `ciso-copilot-azure-scan` Fargate task def (CDK); `build.sh`
  copies the shared `scanner_core`/`ai_scanner` modules.
- **Live-verified:** Quick scan `10ffeb40-…` on the Azure connection
  `79964b99-…` ran `completed`/`phase=done`, 72 findings, **16 entities
  across 5 Azure kinds** (the legacy scanner wrote zero entities).
  Subscription discovery classified one sub `active` (6 Quick modules
  ran), one `empty` (skipped). Subscription-keyed `scope` map written.
- **Not yet wired to production triggers** — invoked manually via
  `ecs run-task`. The legacy Azure Lambda still exists.

**Slice 1b — production triggers on Fargate — DONE (2026-05-22).** Plan
`docs/superpowers/plans/2026-05-22-azure-scanner-uplift-slice-1b.md`;
built subagent-driven on branch **`feat/azure-scanner-slice-1b`**.
- `onboarding_azure_complete` and `connections_list._rescan_azure` now
  start **one** `ciso-copilot-azure-scan` Fargate task per connection
  (all subscriptions, one `scans` row) via `ecs:RunTask` — no more
  per-subscription legacy `lambda.invoke`. The rescan path is
  tier-aware.
- **Live-verified:** a rescan through the real `POST /connections/{id}/
  rescan` API path ran scan `6cad579e-…` to `completed`/`phase=done`,
  72 findings, subscription-keyed scope.
- **Deploy gotcha paid in debugging time:** the first attempt deadlocked
  — the plan's Task 3 added a new `Scan→Api` cross-stack export (Azure
  task-def role ARNs) while removing the legacy Lambda dropped another,
  so neither stack-deploy order worked. Fixed by making the Azure wiring
  create **zero** cross-stack export churn: the task-def family is a
  literal, `iam:PassRole` uses a role-name pattern
  (`CisoCopilotScan-AzureScanTaskDef*`), and `AZURE_RUNNER_FN` stays
  wired (unused by code). The Api stack then deploys alone.
**Legacy Azure Lambda retired — DONE (2026-05-22).** `commit 887e140`.
The deferred follow-up: the `AzureRunner` `DockerImageFunction`
(`ciso-copilot-shasta-runner-azure`) + its cross-stack wiring
(`AZURE_RUNNER_FN` env vars, `grantInvoke`) removed. Shipped via the
clean two-phase deploy — `CisoCopilotApi` first (drops the imports),
then `CisoCopilotScan` (drops the Lambda + its orphaned exports) — which
worked first try since it was a pure removal with no competing new
export. The `shasta-runner-azure` ECR repo stays (the Fargate task def
uses it).

**Slice 2 — web subscription picker — DONE (2026-05-22).** Plan
`docs/superpowers/plans/2026-05-22-azure-scanner-uplift-slice-2.md`;
built subagent-driven on branch **`feat/azure-subscription-picker`**.
- `GET /connections` now returns each connection's `scope`; new
  `PATCH /connections/{id}` updates `scope.selected` (validates the
  list is a non-empty subset of the discovered subscriptions).
- `_rescan_azure` scans `scope.selected` (falls back to `subscriptions`
  for pre-picker connections); onboarding seeds `selected` = all.
- Web Connect page: an expandable subscription checklist on Azure
  connection rows (Save → PATCH), plus the Quick/Medium/Deep `ScanPicker`
  (the Azure row previously had only a flat Medium-only Rescan button).
  `ScanProgress` renders the per-subscription census for Azure scans.
- **Live-verified:** PATCH'd the Azure connection to one of its two
  subscriptions, ran a rescan — the scan's `scope.subscriptions` map
  contained only the selected subscription. Then restored to both.
- Web build/typecheck green; the picker's visual behaviour was not
  browser-tested (agent can't pass Google OAuth) — worth a glance.

**▶ AZURE SCANNER UPLIFT COMPLETE.** Slices 0, 1a, 1b, 2 + the legacy-
Lambda retirement are all shipped. The Azure scanner is the v2
three-stage Fargate pipeline, tier-aware, with user-chosen subscription
scoping — at parity with the AWS scanner. Next major item: see the
Roadmap below (Azure was roadmap #1's Azure leg; GCP / Entra uplifts
remain, or move to roadmap #2+).

## 🚀 AWS Scanner Uplift — state (2026-05-21)

Roadmap major item #1 ("Scanner comprehensiveness uplift"). **PR #4
(`feat/aws-scanner-uplift`, 55 commits) was merged to `main`** on
2026-05-21 (`e127eb8`), after a 3-reviewer whole-branch review.

**Specs (all approved) — `docs/superpowers/specs/`:**
- `2026-05-20-aws-scanner-uplift-design.md` — the overall uplift: tiered
  Quick/Medium/Deep scanning, 7-slice phasing.
- `2026-05-21-region-discovery-design.md` — superseded/extended by ↓.
- `2026-05-21-scan-performance-design.md` **(rev 3)** — "Scan Execution
  v2", the three-stage parallel scanner. **THE current design.**

**Plans — `docs/superpowers/plans/`:** `…slice-0.md`, `…slice-1.md`,
`…region-discovery.md`, `…scan-execution-v2-backend.md`.

### Slice 0 — SHIPPED
Scanner moved Lambda → **ECS Fargate** (`ciso-copilot-scan` cluster,
`ciso-copilot-aws-scan` task def). `scans.tier` column (migration 009).
Coverage scorecard `docs/coverage/aws-scorecard.{md,json}` anchored to
CIS / FSBP / PCI v4 / NIST 800-53. Onboarding triggers `ecs:RunTask`.

### Slice 1 — SHIPPED
In-repo posture **coverage engine** (`app/coverage/`): `model.py`,
`collectors/` + `checks/` for **SQS, Secrets Manager, ECR**, `registry`,
`engine`; tier-filtered checks; scorecard counts engine checks. Plus a
boto3 timeout `Config` (`app/aws_config.py`).

### Region discovery — built, then EXTENDED by Scan Execution v2
Added `region_discovery.py`. Scan Execution v2 **rewrote it** to the
four-state footprint probe; the region-discovery plan's design is
subsumed by the v2 spec.

### Credential fix — SHIPPED
`app/assumed_role.py` — `RefreshableCredentials`: a long multi-region
scan re-assumes the customer role automatically, never hits
`RequestExpired`.

### Scan Execution v2 — backend BUILT, VERIFIED, merged to main
The scanner is now a **three-stage parallel pipeline**: (1) region
eligibility, (2) four-state footprint probe
(`active`/`default_only`/`empty`/`unknown`), (3) tier-aware scan units
run through an in-task `ThreadPoolExecutor` (`scan_pipeline.py`) bounded
by per-service concurrency caps + adaptive retry. **Two-phase Quick**
(First Signal commits early, then Crown Jewel). Per-scan **coverage map**
in `scans.scope`. `scans.phase` column (migration 010). `GET
/v1/scans/{id}` scan-status API (`scans_status` lambda). Fargate task
4 vCPU / 8 GB. `PYTHONUNBUFFERED=1` so scan logs stream live.

New/rewritten modules: `scan_pipeline.py`, `scan_policy.py`,
`region_discovery.py` (rewritten), `main.py` handler (rewritten),
`assumed_role.py`, `scans_status/`. **101 scanner unit tests pass.**

**Plan tasks V2-1..V2-9 — done + two-stage reviewed.**
**V2-10 (build / deploy / E2E verify) — COMPLETE. Medium + Quick both VERIFIED:**
- Image rebuilt + pushed; `CisoCopilotScan` + `CisoCopilotApi` deployed
  (Api re-deployed post-merge for the correct callback URL).
- **Medium discovery scan `b3091a57-87b9-4eca-83cd-5dd812ec254f` —
  VERIFIED.** Completed cleanly: `status=completed`, `phase=done`. The
  four-state footprint probe classified **17 regions → 9 `active` /
  8 `default_only`** (0 errored); per-region coverage map written to
  `scans.scope`; **7,280 findings**. The v2 three-stage parallel
  pipeline works end-to-end.
- **Perf note:** that scan took **~49 min** (23:26→00:15) — completes
  cleanly (the pre-v2 serial scan ran 108 min and died on expired
  creds) but is over the ~15-25 min Medium target. Likely `ai_pass`
  running as a single serial unit + conservative per-service caps
  (flagged in spec §15). Tune later — not a blocker.
- **Quick scan `bb2d4bcb-1e7d-4748-b211-5365548994a6` — VERIFIED
  (2026-05-21).** Same conn as the Medium scan. Moved through
  `region_discovery → first_signal → crown_jewel → done`; **Phase-1
  early commit proven** — 72 findings observable while `phase` was
  still `crown_jewel`, 116 total at `done`. `status=completed`,
  17-region coverage map (9 active / 8 default_only, 0 errors).
  Ran **~4m20s** (00:23:54→00:28:14) — within the ~3-5 min target.
- **Scan-status API verified** — `GET /v1/scans/{id}` (via direct
  `ScansStatusFn` invoke with synthetic Cognito claims) returns
  `tier`/`status`/`phase`/`coverage_map`/`finding_count`, 200 OK.
- Minor note: `ecs describe-tasks` reported `exitCode: null` for the
  stopped Quick task (`stopCode: EssentialContainerExited`); DB state
  (`completed`/`done` + full coverage map + `finished_at`) is the
  authoritative success signal and confirms a clean run.
### Whole-branch review + PR #4 merge (2026-05-21)
Reviewed the full branch (55 commits, 63 files) via 3 parallel reviewers
(scanner pipeline / coverage engine / infra). All returned "merge with
fixes" — architecture sound, but real issues. **Fixed before merge:**
- **A** — `scans_status` selected `started_at`/`finished_at` without
  `::text`; the Data API dropped them → API returned null timestamps.
- **B** — onboarding inserted a scan row relying on the `phase` column
  default `'done'` → a fresh `queued` scan reported `phase=done`. Now
  inserts `phase='region_discovery'` explicitly.
- **C** — `main._absorb` dropped `global/*` unit failures from the
  coverage map → a failed IAM module left the scan `completed` not
  `partial`. Added a `"global"` bucket to `coverage_map`; `scans.scope`
  now carries it as a top-level `global` key (regions stays regional).
- **D** — `coverage/engine.py` didn't wrap `check.evaluate()`; one
  malformed resource threw and killed the whole region's findings. Now
  per-check try/except. New test `test_engine_survives_a_throwing_check`.
- **E (documented, not fixed)** — `run_units` `batch_timeout` does NOT
  bound wall-clock: the `ThreadPoolExecutor` `with`-block joins
  stragglers and `future.cancel()` no-ops a running unit. The real hang
  bound is the boto connect/read timeouts in `SCAN_BOTO_CONFIG`.
  Docstring rewritten to say so honestly.

102 scanner tests pass. **A + B are LIVE** — they touch `scans_status`
and `onboarding_aws_complete`, deployed with the `CisoCopilotApi` deploy
on 2026-05-21. **C / D / E are now LIVE too (2026-05-21)** — `build.sh`
rebuilt + pushed the `shasta-runner:latest` image
(`sha256:7cce1043…`); `CisoCopilotScan` deployed (reported "no changes"
— the ECS task def pins the `:latest` tag, which CDK does not diff, so
the next scan's `RunTask` pulls the updated image). C/D/E take effect on
the next AWS scan.

**Deferred from review (track for Slice 2 / follow-up):**
- Engine collector failures (e.g. missing `sqs:ListQueues`) are logged
  but not surfaced as `not_assessed` in `scans.scope` — a permission
  gap looks like a clean result (spec §10.1 accuracy lever).
- `ecs:RunTask` returns 200 with a `failures[]` array on capacity/subnet
  problems; `onboarding_aws_complete` doesn't inspect it → a task that
  failed to launch logs "started".
- Quick Phase 1 runs global units only — no per-region census, and the
  coverage map is written to `scans.scope` only after Phase 2. Spec
  §7.4/§10.1 and the code disagree; reconcile when building the web UX.
- Spec §8 still claims a wall-clock timeout bound (see E) — reconcile
  the spec or implement real cancellation.
- Engine check-matching is O(checks×resources) per service — fine at 3
  services, regroup before scaling to ~40.

### Scan Progress & Scan-Type UX — SHIPPED + MERGED (2026-05-21)
Scan-performance spec §10. Plan `docs/superpowers/plans/2026-05-21-scan-progress-ux.md`;
built subagent-driven (7 tasks, each spec- + quality-reviewed, plus a
final whole-branch review). Merged to `main` (`dad4c16`).
- **Backend:** `GET /connections` now carries each connection's
  `latest_scan` ({scan_id, tier, status, phase, started_at}); AWS rescan
  (`connections_list._rescan_aws`) is **tier-aware** and runs the v2
  Fargate scanner (was: legacy Lambda, no tier). Dead `SHASTA_RUNNER_FN`
  wiring removed.
- **Web:** shared `web/src/scan/` module (`useScanStatus` polling hook,
  `ScanTypeBadge`, `ScanProgress`, `scanLabels`); the ConnectClouds
  Quick/Medium/Deep scan picker + live progress card (AWS-only); the
  `/contact/deep-scan` Contact-Us route (Deep-tier gate); scan-type
  badges on the Findings / Risks / Dashboard headers.
- **Deployed:** `CisoCopilotApi` + `CisoCopilotScan` (a cross-stack
  export-removal deadlock from dropping the `shastaRunner` prop was
  resolved by the two-phase `--exclusively` deploy — Api first, then
  Scan). Web built + synced to S3 + CloudFront invalidated.
- **E2E verified:** a live tiered Quick rescan (`c660c70b-…`) moved
  `region_discovery → first_signal → crown_jewel → done`, 72 findings
  committed in Phase 1, 116 at completion. `GET /connections` returns
  `latest_scan`; the scan-status API serves the progress data the web
  polls. **The web UI rendering itself was not browser-tested** — verify
  the picker / progress card / badges visually next session.
- **Known limitation carried in:** the live region census in
  `ScanProgress` only shows at completion (the scanner writes the
  coverage map to `scans.scope` only after Phase 2 — the deferred item
  below). The progress card degrades gracefully (phase + finding count
  while running).

**Web UX browser-smoke (2026-05-21) — DONE.** KK smoke-tested the new
web UX on https://$SHASTA_DOMAIN/. Scan picker works; live
scan updates render correctly under the cloud being scanned. **One bug
found + fixed + deployed:** the `ScanPicker` dropdown
(`web/src/routes/ConnectClouds.tsx`) did not dismiss on outside-click —
it stayed stuck on screen if you didn't pick a tier. Added a
`pointerdown`/`Escape` close handler; web rebuilt + synced + CloudFront
invalidated. Also noted: `TEST_PLAN.md` T1.3 is stale — the email-first
sign-in flow is now ported to web (a `you@company.com` field, not the
old "Sign in with corporate account" button).

**UI/UX polish — first batch shipped (2026-05-21).** KK raised a
9-item UI/UX list; the three "looks-broken" ones were fixed + deployed
this session:
- **#2 — empty chat bubbles.** `MessageStream.tsx` now skips rendering
  any message bubble with empty/whitespace text (a tool-only turn or a
  failed/in-flight stream left blank "blob" bubbles). KK reported the
  blobs were transient — this is a defensive guard so they can't
  resurface.
- **#6 — policy editor modal title.** `PolicyEditor` takes an
  `initialTitle` prop; the list passes `p.title` so the header shows the
  name immediately instead of "Loading…".
- **#8 — AWS connection-row identifier.** When `account_identifier` is
  null (e.g. pending AWS rows), the row now shows `Added <date>` instead
  of a bare "—", so two AWS rows are distinguishable.

**▶ NEXT SESSION:** (1) The deferred PR-#4 review items below.
(2) The Azure scanner uplift brainstorm. (3) Incremental #6 — APNs push
end-to-end test. (4) The remaining 6 UI/UX-polish items KK deferred
(skeleton loaders, chat auto-titling, inline-editable risk Owner/Due,
single-bar "By cloud" chart, Connect-page layout, Trust-Center save
toast) — none blocking, batch in a dedicated UI-polish session.

### Gotchas paid in debugging time
- **Assumed-role creds expire at 1 h** → multi-region scans used to die
  with `RequestExpired`. Fixed via `RefreshableCredentials`.
- **Fargate cross-stack export deadlock** — a task-def ARN includes the
  revision number; exporting it ScanStack→ApiStack deadlocks CFN. Pass
  the stable *family name* (`scanTaskDefFamily`), not the ARN.
- **Scanner block-buffered stdout** → a running scan was invisible.
  Fixed with `PYTHONUNBUFFERED=1`.
- **EC2 rejects non-ASCII** in a security-group `description`.
- Scanner unit tests: `cd platform/lambda/shasta_runner &&
  ./.venv/bin/python -m pytest app/tests/` (the `.venv` is gitignored;
  `main.py` imports `shasta.*` so it is NOT importable in that venv —
  it's verified structurally + via live scans).

### ▶ Next major piece — Azure scanner uplift (brainstorm fresh)
Apply the same three-stage / parallel / tier-aware architecture
(`scan_pipeline.py` is deliberately AWS-free, meant to be lifted to a
shared location). **Open design question to resolve in the Azure
brainstorm:** Azure's scope unit is the **subscription** (within a
tenant), not the region. Should the scanner scan **all active
subscriptions**, or let the user **choose** which to scan (some
subscriptions are dev/throwaway)? KK's lean: let the user choose. Note
Azure also has **regions within each subscription** — the three-stage
probe likely nests (subscriptions × regions).

## 🚀 Incremental hardening — #1–#4 shipped + deployed (2026-05-20b)

Four scoped fixes, each TDD'd, committed, and deployed.

**Incremental #6 — APNs push end-to-end test — still pending** (deferred;
the scanner uplift took priority — see the top section for what's next).
Fire a synthetic "act now" finding and confirm the push notification
lands on KK's iPhone (APNs via SNS Mobile Push is wired but never
verified since the v2 cutover). After #6 the major roadmap begins —
see "Roadmap" below.

**Shipped this session:**
- **#1 — `ai_scanner` test rot fixed.** Deleted dead `writer.py` +
  `test_writer.py` (superseded by `unified_writer.py`); regenerated 4
  detector golden fixtures for SP1's `FindingEmission` schema. No deploy
  needed. Commit `6ab40af`.
- **#2 — `check_id → title` catalog.** `scripts/check_titles.py` — 292
  curated generic titles, served as `check_title` by `findings_list` /
  `findings_rollup`; web + iOS consume it; the old strip-heuristic is
  gone. Read-time (no rescan). Commit `7b0b614`. Deployed: API hotswap +
  web.
- **#3 — Bedrock / AI-Lambda inventory.** Shasta's
  `discover_aws_ai_services` drops Bedrock guardrails + AI-Lambda
  functions; the scanner now discovers them itself
  (`ai_pass.discover_bedrock_and_ai_lambdas`) and emits
  `bedrock_guardrail` / `lambda_ai_function` entities. Commit `361a559`.
  Deployed: `shasta-runner` image.
- **#4 — FedRAMP + PCI DSS mappings.** `scripts/framework_map.py` —
  287/292 checks mapped to NIST 800-53 Rev 5 + PCI DSS v4.0.1 controls;
  `merge_framework_map` applied at scan time in all 4 scanners + the AI
  pass. Commit `9f13b4b`. Deployed: web + all 4 scanner images. FedRAMP/
  PCI controls appear on findings at each cloud's next scan.

**#5 (iOS Policies / Questionnaires / Trust views) — cancelled.** iOS is
being rethought as a lightweight companion app (push alerting + hand-off
to Slack / Teams / Jira), not a web-feature port. See Roadmap → item 7.

**Shasta is reference-only.** `~/Projects/Shasta` (and the Shasta GitHub
repo) is a read-only dependency — never edit it. Shasta bugs are worked
around in *this* repo; #3 is the worked example.

### Roadmap

Incremental list: #1–#4 done, #5 cancelled, **#6 = APNs push test (next)**.

Major items, each its own brainstorm → spec → plan before build:
1. **Scanner comprehensiveness uplift** — AWS Security Hub parity, then
   bring Azure / GCP / Entra to the same depth + accuracy. (The first
   big change after #6.) **Slice 0 shipped 2026-05-21 — see the top
   section. Slices 1-6 remain.**
2. **Dynamic dashboards & reports** generated from chat.
3. **Tech-stack-aware threat-intel feeds** — beyond KEV: EPSS, NVD,
   vendor advisories, filtered per tenant.
4. **Unified vulnerability / risk prioritisation register.**
5. **Attack-path analysis** — graph-based, to crown-jewel assets.
6. **AI-powered MDR** — agentic detection + managed (reversible) response
   on the real-time event pipeline.
7. **iOS revamp** — companion app: push alerting + hand-off findings /
   issues to the team over Slack / Teams / Jira (MCP-based). Done after
   the first six majors.

## 🚀 AI Discovery — cloud-AI connector + findings overhaul (2026-05-20)

**Status: shipped, deployed, merged to `main`.** Spec:
`docs/superpowers/specs/2026-05-20-ai-discovery-connectors-design.md`.
Plan: `docs/superpowers/plans/2026-05-20-ai-discovery-cloud-ai.md`.

**Deferred — blocked:** Plan 2 — the **OpenAI / Anthropic provider
connectors** (spec §7). The spec is written; the implementation plan is
NOT. Blocked on KK obtaining enterprise/admin API access to OpenAI +
Anthropic. Once unblocked: research the two admin APIs, write
`docs/superpowers/plans/2026-05-20-ai-discovery-providers.md`, then build
(a new `provider_scanner` Lambda + paste-key connect flow — see spec §7).

**What landed (cloud-AI connector — completes the Discovery module's
cloud surface):**

- **`shasta_runner/app/ai_pass.py`** — new module. Wraps Shasta's
  `discover_aws_ai_services` (SageMaker/Comprehend), `run_full_aws_ai_scan`
  (15 AWS-AI checks), and `compliance/ai` mapper. Folded into **every AWS
  scan** via the `shasta_runner` handler — no separate connection/trigger.
- AI services emit as `domain='cloud'` entities (`sagemaker_endpoint`,
  `sagemaker_model`, `sagemaker_training_job`, `comprehend_endpoint`) +
  `aws_account → contains` edges. AI findings carry `frameworks` with
  **NIST AI RMF / ISO 42001** (also EU AI Act, OWASP LLM, MITRE ATLAS)
  control IDs.
- **`unified_writer` fix** — `_insert_finding` previously hardcoded the
  `findings.frameworks` column to `'{}'`; it now persists
  `FindingEmission.frameworks`. This is what lets `compliance_summary`
  roll AI frameworks into the compliance view (and is a latent fix for
  cloud SOC 2 / CIS findings too — they were also losing framework data).

**Deployed:** `shasta-runner` image rebuilt + pushed to ECR; Lambda
`ciso-copilot-shasta-runner` updated (CodeSha256 `a81711b4…`). Empty-event
smoke test passed (all imports load in the Lambda runtime). **28 unit
tests pass.**

**First E2E (scan `053072ba`, 2026-05-20) — exposed a pre-existing
writer bug, now fixed.** The scan ran cleanly and `ai_pass` succeeded
(`ai_pass: 0 entities, 255 findings` — KK's account has no SageMaker/
Comprehend; the 15 AWS-AI checks produced 255 findings). But `commit_scan`
then failed with Postgres **42P18** (`could not determine data type of
parameter $8`) and rolled the whole transaction back — zero findings
written. Root cause: `unified_writer` passed nullable params
(`evidence_packet`, `subject_entity_id`) as typeless NULLs inside
`CASE WHEN :x IS NULL` — Postgres can't type a NULL-only param in an
`IS NULL` test. **This bug has silently broken every cloud scan since
SP1** (the 2026-05-19 scan failed the same way). Fixed (commit `d17c500`)
— plain typed `CAST(:x AS T)`; `shasta_runner` + `ai_scanner` images
rebuilt and redeployed.

**Second E2E exposed finding-ingestion bugs (2026-05-20) — fixed.** The
re-scan succeeded but the output was wrong: `unified_writer` hardcoded
every finding to `domain='ai'` + `status='fail'` (so cloud IAM/storage/
encryption checks showed inside the AI group, and `not_assessed`
"Unable to check …" per-region results showed as failures), and INSERTed
a fresh row per scan (counts doubled on every rescan). Fixed (commit
`62357b2`): `FindingEmission` now carries real `domain`/`status`/`region`;
`not_assessed`/`not_applicable` results are dropped at ingestion;
`_insert_finding` UPSERTs on a natural key `(tenant, conn, check_id,
resource_arn, region)`; migration `008` adds the unique index and purged
the 3,734 accumulated junk rows. `shasta_runner` + `ai_scanner` redeployed.

**Demo gate — PASSED** (scan `5c62e6d3`, 2026-05-20 23:30): 147 findings,
correctly categorized — storage 65, iam 20, **ai 17**, logging 14,
encryption 13, networking 10, monitoring 8; status 85 pass / 32 fail /
30 partial; zero `not_assessed` noise. (AWS scan takes ~13 min — wait for
`scan complete` in the logs before checking.)

**Findings UX rebuilt + deployed (2026-05-20).** The dashboard now shows
**Fail / Partial / Pass** tiles (was a single fail-only "Open findings");
the Findings page shows *all* findings with a user-chosen grouping —
**Status · Category · Cloud · Compliance Framework** (default Status) —
rolled up by check into cards with **generic titles** (quoted resource
names stripped; the real title + ARN are in the drill-in). Backend:
`findings_summary` returns `by_status`; `partial` added to
`ALLOWED_STATUSES`. This resolves the AI-group-overcount and Bug 5.

**Known limitations / open items:**
- ~~Bedrock model inventory~~ ✅ RESOLVED — incremental #3 (2026-05-20b).
  The scanner discovers Bedrock guardrails + AI-Lambda functions itself
  (`ai_pass.discover_bedrock_and_ai_lambdas`); Shasta untouched.
- ~~Generic finding titles heuristic~~ ✅ RESOLVED — incremental #2;
  replaced by the curated `check_id → title` catalog.
- ~~`ai_scanner` test rot~~ ✅ RESOLVED — incremental #1.
- No route-level web tests for `Dashboard.tsx` / `TopRisks.tsx` — the repo
  has no route-test precedent; verified via type-check + a live endpoint
  smoke test.

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
  token-streamed assistant replies. Deployed to `$SHASTA_DOMAIN`.

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

**Phase 4a demo gate — first authed test FAILED, root cause fixed, awaiting retest.**
KK's first sign-in test (2026-05-20): message sent but no reply; refresh
showed the conversation row but no message text. Root cause: `ChatStreamFn`'s
`_verify_jwt` crashed importing `cryptography` —
`_rust.abi3.so: cannot open shared object file`. The `chatStreamAsset`
bundling installed the `cryptography` wheel (via `PyJWT[crypto]`) for the
host platform, not Lambda's linux x86_64. Every JWT verification failed →
streaming endpoint returned `unauthorized` for every request → no replies,
nothing persisted (the LWA app is what writes both user + assistant rows).
**Fixed** (commit `7c87069`): added `platform: 'linux/amd64'` + manylinux
x86_64 pip flags to the bundling, matching `AiGithubFn`. Redeployed +
verified the `.so` import error is gone. **Next: KK retries the authed
demo; if it passes, Phase 4b (tools + 8 artifact components).**

**SP4 bug-fix round (2026-05-20) — 6 bugs from KK's testing, all fixed + deployed:**
1. **Phantom "Bye"/empty voice messages** — Whisper hallucinates on silence.
   Fixed: client drops empty/whitespace transcripts; server VAD tuned
   (threshold 0.5→0.6, silence 500→700ms).
2. **No compliance-control details** — `TOOL_RULES` over-blocked. Fixed:
   the model may now answer GENERAL framework knowledge (what MCSB AM-1 /
   SOC2 CC2.1 require) from its own knowledge; customer-specific data stays
   tool-gated.
3. **Donut all-red / zero segments** — both `tools.ts` AND server
   `tools_dispatch.py` set explicit red segment colors. Fixed: hint sends
   no color; `ChartDonut`/`ChartBar` apply an 8-hue palette; zero segments
   muted.
4. **Voice reply split into 2 bubbles** + **5. assistant text above the
   user's question** — transcripts were appended in event-arrival order.
   Fixed: voice messages keyed by Realtime `item_id` (`voiceUpsert`
   reducer action); user placeholder created on `conversation.item.created`
   so it lands before the assistant reply; late async transcript fills it.
6. **"IAM issues" returned Key Vault findings** — `query_findings` had no
   domain filter. Fixed: added a `domain` enum param (`findings.domain`:
   iam/storage/encryption/…) so the model can scope queries.
Commits `0b92f4b`, `a0aa068`, `d034e80`, `f93d593`, `4fa55cd`. The earlier
"deferred 4b/4c polish" items (donut, TOOL_RULES) are now resolved.

**SP4 Phase 4d deployed (2026-05-20) — action approvals. SP4 feature-complete.**
The chat can now propose actions and the user approves them:
- `propose_risk_entry` / `propose_policy_draft` tools return a pending
  `approval_card` (NEVER auto-execute — determinism invariant).
- Clicking **Approve** → `POST /risks` or `/policies`, idempotent on the
  card's `approval_id` (atomic `INSERT ... ON CONFLICT DO NOTHING
  RETURNING` — double-tap safe). Card → `approved` with a link.
- Edit-in-place before approving; Cancel. Card state persists via
  `PATCH /v1/conversations/{id}/messages/{message_id}` — reload shows the
  final state.
- Migration `007_approval_idempotency.sql` — `source_approval_id` on
  `risks` + `policies` with a partial unique index.
- The pre-SP4 voice modal (`web/src/voice/`) retired; `excelHelpers.ts`
  moved to `web/src/lib/`.
- **Phase 4d demo gate — pending KK's test:** in chat, "add X to my risk
  register" → editable approval card → Approve → risk created. Same for a
  policy draft. Double-click Approve = no duplicate.
- **SP4 status:** all 4 phases (4a shell+text · 4b tools+artifacts · 4c
  voice · 4d approvals) built + deployed on `feat/sp4-chat-first` and
  merged via PR #3 (commit `06cb4f6`). Deferred polish (see items above):
  compliance donut visual, TOOL_RULES general-knowledge tuning. iOS = SP4.5.

**SP4 Phase 4c deployed (2026-05-20) — voice.** The chat surface now has
voice via OpenAI Realtime over WebRTC:
- **Model: `gpt-realtime-2`** (OpenAI's newer GPT-5-class realtime model —
  validated against the live API; drop-in over `gpt-realtime`).
- **`voice.py`** mints the Realtime ephemeral key with the full persona
  (`prompts.py` — PERSONA + TOOL_RULES + VOICE_ADDENDUM) + the 12-tool
  catalog (the browser supplies the Realtime-shaped tool defs).
- **`voiceClient.ts` + `turnQueue.ts`** — browser WebRTC client (lifted
  from the proven `web/src/voice/` client). Voice tool calls execute
  browser-side via the TS `executeTool`. Transcripts persist per-turn to
  `conversation_messages` (`modality: "voice"`); `fetch(keepalive:true)`
  flushes the pending turn on page unload.
- **Voice UI** — mic toggle in the composer (off/connecting/on), persimmon
  breathing dot in the header, live transcripts into the stream, barge-in
  (`response.cancel` on user speech), sync-warning banner.
- **Gotcha — OpenAI Realtime rejects `session.metadata`.** The first mic
  test failed: every `/v1/realtime/client_secrets` mint 400'd with
  `"Unknown parameter: 'session.metadata'"`. `voice.py` had bound
  `conversation_id` into `session.metadata` — OpenAI's Realtime API has no
  such field. Removed it (commit `9735d36`); `conversation_id` doesn't need
  to reach OpenAI — the browser owns the conversation binding. Lesson:
  validate the FULL session payload against the live API, not a minimal one.
- **Gotcha 2 — voice mint response field.** After the metadata fix the mint
  200'd but the mic still failed: `voice.py` returned the ephemeral key as
  `client_secret`, the web `voiceClient` reads `.value`. Mismatch → `Bearer
  undefined` to `/v1/realtime/calls` → 401. Fixed (commit `713a315`):
  `voice.py` returns `value`. Lesson: the 4c.1 + 4c.2 reviews each checked
  one side vs the spec; neither cross-checked the two sides of the
  Lambda↔client contract.
- **Phase 4c demo gate — pending KK's mic retest** (after the metadata fix):
  toggle the mic, hold a spoken conversation, verify transcripts stream +
  persist + tools work + barge-in. Spec §15: same questions in text vs
  voice → same tool calls / same results.

**SP4 Phase 4b deployed (2026-05-20) — tools + artifacts.** The chat can
now query real tenant data and render it as cards:
- **`tools.ts`** — 12-tool TS catalog (`web/src/chat/`): 8 data, 2 action
  (`propose_*`), 2 side-effect. Used by the browser for the landing
  briefing + (later) voice.
- **`tools_dispatch.py`** — Python server-side mirror in the chat_session
  Lambda. The text path runs the **Anthropic agentic tool-use loop
  server-side** inside the LWA app (`app.py`): the model calls tools, the
  Lambda executes them against Aurora (tenant-scoped), streams back
  `text-delta` + `tool-result` SSE events. Max 6 tool rounds.
- **8 artifact components** + `Artifact.tsx` renderer (`web/src/chat/
  artifacts/`) — kpi_card, entity_list, finding_card, risk_card,
  chart_bar, chart_donut, severity_breakdown, approval_card. Rendered
  inline in the chat stream; persisted as `tool` messages so they
  reconstitute on reload.
- **SourceSideSheet** — clicking a card's `↗ source` chip opens a
  right-edge panel with the underlying entity/finding.
- **Landing morning briefing** — a fresh conversation auto-runs
  `get_morning_briefing` and shows 2-3 posture cards.
- Determinism invariant intact: the LLM never writes — `propose_*` tools
  return pending approval cards only (the approve→POST is Phase 4d).
- **Known 4b limitation:** persisted `tool` messages aren't replayed into
  the Anthropic history across turns (they lack the tool_use/tool_result
  block IDs), so the model re-derives tool calls each turn rather than
  "seeing" prior tool outputs. Cards still reconstitute on reload. Fine
  for 4b; revisit if multi-turn tool memory is needed.
- **Phase 4b demo gate — PASSED** 2026-05-20 (KK: "works like a charm",
  findings + AI inventory render with real data).
- **Deferred 4b polish:** the `chart_donut` for compliance posture renders
  but is visually ineffective — revisit the donut component (sizing /
  legend / segment clarity). KK flagged, agreed to improve later.
- **Deferred 4c polish (prompt tuning):** asked "details for CC 2.1" the
  assistant declined ("not available from findings"). `TOOL_RULES`
  over-constrains — it should let the model answer GENERAL compliance/
  security knowledge (what a control like SOC 2 CC2.1 requires) from its
  own knowledge, while keeping CUSTOMER-SPECIFIC data (the tenant's status
  for that control) gated behind tools. Refine the `prompts.py` TOOL_RULES
  to draw that line. (Bigger future option: a compliance-control reference
  tool/KB.) KK flagged 2026-05-20, agreed to iron out later.

**Post-4a-demo-gate additions (2026-05-20, KK feedback during testing):**
- **Rename + Delete on conversations** (`ConversationRail` hover → ⋯ menu,
  inline rename, delete-with-confirm; backend `PATCH`/`DELETE` already
  existed). Commit `35d6801`.
- **Legacy screens re-themed to Quiet Paper.** Tailwind `blue`/`slate`/
  `white` scales remapped in `web/tailwind.config.js` + `index.css` body —
  flips all ~17 route files to the warm cream/persimmon palette without a
  per-file sweep. Commit `836c256`. Chat surface (`web/src/chat/*`) stays
  on its own inline-hex Quiet Paper styling — two styling systems, same
  palette; a future pass could unify them.
- **Voice mic** is NOT in Phase 4a — it's Phase 4c by design. The 4a
  composer is text-only.

**Gotcha — Python Lambda native deps:** ANY Python Lambda bundling a
package with a compiled extension (`cryptography`, `pydantic-core`, etc.)
MUST bundle with `platform: 'linux/amd64'` + `pip install --platform
manylinux2014_x86_64 --implementation cp --python-version 3.12
--only-binary=:all:`. Otherwise pip on an Apple-Silicon Mac installs the
wrong-arch wheel and the Lambda fails at import. `AiGithubFn` and now
`ChatStreamFn` do this. Pure-Python deps (starlette, uvicorn, PyJWT
itself, boto3) don't need it — but `PyJWT[crypto]` pulls `cryptography`,
which does.

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
`$AWS_ACCOUNT_ID`. **Web sign-in via Google verified end-to-end on 2026-05-18**
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
| AWS account | `$AWS_ACCOUNT_ID` (us-east-1) |
| API base | `$API_BASE_URL/` |
| Web SPA | `https://$SHASTA_DOMAIN/` (custom domain live 2026-05-18; backed by CloudFront `$SHASTA_DOMAIN` which still works) |
| Asset CDN | `https://<CDN_HOSTNAME>/` |
| Cognito User Pool | `us-east-1_jOC1znCSS` (recreated 2026-05-18; old `us-east-1_ePRQ2iwZT` retained, awaiting cleanup) |
| Cognito iOS client | `2r71e13kahf79bvb9stuehm3il` |
| Cognito Web client | `5vroudnp54n7fdqvjj49ff53br` |
| Event bus | `arn:aws:events:us-east-1:$AWS_ACCOUNT_ID:event-bus/ciso-copilot-events` |
| Aurora cluster | `<DB_CLUSTER_NAME>` (db: `ciso_copilot`) |
| iOS bundle | `ai.transilience.cisocopilot` |

## What works (verified end-to-end on 2026-05-18)

- **Google sign-in (iOS + web Cognito hosted UI)** with email-first home-realm discovery (iOS). Web still on the legacy generic-IdP-picker. Web sign-in + sign-out + sign-in-again all verified on 2026-05-18 (this was the test case that drove the pool recreate).
- **Tenant approval gate**: post-confirmation Lambda creates a `tenants` row in `pending` and emails `APPROVAL_RECIPIENT` (currently KK's Gmail; SES sender flipped to `<ADMIN_EMAIL>` because `no-reply@settlingforless.com` isn't DKIM-verified yet).
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

5. **SES sandbox + Gmail spoof-drop** *(FIXED 2026-05-18 later)*: domain `settlingforless.com` now verified in SES (TXT + 3 DKIM CNAMEs + SPF TXT at apex, all published via Google Cloud DNS console). post_confirmation Lambda + `scripts/send_approval_email.py` now both `Source=CISO Copilot <no-reply@settlingforless.com>`. Earlier symptom: when sending FROM <ADMIN_EMAIL> via AWS SES (because Gmail is the only verified-sender identity), Gmail silently spam-foldered or dropped them — Gmail From: arriving from non-Google IPs without Google DKIM signature looks like spoofing. Account still in SES sandbox (200/day, 1/s); sufficient since approval emails only go TO the verified `APPROVAL_RECIPIENT` (<ADMIN_EMAIL>). Request prod access only when we want to send notifications/digests to other users.

6. **CFN templateURL must be S3, not CloudFront**: CloudFormation Console hard-rejects non-S3 URLs. We presign a 1-hour S3 GET URL on every `/onboarding/aws/initiate` call. The Lambda role has `s3:GetObject` on `arn:aws:s3:::<CDN_BUCKET>/cfn/aws-onboard.yaml`. IAM perm propagation can take ~1 min after a fresh deploy; if the first presigned URL 403s, wait and retry.

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

- `users` table: 1 Google user (`<ADMIN_EMAIL>`), 1 Microsoft user (`<ADMIN_EMAIL>`), 1 Google user (`randevak@gmail.com` — KK's wife) all linked to their own admin-role tenant rows. Same rows survived the pool recreate because `users.sso_subject` is keyed on the IdP `sub`, not the Cognito sub.
- `tenants` table: `gmail.com` (approved, KK only), `transilience.ai` (approved, KK only), `randevak@gmail.com` (pending — full email used as tenant key for personal-domain isolation), `Dev Test Tenant` (long-lived scaffold).
- `cloud_connections` table: 3 **active** connections — AWS (`26e97477-...`, account `$AWS_ACCOUNT_ID`), Azure (`79964b99-...`, Entra tenant `017c6f31-...`, 2 subscriptions), GCP (`219f41eb-...`, project `gen-lang-client-0693606939`). All orphan `pending` rows from re-clicked Connect tiles deleted.
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
- **SES from-domain fix**: switched `Source=` to `no-reply@settlingforless.com` (DKIM + SPF verified via Google Cloud DNS). Previously sent from `<ADMIN_EMAIL>` which Gmail silently spam-foldered (Gmail-from-AWS-IP looks like spoofing).
- **SES production access granted**: form submitted by KK; AWS approved. Sending TO unverified recipients now works → user-side approval notifications deliver.
- **admin_decision Lambda error handling**: wraps `_send_user_email` in try/except so SES sandbox failure no longer 500s the approve link (was misleading "Internal Server Error" while the tenant flip had already succeeded).
- **`$SHASTA_DOMAIN` custom domain**: ACM cert issued, Cloud DNS records (CNAME + DKIM validation) added, CloudFront alternate domain attached, Cognito callback URLs include the new domain. SPA reachable at `https://$SHASTA_DOMAIN/` end-to-end.
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
