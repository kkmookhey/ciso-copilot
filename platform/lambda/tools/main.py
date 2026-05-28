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
"""
from __future__ import annotations
import json
import traceback
from typing import Callable


# Tools register themselves into _DISPATCH at module import time.
_DISPATCH: dict[str, Callable[[dict, dict], dict]] = {}


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
    if tool_name not in _DISPATCH:
        return _resp(404, {"error": "unknown_tool", "tool": tool_name})

    body_raw = event.get("body")
    if not body_raw:
        return _resp(400, {"error": "missing_body"})
    try:
        args = json.loads(body_raw)
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    if not claims.get("sub"):
        return _resp(401, {"error": "no_auth"})

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
