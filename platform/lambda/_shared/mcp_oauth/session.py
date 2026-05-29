"""Runtime entry point: resolve user's connector row, JIT-refresh, open MCP session.

This module is sync at the resolution/refresh layer (Aurora Data API is
synchronous via boto3), and async at the MCP layer (mcp SDK is asyncio).
get_session() is an async context manager.
"""
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import os
import boto3
from contextlib import asynccontextmanager
from typing import Literal

from .crypto import encrypt_token, decrypt_token
from .providers import slack as slack_provider

ProviderKind = Literal["slack", "atlassian", "google", "microsoft"]


class ConnectorMissingError(RuntimeError):
    pass


class ConnectorRevokedError(RuntimeError):
    pass


# ---- DB helpers --------------------------------------------------------

_rds_client = None


def _db():
    """Returns the Aurora Data API client wrapper. _db().execute(sql, params)."""
    global _rds_client
    if _rds_client is None:
        _rds_client = _DataAPIWrapper()
    return _rds_client


class _DataAPIWrapper:
    def __init__(self):
        self._client = boto3.client("rds-data")
        self._cluster_arn = os.environ["DB_CLUSTER_ARN"]
        self._secret_arn = os.environ["DB_SECRET_ARN"]
        self._database = os.environ.get("DB_NAME", "ciso_copilot")

    def execute(self, sql: str, parameters: list | None = None):
        resp = self._client.execute_statement(
            resourceArn=self._cluster_arn,
            secretArn=self._secret_arn,
            database=self._database,
            sql=sql,
            parameters=parameters or [],
            includeResultMetadata=True,
        )
        return _Result(resp)


class _Result:
    def __init__(self, resp: dict):
        self._resp = resp

    def fetchone(self) -> dict | None:
        records = self._resp.get("records") or []
        if not records:
            return None
        meta = self._resp.get("columnMetadata") or []
        return _zip_record(meta, records[0])


def _zip_record(meta, record) -> dict:
    """Aurora Data API returns one of:
       {"stringValue": ...}, {"longValue": ...}, {"booleanValue": ...},
       {"blobValue": ...}, {"isNull": True},
       {"arrayValue": {"stringValues"|"longValues"|...: [...]}}.
    Unwrap to native Python values."""
    out = {}
    for col, cell in zip(meta, record):
        if "isNull" in cell:
            out[col["name"]] = None
        elif "arrayValue" in cell:
            av = cell["arrayValue"]
            out[col["name"]] = (
                av.get("stringValues") or av.get("longValues")
                or av.get("booleanValues") or av.get("doubleValues") or []
            )
        else:
            out[col["name"]] = next(iter(cell.values()))
    return out


# ---- Public API --------------------------------------------------------

def lookup_user_connector(*, tenant_id: str, user_id: str, kind: ProviderKind) -> dict:
    db = _db()
    sql = """
        SELECT conn_id, access_token_enc, refresh_token_enc,
               access_expires_at, mcp_server_url
        FROM user_connectors
        WHERE tenant_id = :tid::uuid AND user_id = :uid::uuid
          AND oauth_provider = :provider AND status = 'active'
    """
    row = db.execute(sql, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
        {"name": "provider", "value": {"stringValue": kind}},
    ]).fetchone()
    if not row:
        raise ConnectorMissingError(f"no active {kind} connector for {user_id}")
    return row


def _provider_refresh(kind: ProviderKind, refresh_token_plaintext: str) -> dict:
    cid = os.environ[f"{kind.upper()}_CLIENT_ID"]
    csec = os.environ[f"{kind.upper()}_CLIENT_SECRET"]
    if kind == "slack":
        return slack_provider.refresh_token(
            refresh_token=refresh_token_plaintext,
            client_id=cid, client_secret=csec,
        )
    raise NotImplementedError(f"refresh not implemented for {kind}")


def _seconds_until(expires_at) -> float | None:
    """Returns None if expires_at is NULL — caller treats NULL as 'always refresh'."""
    if expires_at is None:
        return None
    if isinstance(expires_at, str):
        expires_at = dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return (expires_at - dt.datetime.now(dt.timezone.utc)).total_seconds()


