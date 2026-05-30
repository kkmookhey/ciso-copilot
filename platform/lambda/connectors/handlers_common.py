"""Common handlers: revoke + list."""
from __future__ import annotations
import datetime as dt
import requests
from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth.crypto import decrypt_token
from mcp_oauth.session import _db, _zip_record


_REVOKE_URLS = {
    "slack": "https://slack.com/api/auth.revoke",
    # atlassian/google/microsoft added in their slices
}


@_route("DELETE", r"^/connectors/(?P<conn_id>[0-9a-f-]{36})$")
def revoke_connection(event, claims, params):
    from connectors.handlers_slack import _resolve_user_context
    tenant_id, user_id = _resolve_user_context(claims)
    if not tenant_id or not user_id:
        return _resp(401, {"error": "no_tenant_or_user"})
    conn_id = params["conn_id"]

    # Scope by (tenant_id, user_id, conn_id) — a tenant member must not be
    # able to revoke another member's connector by guessing/leaking the
    # conn_id (intra-tenant IDOR). 404 on miss rather than 403 to avoid
    # confirming the conn_id exists in a sibling user's row.
    db = _db()
    row = db.execute("""
        SELECT tenant_id, oauth_provider, access_token_enc, access_data_key_ct,
               mcp_server_url
        FROM user_connectors
        WHERE conn_id = :cid::uuid AND tenant_id = :tid::uuid
          AND user_id = :uid::uuid AND status = 'active'
    """, [
        {"name": "cid", "value": {"stringValue": conn_id}},
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
    ]).fetchone()
    if not row:
        return _resp(404, {"error": "connector_not_found"})

    kind = row["oauth_provider"]
    revoke_url = _REVOKE_URLS.get(kind)
    if revoke_url:
        try:
            # KMS envelope: decrypt needs both the Fernet ciphertext and
            # the per-row data-key ciphertext.
            access = decrypt_token(row["access_token_enc"], row["access_data_key_ct"])
            r = requests.post(revoke_url, data={"token": access}, timeout=5)
            r.raise_for_status()
        except Exception as e:
            # Vendor revoke failure isn't fatal — we still revoke locally.
            print(f"[connectors] vendor revoke failed: {e}; marking locally")

    db.execute("""
        UPDATE user_connectors
        SET status = 'revoked', revoked_at = now()
        WHERE conn_id = :cid::uuid AND tenant_id = :tid::uuid
          AND user_id = :uid::uuid
    """, [
        {"name": "cid", "value": {"stringValue": conn_id}},
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
    ])

    return _resp(200, {"revoked": True, "conn_id": conn_id})


@_route("GET", r"^/connectors/me$")
def list_me(event, claims, _params):
    from connectors.handlers_slack import _resolve_user_context
    tenant_id, user_id = _resolve_user_context(claims)
    if not tenant_id or not user_id:
        return _resp(200, {"connectors": []})

    db = _db()
    rows = db.execute("""
        SELECT conn_id, oauth_provider, vendor_user_id, vendor_workspace_id,
               status, created_at, scopes
        FROM user_connectors
        WHERE tenant_id = :tid::uuid AND user_id = :uid::uuid
          AND status IN ('active','error','expired')
        ORDER BY created_at DESC
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
    ])

    raw = rows._resp.get("records") or []
    meta = rows._resp.get("columnMetadata") or []
    out = []
    for rec in raw:
        # _zip_record handles isNull + arrayValue correctly; the bare
        # next(iter(cell.values())) pattern decoded NULL cells to Python
        # True and TEXT[] cells to raw {'stringValues': [...]} dicts.
        row = _zip_record(meta, rec)
        out.append({
            "conn_id": str(row["conn_id"]),
            "provider": row["oauth_provider"],
            "vendor_user_id": row["vendor_user_id"],
            "vendor_workspace_id": row.get("vendor_workspace_id"),
            "status": row["status"],
            "created_at": str(row["created_at"]),
            "scopes": row.get("scopes") or [],
        })
    return _resp(200, {"connectors": out})
