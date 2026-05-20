# platform/lambda/chat_session/tests/test_app.py
"""Tests for the Starlette streaming app (app.py) served under LWA.

These exercise the request-handling path without a DB or Cognito: the no-auth
cases short-circuit before any DB call, so they prove the route is wired and
the SSE error envelope is correct.
"""
from starlette.testclient import TestClient

from app import app

client = TestClient(app)

STREAM_PATH = "/v1/conversations/abc-123/stream"


def test_no_auth_returns_unauthorized_sse():
    """No Authorization header -> SSE error envelope, event-stream media type."""
    r = client.post(STREAM_PATH, json={"text": "hello"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text == 'data: {"error": "unauthorized"}\n\n'


def test_bad_token_returns_unauthorized_sse():
    """A malformed bearer token fails JWT verification -> unauthorized."""
    r = client.post(
        STREAM_PATH,
        json={"text": "hello"},
        headers={"authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 200
    assert r.text == 'data: {"error": "unauthorized"}\n\n'


def test_unknown_route_404():
    """The app exposes exactly one route."""
    assert client.post("/v1/nope").status_code == 404


def test_wrong_method_405():
    """The stream route is POST-only."""
    assert client.get(STREAM_PATH).status_code == 405
