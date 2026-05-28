# platform/lambda/tools/slack_dm.py
"""Send a Slack DM to a user by email or name.

Goes direct to the Slack Web API (users.lookupByEmail or users.list +
fuzzy match, then chat.postMessage) rather than through
@modelcontextprotocol/server-slack — that server doesn't expose
lookupByEmail under any standard tool name. We already have
SLACK_BOT_TOKEN in env, so direct REST is simpler and lower-latency
than the stdio MCP round-trip.
"""
from __future__ import annotations
import os

import requests

from tools.main import register


_SLACK_BASE = "https://slack.com/api"


@register("slack_dm")
def handle(args: dict, claims: dict) -> dict:
    lookup_val = (args["user_lookup"] or "").strip()
    message    = args["message"]
    token      = os.environ.get("SLACK_BOT_TOKEN") or ""
    if not lookup_val:
        return {
            "sent":      False,
            "reason":    "missing_user_lookup",
            "speakable": "I need a name or email to DM.",
        }
    if not token:
        return {
            "sent":      False,
            "reason":    "no_bot_token",
            "speakable": "Slack bot token not configured.",
        }
    headers = {"Authorization": f"Bearer {token}"}

    user_obj = _resolve_user(lookup_val, headers)
    if not user_obj.get("ok"):
        return {
            "sent":      False,
            "reason":    user_obj.get("error", "user_not_found"),
            "raw":       user_obj,
            "speakable": user_obj.get("speakable",
                                       f"Could not find {lookup_val} in Slack."),
        }
    user_id   = user_obj["user_id"]
    user_name = user_obj["user_name"]

    post = requests.post(
        f"{_SLACK_BASE}/chat.postMessage",
        headers=headers,
        json={"channel": user_id, "text": message},
        timeout=10,
    ).json()
    if not post.get("ok"):
        err = post.get("error") or "post_failed"
        return {
            "sent":      False,
            "reason":    err,
            "raw":       post,
            "speakable": f"Slack rejected the message ({err}).",
        }
    return {
        "sent":      True,
        "ts":        post.get("ts"),
        "channel":   post.get("channel"),
        "speakable": f"Slack DM sent to {user_name.split()[0]}.",
    }


def _resolve_user(lookup_val: str, headers: dict) -> dict:
    """Resolve an arbitrary 'lookup_val' (email or name fragment) to a
    Slack user. Returns {ok, user_id, user_name} or {ok: False, error, speakable}.
    """
    if "@" in lookup_val:
        # Direct email lookup — fastest path.
        r = requests.get(
            f"{_SLACK_BASE}/users.lookupByEmail",
            headers=headers, params={"email": lookup_val}, timeout=10,
        ).json()
        if r.get("ok"):
            u = r["user"]
            return {
                "ok":        True,
                "user_id":   u["id"],
                "user_name": u.get("real_name") or u.get("name") or lookup_val.split("@")[0],
            }
        err = r.get("error", "user_not_found")
        return {
            "ok":        False,
            "error":     err,
            "speakable": f"Could not find {lookup_val.split('@')[0]} in Slack ({err}).",
        }

    # Name path — fuzzy-match against the workspace user list.
    name_lower = lookup_val.lower()
    matches = []
    cursor = ""
    # Slack paginates users.list at 200 / page; the demo workspace is small.
    for _ in range(5):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            f"{_SLACK_BASE}/users.list",
            headers=headers, params=params, timeout=10,
        ).json()
        if not r.get("ok"):
            return {
                "ok":        False,
                "error":     r.get("error", "users_list_failed"),
                "speakable": f"Slack rejected the user lookup ({r.get('error', 'unknown')}).",
            }
        for m in r.get("members", []):
            if m.get("deleted") or m.get("is_bot"):
                continue
            real = (m.get("real_name") or "").lower()
            display = (m.get("profile", {}).get("display_name") or "").lower()
            uname = (m.get("name") or "").lower()
            if name_lower in real or name_lower in display or name_lower in uname:
                matches.append(m)
        cursor = r.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break

    if not matches:
        return {
            "ok":        False,
            "error":     "user_not_found",
            "speakable": f"No Slack member matched '{lookup_val}'.",
        }
    if len(matches) > 1:
        # Ambiguous — surface the candidates so Shasta can re-ask.
        names = [m.get("real_name") or m.get("name") for m in matches[:5]]
        return {
            "ok":        False,
            "error":     "ambiguous_user",
            "candidates": names,
            "speakable": f"Multiple matches for '{lookup_val}': {', '.join(names)}. Which one?",
        }
    u = matches[0]
    return {
        "ok":        True,
        "user_id":   u["id"],
        "user_name": u.get("real_name") or u.get("name") or lookup_val,
    }
