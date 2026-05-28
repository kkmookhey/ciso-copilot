# platform/lambda/tools/create_pr_with_bump.py
"""Open a PR that bumps a single dependency pin in a manifest file."""
from __future__ import annotations
import base64
import re

from _shared.mcp_client import MCPClient, ToolRegistryEntry
from tools.main import register


_mcp_client = MCPClient()
_mcp_client.register("github_get_file", ToolRegistryEntry(
    server="github",
    tool="get_file_contents",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"], "path": a["path"]},
))
_mcp_client.register("github_create_branch", ToolRegistryEntry(
    server="github",
    tool="create_branch",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"],
                            "branch": a["branch"], "from_branch": a.get("from_branch", "main")},
))
_mcp_client.register("github_put_file", ToolRegistryEntry(
    server="github",
    tool="create_or_update_file",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"], "path": a["path"],
                            "content": a["content"], "message": a["message"],
                            "branch": a["branch"], "sha": a.get("sha")},
))
_mcp_client.register("github_create_pr", ToolRegistryEntry(
    server="github",
    tool="create_pull_request",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"],
                            "title": a["title"], "head": a["head"],
                            "base": a.get("base", "main"), "body": a.get("body", "")},
))


def _bump_version_in_requirements(content: str, pkg: str, new_version: str) -> str:
    """Replace `pkg==<old>` with `pkg==<new>`. Leaves >= or other comparators alone."""
    pattern = re.compile(rf"^({re.escape(pkg)})==[\w\.\-]+", re.MULTILINE)
    return pattern.sub(f"{pkg}=={new_version}", content)


@register("create_pr_with_bump")
def handle(args: dict, claims: dict) -> dict:
    repo_full      = args["repo"]                              # "owner/repo"
    dependency     = args["dependency"]
    target_version = args["target_version"]
    manifest_path  = args.get("manifest_path", "requirements.txt")
    reviewer       = args.get("reviewer_lookup")
    owner, repo    = repo_full.split("/", 1)

    branch = f"shasta/bump-{dependency}-{target_version}"
    title  = f"Bump {dependency} to {target_version}"
    body   = (f"Shasta opened this PR after KEV-listed CVE matched against "
              f"`{dependency}` in active runtime use.\n\n"
              f"Reviewer suggested: {reviewer or 'unassigned'}.")

    cur = _mcp_client.call("github_get_file", {
        "owner": owner, "repo": repo, "path": manifest_path,
    })
    raw_content = cur.get("content", "")
    if cur.get("encoding") == "base64":
        # GitHub MCP sometimes returns the base64 with embedded newlines and
        # without trailing '=' padding. Strip whitespace and re-pad to a
        # multiple of 4 before decoding so b64decode doesn't 400.
        clean = "".join(raw_content.split())
        clean += "=" * (-len(clean) % 4)
        raw_content = base64.b64decode(clean).decode()
    new_content = _bump_version_in_requirements(raw_content, dependency, target_version)
    if new_content == raw_content:
        return {
            "created":   False,
            "reason":    "no_pin_to_bump",
            "speakable": f"No pin for {dependency} found in {manifest_path}.",
        }

    _mcp_client.call("github_create_branch", {
        "owner": owner, "repo": repo, "branch": branch, "from_branch": "main",
    })
    _mcp_client.call("github_put_file", {
        "owner": owner, "repo": repo, "path": manifest_path,
        "content": new_content, "message": title, "branch": branch,
        "sha": cur.get("sha"),
    })
    pr = _mcp_client.call("github_create_pr", {
        "owner": owner, "repo": repo, "title": title,
        "head": branch, "base": "main", "body": body,
    })
    return {
        "created":   True,
        "pr_number": pr.get("number"),
        "url":       pr.get("html_url"),
        "speakable": f"PR opened — link is in your Slack.",
    }
