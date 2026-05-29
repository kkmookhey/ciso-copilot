"""Common handlers: revoke + list."""
from __future__ import annotations
import datetime as dt
import requests
from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth.crypto import decrypt_token
from mcp_oauth.session import _db


_REVOKE_URLS = {
    "slack": "https://slack.com/api/auth.revoke",
    # atlassian/google/microsoft added in their slices
}


@_route("DELETE", r"^/v1/connectors/(?P<conn_id>[0-9a-f-]{36})$")
def revoke_connection(event, claims, params):
    tenant_id = claims.get("custom:tenant_id")
    if not tenant_id:
        return _resp(400, {"error": "missing_tenant_id"})
    conn_id = params["conn_id"]

    db = _db()
    row = db.execute("""
        SELECT tenant_id, oauth_provider, access_token_enc, mcp_server_url
        FROM user_connectors
        WHERE conn_id = :cid AND tenant_id = :tid AND status = 'active'
    """, [
        {"name": "cid", "value": {"stringValue": conn_id}},
        {"name": "tid", "value": {"stringValue": tenant_id}},
    ]).fetchone()
    if not row:
        return _resp(404, {"error": "connector_not_found"})

    kind = row["oauth_provider"]
    revoke_url = _REVOKE_URLS.get(kind)
    if revoke_url:
        try:
            access = decrypt_token(row["access_token_enc"])
            r = requests.post(revoke_url, data={"token": access}, timeout=5)
            r.raise_for_status()
        except Exception as e:
            # Vendor revoke failure isn't fatal — we still revoke locally.
            print(f"[connectors] vendor revoke failed: {e}; marking locally")

    db.execute("""
        UPDATE user_connectors
        SET status = 'revoked', revoked_at = now()
        WHERE conn_id = :cid
    """, [{"name": "cid", "value": {"stringValue": conn_id}}])

    return _resp(200, {"revoked": True, "conn_id": conn_id})


@_route("GET", r"^/v1/connectors/me$")
def list_me(event, claims, _params):
    tenant_id = claims.get("custom:tenant_id")
    subject = subject_from_claims(claims)
    if not tenant_id:
        return _resp(400, {"error": "missing_tenant_id"})

    db = _db()
    u = db.execute("""
        SELECT user_id FROM users
        WHERE tenant_id = :tid AND sso_subject = :sub
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "sub", "value": {"stringValue": subject}},
    ]).fetchone()
    if not u:
        return _resp(200, {"connectors": []})

    rows = db.execute("""
        SELECT conn_id, oauth_provider, vendor_user_id, vendor_workspace_id,
               status, created_at, scopes
        FROM user_connectors
        WHERE tenant_id = :tid AND user_id = :uid
          AND status IN ('active','error','expired')
        ORDER BY created_at DESC
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": str(u["user_id"])}},
    ])

    raw = rows._resp.get("records") or []
    meta = rows._resp.get("columnMetadata") or []
    out = []
    for rec in raw:
        from connectors.main import _resp as _  # noqa
        row = {col["name"]: next(iter(cell.values())) for col, cell in zip(meta, rec)}
        out.append({
            "conn_id": str(row["conn_id"]),
            "provider": row["oauth_provider"],
            "vendor_user_id": row["vendor_user_id"],
            "vendor_workspace_id": row.get("vendor_workspace_id"),
            "status": row["status"],
            "created_at": str(row["created_at"]),
            "scopes": str(row.get("scopes") or "").strip("{}").split(",") if row.get("scopes") else [],
        })
    return _resp(200, {"connectors": out})
