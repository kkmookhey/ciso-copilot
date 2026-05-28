# MCP Connectors — productivity tools for the agent surface

> Customer-facing Settings → Connectors page that lets each SOC analyst
> connect their own Slack, Atlassian (Jira), Google Workspace (Gmail), and
> Microsoft 365 accounts so the Shasta voice agent and the Risks-page "act"
> buttons can take action on their behalf using official remote MCP servers.
> Plus one hardcoded autonomous rule: post a structured card to the tenant's
> designated Slack channel whenever a CRITICAL finding lands.
>
> Architecture is MCP-only across all four vendors via their first-party
> remote MCP endpoints — no community servers, no per-vendor REST forks.
> M365 ships read-only (vendor's first-party MCP doesn't cover writes yet);
> we accept that constraint and document the upgrade path.
>
> Brainstorm date: 2026-05-28.

## 1. Goal and success criteria

A customer-facing connectors catalog where each analyst self-serves four
productivity-tool integrations, plus one autonomous broadcast rule the
admin enables once per tenant.

**Success criteria:**

1. A new SOC analyst lands on `/settings/connectors`, connects Slack via
   OAuth in under 30 seconds, and the voice agent's next response uses
   their Slack tools without restarting the session.
2. When the analyst says "DM John about this finding", the message
   appears in Slack as **from the analyst**, not from a shared bot
   identity.
3. When an admin installs the Shasta Slack app in their workspace and
   picks a broadcast channel, every CRITICAL finding fires a structured
   Block Kit card to that channel within ~60 seconds, with a button that
   deep-links into Shasta and survives the unauthenticated browser tab.
4. The voice agent's tool registry adapts dynamically: an analyst who
   connects Slack + Jira sees Slack + Jira tools; an analyst who only
   connects Slack sees only Slack tools. Tool definitions come from each
   vendor's MCP `list_tools()`, not hardcoded in Shasta.
5. Three layers of kill switch work: per-tenant toggle (instant), admin
   disconnect (instant), global SSM parameter (≤60s propagation).
6. M365 ships in the catalog with PREVIEW labeling and an honest
   read-only capability surface — no pretending we can send mail.

## 2. Why this design

- **MCP-only over direct REST** ([[project-integrations-mcp]] + 2026 MCP
  ecosystem research): three of four vendors have first-party remote MCP
  servers GA in 2026 (Slack since Feb, Atlassian since Feb, Google
  rolling out May). MCP Authorization spec shipped Nov 2025; OAuth 2.1 +
  PKCE flow is standardized across vendors. One Python SDK (`pip install
  mcp`), one OAuth flow shape, dynamic tool discovery — exactly the
  "future is MCP, not REST" thesis applied with the data behind it.
- **Per-user identity over shared bot identity**: every connector action
  is attributable to a human in the customer's vendor-side audit. Legal
  sees Jane's email, not "shasta-bot". Costs more storage (per-user
  token rows) but pays back in audit + accountability.
- **Outbound actions only, no evidence ingestion**: this sub-project
  expands the voice + web action surface. Inbound (read Slack threads
  as finding context, ingest Gmail security alerts as evidence) is
  deliberately deferred — it's a different data-flow shape and deserves
  its own brainstorm.
- **Hardcoded autonomous rule, no rule builder**: ship one well-designed
  autonomous side effect (CRITICAL → Slack broadcast). Defers the
  rule-builder UI (trigger conditions, action templates, multi-channel
  routing) to a follow-on "Agentic Workflows" sub-project that can
  inherit this slice's infra.
- **M365 read-only honestly**: first-party Microsoft Graph MCP is
  preview, covers read scopes only (no `Mail.Send`, no Teams DM). We
  use it as-is rather than fork the community `softeria/ms-365-mcp-server`
  to add writes — preserves the "vendor-official only" positioning.

## 3. Scope

### In scope

- Four connectors: Slack, Atlassian (Jira), Google Workspace (Gmail),
  Microsoft 365
- Per-user OAuth + token storage for all four (read+write where
  vendor MCP supports it; read-only for M365)
- Admin-installed Slack workspace bot for autonomous broadcasts
- Shared `mcp_client` Python package for Lambda
- `/v1/connectors/*` Lambda for OAuth orchestration
- `/settings/connectors` web page (one-time setup UX, role-gated admin block)
- Tools dispatcher extension for the namespaced `kind__tool` routing
- `voice_session` bootstrap extension for dynamic tool registry
- Web "act" buttons on Risks page (per-vendor, gated on connection state)
- `findings_subscriber` Lambda + SQS for the one autonomous rule
- `<DeepLinkGate>` wrapper component for `/risks/:finding_id`

### Out of scope (deferred)

