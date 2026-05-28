# platform/lambda/tools/tests/test_tools.py
import json

from tools.main import handler


def _event(tool_name: str, body: dict) -> dict:
    return {
        "requestContext": {"authorizer": {"claims": {"sub": "test-user", "email": "kk@x.io"}}},
        "pathParameters": {"tool_name": tool_name},
        "body": json.dumps(body),
    }


def test_unknown_tool_returns_404():
    resp = handler(_event("nonexistent_tool", {}), None)
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "unknown_tool"


def test_missing_body_is_handled():
    resp = handler({
        "requestContext": {"authorizer": {"claims": {"sub": "test-user"}}},
        "pathParameters": {"tool_name": "revoke_oauth_grant"},
    }, None)
    # Either 400 (no body) or 401 (no tenant) — should NOT 500.
    assert resp["statusCode"] in (400, 401)
