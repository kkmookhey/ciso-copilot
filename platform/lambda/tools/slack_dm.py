# platform/lambda/tools/slack_dm.py
"""Send a Slack DM to a user via email lookup.

Goes direct to the Slack Web API (users.lookupByEmail + chat.postMessage)
rather than through the @modelcontextprotocol/server-slack MCP server,
because that server doesn't expose lookupByEmail under any standardized
tool name. We already have SLACK_BOT_TOKEN in env, so the direct path is
both simpler and lower-latency than the stdio MCP round-trip.
"""
from __future__ import annotations
import os

import requests

from tools.main import register


_SLACK_BASE = "https://slack.com/api"


@register("slack_dm")
def handle(args: dict, claims: dict) -> dict:
    email   = args["user_lookup"]
    message = args["message"]
    token   = os.environ.get("SLACK_BOT_TOKEN") or ""
    if not token:
        return {
            "sent":      False,
            "reason":    "no_bot_token",
            "speakable": "Slack bot token not configured.",
        }
    headers = {"Authorization": f"Bearer {token}"}

    lookup = requests.get(
        f"{_SLACK_BASE}/users.lookupByEmail",
        headers=headers, params={"email": email}, timeout=10,
    ).json()
    if not lookup.get("ok"):
        err = lookup.get("error") or "user_not_found"
        return {
            "sent":      False,
            "reason":    err,
            "raw":       lookup,
            "speakable": f"Could not find {email.split('@')[0]} in Slack ({err}).",
        }
    user_id = lookup["user"]["id"]
    user_name = lookup["user"].get("real_name") or lookup["user"].get("name") or email.split("@")[0]

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
