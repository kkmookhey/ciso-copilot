# MCP Connectors Slice 2 — Admin Slack bot + autonomous CRITICAL broadcast

> Production-ready autonomous broadcast surface: when a CRITICAL finding lands,
> Shasta posts a Block Kit card to the tenant's designated Slack channel.
> Admin installs the Shasta Slack workspace bot once, picks a channel, toggles
> autonomous broadcasts on. Brainstormed 2026-05-31, follows the load-bearing
> infra shipped in Slice 1 (`docs/superpowers/specs/2026-05-28-mcp-connectors-design.md`).

## 1. Goal and success criteria

**Goal:** prove the autonomous-broadcast surface end-to-end. Slice 1 proved the
per-user, on-demand action surface (analyst says "DM John"; Risks page button
posts to Slack). Slice 2 proves Shasta can act *without* a human in the loop,
under three layers of safety control.

**Success criteria:**

1. An admin opens `/settings` → Connectors tab → installs the Shasta bot to
   their Slack workspace in under 60 seconds, picks `#shasta-alerts` as the
   broadcast channel, and sees "Installed · #shasta-alerts · Autonomous
   broadcasts ON" in the UI.
2. When a scanner Lambda (AWS, Azure, AI, SOC drift) inserts a finding with
   `severity='critical' AND status='fail'`, a structured Block Kit card lands
   in the configured channel within ~60 seconds, with a button that
   deep-links into Shasta and survives an unauthenticated browser tab.
3. The same finding never broadcasts twice in the same scan
   (idempotency on `(tenant_id, finding_id, scan_id)`, 7-day TTL).
4. Three layers of kill switch each independently stop the broadcast:
   per-tenant admin toggle (instant), admin disconnect (instant), global SSM
   parameter (≤60s propagation).
5. The fan-out hook is wired into the *shared* `unified_writer` module, not
   duplicated per scanner. A new scanner added in a future slice inherits the
   broadcast trigger by importing the shared module.
6. Post-merge re-smoke can flip a deleted channel into the picker, watch the
   DLQ accumulate, and CloudWatch alarms within ~5 minutes.

## 2. Why this design (and what was reconsidered)

- **Production-hardened, not demo-thin.** A thinner Slice 2 (direct invoke
  instead of SQS, no DLQ, no DeepLinkGate, no global kill switch) was
  considered and rejected — Shasta is positioned as a security platform, and
  silent alert loss or accidental data leakage in the broadcast template are
  both operationally unacceptable. The complexity is load-bearing.
- **`unified_writer` consolidation rolled into this slice, not deferred.**
  Today the same finding-write logic is duplicated across
  `ai_scanner/unified_writer.py`, `shasta_runner_azure/app/unified_writer.py`,
  and one or two other places. Without consolidation, the fan-out hook would
  have to be edited in N files every time it changes — drift risk in
  perpetuity. Consolidating into `_shared/unified_writer.py` and having
  scanners import it is the lasting fix.
- **MCP SDK does the runtime; we own the OAuth + plumbing.** The `mcp`
  Python SDK abstracts streamable HTTP + `list_tools()` + `call_tool()` —
  about 10 lines of code in `_open_session_for_user`. What it does NOT
  abstract is multi-tenant server-side OAuth, KMS-encrypted token storage,
  refresh in a stateless Lambda, or the autonomous-broadcast plumbing.
  Slice 1 built the OAuth half. Slice 2 builds the plumbing half. The MCP
  call inside `findings_subscriber` is one `async with` + one `call_tool`.
- **One Slack app, two OAuth flows.** Admin install reuses the existing
  Shasta Slack app (registered in Slice 1) but hits a different OAuth route
  with bot scopes (`chat:write`, `channels:read`, `groups:read`). Avoids
  doubling the SSM credential surface and the Slack-app maintenance burden.
