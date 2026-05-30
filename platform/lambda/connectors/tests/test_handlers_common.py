from __future__ import annotations
import json
from unittest.mock import patch


def test_revoke_marks_row_revoked(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    calls = []
    class FakeDB:
        def execute(self, sql, params=None):
            # Keep full SQL so assertions can scan for FROM/UPDATE clauses
            # regardless of their offset.
            calls.append((sql, params))
            class R:
                def fetchone(self_inner):
                    if "FROM user_connectors" in sql:
                        return {
                            "tenant_id": "t-uuid",
                            "oauth_provider": "slack",
                            "access_token_enc": b"E:xoxp-A",
                            "access_data_key_ct": b"DK:xoxp-A",
                            "mcp_server_url": "https://mcp.slack.com/mcp",
                        }
                    return None
            return R()
    from connectors import handlers_common as h
    from connectors import handlers_slack as hs
    monkeypatch.setattr(h, "_db", lambda: FakeDB())
    # decrypt_token takes (ciphertext, data_key_ciphertext) — KMS envelope.
    monkeypatch.setattr(h, "decrypt_token", lambda ct, dk: "xoxp-A")
    monkeypatch.setattr(hs, "_resolve_user_context",
                        lambda claims: ("t-uuid", "u-uuid"))
    monkeypatch.setattr(h.requests, "post", lambda *a, **kw: type("R", (), {
        "json": staticmethod(lambda: {"ok": True}),
        "raise_for_status": staticmethod(lambda: None),
    })())

    from connectors import main as m
    ev = {
        "httpMethod": "DELETE",
        "rawPath": "/connectors/00000000-0000-0000-0000-000000000001",
        "requestContext": {"authorizer": {"claims": {"sub": "subject-1"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    assert any("UPDATE user_connectors" in s for s, _ in calls)
    # SELECT and UPDATE must both scope by user_id (intra-tenant IDOR fix).
    select_calls = [(s, p) for s, p in calls if "FROM user_connectors" in s]
    update_calls = [(s, p) for s, p in calls if "UPDATE user_connectors" in s]
    assert select_calls, "expected a SELECT call"
    assert update_calls, "expected an UPDATE call"
    for _, params in select_calls + update_calls:
        names = {p["name"] for p in params}
        assert "uid" in names, f"missing user_id bind, got {names}"


def test_revoke_blocks_other_users_conn_id(monkeypatch):
    """Caller is user A in tenant T; conn_id belongs to user B in tenant T.
    The SELECT must miss because of the user_id filter, returning 404."""
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    class FakeDB:
        def execute(self, sql, params=None):
            assert "user_id = :uid::uuid" in sql, "user_id filter missing"
            class R:
                def fetchone(self_inner):
                    return None
            return R()
    from connectors import handlers_common as h
    from connectors import handlers_slack as hs
    monkeypatch.setattr(h, "_db", lambda: FakeDB())
    monkeypatch.setattr(hs, "_resolve_user_context",
                        lambda claims: ("t-uuid", "user-A"))

    from connectors import main as m
    ev = {
        "httpMethod": "DELETE",
        # conn_id belongs to user-B in same tenant.
        "rawPath": "/connectors/00000000-0000-0000-0000-0000000000bb",
        "requestContext": {"authorizer": {"claims": {"sub": "subject-A"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 404
    body = json.loads(resp["body"])
    assert body["error"] == "connector_not_found"


def test_list_me_returns_active_connectors(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    # Mock Aurora Data API's actual response shape: TEXT[] arrives as
    # {"arrayValue": {"stringValues": [...]}} and NULL as {"isNull": True}.
    # The old test used {"stringValue": "{a,b,c}"} which is the wire shape
    # for a string-cast literal, not what the SELECT returns.
    class FakeDB:
        def execute(self, sql, params=None):
            class R:
                def fetchone(self_inner): return {"user_id": "u-1"}
            R._resp = {
                "columnMetadata": [
                    {"name": "conn_id"}, {"name": "oauth_provider"},
                    {"name": "vendor_user_id"}, {"name": "vendor_workspace_id"},
                    {"name": "status"}, {"name": "created_at"}, {"name": "scopes"},
                ],
                "records": [
                    [
                        {"stringValue": "c-1"}, {"stringValue": "slack"},
                        {"stringValue": "U0X"}, {"stringValue": "T0X"},
                        {"stringValue": "active"},
                        {"stringValue": "2026-05-28T12:00:00+00:00"},
                        {"arrayValue": {"stringValues": ["chat:write", "im:write"]}},
                    ],
                    # Second row exercises NULL vendor_workspace_id.
                    [
                        {"stringValue": "c-2"}, {"stringValue": "slack"},
                        {"stringValue": "U0Y"}, {"isNull": True},
                        {"stringValue": "active"},
                        {"stringValue": "2026-05-27T12:00:00+00:00"},
                        {"arrayValue": {"stringValues": ["chat:write"]}},
                    ],
                ],
            }
            return R()
    from connectors import handlers_common as h
    from connectors import handlers_slack as hs
    monkeypatch.setattr(h, "_db", lambda: FakeDB())
    monkeypatch.setattr(hs, "_resolve_user_context",
                        lambda claims: ("t-uuid", "u-1"))

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/me",
        "requestContext": {"authorizer": {"claims": {"sub": "subject-1"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    rows = body["connectors"]
    assert rows[0]["provider"] == "slack"
    assert rows[0]["vendor_workspace_id"] == "T0X"
    assert rows[0]["scopes"] == ["chat:write", "im:write"]
    # NULL workspace must decode to None, not True; scopes must be a real
    # list, not a mangled dict-repr string. Both were broken pre-fix.
    assert rows[1]["vendor_workspace_id"] is None
    assert rows[1]["scopes"] == ["chat:write"]
