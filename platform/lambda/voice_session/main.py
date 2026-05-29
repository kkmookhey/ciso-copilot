"""POST /voice/session — mints an OpenAI Realtime ephemeral session token.

JWT-authed. The iOS client uses the returned client_secret to open a
WebSocket directly to OpenAI; our backend is not on the audio path.

Session is configured with:
  - System prompt parametrized with user email, tenant name, connected clouds
  - server_vad turn detection (OpenAI auto-detects pauses)
  - Tool definitions for the model to call via the client
"""
from __future__ import annotations

import asyncio as _asyncio
import json
import os
import urllib.request
import urllib.error

import boto3

from system_prompt import render as render_system_prompt

DB_CLUSTER_ARN     = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN      = os.environ["DB_SECRET_ARN"]
DB_NAME            = os.environ["DB_NAME"]
OPENAI_SECRET_NAME = os.environ["OPENAI_SECRET_NAME"]

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")

# Module-level cache (Lambda container reuse).
_openai_key: str | None = None


def handler(event: dict, context) -> dict:
    user_email, tenant_id, tenant_name = _resolve_user_context(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})
    # Per-user MCP tool discovery — namespaced names live alongside the
    # native Realtime tools so the iOS client's tool-call dispatcher can
    # route them via POST /v1/tools/{kind}__{tool_name}.
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    subject = _subject_from_claims(claims) or ""

    key = _openai_api_key()
    if not key:
        return _resp(503, {
            "error":   "openai_not_configured",
            "message": "OpenAI API key missing — run: aws secretsmanager put-secret-value "
                       "--secret-id ciso-copilot/openai-api-key --secret-string '{\"api_key\":\"<KEY>\"}'",
        })

    connected = _list_clouds(tenant_id)

    # OpenAI Realtime GA shape (the Beta `/v1/realtime/sessions` endpoint was
    # retired). Now: POST /v1/realtime/client_secrets with the session config
    # nested under "session". Response carries the ephemeral key in "value".
    payload = {
        "session": {
            "type":              "realtime",
            "model":             "gpt-realtime",
            "instructions":      render_system_prompt(
                first_name=_first_name_from_email(user_email),
                clouds=connected,
            ),
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format":         {"type": "audio/pcm", "rate": 24000},
                    "transcription":  {"model": "whisper-1"},
                    "turn_detection": {
                        "type":                "server_vad",
                        "threshold":           0.5,
                        "prefix_padding_ms":   300,
                        "silence_duration_ms": 500,
                        "create_response":     True,
                        # Don't let the model interrupt itself on echo from
                        # the iPhone speaker — iOS AEC isn't strong enough
                        # at speakerphone volume.
                        # When the user speaks while Shasta is mid-response,
                        # let OpenAI cancel the stale response automatically.
                        # Without this we hit "active response in progress"
                        # 400s on every user interrupt.
                        "interrupt_response":  True,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice":  "coral",
                },
            },
            "tools":       _build_openai_tools_sync(subject=subject, tenant_id=tenant_id),
            "tool_choice": "auto",
            # OpenAI Realtime GA (2026) removed session.temperature — tone
            # is now controlled via voice + system prompt only.
        },
    }

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/realtime/client_secrets",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        print(f"OpenAI session mint failed: {e.code} {detail}")
        return _resp(502, {"error": "openai_failed", "status": e.code, "detail": detail})
    except Exception as e:
        print(f"OpenAI session mint exception: {e}")
        return _resp(502, {"error": "openai_failed", "detail": str(e)[:200]})

    session = body.get("session") or {}
    return _resp(200, {
        "session_id":    session.get("id"),
        "client_secret": body.get("value"),       # ephemeral key, prefix "ek_"
        "expires_at":    body.get("expires_at"),
        "model":         session.get("model"),
    })


# ============================================================================
# Tool definitions (client executes these — they call our HTTP API)
# ============================================================================

