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
