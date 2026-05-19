# platform/lambda/chat_session/main.py
"""chat_session Lambda — REST router.

Routes (API Gateway REST):
  POST   /v1/conversations
  GET    /v1/conversations
  GET    /v1/conversations/{id}
  PATCH  /v1/conversations/{id}
  DELETE /v1/conversations/{id}
  POST   /v1/conversations/{id}/messages
  POST   /v1/conversations/{id}/voice

The streaming text route POST /v1/conversations/{id}/stream is served
by the SAME deployment artifact but invoked through a Lambda Function
URL — see stream_handler in messages_stream.py (Task 4a.6).
"""
from __future__ import annotations

import json

import conversations as C
import messages as M
from _db import _resp, _resolve_user_context


def _body(event: dict) -> dict:
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def handler(event: dict, context) -> dict:
    email, tenant_id, user_id = _resolve_user_context(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})

    method = event.get("httpMethod", "GET")
    path = event.get("path") or ""
    cid = (event.get("pathParameters") or {}).get("id")

    if method == "POST" and path.endswith("/voice") and cid:
        import voice
        return voice.mint(event, tenant_id, cid)
    if method == "POST" and path.endswith("/messages") and cid:
        body = _body(event)
        if not C.get(tenant_id, cid):
            return _resp(404, {"error": "not_found"})
        out = M.append(cid, body.get("role", "user"), body.get("content", {}))
        return _resp(200, out)
    if method == "POST" and path.rstrip("/").endswith("/conversations"):
        return _resp(200, C.create(tenant_id, user_id))
    if method == "GET" and path.rstrip("/").endswith("/conversations"):
        return _resp(200, C.list_for(tenant_id, user_id))
    if method == "GET" and cid:
        conv = C.get(tenant_id, cid)
        return _resp(200, conv) if conv else _resp(404, {"error": "not_found"})
    if method == "PATCH" and cid:
        ok = C.patch_title(tenant_id, cid, _body(event).get("title", ""))
        return _resp(200, {"ok": True}) if ok else _resp(404, {"error": "not_found"})
    if method == "DELETE" and cid:
        ok = C.soft_delete(tenant_id, cid)
        return _resp(200, {"ok": True}) if ok else _resp(404, {"error": "not_found"})
    return _resp(400, {"error": "unsupported"})