- Customer-defined rule builder UI (next sub-project)
- Per-vendor action audit log table (CloudTrail + vendor-side audit is
  sufficient for v1)
- iOS lockscreen notification actions
- Evidence ingestion from connector data (read Slack threads as context,
  ingest Gmail alerts as evidence)
- Additional connectors beyond the initial four (Confluence, Notion,
  GitHub-as-connector, Linear, Teams writes via community server)
- Customer-editable Slack message template
- Multi-channel broadcast routing

## 4. Architecture overview

Five components. First three are net-new; last two extend wow-demo code.

```
                  ┌─────────────────────────────┐
                  │  web/src/routes/Settings/   │
                  │  ConnectorsPage.tsx (new)   │
                  └──────────────┬──────────────┘
                                 │ initiate / revoke
                  ┌──────────────▼──────────────┐
                  │  /v1/connectors/* Lambda    │   per-tenant×user×tool tokens,
                  │  • POST /connect/{kind}     │   KMS-encrypted bytea columns,
                  │  • GET  /callback/{kind}    │   ↓ stored in Aurora
                  │  • DELETE /{conn_id}        │
                  │  • GET  /me                 │   ┌──────────────────────────┐
                  └──────────────┬──────────────┘   │ Aurora: user_connectors  │
                                 │                  │ + tenant_bot_connectors  │
                                 ▼                  └──────────────────────────┘
                  ┌─────────────────────────────┐         ▲
                  │   mcp_client (Python pkg)   │─────────┘ lookup by
                  │   • get_session(subject,    │           subject_from_claims
                  │     tool_kind) -> Session   │
                  │   • Streamable HTTP +       │
                  │     OAuth-2.1 token mint    │
                  └──────┬───────────────┬──────┘
                         │               │
       ┌─────────────────▼───┐    ┌──────▼───────────────────┐
       │ tools/ Lambda       │    │ findings_subscriber       │
       │ (per-user actions)  │    │ Lambda (autonomous rule)  │
       │ - voice tools       │    │ - SQS-fed from            │
       │ - web 'act' buttons │    │   findings_writer         │
       └─────────────────────┘    │ - posts via admin bot     │
                                  └───────────────────────────┘
```

**Components:**

1. **`/v1/connectors/*` Lambda** — OAuth orchestration: `initiate`,
   `callback`, `revoke`, `list`, plus the admin-only Slack workspace
   install. One handler dispatching per-`kind`. Stores tokens in Aurora
   `bytea` columns encrypted with `pgcrypto` + a KMS-derived data key
   cached in Lambda memory.
2. **Aurora schema additions** — `user_connectors` (per analyst, per
   tool) + `tenant_bot_connectors` (admin-installed workspace bot, just
   Slack for v1).
3. **`mcp_client` shared Python package** — thin wrapper over `pip install mcp`
   + per-vendor OAuth refresh. Gives the tools layer one call:
   `get_session(subject_from_claims, "slack") -> MCPClientSession`.
   Lives in `platform/lambda/_shared/mcp_client/`, bundled into multiple
   Lambda images.
