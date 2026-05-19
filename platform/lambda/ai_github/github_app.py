"""GitHub App client: App-level JWT, installation tokens, repo listing.

App-level JWT (RS256, 10min TTL) is minted with the App's private key.
Installation tokens (1hr TTL) are exchanged via POST /app/installations/
{id}/access_tokens. Tokens are cached in-process per warm Lambda
container with a 50-min TTL.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import boto3
import jwt as pyjwt

GITHUB_APP_SECRET_ARN = os.environ["GITHUB_APP_SECRET_ARN"]
GITHUB_API_BASE = "https://api.github.com"

_sm = boto3.client("secretsmanager")
_credentials_cache: dict | None = None
_installation_token_cache: dict[int, tuple[str, float]] = {}  # installation_id → (token, expires_at_unix)


def credentials() -> dict:
    """{app_id, client_id, client_secret, private_key}"""
    global _credentials_cache
    if _credentials_cache is None:
        v = _sm.get_secret_value(SecretId=GITHUB_APP_SECRET_ARN)
        _credentials_cache = json.loads(v["SecretString"])
    return _credentials_cache


def mint_app_jwt() -> str:
    """RS256 JWT signed with the App's private key. 10-minute TTL."""
    c = credentials()
    now = int(time.time())
    iat = now - 30              # 30s clock-skew tolerance per GitHub recommendation
    payload = {
        "iat": iat,
        "exp": iat + 600,       # 10 minutes TTL from iat (max permitted by GitHub)
        "iss": c["client_id"],  # GitHub now prefers client_id over numeric app_id
    }
    return pyjwt.encode(payload, c["private_key"], algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """Return a cached or freshly-minted installation access token."""
    cached = _installation_token_cache.get(installation_id)
    now = time.time()
    if cached and cached[1] > now + 60:  # 60s safety margin
        return cached[0]

    app_jwt = mint_app_jwt()
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    status, body = _http_post(url, headers={
        "Authorization": f"Bearer {app_jwt}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }, body=b"")
    if status != 201:
        raise RuntimeError(f"installation token mint failed: {status} {body}")
    token = body["token"]
    # parse 2026-05-18T11:00:00Z → unix
    import datetime as dt
    exp_unix = dt.datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
    _installation_token_cache[installation_id] = (token, exp_unix)
    return token


def list_authorized_repos(installation_id: int, page: int = 1, per_page: int = 30) -> dict[str, Any]:
    """Page through the installation's accessible repos and normalise the shape."""
    token = get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/installation/repositories?page={page}&per_page={per_page}"
    status, body, _ = _http_get(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if status != 200:
        raise RuntimeError(f"list repos failed: {status} {body}")
    total = body["total_count"]
    pages = math.ceil(total / per_page) if total else 0
    next_page = page + 1 if page < pages else None
    return {
        "repos": [_normalise_repo(r) for r in body["repositories"]],
        "next_page": next_page,
        "total_count": total,
    }


def revoke_installation_token(installation_id: int) -> None:
    """Revoke the current installation token (best-effort cleanup on DELETE)."""
    token = get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/installation/token"
    status, _ = _http_delete(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
    })
    _installation_token_cache.pop(installation_id, None)
    if status not in (204, 401):  # 401 means the token was already invalid — fine
        raise RuntimeError(f"revoke token failed: {status}")


def _normalise_repo(r: dict) -> dict:
    return {
        "full_name":        r["full_name"],
        "default_branch":   r.get("default_branch"),
        "last_pushed_at":   r.get("pushed_at"),
        "size_kb":          r.get("size"),
        "primary_language": r.get("language"),
        "is_private":       r.get("private", False),
    }


def _http_get(url: str, headers: dict) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            return r.status, body, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def _http_post(url: str, headers: dict, body: bytes) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _http_delete(url: str, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="DELETE", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
