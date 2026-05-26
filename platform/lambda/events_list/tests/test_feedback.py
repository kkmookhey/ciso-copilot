import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def test_feedback_writes_to_feedback_table(monkeypatch):
    monkeypatch.setattr(main, "_resolve_tenant_id", lambda e: "t1")
    monkeypatch.setattr(main, "_resolve_user_id",  lambda e: "u1")

    calls = []
    def fake_exec(**kw):
        calls.append(kw["sql"])
        return {"records": []}
    monkeypatch.setattr(main.rds_data, "execute_statement", fake_exec)

    resp = main.handler({
        "resource": "/events/{event_id}/feedback",
        "httpMethod": "POST",
        "pathParameters": {"event_id": "11111111-1111-1111-1111-111111111111"},
        "body": json.dumps({"sentiment": "up", "reason": "useful narrative"}),
    }, None)

    assert resp["statusCode"] == 200
    assert any("INSERT INTO feedback" in s for s in calls)


def test_resolve_user_id_joins_users_on_sso_subject(monkeypatch):
    """The Cognito 'sub' claim is users.sso_subject; we must JOIN to get users.user_id."""
    calls = []
    def fake_exec(**kw):
        calls.append(kw)
        return {"records": [[{"stringValue": "00000000-0000-0000-0000-000000000abc"}]]}
    monkeypatch.setattr(main.rds_data, "execute_statement", fake_exec)

    user_id = main._resolve_user_id({
        "requestContext": {"authorizer": {"claims": {"sub": "cognito-sub-xyz"}}},
    })

    assert user_id == "00000000-0000-0000-0000-000000000abc"
    assert len(calls) == 1
    assert "FROM users" in calls[0]["sql"]
    assert "sso_subject" in calls[0]["sql"]
    assert calls[0]["parameters"][0]["value"]["stringValue"] == "cognito-sub-xyz"


def test_resolve_user_id_returns_none_when_no_sub():
    assert main._resolve_user_id({"requestContext": {"authorizer": {"claims": {}}}}) is None


def test_resolve_user_id_returns_none_when_no_matching_user(monkeypatch):
    monkeypatch.setattr(main.rds_data, "execute_statement",
                        lambda **kw: {"records": []})
    user_id = main._resolve_user_id({
        "requestContext": {"authorizer": {"claims": {"sub": "unknown-sub"}}},
    })
    assert user_id is None