4. **`tools/` Lambda (extends wow-demo's tools dispatcher)** — drops the
   shared `SLACK_BOT_TOKEN`/`JIRA_URL`/etc. env vars. Each tool call
   resolves via `mcp_client.get_session()`. Same dispatcher contract for
   voice + web buttons.
5. **`findings_subscriber` Lambda (new)** — SQS-fed from `findings_writer`
   whenever a CRITICAL finding lands. Looks up the tenant's admin Slack
   bot, posts the Block Kit card.

**Adding a fifth connector later = a `providers/{kind}.py` config file
+ OAuth provider registration. No new tool code.**

## 5. Data model

Two new Aurora tables. Schema written against the gotchas in CLAUDE.md
(UUID PKs via `gen_random_uuid()`, no JSON arrays where text arrays fit,
FK to `tenants(tenant_id)` and `users(user_id)`).

### `user_connectors` — per-analyst, per-tool tokens

```sql
CREATE TABLE user_connectors (
  conn_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id              UUID NOT NULL REFERENCES users(user_id),
  oauth_provider       TEXT NOT NULL,        -- 'slack'|'atlassian'|'google'|'microsoft'
  mcp_server_url       TEXT NOT NULL,        -- resource URL the token was minted against
  vendor_user_id       TEXT NOT NULL,        -- Slack user_id, Google email, Atlassian accountId, Entra oid
  vendor_workspace_id  TEXT,                 -- Slack team_id, Atlassian cloud_id, Entra tenant_id; NULL for Google
  access_token_enc     BYTEA NOT NULL,       -- pgp_sym_encrypt with KMS-derived key
  refresh_token_enc    BYTEA NOT NULL,
  access_expires_at    TIMESTAMPTZ NOT NULL,
  scopes               TEXT[] NOT NULL,
  status               TEXT NOT NULL DEFAULT 'active',  -- 'active'|'revoked'|'expired'|'error'
  last_error           TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at         TIMESTAMPTZ,
  revoked_at           TIMESTAMPTZ,
  UNIQUE (tenant_id, user_id, oauth_provider)
);
CREATE INDEX ix_user_connectors_lookup
  ON user_connectors (tenant_id, user_id, oauth_provider) WHERE status = 'active';
CREATE INDEX ix_user_connectors_refresh
  ON user_connectors (access_expires_at) WHERE status = 'active';
```

### `tenant_bot_connectors` — admin-installed workspace bots

```sql
CREATE TABLE tenant_bot_connectors (
  bot_id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                    UUID NOT NULL REFERENCES tenants(tenant_id),
  oauth_provider               TEXT NOT NULL,    -- 'slack' for v1; designed for expansion
  mcp_server_url               TEXT NOT NULL,
  vendor_workspace_id          TEXT NOT NULL,    -- Slack team_id
  access_token_enc             BYTEA NOT NULL,
  refresh_token_enc            BYTEA,            -- nullable; some bot installs don't return one
  access_expires_at            TIMESTAMPTZ,
  scopes                       TEXT[] NOT NULL,
  broadcast_channel_id         TEXT,             -- the configured channel for autonomous posts
  broadcast_channel_name       TEXT,             -- for display only
  autonomous_rule_enabled      BOOLEAN NOT NULL DEFAULT true,
  installed_by_user_id         UUID NOT NULL REFERENCES users(user_id),
  status                       TEXT NOT NULL DEFAULT 'active',
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at                 TIMESTAMPTZ,
  revoked_at                   TIMESTAMPTZ,
  UNIQUE (tenant_id, oauth_provider)
);
```

### Token encryption

KMS-derived column-level encryption using `pgcrypto`'s `pgp_sym_encrypt`.
One KMS CMK per environment (`alias/cisocopilot-connector-tokens`).
Lambda calls `kms:GenerateDataKey` once per cold start, caches the
plaintext data key in module-level memory, uses it to encrypt/decrypt
token bytes. Cheaper than Secrets-Manager-per-row at scale; reads stay
sub-millisecond.

### Subject resolution

Runtime lookup is `subject_from_claims(jwt) → users.user_id →
user_connectors WHERE tenant_id+user_id+oauth_provider`. Reuses the
existing canonical helper (`voice_session._subject_from_claims`) that
handles federated logins per CLAUDE.md.

### What's deliberately NOT in the schema

- No `connector_action_audit` table. CloudTrail + per-vendor audit
  (Slack message history, Jira issue history, Gmail sent folder)
  covers v1. Add a dedicated table when the rule builder ships.
- No "approved actions" allowlist. Voice command and web button click
  ARE the authorization. Destructive actions (closing Jira tickets)
  get a UI confirm dialog client-side.
- No per-user OAuth state table. The OAuth `state` parameter is a
  signed JWT (`HS256`, 5-min expiry) so the callback handler validates
  without a DB roundtrip.

## 6. OAuth flow

One end-to-end shape, four per-vendor providers.

### Initiate → callback → store

```
Browser                /v1/connectors           Vendor AS (Slack/Atlassian/Google/Entra)
   │                          │                            │
   ├─ POST /connect/slack ───▶│                            │
   │                          │ 1. Discover MCP server's   │
   │                          │    .well-known/oauth-      │
   │                          │    protected-resource      │
   │                          │ 2. Build state JWT:        │
   │                          │    {tenant_id, user_id,    │
   │                          │     provider, nonce,       │
   │                          │     pkce_verifier_hash}    │
   │                          │ 3. Store PKCE verifier     │
   │                          │    in DynamoDB (TTL 5min)  │
   │                          │ 4. Build authorize URL +   │
   │                          │    PKCE challenge          │
   │◀─ 200 {authorize_url} ───┤                            │
   ├─ window.location ────────────────────────────────────▶│
   │                          │              user consents │
   │◀────────────────────────────── redirect_uri?code=… ──┤
   │                          │                            │
   ├─ GET /callback/slack? ──▶│                            │
   │  code=…&state=…          │ 5. Verify state JWT        │
   │                          │ 6. Look up PKCE verifier   │
   │                          │ 7. POST token endpoint     │
   │                          │    with code + verifier ──▶│
   │                          │◀──── {access, refresh} ────┤
   │                          │ 8. KMS-encrypt + INSERT    │
   │                          │    user_connectors         │
   │◀─ 302 /settings/         ┤                            │
   │   connectors?ok=slack    │                            │
```

### State parameter

Signed JWT, `HS256` with a per-environment secret in SSM
(`/cisocopilot/connectors/state-jwt-secret`), 5-minute expiry:

```json
{ "tenant_id": "...", "user_id": "...", "provider": "slack",
  "pkce_verifier_hash": "sha256(...)", "nonce": "...", "exp": ... }
```

The PKCE *verifier* itself lives in DynamoDB (table:
`connector_pkce_verifiers`, TTL 5 min, partition key = `nonce`). State
JWT carries only the hash so the callback validates that the stored
verifier matches what was used to build the challenge.

### Per-vendor specifics

The only places they diverge:

| Vendor | Authorize URL | Extra step after callback | Refresh model |
|---|---|---|---|
| **Slack** | `slack.com/oauth/v2/authorize` | Read `team_id` + `authed_user.id` from token response → `vendor_workspace_id`/`vendor_user_id`. No additional API call. | Slack rotates tokens (`token_rotation_enabled=true` on app); refresh every ~12h. |
| **Atlassian** | `auth.atlassian.com/authorize?audience=api.atlassian.com` | After token exchange, GET `api.atlassian.com/oauth/token/accessible-resources` → pick cloud ID. If response has >1, show picker dialog. | Refresh tokens are 1-time-use rotating; we replace `refresh_token_enc` on every refresh. |
| **Google** | `accounts.google.com/o/oauth2/v2/auth?access_type=offline&prompt=consent` | `access_type=offline` is REQUIRED to get a refresh token. Without `prompt=consent` Google won't re-issue one on re-auth. Store user email as `vendor_user_id`. | Standard OAuth 2.1 refresh. Refresh tokens don't rotate. |
| **Microsoft** | `login.microsoftonline.com/common/oauth2/v2.0/authorize` | Multi-tenant app registration (`common` endpoint). Read `tid` from ID token → `vendor_workspace_id`. Read `oid` → `vendor_user_id`. Scopes: `Mail.Read offline_access User.Read`. | Standard refresh. |

### Slack admin bot-install (separate flow)

Distinct route: `POST /connect/slack-workspace-bot`, role-gated
(`users.role = 'admin'`). Different scopes (`chat:write`, `channels:read`,
`groups:read`), grants a *bot token* not a user token. After install,
a second step calls Slack `conversations.list` via the bot token to
populate a channel picker; admin selects the broadcast channel which
populates `tenant_bot_connectors.broadcast_channel_id`.

### Refresh strategy

**Just-in-time, not background**. `mcp_client.get_session()` checks
`access_expires_at - now() < 60s` and refreshes inline before opening
the MCP session. Simpler than a background EventBridge cron, no race
on revoked-during-refresh. Cost: ~50-100ms added to the first call
after expiry.

### Revoke

Two paths:

1. **User-initiated** (DELETE `/v1/connectors/{conn_id}`): call vendor's
   revoke endpoint (Slack `auth.revoke`, Google `oauth2/revoke`, etc.),
   then `UPDATE user_connectors SET status='revoked', revoked_at=now()`.
   If vendor revoke fails (network), we still mark row revoked locally
   — vendor token will rot on its own.
2. **Token-error-on-use** (the helper catches 401/`invalid_grant` from
   MCP): same UPDATE, status `'error'`, `last_error` populated. Next
   time the user visits Settings they see "Reconnect Slack" instead of
   "Connected".

## 7. Runtime execution path

**Tool discovery is dynamic** (the MCP server tells us its tool list at
session start; we don't hardcode). **Voice agent's tool registry is
session-scoped** — built fresh on each session bootstrap from the
analyst's currently-connected vendors.

### Shared package API

```python
# platform/lambda/_shared/mcp_client/__init__.py
from contextlib import asynccontextmanager
from typing import Literal

ProviderKind = Literal["slack", "atlassian", "google", "microsoft"]

@asynccontextmanager
async def get_session(subject: str, kind: ProviderKind, *, tenant_id: str):
    """Open MCP session for THIS user against THIS provider.
    Refreshes access token JIT; raises ConnectorMissingError /
    ConnectorRevokedError on lookup failure."""

@asynccontextmanager
async def get_admin_session(tenant_id: str, kind: Literal["slack"]):
    """Open MCP session using tenant's admin-installed bot token
    (autonomous broadcast path)."""

async def discover_tools(subject: str, *, tenant_id: str) -> dict[ProviderKind, list[Tool]]:
    """For each provider the user has connected, return its tool manifest.
    Lambda module-memory cache, 5-min TTL keyed by
    (vendor_workspace_id, scopes_hash)."""
```

### Call site 1 — voice agent

`voice_session` Lambda's existing session bootstrap gains one step:

```python
# voice_session/main.py — session bootstrap (added lines)
subject  = _subject_from_claims(jwt)
tenant   = _resolve_tenant_id(jwt)

connected = await mcp_client.discover_tools(subject, tenant_id=tenant)
# {"slack": [Tool(name="send_message", …)], "atlassian": [Tool(name="create_issue", …)]}

openai_tools = []
for kind, tools in connected.items():
    for t in tools:
        openai_tools.append({
            "type": "function",
            "name": f"{kind}__{t.name}",        # namespace prefix avoids vendor collisions
            "description": t.description,
            "parameters": t.input_schema,
        })
openai_tools.extend(SHASTA_NATIVE_TOOLS)  # run_forensic_scan, etc. unchanged
session_config = {"tools": openai_tools, ...}
```

Dispatcher decodes the namespace prefix:

```python
# tools/main.py — added branch
kind, tool_name = name.split("__", 1) if "__" in name else (None, name)
if kind in PROVIDER_KINDS:
    async with mcp_client.get_session(subject, kind, tenant_id=tenant) as s:
        return await s.call_tool(tool_name, args)
# else: existing Shasta-native tool handler (run_forensic_scan, etc.)
```

System prompt gets a one-liner per connected vendor, generated at
bootstrap from MCP `list_resources` calls:
`"You can act in this user's Slack workspace (transilience.slack.com)
and Jira site (shasta-demo.atlassian.net)."`

### Call site 2 — web "act" buttons

Findings page renders all action buttons regardless of connection state.
Connection check happens client-side at render via `GET /v1/connectors/me`:

```typescript
// web/src/components/findings/ActButtons.tsx
const { connectors } = useConnectors();  // SWR-cached
const slackConnected = connectors.some(
  c => c.provider === "slack" && c.status === "active"
);

<Button
  disabled={!slackConnected}
  tooltip={slackConnected
    ? "DM resource owner"
    : "Connect Slack in Settings to use this"}
  onClick={() => api.callTool("slack__send_message", {
    channel: owner.slack_id, text: dmText
  })}
/>
```

Server-side path is identical to voice — same `tools/` Lambda, same
dispatcher, same `mcp_client.get_session()`.

### Call site 3 — autonomous rule (`findings_subscriber`)

```python
# findings_subscriber/main.py — fires on CRITICAL findings from SQS
async def handle(event):
    for record in event["Records"]:
        body = json.loads(record["body"])
        # 1. Idempotency check
        if seen_finding(body["finding_id"]):
            continue
        # 2. Global kill switch
        if not _global_enabled():  # SSM, cached 60s
            continue
        # 3. Per-tenant install + toggle
        bot = lookup_tenant_bot(body["tenant_id"], "slack")
        if not bot or not bot.autonomous_rule_enabled or not bot.broadcast_channel_id:
            continue
        # 4. Re-read finding (subscriber may lag writer)
        finding = load_finding(body["finding_id"])
        # 5. Send
        async with mcp_client.get_admin_session(body["tenant_id"], "slack") as s:
            await s.call_tool("slack_send_message", {
                "channel": bot.broadcast_channel_id,
                "blocks": format_finding_card(finding),
            })
        mark_seen(body["finding_id"])
```

### Error taxonomy (consistent across all three call sites)

| Error | Voice agent response | Web button response | Autonomous behavior |
|---|---|---|---|
| `ConnectorMissingError` | "Looks like you haven't connected Slack — set it up in Settings and we can do that." | Button disabled with tooltip | Skip silently (no bot installed) |
| `ConnectorRevokedError` | "Your Slack connection expired — reconnect in Settings." | Button re-enables after API returns; toast on Settings link | Mark `tenant_bot_connectors.status='error'`; admin sees banner next visit |
| MCP server 4xx/5xx | Pass through error message to agent; agent paraphrases | Toast with vendor error | Retry once with backoff, then drop + CloudWatch metric |

### Tool discovery caching

`discover_tools` keeps a Lambda module-level dict keyed by
`(vendor_workspace_id, scopes_hash)` → tool list, 5-min TTL. Cold
start: 1 round-trip per connected vendor in parallel via
`asyncio.gather`. Warm start: cache hit. Worst case (4 vendors connected,
cold start): ~300-600ms added to session init.

## 8. Web UI

`/settings` becomes a tabbed top-level page consolidating one-time
setup. Tabs: Profile / Cloud connections / **Connectors** / Team / Billing.
`/connect-clouds` moves into the Cloud connections tab.

### Connectors page layout

- **Catalog grid** — 2-column grid of vendor cards. Per card: logo →
  vendor name + small ⓘ tooltip with the MCP endpoint URL → capability
  chips → connection state (green dot + identity / gray dot + "Not
  connected") → primary action button (Connect / Disconnect).
- **M365 card** displays a PREVIEW badge and an explicit "read-only
  today; we'll add write actions when Microsoft ships them" note.
- **Admin block** at the bottom of the same page, role-gated to
  `users.role='admin'`. Shows the workspace Slack install state, the
  broadcast channel picker, and the autonomous-rule on/off toggle.
  Non-admins don't see the block.
- **State indicators** match `ConnectClouds.tsx`: green = active, gray
  = not connected, yellow = expired (reconnect needed).
- **Deep-link wrapper** — new `<DeepLinkGate>` component wrapping the
  `/risks/:finding_id` route handler. On no-session, redirects to
  `/signin?after=/risks/{id}`; the existing Cognito callback honors the
  `after` param and bounces back. Same pattern iOS uses via
  `IncidentRouter`.

### Files

```
web/src/routes/Settings/
  Settings.tsx                # tabbed shell
  ProfileTab.tsx              # placeholder; not built in this sub-project
  CloudConnectionsTab.tsx     # moves from web/src/routes/ConnectClouds.tsx
  ConnectorsTab.tsx           # catalog grid (this sub-project)
  TeamTab.tsx                 # placeholder
  BillingTab.tsx              # placeholder
web/src/components/connectors/
  ConnectorCard.tsx           # per-vendor card
  ConnectorAdminBlock.tsx     # admin-only workspace install + autonomous rule
  ChannelPicker.tsx           # Slack channel picker dialog
  CloudIdPicker.tsx           # Atlassian multi-site picker dialog
web/src/components/DeepLinkGate.tsx  # auth bounce wrapper
web/src/components/findings/
  ActButtons.tsx              # per-vendor act buttons on Risks page (extended)
web/src/lib/
  connectors.ts               # API client, useConnectors() SWR hook
```

### Catalog wireframe

The mockup approved during brainstorm is described in full detail in
this spec section. The transient HTML reference lives under
`.superpowers/brainstorm/.../connectors-page-v2.html` (gitignored, may
be cleaned up). Implementation may polish styling, but the locked
layout decisions are: 2-col card grid with the admin block at bottom
of the same page; ⓘ tooltip for the MCP endpoint URL on each card
(not visible inline); PREVIEW badge with explicit read-only note for
M365; state indicators matching `ConnectClouds.tsx` (green = active,
gray = not connected, yellow = expired).

## 9. Autonomous broadcast rule

The one hardcoded rule: **post a Block Kit card to the tenant's
designated Slack channel whenever a CRITICAL finding lands**.

### Trigger plumbing

```
findings_writer Lambda ──INSERT findings row──▶ Aurora
        │
        │ if row.severity == 'critical' AND row.status == 'fail':
        ▼
   SQS standard queue (autonomous_broadcast_queue)
        │  message body: { finding_id, tenant_id }
        ▼
findings_subscriber Lambda
   ├─ idempotency check (DynamoDB connector_broadcast_seen, 7d TTL)
   ├─ kill-switch checks (SSM global, then per-tenant toggle)
   ├─ load tenant_bot_connectors row
   ├─ re-read finding from Aurora
   └─ mcp_client.get_admin_session(tenant_id, 'slack')
       → call_tool('slack_send_message', { channel, blocks })
```

SQS standard queue (not direct invoke) for at-least-once delivery,
retry with backoff, and DLQ. `findings_writer` stays simple — fire-and-
forget `SendMessage`.

### Message format (Slack Block Kit)

Target 4-6 visual lines. Channel members already opted in; respect
their attention.

```
🚨 CRITICAL — Public S3 bucket with PII-tagged data
─────────────────────────────────────────────────
Bucket: arn:aws:s3:::acme-customer-exports
Scanner: AWS  •  Frameworks: PCI-DSS, CIS-AWS
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
                    f"*Scanner:* {f.scanner}  •  *Frameworks:* {', '.join(f.frameworks_list)}\n"
                    f"*Detected:* <!date^{int(f.created_at.timestamp())}^"
                    f"{{date_short}} {{time}}|just now>"}},
        {"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text",
                     "text": "View full details and remediation"},
            "url": f"{WEB_BASE_URL}/risks/{f.finding_id}",
            "style": "primary",
        }]},
    ]
```

**Deliberately NOT in the message:**

- Full evidence (sensitive — leaks into channel)
- Authoritative remediation steps (canonical fix lives in the platform
  UI; channel post links there)
- @mentions (no spam — channel is opt-in, not paging)
- Batched findings (one finding = one message; clean for threading)

### Throttling

- **Per-tenant burst cap**: subscriber tracks
  `messages_sent_this_minute[tenant_id]` in Lambda module memory. If
  > 30 in the last 60s, push back to SQS with `DelaySeconds=60`.
- **Slack rate limit**: official is ~1 msg/sec/channel. Lambda
  concurrency cap = 5 to stay under.
- **DLQ**: after 5 SQS receive failures, `autonomous_broadcast_dlq`.
  CloudWatch alarm on `ApproximateNumberOfMessagesVisible > 0`.

### Kill switches (three layers)

1. **Per-tenant admin toggle** (`autonomous_rule_enabled = false` on
   `tenant_bot_connectors`) — instant.
2. **Per-tenant admin disconnect** (status='revoked') — instant.
3. **Global SSM parameter** `/cisocopilot/autonomous_rule/enabled =
   false` — kills the rule for ALL tenants. Subscriber checks with 60s
   cache; worst-case 60s to propagate. Used in emergency (e.g., we
   discover the template is leaking sensitive data).

## 10. Slicing and sequencing

Five vertical slices. Each crosses DB + Lambda + Web (and SQS in
slice 2). No horizontal phases.

### Slice 1 — Infrastructure + Slack per-user

**Goal: prove the entire pattern end-to-end with one vendor.**

- DB: `user_connectors`, `tenant_bot_connectors` (with
  `autonomous_rule_enabled`) tables + KMS key + `pgcrypto` helpers
- Shared package: `platform/lambda/_shared/mcp_client/` — base
  `get_session()`, `providers/slack.py`, DynamoDB PKCE-verifier store
- Lambda: `/v1/connectors/*` Lambda — `initiate/callback/revoke/list`
  with Slack OAuth flow
- Lambda extension: `tools/main.py` dispatcher gains the `kind__tool`
  MCP path; `voice_session` bootstrap gains dynamic tool discovery
- Web: `/settings` shell with tabbed nav. Connectors tab renders Slack
  card live; other vendors as "Coming soon" placeholders
- Web: Slack "act" buttons on Risks page finding cards

The hard parts (OAuth, KMS, MCP client, dynamic discovery, namespace
prefix routing) all land here. Slices 3-5 become mostly provider config.

### Slice 2 — Admin Slack bot + autonomous broadcast rule

**Goal: prove the autonomous surface end-to-end.**

- Lambda: `/v1/connectors/slack-workspace-bot/{install|callback|revoke}`
  with bot scopes; channel-picker endpoint (`conversations.list`)
- Lambda: `findings_subscriber/` — SQS consumer, idempotency via
  DynamoDB, three-layer kill switch, Slack Block Kit template, DLQ +
  CloudWatch alarms
- Lambda extension: `findings_writer/` gains the SQS fan-out for
  CRITICAL/fail rows
- Web: admin block on Connectors page (install / channel picker /
  on-off toggle / disconnect)
- Web: `<DeepLinkGate>` wrapper for `/risks/:finding_id`

### Slice 3 — Atlassian (Jira)

**Pattern-driven; provider config only.**

- `providers/atlassian.py` — `auth.atlassian.com` endpoints,
  `accessible-resources` cloud-ID picker
- Web: cloud-picker dialog if user authorized >1 site
- Catalog card live; voice agent picks up Jira tools automatically via
  `discover_tools()`
- Jira "act" buttons on Risks page (Create issue, comment, transition)

### Slice 4 — Google Workspace (Gmail)

**Pattern-driven; provider config with two specific gotchas.**

- `providers/google.py` — `access_type=offline` + `prompt=consent` for
  refresh token (skipping these breaks refresh on re-auth)
- Custom OAuth client setup documented in
  `docs/connectors/google-oauth-setup.md` for self-hosted customers
- Catalog card live; Gmail "act" buttons on Risks page (draft to owner,
  search inbox)

### Slice 5 — Microsoft 365 (read-only preview)

**Pattern-driven; capability surface deliberately constrained.**

- `providers/microsoft.py` — multi-tenant Entra app, `common`
  endpoint, scopes `Mail.Read offline_access User.Read`
- Catalog card with PREVIEW badge + read-only note (mockup-locked)
- `voice_session` system prompt addendum: "Microsoft 365 is read-only
  — you can search Outlook and Teams, but cannot send mail or DM. Tell
  the user to use a different channel for outbound."
- No "act" buttons (no write actions to surface)

### Slicing principles

- **Vertical**: each slice crosses DB + Lambda + Web. No horizontal
  phases.
- **Slice 1 carries the load-bearing work**; 3-5 are stamped from the
  same template.
- **Slice 2 is independent from slices 3-5** — they could ship in any
  order. Recommended order pairs slice 1 + 2 as the "Slack story" so
  the next demo can show both per-user actions + autonomous broadcast,
  then layer the other vendors.

### Estimate

Roughly 3-5 days per slice → ~3-4 weeks total. Slice 1 + 2 is the
load-bearing pair; slices 3-5 should accelerate.

## 11. Testing strategy

### Unit tests

- `mcp_client` package: PKCE generation, state JWT signing/verification,
  token encryption round-trip, refresh logic boundary conditions.
- `findings_subscriber`: idempotency dedup, kill-switch precedence,
  throttling counter.
- `format_finding_card`: Block Kit shape against fixture findings.

### Integration tests (per provider)

- Auth flow happy path against a vendor test workspace
  (Slack: `transilience-test.slack.com`; Atlassian: a free test site;
  Google: a personal test account; Microsoft: a test tenant).
- Token refresh after forced expiry.
- Revoke + re-auth cycle.
- MCP `list_tools()` returns expected tool names.

### E2E tests (slice 1 + 2)

- New user connects Slack via OAuth (Playwright against a Slack test
  workspace).
- Voice agent calls `slack__send_message` and verifies message lands.
- Admin installs workspace bot, picks channel, fires a synthetic
  CRITICAL finding, verifies broadcast lands in channel.
- Click broadcast button from Slack on fresh browser tab → bounces
  through Cognito → lands on `/risks/{finding_id}`.
- Kill switch propagation: flip SSM, fire CRITICAL, verify no message.

### Manual smoke tests (per slice)

- Connect, disconnect, reconnect for each vendor in the catalog.
- Voice agent tool registry reflects per-analyst connection state.
- "Act" buttons disabled on findings when relevant vendor not connected;
  enabled and functional when connected.

## 12. Open questions and risks

### Open questions

- **Bot user identity on Slack autonomous posts**: should the broadcast
  card author be a custom display name (`@shasta`) or the literal app
  name from the Slack manifest? Likely manifest default; revisit if
  customers want vanity branding.
- **Cross-tenant token leak surface**: Lambda module-memory cache of
  KMS-derived data key — single-tenant Lambda invocation is per-tenant
  safe, but if we ever co-tenant invocations, the cache must key on
  tenant. Confirm Lambda execution context isolation guarantees before
  caching plaintext data keys.

### Risks

- **Google Workspace MCP rolling out May 2026** — vendor's general
  availability might lag our slice 4 ship. Mitigation: have a "service
  not yet GA" empty-state on the Gmail card if MCP discovery returns
  no tools; slice 4 still ships the OAuth and storage; tools light up
  when vendor catches up.
- **Slack token rotation timing**: 12h rotation could cause unexpected
  401s for long-running voice sessions. Mitigation: refresh on every
  `get_session()` if `access_expires_at - now() < 60s`; also catch 401
  inside MCP session and retry once with a forced refresh.
- **OpenAI Realtime tool registry size limits**: if 4 vendors × 6-8
  tools each = 24-32 functions in the session config, may bump against
  the Realtime API's tool list limit. Mitigation: confirm current
  limit; if hit, expose a per-vendor subset (top 3 tools each by usage)
  rather than the full surface.
- **MCP-Auth spec is still in late-stage drafts**: future spec changes
  could break early implementations. Mitigation: pin to `mcp` SDK
  version that matches the spec rev each vendor's server implements
  (currently `2025-11-25`); plan to revisit in 6 months.
- **Concurrent token refresh race**: two Lambda invocations for the
  same user could both detect "expires in <60s" and both call the
  vendor's refresh endpoint. For rotating refresh tokens (Slack,
  Atlassian) this can invalidate the loser's new token, causing the
  next call to 401. Mitigation: wrap the refresh + UPDATE in a Postgres
  advisory lock keyed by `hashtext(conn_id::text)` so only one
  invocation refreshes at a time; the other waits and re-reads the
  fresh access token. Add to `mcp_client.get_session()`.

## 13. Cross-references

- Builds on [[project-wow-demo-shipped]] — replaces wow-demo's shared
  Slack/Jira tokens with per-user OAuth
- Touches [[project-settings-page-consolidation]] — drives the
  `/settings` tabbed shell
- Sets up [[project-mcp-connectors-next]] — next sub-project after this
  is the rule builder (extends `tenant_bot_connectors` schema with rule
  definitions, swaps hardcoded autonomous rule for user-defined rules)
- Respects [[project-integrations-mcp]] — and reframes the rule with
  vendor research: MCP for 3 vendors today, M365 read-only via
  first-party MCP, no community forks
- Honors [[feedback-momentum-style]] — five sliced PRs over one
  mega-PR; each ships value