- **SQS standard queue, not direct invoke.** Standard queue gives
  at-least-once delivery + retry-with-backoff + DLQ for free. Direct invoke
  would lose retry on transient Slack errors, and ECS-DLQ-equivalent doesn't
  exist for Lambda invokes.
- **`findings_subscriber` is its own Lambda, not a folded-in handler.**
  Different trigger source (SQS, no HTTP), different IAM surface, different
  cold-start cost profile. Conflating it with the OAuth Lambda would
  penalize every OAuth request with the subscriber's import-time cost.

## 3. Scope

### In scope

- Admin Slack workspace install flow (OAuth with bot scopes) — extends the
  existing `connectors/` Lambda with new route handlers
- Channel picker (calls Slack `conversations.list` via the bot's MCP session)
  + broadcast-channel setter route
- `findings_subscriber/` Lambda (SQS-consumed, idempotent, kill-switched)
- Block Kit card formatter with golden-test coverage per finding shape
- Three-layer kill switch (per-tenant toggle, admin disconnect, global SSM)
- `unified_writer` consolidation: hoist the duplicated writer code into
  `_shared/unified_writer.py` and convert `ai_scanner`, `shasta_runner_azure`,
  and any other scanner with its own copy to import the shared module
- Fan-out hook (`_fanout_critical`) inside the shared writer
- `mcp_oauth.get_admin_session(tenant_id, "slack")` — mirrors `get_session`
  but resolves against `tenant_bot_connectors`
- Web: admin block on `ConnectorsTab.tsx` (gated to `role='admin'`) with
  install / channel picker / on-off toggle / disconnect
- Web: `<DeepLinkGate>` wrapper for `/risks/:finding_id` so the Slack card's
  "View" button survives an unauthenticated browser tab
- CDK: `autonomousBroadcastQueue` (SQS) + `autonomousBroadcastDlq` +
  `autonomousBroadcastSeenTable` (DDB) + `findingsSubscriberFn` Lambda +
  IAM grants on scanner Lambdas for `sqs:SendMessage`
- CloudWatch: DLQ depth alarm + "critical finding inserts vs broadcasts
  sent" drift metric

### Out of scope (deferred)

- Customer-defined rule builder (different sub-project — "Agentic Workflows")
- Per-vendor audit log table (CloudTrail + vendor-side audit is sufficient
  for v1; same call as Slice 1)
- Batched / digest broadcasts (one finding = one message, clean for
  threading)
- Multi-channel routing
- Customer-editable Block Kit template
- iOS lockscreen notification of the same broadcast (HANDOFF tracks iOS as
  a separate companion-app vision)
- Slack message-update flows (e.g. updating the card if status flips to
  pass; today we send-and-forget)

## 4. Architecture

```
   AWS scanner   Azure scanner   AI scanner   SOC drift ingest
       │              │              │              │
       └──────────────┴──────┬───────┴──────────────┘
                             │ all call into shared module
                             ▼
       platform/lambda/_shared/unified_writer.py  ← consolidated
              ├─ INSERT INTO findings (...)
              └─ if severity='critical' AND status='fail':
                   sqs.send_message(...)   ← best-effort, non-blocking
                             │
                             ▼
           SQS: autonomous-broadcast-queue
              (visibility 30s, maxReceiveCount 5 → DLQ)
                             │
                             ▼
       platform/lambda/findings_subscriber/   ← NEW Lambda
           main.py       (handler, 1 msg/invoke)
           idempotency.py (DDB seen-table, conditional PutItem)
           kill_switch.py (SSM cached 60s, fail-open)
           block_kit.py   (Slack template + escape helpers)

   platform/lambda/connectors/   ← EXTENDED, no new Lambda
       handlers_slack_workspace_bot.py   ← new route handlers
       handlers_admin_slack.py            ← channel picker + setter
       (admin gating via _require_admin helper)

   platform/lambda/_shared/mcp_oauth/admin_session.py   ← new
       get_admin_session(tenant_id, kind="slack")
       (mirrors get_session but resolves against tenant_bot_connectors)

   web/src/components/connectors/
       ConnectorAdminBlock.tsx   ← admin-only block on Connectors tab
       ChannelPicker.tsx          ← modal listing Slack channels
   web/src/components/DeepLinkGate.tsx   ← /risks/:id auth bounce wrapper
```

## 5. Components

### A. `findings_subscriber/` Lambda (new)

- **Trigger:** SQS event source on `autonomous-broadcast-queue`. Batch size 1
  — per-message idempotency is simpler than batch partial-success.
- **Bundling:** Same pattern as `connectors/` — `lambda/findings_subscriber/`
  + `_shared/mcp_oauth` copied into `/asset-output` so
  `mcp_oauth.get_admin_session(...)` resolves at runtime.
- **Files:**
  - `main.py` — handler entry + orchestration
  - `idempotency.py` — DDB get/conditional-put on the seen table
  - `kill_switch.py` — SSM cached read with 60-second TTL
  - `block_kit.py` — Slack template builder + `escape()` helpers
  - `requirements.txt` — `mcp`, `boto3`, `cryptography` (KMS envelope decrypt
    via `mcp_oauth.crypto`)
- **IAM grants:**
  - `sqs:ReceiveMessage`, `sqs:DeleteMessage`,
    `sqs:GetQueueAttributes`, `sqs:ChangeMessageVisibility` on the queue +
    DLQ
  - `dynamodb:GetItem`, `dynamodb:PutItem` on `autonomous-broadcast-seen`
  - `kms:Encrypt`, `kms:Decrypt` on `connectorTokensKey` (token decrypt + JIT
    refresh re-encrypt)
  - `ssm:GetParameter` on `/cisocopilot/autonomous_rule/enabled` +
    `/cisocopilot/connectors/slack/client-id` +
    `/cisocopilot/connectors/slack/client-secret`
  - `kms:Decrypt` on `alias/aws/ssm`
  - Aurora Data API via `props.dbCluster.grantDataApiAccess(fn)`
- **Memory / timeout:** 256 MB, 30 s. Slack's API responds in <2 s; the JIT
  refresh adds up to 1 s.

### B. `connectors/main.py` extension

- **New module:** `handlers_slack_workspace_bot.py` registers two routes via
  the existing `_route` decorator:
  - `POST /connectors/connect/slack-workspace-bot` — admin gate; returns
    `{ authorize_url }`
  - `GET /connectors/callback/slack-workspace-bot` — exchanges code, inserts
    `tenant_bot_connectors`, 302 to `/settings?tab=connectors&ok=slack-bot`
- **New module:** `handlers_admin_slack.py` registers three routes:
  - `GET /connectors/admin/slack/channels` — calls `conversations.list` via
    bot's MCP session, returns `[{id, name, is_private}]`
  - `POST /connectors/admin/slack/broadcast-channel` — validates channel_id
    against a fresh `conversations.list` (anti-tamper), updates
    `tenant_bot_connectors.broadcast_channel_id` and
    `autonomous_rule_enabled` flag
  - `DELETE /connectors/admin/slack` — revokes bot token with Slack, marks
    row `status='revoked'`
- **Admin gate helper:** `_require_admin(claims) -> (tenant_id, user_id) |
  Resp403`. Does the same `sso_subject` join as
  `_resolve_user_context` plus a `role='admin'` filter. 403 if the join
  returns nothing.
- **State JWT audience:** new value `slack-bot-callback` for this flow.
  `state.verify_state(token, expected_provider="slack-bot")` enforces it —
  prevents a `slack` user-flow JWT from being replayed at the bot callback
  and vice versa.

### C. `mcp_oauth.get_admin_session` (new shared module)

- **File:** `platform/lambda/_shared/mcp_oauth/admin_session.py`
- **API:**
  ```python
  @asynccontextmanager
  async def get_admin_session(tenant_id: str, kind: Literal["slack"]):
      """Open an MCP session using the tenant's admin-installed bot token.
      Autonomous broadcast path. Same shape as user get_session but resolves
      against tenant_bot_connectors and uses bot_id as the advisory-lock key."""
  ```
- **Reuses:** the existing `crypto.encrypt_token` / `decrypt_token` envelope,
  the transaction-scoped advisory-lock pattern from
  `session.refresh_if_near_expiry`, the same `streamablehttp_client` + MCP
  `ClientSession` shape.
- **Does NOT touch `session.py`.** Keeping the per-user path untouched
  reduces blast radius. Refactoring later if both paths grow more
  duplication is a separate cleanup.

### D. `_shared/unified_writer.py` (consolidation + fan-out)

- **Pre-existing files to consolidate:**
  - `platform/lambda/ai_scanner/unified_writer.py` (~360 lines)
  - `platform/lambda/shasta_runner_azure/app/unified_writer.py` (~355 lines)
  - Any other scanner that has its own copy (audit via
    `grep -rln "INSERT INTO findings" platform/lambda/`)
- **Target:** `platform/lambda/_shared/unified_writer.py` — single source of
  truth, imported by every scanner Lambda via the `_shared/` bundle copy
  pattern.
- **Fan-out hook:**
  ```python
  def _fanout_critical(tenant_id, finding_id, scan_id, severity, status):
      """Best-effort SQS publish for the autonomous broadcast rule.
      Failures log but don't propagate — a missed broadcast is recoverable,
      a failed scanner write is data loss."""
      if not os.environ.get("AUTONOMOUS_BROADCAST_QUEUE_URL"):
          return
      if severity != "critical" or status != "fail":
          return
      try:
          _sqs.send_message(
              QueueUrl=os.environ["AUTONOMOUS_BROADCAST_QUEUE_URL"],
              MessageBody=json.dumps({
                  "tenant_id": tenant_id,
                  "finding_id": finding_id,
                  "scan_id": scan_id,
              }),
          )
      except Exception as e:
          print(f"[unified_writer] fan-out failed: {e!r} (finding={finding_id})")
  ```
- **Call site:** inside the existing INSERT/UPSERT block, after the row is
  confirmed-written (RETURNING xmax or equivalent). NOT inside a try/except
  swallow — the hook itself swallows its own errors.

### E. Web — `<DeepLinkGate>` + admin block

- **`web/src/components/DeepLinkGate.tsx`:** wraps `/risks/:finding_id`. On
  no Cognito session, navigates to `/signin?after=/risks/{id}`. Existing
  Cognito callback already honors `?after=`.
- **`web/src/components/connectors/ConnectorAdminBlock.tsx`:** new component
  rendered at the bottom of `ConnectorsTab.tsx`, only when
  `currentUser.role === 'admin'`. Three states:
  1. Not installed → "Install Shasta to your Slack workspace" button
  2. Installed, `broadcast_channel_id IS NULL` → ChannelPicker modal
  3. Installed + channel set → channel name + autonomous-rule toggle +
     Disconnect
- **`web/src/components/connectors/ChannelPicker.tsx`:** modal listing
  channels from `GET /v1/connectors/admin/slack/channels`. Filterable text
  input, channel selection, "Save" calls
  `POST /v1/connectors/admin/slack/broadcast-channel`.
- **`web/src/lib/api.ts` additions:** `installSlackWorkspaceBot()`,
  `listSlackChannels()`, `setBroadcastChannel(channel_id)`,
  `revokeSlackBot()`.

### F. CDK

- **`data-stack.ts`:**
  - `autonomousBroadcastQueue` (SQS standard, visibility 30 s)
  - `autonomousBroadcastDlq` (SQS standard) — redrive policy
    `maxReceiveCount=5`
  - `autonomousBroadcastSeenTable` (DDB, PK `seen_key` STRING, TTL on
    `ttl_epoch`, billing mode `PAY_PER_REQUEST`)
- **`api-stack.ts`:**
  - `findingsSubscriberFn` (Python 3.12 Lambda, container Lambda not needed
    — no npm/msal shell-outs) with IAM grants per §5.A
  - Existing `connectorsFn` gains zero new env vars — the admin routes
    reuse the same KMS/SSM/DDB-PKCE surface
  - Scanner Lambdas (`aiScannerFn`, `shastaRunnerAwsFn`,
    `shastaRunnerAzureFn`, any others writing findings) gain:
    - `AUTONOMOUS_BROADCAST_QUEUE_URL` env var
    - `sqs:SendMessage` grant on the queue (NOT the DLQ)
- **CloudWatch:**
  - DLQ depth alarm: `ApproximateNumberOfMessagesVisible > 0` for 5 minutes
  - Drift metric: a `MetricFilter` on the unified_writer's CloudWatch log
    counting critical-fail inserts vs. an `SQS ApproximateNumberOfMessagesReceived`
    on the queue; raw drift > 2/hour fires a soft alarm

## 6. Data flow

### Path 1: Admin installs Shasta to their Slack workspace

```
1. Admin → /settings → Connectors tab → "Admin: Slack workspace" block
2. Click "Install Shasta to your Slack workspace"
   → POST /v1/connectors/connect/slack-workspace-bot
3. Lambda: _require_admin() → 403 if role != 'admin'
   pkce.generate_pair() + state_jwt.sign_state(provider="slack-bot", ...)
   → {authorize_url} pointing at slack.com/oauth/v2/authorize with bot scopes
4. Browser → Slack → "Allow"
5. Slack 302 → /v1/connectors/callback/slack-workspace-bot?code=...&state=...
6. Lambda: verify_state(token, expected_provider="slack-bot")
   pkce.fetch_verifier(nonce)  # one-shot atomic delete
   slack.oauth.v2.access(code) → bot token + team_id
7. encrypt_token() → INSERT INTO tenant_bot_connectors
   (autonomous_rule_enabled=true, broadcast_channel_id=NULL)
8. 302 → /settings?tab=connectors&ok=slack-bot
9. UI fires GET /v1/connectors/admin/slack/channels
   Lambda → mcp_oauth.get_admin_session → conversations.list
   → returns [{id, name, is_private}]
10. Admin picks #shasta-alerts → POST /v1/connectors/admin/slack/broadcast-channel
    UPDATE tenant_bot_connectors SET broadcast_channel_id=...
11. UI flips to "Installed · #shasta-alerts · [Autonomous broadcasts ON]"
```

### Path 2: Critical finding lands → broadcast fires

```
1. Scanner Lambda → unified_writer.write_finding(...)
2. unified_writer:
   - INSERT INTO findings (...) ON CONFLICT (...) DO UPDATE ... RETURNING
   - if severity='critical' AND status='fail':
       _fanout_critical(tenant_id, finding_id, scan_id, severity, status)
       → sqs.send_message(MessageBody=json.dumps({...}))
   - Hook errors are logged, never propagated to the writer caller
3. SQS → findings_subscriber Lambda (batch=1)
4. Subscriber:
   a. idempotency.seen?(sha256(tenant_id||finding_id||scan_id)) → ack if seen
   b. kill_switch.global_enabled?()  # SSM cached 60s; fail-open
   c. SELECT broadcast_channel_id, autonomous_rule_enabled
      FROM tenant_bot_connectors WHERE tenant_id=:t AND status='active'
      → silent ack if missing OR !enabled OR NULL channel
   d. SELECT ... FROM findings WHERE finding_id=:f
      (re-read; subscriber may lag writer by ms)
      → silent ack if missing
   e. async with mcp_oauth.get_admin_session(tenant_id, "slack") as session:
          session.call_tool("send_message", {
              channel: bot.broadcast_channel_id,
              blocks:  format_finding_card(finding),
          })
   f. idempotency.mark_seen(key, ttl_epoch=now+7d)
      # Conditional PutItem; if a parallel invoke marked first, no-op
   g. ack message
5. Unhandled exception → SQS retry up to 5x → DLQ
```

### Path 3: User clicks "View full details" in the Slack card

```
1. Card button URL = https://shasta.transilience.cloud/risks/{finding_id}
2. SPA route /risks/:finding_id
3. <DeepLinkGate>:
   - Signed in → render <Risks finding={id} />
   - Not signed in → navigate("/signin?after=/risks/" + id)
4. Cognito callback (existing) honors ?after= → post-auth lands on /risks/:id
```

## 7. Error handling

### Path 1 (admin install)

| Failure | Response | Recovery |
|---|---|---|
| `role != 'admin'` | 403 `{"error": "admin_required"}` | UI shows "Ask your admin to install Shasta" |
| State JWT expired (>5 min) | 400 `invalid_state` | Re-click Install; fresh JWT |
| Slack `oauth.v2.access` rejects | 400 with sanitized vendor error | Re-install with corrected scopes |
| UNIQUE conflict on `tenant_bot_connectors` | `ON CONFLICT (tenant_id, oauth_provider) DO UPDATE` | Re-install replaces row, no manual revoke needed |
| `conversations.list` fails post-install | UI toast, install row persists | Retry picker; or "Skip — pick later" leaves channel NULL |

### Path 2 (autonomous broadcast subscriber)

| Failure point | Behavior | Rationale |
|---|---|---|
| DDB idempotency GetItem fails | Re-raise → SQS retry → eventually DLQ | Double-send is worse than delay |
| SSM kill-switch read fails | Fail-open (treat as enabled) | Flaky SSM shouldn't silence alerts; per-tenant toggle is the authoritative kill |
| `tenant_bot_connectors` row missing / inactive | Silent ack | Uninstalled = expected state |
| `autonomous_rule_enabled = false` | Silent ack | Tenant opted out |
| `broadcast_channel_id IS NULL` | Silent ack | Pre-config state |
| `findings` row missing on re-read | Silent ack | Race; nothing to broadcast |
| `ConnectorMissingError` | Silent ack | Revoked between SQS publish + consume |
| `ConnectorRevokedError` (Slack 401) | UPDATE `tenant_bot_connectors.status='error'` + ack | Admin sees banner on next Settings visit |
| Slack 4xx (channel deleted, bot kicked) | UPDATE `status='error', last_error=...` + ack | Per-tenant DLQ would retry forever |
| Slack 5xx / transient | Re-raise → SQS retry up to 5x → DLQ | Transient heals; persistent goes to DLQ |
| Per-tenant burst > 30/min (module-memory counter) | `ChangeMessageVisibility(VisibilityTimeout=60)` on the receipt handle + ack-decline (re-raise to keep msg in flight); SQS re-delivers in 60s | Spread out, don't drop; SendMessage `DelaySeconds` is producer-side only, not usable for consumer back-pressure |
| `mark_seen` fails AFTER successful send | Log; do NOT re-raise | Duplicate seen-row is cheaper than double-broadcast |

### Path 3 (DeepLinkGate)

| Failure | Response |
|---|---|
| Signed in but no access to finding's tenant | `<Risks>` returns 404 (existing behavior) |
| Cognito session expired during navigation | DeepLinkGate redirects to `/signin?after=...` |
| `?after=` param malformed | Cognito callback regex-validates; falls back to `/` |

### Out-of-band drift detection

A CloudWatch `MetricFilter` on the unified_writer's log group counts
critical-fail inserts. Compared against
`SQS ApproximateNumberOfMessagesReceived` on the broadcast queue. Drift
> 2/hour fires a soft alarm — usually means a scanner Lambda's
`sqs:SendMessage` grant got missed in a deploy. Worth landing the metric in
this slice because the failure mode is silent (hook short-circuits when env
var is unset).

## 8. Block Kit template

Target 4-6 visual lines. Channel members already opted in; respect attention.

```
🚨 CRITICAL — Public S3 bucket with PII-tagged data
─────────────────────────────────────────────────────
Resource: arn:aws:s3:::acme-customer-exports
Scanner: AWS · Frameworks: PCI-DSS, CIS-AWS
Detected: 2 minutes ago

[ View full details and remediation → ]
```

```python
def format_finding_card(f: Finding) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"🚨 *CRITICAL — {escape(f.title)}*"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Resource:* `{escape(f.resource_arn)}`\n"
                    f"*Scanner:* {f.scanner} · *Frameworks:* {', '.join(f.frameworks_list)}\n"
                    f"*Detected:* <!date^{int(f.created_at.timestamp())}^"
                    f"{{date_short}} {{time}}|just now>"}},
        {"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "View full details and remediation"},
            "url": f"{WEB_BASE_URL}/risks/{f.finding_id}",
            "style": "primary",
        }]},
    ]
```

**Deliberately NOT in the card:**

- Full evidence (sensitive — leaks into channel)
- Authoritative remediation steps (canonical fix lives in the platform UI)
- `@mentions` (no spam — channel is opt-in, not paging)
- Batched findings (one finding = one message; clean threading)

**Escaping:** `escape()` everywhere user-controlled (titles, ARNs, framework
names). Slack mrkdwn special chars: `<`, `>`, `&` are replaced;
`\`, `_`, `*` are passed through (legal in mrkdwn). Resource ARNs that
contain `}` `{` `&` are tested in the golden suite.

## 9. Testing

### Unit tests (sync, fast, mocked AWS)

| Module | Coverage |
|---|---|
| `findings_subscriber/tests/test_idempotency.py` | DDB miss → not seen; hit with future TTL → seen; conditional PutItem race → second writer no-ops |
| `findings_subscriber/tests/test_kill_switch.py` | SSM false → skip; SSM true → proceed; SSM throws → fail-open; 60s cache hit doesn't re-call SSM |
| `findings_subscriber/tests/test_block_kit.py` | Golden test per finding shape (AWS/Azure/AI/SOC); ARNs with `}` `{` `&` escape; long titles truncate cleanly |
| `findings_subscriber/tests/test_main.py` | Happy path + every silent-ack branch + status='error' branches + DLQ-eligible exceptions + mark_seen failure |
| `connectors/tests/test_handlers_slack_workspace_bot.py` | Non-admin → 403; admin → 200 + bot scopes in URL; state JWT aud = `slack-bot-callback`; callback inserts `tenant_bot_connectors`; re-install ON CONFLICT replaces |
| `connectors/tests/test_handlers_admin_slack.py` | GET channels requires admin + active install; POST broadcast-channel validates channel_id against picker response (anti-tamper) |
| `_shared/tests/test_admin_session.py` | Resolves `tenant_bot_connectors` (not `user_connectors`); refresh uses `bot_id` as lock key; KMS envelope path shared with Slice 1 |
| `_shared/tests/test_unified_writer.py` | Critical+fail → hook called; non-critical or non-fail → not called; SQS failure → logged + write succeeds; missing env var → short-circuits |
| `web/src/components/__tests__/DeepLinkGate.test.tsx` | Signed-in → renders; no session → navigates to `/signin?after=...`; malformed `:finding_id` → 404 |
| `web/src/components/connectors/__tests__/ConnectorAdminBlock.test.tsx` | Non-admin → hidden; admin + no install → Install button; admin + install + no channel → ChannelPicker; full state → toggle + Disconnect |

### Integration tests (manual, against deployed dev)

Kept as a checklist in `TEST_PLAN.md`, run once per Slice 2 deploy:

1. **End-to-end broadcast** — manually INSERT a critical-fail finding into
   Aurora dev; verify Block Kit card lands in `#shasta-alerts` within 60s
2. **Idempotency** — INSERT same finding twice with different scan_ids →
   two broadcasts; same scan_id → one broadcast
3. **Global kill switch** — `aws ssm put-parameter
   /cisocopilot/autonomous_rule/enabled false`; INSERT critical → no
   broadcast; restore + INSERT → broadcast
4. **Per-tenant toggle** — flip `autonomous_rule_enabled=false`; INSERT →
   no broadcast (row persists in `findings`)
5. **DLQ path** — set `broadcast_channel_id` to a deleted channel; INSERT →
   DLQ accumulates after 5 retries; CloudWatch alarm fires

### Aggregate target

~30 new tests across `_shared/`, `connectors/`, `findings_subscriber/`,
`web/`. Existing 70 + 8 + 22 + 7 stays green.

## 10. Slicing within Slice 2

Slice 2 is itself decomposable. Five vertical sub-slices, each testable
end-to-end. **Each sub-slice can be a separate PR.**

### Sub-slice 2.1 — unified_writer consolidation

- Hoist `ai_scanner/unified_writer.py` and
  `shasta_runner_azure/app/unified_writer.py` into
  `_shared/unified_writer.py`
- Convert scanners to import the shared module
- No behavior change. Pure refactor — but tested end-to-end (each scanner
  still writes findings correctly)
- Lands the foundation for everything else

### Sub-slice 2.2 — admin Slack bot install flow

- `connectors/handlers_slack_workspace_bot.py` (initiate + callback)
- `_require_admin` helper
- State JWT audience extension (`slack-bot-callback`)
- Web: `ConnectorAdminBlock.tsx` install-only state
- Verified by: admin can install, row appears in `tenant_bot_connectors`,
  `bot_id` decryptable via KMS envelope

### Sub-slice 2.3 — channel picker + autonomous toggle

- `connectors/handlers_admin_slack.py` (channels list + set)
- `_shared/mcp_oauth/admin_session.py`
- Web: `ChannelPicker.tsx` modal + autonomous-toggle wiring
- Verified by: admin picks a channel, row's `broadcast_channel_id` set;
  toggle flips `autonomous_rule_enabled`

### Sub-slice 2.4 — broadcast plumbing (the autonomous rule)

- SQS queue + DLQ (CDK)
- DDB seen table (CDK)
- `findings_subscriber/` Lambda
- `_fanout_critical` hook in `_shared/unified_writer.py`
- Scanner IAM grants + env var
- Block Kit template
- Three-layer kill switch
- Verified by: integration checklist 1–5

### Sub-slice 2.5 — DeepLinkGate + production hardening

- `web/src/components/DeepLinkGate.tsx` wrapper
- CloudWatch DLQ alarm + drift metric
- Verified by: cold deep-link in Incognito bounces through `/signin` and
  lands on `/risks/:id`; CloudWatch alarms validated by integration test 5

## 11. Open questions (none load-bearing)

These can be settled during implementation without changing the spec:

- **Bot token rotation:** Slack rotates bot tokens like user tokens when
  `token_rotation_enabled=true`. The admin install honors this if the app
  manifest is configured. Verify in implementation that the Shasta Slack
  App's manifest has rotation enabled for bot scopes too (currently
  enabled for user scopes per Slice 1).
- **Drift metric implementation:** the spec says "MetricFilter on the
  unified_writer log group" — could also be done via an EMF metric emitted
  from the Lambda itself (more reliable, less log-pattern coupling).
  Implementer's call.
- **DLQ alarm threshold:** "ApproximateNumberOfMessagesVisible > 0 for 5
  minutes" — could be tighter (1 minute) or looser depending on operational
  preference. Default to 5 minutes; tune after seeing real noise.

## 12. References

- Slice 1 spec: `docs/superpowers/specs/2026-05-28-mcp-connectors-design.md`
- Slice 1 implementation handoff: `HANDOFF.md` § "MCP Connectors Slice 1"
- Slack OAuth v2 + bot scopes: https://api.slack.com/methods/oauth.v2.access
- Slack `conversations.list`: https://api.slack.com/methods/conversations.list
- Slack Block Kit: https://api.slack.com/block-kit
- SQS standard queue + DLQ:
  https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
