from __future__ import annotations
import json


def _ev(*, method, path, claims=None, body=None, query=None):
    return {
        "httpMethod": method,
        "rawPath": path,
        "queryStringParameters": query or {},
        "body": json.dumps(body) if body else None,
        "requestContext": {"authorizer": {"claims": claims or {"sub": "u-1"}}},
    }


def test_unknown_route_returns_404():
    from connectors.main import handler

    resp = handler(_ev(method="GET", path="/connectors/something-bad"), None)
    assert resp["statusCode"] == 404


def test_no_auth_returns_401():
    from connectors.main import handler

    ev = _ev(method="GET", path="/connectors/me")
    ev["requestContext"]["authorizer"]["claims"] = {}
    resp = handler(ev, None)
    assert resp["statusCode"] == 401