def _tools() -> list[dict]:
    return [
        {
            "type":        "function",
            "name":        "get_top_risks",
            "description": "Get the top open security findings for the user's tenant, sorted by severity. Use for 'what are my biggest risks' style questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":    {"type": "integer", "description": "Max findings to return (default 5)."},
                    "severity": {"type": "string",  "description": "Comma-separated severity filter, e.g. 'critical,high'. Default 'critical,high'."},
                    "cloud":    {"type": "string",  "description": "Optional single-cloud filter: aws | azure | gcp | entra."},
                },
            },
        },
        {
            "type":        "function",
            "name":        "list_connected_clouds",
            "description": "List the cloud accounts/subscriptions/tenants/projects connected to CISO Copilot for this user's tenant.",
            "parameters":  {"type": "object", "properties": {}},
        },
        {
            "type":        "function",
            "name":        "get_compliance_summary",
            "description": "Get the compliance score per framework (SOC 2, CIS AWS, CIS Azure, CIS GCP, MCSB, ISO 27001, HIPAA). Use for 'how are we doing on SOC 2' style questions.",
            "parameters":  {"type": "object", "properties": {}},
        },
        {
            "type":        "function",
            "name":        "list_recent_alerts",
            "description": "List the most recent real-time alerts (GuardDuty, Security Hub, etc.) and drift events for the user's tenant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":    {"type": "integer", "description": "Max events to return (default 5)."},
                    "severity": {"type": "string",  "description": "Comma-separated severity filter."},
                },
            },
        },
        {
            "type":        "function",
            "name":        "list_risks",
            "description": "List risks in the user's risk register. Filter by status to see what's open / accepted / closed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status":   {"type": "string", "description": "open | mitigated | accepted | transferred | closed. Default 'open'."},
                    "severity": {"type": "string", "description": "Optional severity filter."},
                },
            },
        },
        {
            "type":        "function",
            "name":        "navigate_to",
            "description": "Navigate the user's screen to a specific view in the CISO Copilot app. Use when the user says 'show me my findings', 'take me to risks', 'open the questionnaires', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type":        "string",
                        "description": "One of: overview, findings, risks, policies, questionnaires, connect, admin.",
                        "enum":        ["overview", "findings", "risks", "policies", "questionnaires", "connect", "admin"],
                    },
                },
                "required": ["view"],
            },
        },
        {
            "type":        "function",
            "name":        "filter_findings_view",
            "description": "Open the Findings view filtered to a specific severity/cloud/framework. Use when the user says 'show me my critical AWS findings' or 'what's failing on SOC 2'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity":  {"type": "string", "description": "Optional severity, e.g. 'critical' or 'critical,high'."},
                    "cloud":     {"type": "string", "description": "Optional single cloud: aws | azure | gcp | entra."},
                    "framework": {"type": "string", "description": "Optional framework key: soc2 | cis_aws | cis_azure | cis_gcp | mcsb | iso27001 | hipaa."},
                },
            },
        },
        {
            "type":        "function",
            "name":        "add_risk",
            "description": "Add a risk to the register. Use when the user says 'add to my risk register' or 'track this as a risk'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "Short title of the risk."},
                    "severity":    {"type": "string", "description": "critical | high | medium | low | info."},
                    "description": {"type": "string", "description": "Optional fuller description."},
                    "owner":       {"type": "string", "description": "Optional owner email."},
                    "due_date":    {"type": "string", "description": "Optional ISO date (YYYY-MM-DD)."},
                },
                "required": ["title", "severity"],
            },
        },
        # ===== Wow-demo action tools (dispatched via POST /v1/tools/{name}) =====
        {
            "type":        "function",
            "name":        "slack_dm",
            "description": "Send a Slack DM to a user. user_lookup can be either an email (preferred) or a name fragment (e.g. 'Venkat', 'Ratanshi'). When given a name, the tool fuzzy-matches against the workspace's user list — if multiple people match, it returns 'ambiguous_user' with candidates so you can re-ask. Use when the user says 'message X', 'Slack them', or 'let X know'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_lookup": {"type": "string", "description": "Email address OR name fragment of the target Slack user."},
                    "message":     {"type": "string", "description": "Plain-text body of the DM."},
                },
                "required": ["user_lookup", "message"],
            },
        },
        {
            "type":        "function",
            "name":        "create_jira_ticket",
            "description": "Create a JIRA issue in a specified project. Use when the user says 'open a JIRA', 'file a ticket', or 'track this in JIRA'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_key":     {"type": "string", "description": "JIRA project key (e.g. 'KAN', 'ITSEC')."},
                    "summary":         {"type": "string", "description": "One-line summary of the issue."},
                    "description":     {"type": "string", "description": "Optional fuller description."},
                    "assignee_lookup": {"type": "string", "description": "Optional assignee email."},
                },
                "required": ["project_key", "summary"],
            },
        },
        {
            "type":        "function",
            "name":        "revoke_oauth_grant",
            "description": "Revoke an Entra OAuth permission grant for a user/app pair. Use when the user says 'revoke X's access' or 'cut off the consent'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_object_id": {"type": "string", "description": "Entra user object id (GUID)."},
                    "app_id":         {"type": "string", "description": "Entra app id (GUID) whose grant should be revoked."},
                },
                "required": ["user_object_id", "app_id"],
            },
        },
        {
            "type":        "function",
            "name":        "create_pr_with_bump",
            "description": "Open a GitHub PR that bumps a dependency pin in a manifest. Use when the user says 'open a PR to bump X' or 'patch the langchain version'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo":             {"type": "string", "description": "owner/repo on GitHub (e.g. 'kkmookhey/wow-demo-pricing-system')."},
                    "dependency":       {"type": "string", "description": "Package name to bump (e.g. 'langchain')."},
                    "target_version":   {"type": "string", "description": "New version pin (e.g. '0.0.354')."},
                    "reviewer_lookup":  {"type": "string", "description": "Optional reviewer email/handle for the PR body."},
                    "manifest_path":    {"type": "string", "description": "Optional manifest path (default 'requirements.txt')."},
                },
                "required": ["repo", "dependency", "target_version"],
            },
        },
        {
            "type":        "function",
            "name":        "tail_lambda_logs_for_pattern",
            "description": "Search a Lambda function's recent CloudWatch logs for a regex pattern. Use when the user says 'check the logs for X' or 'look for exploit signature Y'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "function_name": {"type": "string", "description": "Lambda function name (not ARN)."},
                    "regex":         {"type": "string", "description": "Regex pattern to filter log lines."},
                    "window_hours":  {"type": "integer", "description": "Lookback window in hours (default 72)."},
                },
                "required": ["function_name", "regex"],
            },
        },
        {
            "type":        "function",
            "name":        "run_forensic_scan",
            "description": "Kick off a forensic scan on a resource. Returns immediately with an ETA; a push notification follows when results are ready. Use when the user says 'run a forensic scan' or 'check for compromise'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_arn":      {"type": "string", "description": "ARN of the resource to scan (Lambda, EC2, etc)."},
                    "check_kind":      {"type": "string", "description": "Kind of check: supply_chain_active_exploit | role_chain | data_exfil."},
                    "conversation_id": {"type": "string", "description": "Conversation id so the callback push can resume this thread."},
                },
                "required": ["target_arn", "check_kind", "conversation_id"],
            },
        },
    ]


