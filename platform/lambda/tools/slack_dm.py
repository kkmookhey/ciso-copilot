# platform/lambda/tools/slack_dm.py
"""Send a Slack DM to a user via the Slack MCP server.

Two-step: look up user by email (slack_get_user_by_email), then post a
message to their DM channel using their user-ID as the channel
(slack_post_message accepts a user-ID directly and opens/reuses the DM
channel automatically).

NOTE: tool names on the upstream @modelcontextprotocol/server-slack are
best-guess from the published spec. They'll be confirmed when the Slack
App credential batch is ready; a runtime mismatch surfaces as a clear
MCP error, not a silent failure.
"""
from __future__ import annotations

from _shared.mcp_client import MCPClient, ToolRegistryEntry
from tools.main import register

_mcp_client = MCPClient()
_mcp_client.register("slack_lookup_user", ToolRegistryEntry(
    server="slack",
    tool="slack_get_user_by_email",
    args_mapping=lambda args: {"email": args["email"]},
))
_mcp_client.register("slack_post_message", ToolRegistryEntry(
    server="slack",
    tool="slack_post_message",
    args_mapping=lambda args: {"channel": args["channel"], "text": args["text"]},
))


@register("slack_dm")
def handle(args: dict, claims: dict) -> dict:
    email   = args["user_lookup"]
    message = args["message"]

    lookup = _mcp_client.call("slack_lookup_user", {"email": email})
    user = lookup.get("user") or lookup.get("data", {}).get("user")
    if not user or "id" not in user:
        return {
            "sent":      False,
            "reason":    "user_not_found",
            "speakable": f"Could not find {email} in Slack.",
        }

    user_id = user["id"]
    post = _mcp_client.call("slack_post_message", {"channel": user_id, "text": message})
    return {
        "sent":      True,
        "ts":        post.get("ts"),
        "channel":   post.get("channel"),
        "speakable": f"Slack DM sent to {email.split('@')[0]}.",
    }
