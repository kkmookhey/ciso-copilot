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
    def __init__(self, txn_id: str | None = None):
        self._client = boto3.client("rds-data")
        self._cluster_arn = os.environ["DB_CLUSTER_ARN"]
        self._secret_arn = os.environ["DB_SECRET_ARN"]
        self._database = os.environ.get("DB_NAME", "ciso_copilot")
        self._txn_id = txn_id

    def execute(self, sql: str, parameters: list | None = None):
        kwargs = dict(
            resourceArn=self._cluster_arn,
            secretArn=self._secret_arn,
            database=self._database,
            sql=sql,
            parameters=parameters or [],
            includeResultMetadata=True,
        )
        # When set, every statement runs inside the same Data API transaction,
        # which is what makes pg_advisory_xact_lock actually hold across the
        # subsequent SELECT and UPDATE. Without transactionId the Data API
        # auto-commits each statement and the lock releases instantly.
        if self._txn_id is not None:
            kwargs["transactionId"] = self._txn_id
        resp = self._client.execute_statement(**kwargs)
        return _Result(resp)

    @contextlib.contextmanager
    def transaction(self):
        """Open an explicit Aurora Data API transaction.

        Yields a wrapper whose execute() statements all run under the same
        transactionId, so transaction-scoped Postgres state (advisory locks,
        SAVEPOINTs, etc.) survives across statements. Commits on normal
        exit; rolls back if the body raises.
        """
        txn = self._client.begin_transaction(
            resourceArn=self._cluster_arn,
            secretArn=self._secret_arn,
            database=self._database,
        )
        txn_id = txn["transactionId"]
        txn_db = _DataAPIWrapper.__new__(_DataAPIWrapper)
        txn_db._client = self._client
        txn_db._cluster_arn = self._cluster_arn
        txn_db._secret_arn = self._secret_arn
        txn_db._database = self._database
        txn_db._txn_id = txn_id
        try:
            yield txn_db
        except Exception:
            self._client.rollback_transaction(
                resourceArn=self._cluster_arn,
                secretArn=self._secret_arn,
                transactionId=txn_id,
            )
            raise
        else:
            self._client.commit_transaction(
                resourceArn=self._cluster_arn,
                secretArn=self._secret_arn,
                transactionId=txn_id,
            )


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
    # access_data_key_ct + refresh_data_key_ct are required to decrypt the
    # paired *_token_enc columns (KMS envelope; see crypto.py).
    sql = """
        SELECT conn_id, access_token_enc, access_data_key_ct,
               refresh_token_enc, refresh_data_key_ct,
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


_SSM_PROVIDER_PARAMS = {
    "slack": {
        "SLACK_CLIENT_ID":     "/cisocopilot/connectors/slack/client-id",
        "SLACK_CLIENT_SECRET": "/cisocopilot/connectors/slack/client-secret",
    },
}


def _ensure_provider_secrets(kind: ProviderKind) -> None:
    """Lazy-load SSM SecureString OAuth client creds into os.environ.

    The connectors Lambda eagerly loads these at import time via
    connectors/_secrets.py, but the tools and voice_session Lambdas also
    go through mcp_oauth.session._provider_refresh now (B2 fix) and need
    the same values. CloudFormation can't inject SecureString params as
    Lambda env vars, so each consumer fetches via ssm:GetParameter on
    first refresh. One SSM round-trip per cold start, then cached for the
    life of the execution context.
    """
    needed = _SSM_PROVIDER_PARAMS.get(kind, {})
    missing = [(env, path) for env, path in needed.items() if not os.environ.get(env)]
    if not missing:
        return
    ssm = boto3.client("ssm")
    for env_name, path in missing:
        resp = ssm.get_parameter(Name=path, WithDecryption=True)
        os.environ[env_name] = resp["Parameter"]["Value"]


def _provider_refresh(kind: ProviderKind, refresh_token_plaintext: str) -> dict:
    _ensure_provider_secrets(kind)
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
        return decrypt_token(row["access_token_enc"], row["access_data_key_ct"])

    # Concurrent-refresh race mitigation: Postgres advisory lock keyed by
    # conn_id. Slack and Atlassian use single-use rotating refresh tokens —
    # without this lock, two simultaneous invocations both refresh and one's
    # new token is invalidated server-side (spec §6 "Concurrent-refresh
    # race").
    #
    # CRITICAL: the lock + re-read + refresh + UPDATE must all run inside a
    # single Aurora Data API transaction. pg_advisory_xact_lock is
    # transaction-scoped, so without begin_transaction the Data API
    # auto-commits each statement and the lock releases instantly — making
    # the mitigation a no-op. The provider HTTP call is held inside the
    # transaction too; that's intentional (we need the lock to span the
    # decision to refresh).
    db = _db()
    lock_key = int(hashlib.sha256(str(row["conn_id"]).encode()).hexdigest()[:15], 16)
    with db.transaction() as txn:
        txn.execute("SELECT pg_advisory_xact_lock(:k)", [
            {"name": "k", "value": {"longValue": lock_key}}
        ]).fetchone()

        # Re-read under the lock — another invocation may have already
        # refreshed. Pull the data-key ciphertexts alongside, since they're
        # required to decrypt either token.
        refreshed = txn.execute("""
            SELECT access_token_enc, access_data_key_ct,
                   refresh_token_enc, refresh_data_key_ct,
                   access_expires_at
            FROM user_connectors WHERE conn_id = :cid::uuid
        """, [{"name": "cid", "value": {"stringValue": str(row["conn_id"])}}]).fetchone()

        if not refreshed:
            # Row was deleted between the outer lookup and this re-read
            # (concurrent Disconnect from the web UI). Surface as
            # ConnectorMissingError so the caller returns 409 + an
            # actionable "reconnect in Settings" prompt instead of a
            # generic 500. Raising rolls back the transaction.
            raise ConnectorMissingError(
                f"connector {row['conn_id']} disappeared during refresh"
            )

        re_remaining = _seconds_until(refreshed["access_expires_at"])
        if re_remaining is not None and re_remaining > threshold_seconds:
            # Another invocation already refreshed. Commit the no-op
            # transaction (releases the lock) and return its token.
            return decrypt_token(
                refreshed["access_token_enc"],
                refreshed["access_data_key_ct"],
            )

        # Still expired — actually refresh. The provider HTTP call runs
        # inside the transaction so the lock spans the decision-to-refresh
        # window. Transactions can safely sit open for the ~2s Slack takes.
        now = dt.datetime.now(dt.timezone.utc)
        refresh_plain = decrypt_token(
            refreshed["refresh_token_enc"],
            refreshed["refresh_data_key_ct"],
        )
        new_tokens = _provider_refresh(kind, refresh_plain)
        new_access_enc, new_access_dk = encrypt_token(new_tokens["access_token"])
        new_refresh_enc, new_refresh_dk = encrypt_token(new_tokens["refresh_token"])
        new_expires_at = now + dt.timedelta(seconds=int(new_tokens["expires_in"]))

        txn.execute("""
            UPDATE user_connectors
            SET access_token_enc    = :a,
                access_data_key_ct  = :adk,
                refresh_token_enc   = :r,
                refresh_data_key_ct = :rdk,
                access_expires_at   = CAST(:e AS TIMESTAMPTZ),
                last_used_at        = now()
            WHERE conn_id = :cid::uuid
        """, [
            {"name": "a",   "value": {"blobValue": new_access_enc}},
            {"name": "adk", "value": {"blobValue": new_access_dk}},
            {"name": "r",   "value": {"blobValue": new_refresh_enc}},
            {"name": "rdk", "value": {"blobValue": new_refresh_dk}},
            {"name": "e",   "value": {"stringValue": new_expires_at.isoformat()}},
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
        SELECT conn_id, oauth_provider,
               access_token_enc, access_data_key_ct,
               refresh_token_enc, refresh_data_key_ct,
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