# Shasta-native tool list, captured at import time. Always present in the
# Realtime session config; MCP-discovered per-vendor tools are appended on
# top per-session by _build_openai_tools.
_NATIVE_TOOLS = _tools()


async def _build_openai_tools(*, subject: str, tenant_id: str,
                                discover_fn=None, native_tools=None):
    """Per-session OpenAI tool registry.

    Combines:
      - Shasta-native tools (run_forensic_scan, etc.) — always present.
      - Per-vendor MCP tools discovered live from each connected provider
        with namespace prefix `{kind}__{tool_name}`.

    The MCP SDK exposes tool input schemas as `inputSchema` (camelCase)
    despite some spec snippets showing `input_schema` — we read camelCase
    first, snake_case as fallback.
    """
    discover_fn = discover_fn or _default_discover
    native_tools = native_tools if native_tools is not None else _NATIVE_TOOLS

    out = list(native_tools)
    try:
        connected = await discover_fn(subject, tenant_id=tenant_id)
    except Exception as e:
        print(f"[voice_session] discover_tools failed: {e!r}; native tools only")
        return out

    for kind, tools in connected.items():
        for t in tools:
            name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
            desc = getattr(t, "description", None) or (
                t.get("description", "") if isinstance(t, dict) else ""
            )
            schema = (
                getattr(t, "inputSchema", None)
                or getattr(t, "input_schema", None)
                or (t.get("inputSchema") if isinstance(t, dict) else None)
                or {"type": "object"}
            )
            out.append({
                "type":        "function",
                "name":        f"{kind}__{name}",
                "description": desc,
                "parameters":  schema,
            })
    return out


