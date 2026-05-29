from __future__ import annotations
import json
from unittest.mock import patch


def test_revoke_marks_row_revoked(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    calls = []
    class FakeDB:
        def execute(self, sql, params=None):
            calls.append((sql.strip()[:60], params))
            class R:
                def fetchone(self_inner):
                    if "FROM user_connectors" in sql:
                        return {
                            "tenant_id": "t-uuid",
                            "oauth_provider": "slack",
                            "access_token_enc": b"E:xoxp-A",
                            "mcp_server_url": "https://mcp.slack.com/mcp",
                        }
                    return None
            return R()
    from connectors import handlers_common as h
    monkeypatch.setattr(h, "_db", lambda: FakeDB())
    monkeypatch.setattr(h, "decrypt_token", lambda b: "xoxp-A")
    monkeypatch.setattr(h.requests, "post", lambda *a, **kw: type("R", (), {
        "json": staticmethod(lambda: {"ok": True}),
        "raise_for_status": staticmethod(lambda: None),
    })())

    from connectors import main as m
    ev = {
        "httpMethod": "DELETE",
        "rawPath": "/connectors/00000000-0000-0000-0000-000000000001",
        "requestContext": {"authorizer": {"claims": {
            "sub": "subject-1", "custom:tenant_id": "t-uuid"
        }}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    # First the SELECT, then the UPDATE
    assert any("UPDATE user_connectors" in s for s, _ in calls)


def test_list_me_returns_active_connectors(monkeypatch):
    import datetime as dt
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

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
                "records": [[
                    {"stringValue": "c-1"}, {"stringValue": "slack"},
                    {"stringValue": "U0X"}, {"stringValue": "T0X"},
                    {"stringValue": "active"}, {"stringValue": "2026-05-28T12:00:00+00:00"},
                    {"stringValue": "{chat:write,im:write}"},
                ]]
            }
            return R()
    from connectors import handlers_common as h
    monkeypatch.setattr(h, "_db", lambda: FakeDB())

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/connectors/me",
        "requestContext": {"authorizer": {"claims": {
            "sub": "subject-1", "custom:tenant_id": "t-uuid"
        }}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["connectors"][0]["provider"] == "slack"
    assert body["connectors"][0]["vendor_workspace_id"] == "T0X"
