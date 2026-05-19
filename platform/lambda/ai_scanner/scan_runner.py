# platform/lambda/ai_scanner/scan_runner.py
"""Scan orchestration: build context, clone repo, hand off to detectors."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
import jwt as pyjwt

GITHUB_APP_SECRET_ARN = os.environ["GITHUB_APP_SECRET_ARN"]
SCANNER_VERSION = os.environ.get("SCANNER_VERSION", "0.1.0")
MAX_CLONE_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB

_sm = boto3.client("secretsmanager")
_credentials_cache: dict | None = None
_token_cache: dict[int, tuple[str, float]] = {}


class RepoTooLarge(RuntimeError):
    """Repo exceeds the scanner's 4 GB clone ceiling."""


@dataclass(frozen=True)
class ScanContext:
    scan_id:         str
    tenant_id:       str
    connection_id:   str
    repo_asset_id:   str
    repo_full_name:  str
    default_branch:  str
    head_commit_sha: str
    installation_id: int
    repo_workdir:    Path
    scanner_version: str = SCANNER_VERSION

    @classmethod
    def from_message(cls, body: dict, repo_workdir: Path, head_commit_sha: str) -> "ScanContext":
        return cls(
            scan_id=body["scan_id"],
            tenant_id=body["tenant_id"],
            connection_id=body["connection_id"],
            repo_asset_id=body["repo_asset_id"],
            repo_full_name=body["repo_full_name"],
            default_branch=body["default_branch"],
            head_commit_sha=head_commit_sha,
            installation_id=body["installation_id"],
            repo_workdir=repo_workdir,
        )


def clone_repo(installation_id: int, repo_full_name: str, default_branch: str,
               workdir: Path) -> str:
    """Shallow-clone the repo and return the head commit SHA. Raises RepoTooLarge."""
    workdir.mkdir(parents=True, exist_ok=True)
    token = _installation_token(installation_id)
    url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"

    subprocess.run(
        ["git", "clone", "--depth=1", "--single-branch", "--branch", default_branch,
         url, str(workdir)],
        check=True, capture_output=True,
    )

    # Sanity-check size BEFORE we hand off to detectors.
    out = subprocess.check_output(["du", "-s", "-B1", str(workdir)])
    m = re.search(rb"(\d+)", out)
    bytes_used = int(m.group(1)) if m else 0
    if bytes_used > MAX_CLONE_BYTES:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RepoTooLarge(f"{repo_full_name} is {bytes_used} bytes, ceiling is {MAX_CLONE_BYTES}")

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(workdir),
        check=True, capture_output=True,
    ).stdout.decode().strip()
    return sha


# ----- GitHub App auth (lifted from ai_github Lambda) -----------------------

def _credentials() -> dict:
    global _credentials_cache
    if _credentials_cache is None:
        v = _sm.get_secret_value(SecretId=GITHUB_APP_SECRET_ARN)
        _credentials_cache = json.loads(v["SecretString"])
    return _credentials_cache


def _installation_token(installation_id: int) -> str:
    cached = _token_cache.get(installation_id)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    c = _credentials()
    iss = c.get("client_id") or str(c.get("app_id") or "")
    now = int(time.time())
    app_jwt = pyjwt.encode(
        {"iat": now - 30, "exp": now + 600 - 30, "iss": iss},
        c["private_key"], algorithm="RS256",
    )

    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=b"", method="POST",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read())
    token = body["token"]
    import datetime as dt
    exp = dt.datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
    _token_cache[installation_id] = (token, exp)
    return token