async def _default_discover(subject: str, *, tenant_id: str):
    from mcp_oauth import discover_tools
    return await discover_tools(subject, tenant_id=tenant_id)


def _build_openai_tools_sync(*, subject: str, tenant_id: str) -> list:
    """Sync bridge: voice_session handler is sync today. If someone
    refactors it to async later, this still works — we detect a running
    loop and use it; otherwise we spin one up just for this call."""
    coro = _build_openai_tools(subject=subject, tenant_id=tenant_id)
    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        return _asyncio.run(coro)
    # Already in an event loop — schedule and wait on a worker thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: _asyncio.run(coro)).result()


# ============================================================================
# Helpers
# ============================================================================

def _resolve_user_context(event: dict) -> tuple[str | None, str | None, str | None]:
    """Returns (email, tenant_id, tenant_name)."""
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    sso_subject = _subject_from_claims(claims)
    if not sso_subject:
        return None, None, None

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT u.email, u.tenant_id::text, t.display_name "
            "FROM users u JOIN tenants t USING (tenant_id) "
            "WHERE u.sso_subject = :s LIMIT 1"
        ),
        parameters=[{"name": "s", "value": {"stringValue": sso_subject}}],
    )
    rows = rs.get("records", [])
    if not rows:
        return None, None, None
    r = rows[0]
    return (
        r[0].get("stringValue"),
        r[1].get("stringValue"),
        r[2].get("stringValue"),
    )


def _subject_from_claims(claims: dict) -> str | None:
    raw = claims.get("identities")
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                return ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    return claims.get("sub")


def _list_clouds(tenant_id: str) -> list[str]:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT cloud_type, display_name FROM cloud_connections "
            "WHERE tenant_id = CAST(:t AS UUID) AND status = 'active'"
        ),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    return [
        f"{r[0].get('stringValue')} ({r[1].get('stringValue')})"
        for r in rs.get("records", [])
    ]


def _openai_api_key() -> str | None:
    global _openai_key
    if _openai_key is None:
        try:
            v = sm.get_secret_value(SecretId=OPENAI_SECRET_NAME)
            raw = v["SecretString"]
            # Stored either as raw string or as {"api_key": "..."} JSON.
            if raw.startswith("{"):
                parsed = json.loads(raw)
                _openai_key = parsed.get("api_key") or ""
            else:
                _openai_key = raw
            if not _openai_key:
                return None
        except Exception as e:
            print(f"OpenAI key load failed: {e}")
            return None
    return _openai_key


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }


def _first_name_from_email(email: str | None) -> str:
    """Best-effort first name from email prefix. 'kkmookhey@gmail.com' -> 'KK'."""
    if not email or "@" not in email:
        return "the user"
    prefix = email.split("@")[0]
    # Strip common dot/underscore separators; take the first segment.
    head = prefix.replace("_", ".").split(".")[0]
    # KK is a known special case (initials, uppercase).
    if head.lower() in {"kk", "kkmookhey"}:
        return "KK"
    return head.capitalize()
