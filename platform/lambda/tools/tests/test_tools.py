# platform/lambda/tools/tests/test_tools.py
import json

from tools.main import handler, subject_from_claims


def _event(tool_name: str, body: dict, *, sub: str = "test-user") -> dict:
    return {
        "requestContext": {"authorizer": {"claims": {"sub": sub, "email": "kk@x.io"}}},
        "pathParameters": {"tool_name": tool_name},
        "body": json.dumps(body),
    }


def test_unknown_tool_returns_404():
    resp = handler(_event("nonexistent_tool", {}), None)
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "unknown_tool"


def test_missing_body_returns_400():
    # Body check is intentionally before the auth check.
    resp = handler({
        "requestContext": {"authorizer": {"claims": {"sub": "test-user"}}},
        "pathParameters": {"tool_name": "revoke_oauth_grant"},
    }, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "missing_body"


def test_invalid_json_body_returns_400():
    resp = handler({
        "requestContext": {"authorizer": {"claims": {"sub": "test-user"}}},
        "pathParameters": {"tool_name": "revoke_oauth_grant"},
        "body": "not-json{",
    }, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "invalid_json"


def test_missing_auth_returns_401():
    resp = handler({
        "requestContext": {"authorizer": {"claims": {}}},
        "pathParameters": {"tool_name": "revoke_oauth_grant"},
        "body": json.dumps({}),
    }, None)
    assert resp["statusCode"] == 401
    assert json.loads(resp["body"])["error"] == "no_auth"


def test_tool_raises_returns_500_with_detail():
    # revoke_oauth_grant raises KeyError on missing args — confirm the
    # dispatcher converts that to a 500 with the exception detail.
    resp = handler(_event("revoke_oauth_grant", {}), None)
    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body["error"] == "tool_failed"
    assert body["tool"] == "revoke_oauth_grant"
    assert "detail" in body


class TestSubjectFromClaims:
    def test_falls_back_to_sub_when_no_identities(self):
        assert subject_from_claims({"sub": "abc"}) == "abc"

    def test_returns_none_when_empty(self):
        assert subject_from_claims({}) is None

    def test_uses_identities_userId_over_sub(self):
        claims = {
            "sub": "cognito-sub",
            "identities": [{"userId": "upstream-id", "providerName": "Microsoft"}],
        }
        assert subject_from_claims(claims) == "upstream-id"

    def test_parses_identities_json_string(self):
        # API Gateway sometimes serializes the identities claim as a JSON string.
        claims = {
            "sub": "cognito-sub",
            "identities": '[{"userId": "upstream-id", "providerName": "Google"}]',
        }
        assert subject_from_claims(claims) == "upstream-id"


def test_namespaced_mcp_tool_dispatched_via_mcp_oauth(monkeypatch):
    import contextlib
    import json
    from unittest.mock import AsyncMock, MagicMock

    # Federated (Microsoft) user — Cognito JWT carries identities[0].userId
    # but NOT custom:tenant_id. The handler must resolve tenant_id by
    # joining users.sso_subject. Prior code read claims.get("custom:tenant_id")
    # and 400'd every federated tool call.
    ev = {
        "pathParameters": {"tool_name": "slack__send_message"},
        "body": json.dumps({"channel": "C0X", "text": "hi"}),
        "requestContext": {"authorizer": {"claims": {
            "sub": "cognito-pool-sub",
            "identities": [{"userId": "upstream-ms-sub",
                            "providerName": "Microsoft"}],
        }}},
    }

    fake_session = AsyncMock()
    fake_session.call_tool.return_value = MagicMock(
        content=[MagicMock(text=json.dumps({"ok": True, "ts": "1.0"}))]
    )

    @contextlib.asynccontextmanager
    async def fake_get_session(*a, **kw):
        # Capture args to assert the resolved tenant_id is passed through.
        fake_get_session.calls.append((a, kw))
        yield fake_session
    fake_get_session.calls = []

    # Stub the Aurora Data API lookup that _resolve_tenant_id performs.
    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = {"tenant_id": "t-uuid"}
    monkeypatch.setattr("mcp_oauth.session._db", lambda: fake_db)
    monkeypatch.setattr("mcp_oauth.get_session", fake_get_session)

    from tools.main import handler
    resp = handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ok"] is True
    # Subject passed downstream must be the upstream IdP sub, not the
    # Cognito pool sub — otherwise mcp_oauth.session can't find the user.
    # _call_mcp_tool invokes get_session(subject, kind, tenant_id=...).
    assert fake_get_session.calls
    args, kwargs = fake_get_session.calls[0]
    assert args[0] == "upstream-ms-sub"
    assert args[1] == "slack"
    assert kwargs.get("tenant_id") == "t-uuid"


def test_namespaced_mcp_returns_400_when_user_not_provisioned(monkeypatch):
    """sso_subject doesn't resolve to any users row (e.g., approval pending).
    Must return 400 missing_tenant_id, not 500."""
    import json
    from unittest.mock import MagicMock

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr("mcp_oauth.session._db", lambda: fake_db)

    ev = {
        "pathParameters": {"tool_name": "slack__send_message"},
        "body": json.dumps({"channel": "C0X", "text": "hi"}),
        "requestContext": {"authorizer": {"claims": {"sub": "unknown-user"}}},
    }
    from tools.main import handler
    resp = handler(ev, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "missing_tenant_id"
