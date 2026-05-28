# platform/lambda/tools/create_pr_with_bump.py
"""Open a PR that bumps a single dependency pin in a manifest file.

Goes direct to the GitHub REST API (PAT in env) rather than through
the @modelcontextprotocol/server-github MCP server, because that server's
get_file_contents returns malformed base64 (count-not-mod-4) for short
manifest files. Direct REST is simpler and more reliable.
"""
from __future__ import annotations
import base64
import os
import re
from typing import Any

import requests

from tools.main import register


_GH_BASE = "https://api.github.com"


def _bump_version_in_requirements(content: str, pkg: str, new_version: str) -> str:
    """Replace `pkg==<old>` with `pkg==<new>`. Leaves >= or other comparators alone."""
    pattern = re.compile(rf"^({re.escape(pkg)})==[\w\.\-]+", re.MULTILINE)
    return pattern.sub(f"{pkg}=={new_version}", content)


def _headers() -> dict[str, str]:
    pat = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or ""
    if not pat:
        raise RuntimeError("GITHUB_PERSONAL_ACCESS_TOKEN not set")
    return {
        "Authorization": f"Bearer {pat}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh(method: str, path: str, **kwargs) -> dict[str, Any]:
    r = requests.request(method, f"{_GH_BASE}{path}", headers=_headers(), timeout=15, **kwargs)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:300]
        raise RuntimeError(f"GitHub {method} {path} → {r.status_code}: {detail}")
    return r.json() if r.content else {}


@register("create_pr_with_bump")
def handle(args: dict, claims: dict) -> dict:
    repo_full      = args["repo"]
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

    # 1. Read current manifest.
    cur = _gh("GET", f"/repos/{owner}/{repo}/contents/{manifest_path}")
    if cur.get("encoding") != "base64" or not cur.get("content"):
        return {
            "created":   False,
            "reason":    "manifest_not_readable",
            "speakable": f"Could not read {manifest_path} from {repo_full}.",
        }
    raw_content = base64.b64decode(cur["content"]).decode()
    new_content = _bump_version_in_requirements(raw_content, dependency, target_version)
    if new_content == raw_content:
        return {
            "created":   False,
            "reason":    "no_pin_to_bump",
            "speakable": f"No pin for {dependency} found in {manifest_path}.",
        }

    # 2. Find default branch SHA so the new branch can fork from it.
    default_branch = _gh("GET", f"/repos/{owner}/{repo}").get("default_branch", "main")
    base_ref = _gh("GET", f"/repos/{owner}/{repo}/git/ref/heads/{default_branch}")
    base_sha = base_ref["object"]["sha"]

    # 3. Create branch (idempotent — ignore "already exists").
    try:
        _gh("POST", f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha})
    except RuntimeError as e:
        if "Reference already exists" not in str(e):
            raise

    # 4. Commit the bumped manifest onto the new branch.
    new_b64 = base64.b64encode(new_content.encode()).decode()
    _gh("PUT", f"/repos/{owner}/{repo}/contents/{manifest_path}",
        json={"message": title, "content": new_b64,
              "branch": branch, "sha": cur["sha"]})

    # 5. Open the PR.
    pr = _gh("POST", f"/repos/{owner}/{repo}/pulls",
             json={"title": title, "head": branch,
                   "base": default_branch, "body": body})

    return {
        "created":   True,
        "pr_number": pr.get("number"),
        "url":       pr.get("html_url"),
        "speakable": f"PR opened — bump {dependency} to {target_version}, number {pr.get('number')}.",
    }
