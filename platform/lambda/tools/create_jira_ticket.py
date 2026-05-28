# platform/lambda/tools/create_jira_ticket.py
"""Create a JIRA issue via the mcp-atlassian MCP server."""
from __future__ import annotations
import os

from _shared.mcp_client import MCPClient, ToolRegistryEntry
from tools.main import register


_mcp_client = MCPClient()
_mcp_client.register("jira_create_issue", ToolRegistryEntry(
    server="atlassian",
    tool="jira_create_issue",
    args_mapping=lambda a: {
        "project_key":  a["project_key"],
        "summary":      a["summary"],
        "issue_type":   a.get("issue_type", "Task"),
        "description":  a.get("description", ""),
        "assignee":     a.get("assignee_lookup"),
    },
))


@register("create_jira_ticket")
def handle(args: dict, claims: dict) -> dict:
    project_key = args["project_key"]
    summary     = args["summary"]

    result = _mcp_client.call("jira_create_issue", args)
    # mcp-atlassian wraps the created issue under result["issue"] (verified
    # against the live MCP server); older shapes had key at top level. Handle
    # both defensively.
    issue = result.get("issue") or {}
    key = issue.get("key") or result.get("key")
    if not key:
        return {
            "created":   False,
            "reason":    "no_key_returned",
            "raw":       result,
            "speakable": "JIRA returned no issue key — check the project key and assignee.",
        }
    base = os.environ.get("JIRA_URL", "").rstrip("/")
    assignee = args.get("assignee_lookup")
    if assignee:
        speakable = f"JIRA {key} opened, assigned to {assignee.split('@')[0]}."
    else:
        speakable = f"JIRA {key} opened."
    return {
        "created":   True,
        "key":       key,
        "url":       f"{base}/browse/{key}" if base else key,
        "speakable": speakable,
    }
