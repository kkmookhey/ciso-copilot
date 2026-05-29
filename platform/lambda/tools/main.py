"""Dispatcher Lambda for Shasta's action tools.

Routes POST /v1/tools/{tool_name} to one of:
  - revoke_oauth_grant   (Microsoft Graph)
  - slack_dm             (Slack MCP)
  - create_jira_ticket   (Atlassian MCP)
  - create_pr_with_bump  (GitHub MCP)
  - tail_lambda_logs_for_pattern  (CloudWatch Logs Insights)
  - run_forensic_scan    (staged for demo; returns scan_id + ETA)

Each handler returns either a paired {speakable, identifier} result dict OR
a non-2xx error. The voice_session Lambda calls these by HTTP from the
Realtime tool-call dispatch on the iOS client side.

Auth: any tool that needs to JOIN users.sso_subject MUST resolve the subject
via subject_from_claims(claims) — NOT claims.get("sub") directly. For
federated logins the Cognito-pool sub is not the upstream IdP sub, so a bare
sub-based JOIN silently 401s every Microsoft/Google user. The canonical
pattern lives here and is mirrored from voice_session._subject_from_claims.
"""
from __future__ import annotations
import asyncio
import json
import traceback
from typing import Callable


# Tools register themselves into _DISPATCH at module import time.
_DISPATCH: dict[str, Callable[[dict, dict], dict]] = {}


# MCP-namespaced tool dispatch. Names look like `{kind}__{tool_name}`
# (e.g. `slack__send_message`). Routed through mcp_oauth.get_session with
# the caller's per-user token rather than the local _DISPATCH registry.
_MCP_PROVIDER_KINDS = {"slack", "atlassian", "google", "microsoft"}


def _is_namespaced_mcp(name: str) -> tuple[str | None, str | None]:
    if "__" not in name:
        return None, None
    kind, _, rest = name.partition("__")
    if kind not in _MCP_PROVIDER_KINDS:
        return None, None
    return kind, rest


async def _call_mcp_tool(*, kind: str, tool_name: str, args: dict,
                          subject: str, tenant_id: str) -> dict:
    from mcp_oauth import get_session
    async with get_session(subject, kind, tenant_id=tenant_id) as session:
        result = await session.call_tool(tool_name, args)
    return _extract_mcp_result(result)


def _extract_mcp_result(result) -> dict:
    if not getattr(result, "content", None):
        return {}
    first = result.content[0]
    text = getattr(first, "text", None)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def subject_from_claims(claims: dict) -> str | None:
    """Resolve the upstream-IdP subject for users.sso_subject JOINs.

    For federated logins (Microsoft/Google), `claims.sub` is the Cognito-
    user-pool sub — NOT the upstream IdP sub. `identities[0].userId` is the
    upstream value. Returns None when neither is present (caller should
    return 401).
    """
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


def register(name: str):
    def deco(fn):
        _DISPATCH[name] = fn
        return fn
    return deco


# Import tool modules so they can register. Each module decorates its handler
# with @register("tool_name"). New tools land in subsequent tasks.
from tools import revoke_oauth_grant  # noqa: F401,E402
from tools import slack_dm            # noqa: F401,E402
from tools import create_jira_ticket  # noqa: F401,E402
from tools import create_pr_with_bump # noqa: F401,E402
from tools import tail_lambda_logs    # noqa: F401,E402
from tools import run_forensic_scan   # noqa: F401,E402


def handler(event: dict, context) -> dict:
    tool_name = (event.get("pathParameters") or {}).get("tool_name")
    kind, mcp_tool = _is_namespaced_mcp(tool_name or "")
    if not kind and tool_name not in _DISPATCH:
        return _resp(404, {"error": "unknown_tool", "tool": tool_name})

    body_raw = event.get("body")
    if not body_raw:
        return _resp(400, {"error": "missing_body"})
    try:
        args = json.loads(body_raw)
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    # Use the canonical subject extraction so federated users aren't 401'd.
    if not subject_from_claims(claims):
        return _resp(401, {"error": "no_auth"})

    # Log the inbound args so we can see exactly what the model passed.
    # Redact obvious secrets just in case.
    safe_args = {k: ("<redacted>" if "token" in k.lower() or "secret" in k.lower()
                                    or "password" in k.lower() else v)
                 for k, v in (args or {}).items()}
    print(f"[tools] dispatch {tool_name} args={json.dumps(safe_args)[:500]}")

    # MCP-namespaced tool? Route via mcp_oauth.
    if kind:
        tenant_id = claims.get("custom:tenant_id")
        if not tenant_id:
            return _resp(400, {"error": "missing_tenant_id"})
        subject = subject_from_claims(claims)
        try:
            result = asyncio.run(_call_mcp_tool(
                kind=kind, tool_name=mcp_tool, args=args,
                subject=subject, tenant_id=tenant_id,
            ))
            return _resp(200, result)
        except Exception as e:
            from mcp_oauth.session import ConnectorMissingError, ConnectorRevokedError
            if isinstance(e, ConnectorMissingError):
                return _resp(409, {
                    "error":   "connector_missing",
                    "kind":    kind,
                    "message": f"Connect your {kind.title()} in Settings to use this.",
                })
            if isinstance(e, ConnectorRevokedError):
                return _resp(409, {
                    "error":   "connector_revoked",
                    "kind":    kind,
                    "message": f"Your {kind.title()} connection expired — reconnect in Settings.",
                })
            print(f"[tools] mcp call {tool_name} failed: {type(e).__name__}: {e}")
            return _resp(502, {"error": "mcp_failed", "detail": str(e)[:200]})

    try:
        result = _DISPATCH[tool_name](args, claims)
        return _resp(200, result)
    except Exception as e:
        print(f"tool {tool_name} failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return _resp(500, {"error": "tool_failed", "tool": tool_name, "detail": str(e)[:200]})


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
