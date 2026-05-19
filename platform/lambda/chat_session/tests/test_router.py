# platform/lambda/chat_session/tests/test_router.py
"""Router-level tests for main.handler.

Resolution is always monkeypatched — no DB or AWS needed.
"""
import json

import main
import conversations as C
import messages as M


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolved(email="u@t.com", tenant_id="tenant-1", user_id="user-1"):
    """Return a _resolve_user_context replacement that yields fixed values."""
    return lambda event: (email, tenant_id, user_id)


def _no_tenant():
    """Return a _resolve_user_context replacement that yields no tenant."""
    return lambda event: (None, None, None)


def _evt(method, path, path_params=None, body=None):
    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params or {},
        "body": json.dumps(body) if body is not None else None,
    }


# ---------------------------------------------------------------------------
# Auth / tenant guard
# ---------------------------------------------------------------------------

def test_no_tenant_401(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _no_tenant())
    r = main.handler(_evt("GET", "/v1/conversations"), None)
    assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# Unknown / unsupported routes
# ---------------------------------------------------------------------------

def test_unknown_route_400(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    r = main.handler(_evt("GET", "/v1/nonsense"), None)
    assert r["statusCode"] == 400


def test_delete_without_id_400(monkeypatch):
    """DELETE /v1/conversations (no {id}) must return 400, not crash."""
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    r = main.handler(_evt("DELETE", "/v1/conversations"), None)
    assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# POST /v1/conversations  →  C.create
# ---------------------------------------------------------------------------

def test_post_conversations_returns_200(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "create", lambda tid, uid: {"conversation_id": "new-cid"})
    r = main.handler(_evt("POST", "/v1/conversations"), None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["conversation_id"] == "new-cid"


# ---------------------------------------------------------------------------
# GET /v1/conversations  →  C.list_for
# ---------------------------------------------------------------------------

def test_get_conversations_returns_200(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "list_for", lambda tid, uid: {"conversations": []})
    r = main.handler(_evt("GET", "/v1/conversations"), None)
    assert r["statusCode"] == 200
    assert "conversations" in json.loads(r["body"])


# ---------------------------------------------------------------------------
# GET /v1/conversations/{id}  →  C.get
# ---------------------------------------------------------------------------

def test_get_conversation_by_id_found(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "get", lambda tid, cid: {"id": cid, "title": "t", "messages": []})
    r = main.handler(_evt("GET", "/v1/conversations/abc", path_params={"id": "abc"}), None)
    assert r["statusCode"] == 200


def test_get_conversation_by_id_not_found(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "get", lambda tid, cid: None)
    r = main.handler(_evt("GET", "/v1/conversations/abc", path_params={"id": "abc"}), None)
    assert r["statusCode"] == 404


# ---------------------------------------------------------------------------
# PATCH /v1/conversations/{id}  →  C.patch_title
# ---------------------------------------------------------------------------

def test_patch_title_ok(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "patch_title", lambda tid, cid, t: True)
    r = main.handler(_evt("PATCH", "/v1/conversations/abc",
                          path_params={"id": "abc"},
                          body={"title": "New title"}), None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["ok"] is True


def test_patch_title_not_found(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "patch_title", lambda tid, cid, t: False)
    r = main.handler(_evt("PATCH", "/v1/conversations/abc",
                          path_params={"id": "abc"},
                          body={"title": "x"}), None)
    assert r["statusCode"] == 404


# ---------------------------------------------------------------------------
# DELETE /v1/conversations/{id}  →  C.soft_delete
# ---------------------------------------------------------------------------

def test_delete_conversation_ok(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "soft_delete", lambda tid, cid: True)
    r = main.handler(_evt("DELETE", "/v1/conversations/abc", path_params={"id": "abc"}), None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["ok"] is True


def test_delete_conversation_not_found(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "soft_delete", lambda tid, cid: False)
    r = main.handler(_evt("DELETE", "/v1/conversations/abc", path_params={"id": "abc"}), None)
    assert r["statusCode"] == 404


# ---------------------------------------------------------------------------
# POST /v1/conversations/{id}/messages  →  M.append
# ---------------------------------------------------------------------------

def test_post_messages_ok(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "get", lambda tid, cid: {"id": cid, "title": "t", "messages": []})
    monkeypatch.setattr(M, "append", lambda cid, role, content: {"message_id": "mid-1"})
    r = main.handler(
        _evt("POST", "/v1/conversations/abc/messages",
             path_params={"id": "abc"},
             body={"role": "user", "content": {"text": "hello"}}),
        None,
    )
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["message_id"] == "mid-1"


def test_post_messages_conversation_not_found(monkeypatch):
    monkeypatch.setattr(main, "_resolve_user_context", _resolved())
    monkeypatch.setattr(C, "get", lambda tid, cid: None)
    r = main.handler(
        _evt("POST", "/v1/conversations/abc/messages",
             path_params={"id": "abc"},
             body={"role": "user", "content": {}}),
        None,
    )
    assert r["statusCode"] == 404