def refresh_if_near_expiry(row: dict, *, kind: ProviderKind,
                            tenant_id: str, user_id: str,
                            threshold_seconds: int = 60) -> str:
    """Returns plaintext access_token. Refreshes inline when:
       (a) access_expires_at IS NULL (no expiry returned by vendor — legacy
           non-rotating Slack apps), OR
       (b) access_expires_at - now() < threshold_seconds.
    Matches spec §6 NULL-safe predicate."""
    remaining = _seconds_until(row["access_expires_at"])
    if remaining is not None and remaining > threshold_seconds:
        return decrypt_token(row["access_token_enc"])

    # Concurrent-refresh race mitigation: Postgres advisory lock keyed by conn_id.
    # Slack and Atlassian use single-use rotating refresh tokens — without this
    # lock, two simultaneous invocations both refresh and one's new token is
    # invalidated server-side (spec §6 "Concurrent-refresh race").
    db = _db()
    lock_key = int(hashlib.sha256(str(row["conn_id"]).encode()).hexdigest()[:15], 16)
    db.execute("SELECT pg_advisory_xact_lock(:k)", [
        {"name": "k", "value": {"longValue": lock_key}}
    ]).fetchone()

    # Re-read under the lock — another invocation may have already refreshed.
    refreshed = db.execute("""
        SELECT access_token_enc, refresh_token_enc, access_expires_at
        FROM user_connectors WHERE conn_id = :cid::uuid
    """, [{"name": "cid", "value": {"stringValue": str(row["conn_id"])}}]).fetchone()

    re_remaining = _seconds_until(refreshed["access_expires_at"])
    if re_remaining is not None and re_remaining > threshold_seconds:
        return decrypt_token(refreshed["access_token_enc"])
    now = dt.datetime.now(dt.timezone.utc)

    # Still expired — actually refresh.
    refresh_plain = decrypt_token(refreshed["refresh_token_enc"])
    new_tokens = _provider_refresh(kind, refresh_plain)
    new_access_enc = encrypt_token(new_tokens["access_token"])
    new_refresh_enc = encrypt_token(new_tokens["refresh_token"])
    new_expires_at = now + dt.timedelta(seconds=int(new_tokens["expires_in"]))

    db.execute("""
        UPDATE user_connectors
        SET access_token_enc = :a, refresh_token_enc = :r,
            access_expires_at = CAST(:e AS TIMESTAMPTZ), last_used_at = now()
        WHERE conn_id = :cid::uuid
    """, [
        {"name": "a", "value": {"blobValue": new_access_enc}},
        {"name": "r", "value": {"blobValue": new_refresh_enc}},
        {"name": "e", "value": {"stringValue": new_expires_at.isoformat()}},
        {"name": "cid", "value": {"stringValue": str(row["conn_id"])}},
    ]).fetchone()
    return new_tokens["access_token"]


@asynccontextmanager
async def get_session(subject: str, kind: ProviderKind, *, tenant_id: str):
    """Open an MCP session for THIS user against THIS provider."""
    user_id = _resolve_user_id(subject, tenant_id=tenant_id)
    row = lookup_user_connector(tenant_id=tenant_id, user_id=user_id, kind=kind)
    access_token = refresh_if_near_expiry(row, kind=kind, tenant_id=tenant_id, user_id=user_id)

    # Lazy import to keep cold-start light if MCP isn't used in this path.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(row["mcp_server_url"], headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _resolve_user_id(subject: str, *, tenant_id: str) -> str:
    """Look up users.user_id from sso_subject."""
    row = _db().execute("""
        SELECT user_id FROM users
        WHERE tenant_id = :tid::uuid AND sso_subject = :sub
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "sub", "value": {"stringValue": subject}},
    ]).fetchone()
    if not row:
        raise ConnectorMissingError(f"no users row for subject={subject} tenant={tenant_id}")
    return str(row["user_id"])


# ---- Tool discovery cache ---------------------------------------------

import hashlib as _hashlib_dis
_tool_cache: dict[str, tuple[float, list]] = {}
_TOOL_CACHE_TTL = 300  # 5 minutes


def _cache_signature(row: dict) -> str:
    workspace = row.get("vendor_workspace_id") or ""
    scopes_hash = _hashlib_dis.sha256(
        (",".join(sorted(row.get("scopes") or []))).encode()
    ).hexdigest()[:16]
    return f"{workspace}:{scopes_hash}"


@contextlib.asynccontextmanager
async def _open_session_for_user(user_id: str, *, kind: ProviderKind,
                                   tenant_id: str, row: dict):
    """Async context manager that yields a connected MCP ClientSession.

    Bypass the lookup-row step when caller already has the row.
    Caller invokes as: `async with _open_session_for_user(...) as session: ...`
    """
    access_token = refresh_if_near_expiry(
        row, kind=kind, tenant_id=tenant_id, user_id=user_id,
    )
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(row["mcp_server_url"], headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


async def _discover_tools_for_user(user_id: str, *, kind: ProviderKind,
                                    tenant_id: str, row: dict) -> list:
    import time as _time
    sig = _cache_signature(row)
    cached = _tool_cache.get(f"{kind}:{sig}")
    now = _time.time()
    if cached and now - cached[0] < _TOOL_CACHE_TTL:
        return cached[1]

    async with _open_session_for_user(
        user_id, kind=kind, tenant_id=tenant_id, row=row,
    ) as session:
        result = await session.list_tools()
    tools = list(result.tools)
    _tool_cache[f"{kind}:{sig}"] = (now, tools)
    return tools


async def discover_tools(subject: str, *, tenant_id: str) -> dict[ProviderKind, list]:
    """For each provider the user has connected, return its tool manifest."""
    user_id = _resolve_user_id(subject, tenant_id=tenant_id)
    rows = _db().execute("""
        SELECT conn_id, oauth_provider, access_token_enc, refresh_token_enc,
               access_expires_at, mcp_server_url, vendor_workspace_id, scopes
        FROM user_connectors
        WHERE tenant_id = :tid::uuid AND user_id = :uid::uuid AND status = 'active'
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
    ])
    # Data API returns one record per row; convert all.
    out: dict[str, list] = {}
    raw = rows._resp.get("records") or []  # type: ignore[attr-defined]
    meta = rows._resp.get("columnMetadata") or []  # type: ignore[attr-defined]
    import asyncio as _asyncio
    tasks = []
    for rec in raw:
        row = _zip_record(meta, rec)
        kind = row["oauth_provider"]
        tasks.append(_discover_tools_for_user(user_id, kind=kind, tenant_id=tenant_id, row=row))
        out[kind] = []  # placeholder
    results = await _asyncio.gather(*tasks, return_exceptions=True)
    for kind, res in zip(list(out.keys()), results):
        if isinstance(res, Exception):
            print(f"[discover_tools] {kind} failed: {res!r}")
            out[kind] = []
        else:
            out[kind] = res
    return out
