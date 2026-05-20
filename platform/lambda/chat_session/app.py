"""ASGI streaming app for the chat text turn — served under Lambda Web Adapter.

ONE route: POST /v1/conversations/{id}/stream  body {"text": "..."}

Transport: the Lambda Web Adapter layer proxies the Function URL request to
this uvicorn-served Starlette app on AWS_LWA_PORT. Because the Function URL is
InvokeMode=RESPONSE_STREAM and the Lambda has AWS_LWA_INVOKE_MODE=response_stream,
a Starlette StreamingResponse is flushed to the caller chunk-by-chunk — real
token-by-token Server-Sent-Events.

Auth: the Function URL is AuthType=NONE at the AWS layer, so the JWT is verified
here against the Cognito user-pool JWKS (see messages_stream._verify_jwt).

SSE wire format (the web client depends on this exactly):
  data: {"type":"text-delta","text":"..."}\n\n
  data: {"type":"done"}\n\n
  data: {"error":"..."}\n\n            (on any failure)
"""
from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

import conversations as C
import messages as M
import prompts
from anthropic_call import stream_messages
from messages_stream import _extract_bearer, _resolve_from_claims, _verify_jwt


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _error_stream(code: str):
    """A one-shot generator that emits a single SSE error event."""
    async def gen():
        yield _sse({"error": code})
    return gen()


def _sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            "x-accel-buffering": "no",  # disable any proxy buffering
        },
    )


def _to_anthropic(content: dict) -> str:
    """Extract plain text from a stored message content dict."""
    return content.get("text", "")


async def stream_turn(request: Request) -> StreamingResponse:
    """POST /v1/conversations/{id}/stream — stream one assistant text turn."""
    # --- auth -----------------------------------------------------------
    token  = _extract_bearer(dict(request.headers))
    claims = _verify_jwt(token) if token else None
    if not claims:
        return _sse_response(_error_stream("unauthorized"))

    tenant_id, _user_id = _resolve_from_claims(claims)
    if not tenant_id:
        return _sse_response(_error_stream("tenant_not_found"))

    # --- request -------------------------------------------------------
    cid = request.path_params.get("id")
    try:
        body = json.loads(await request.body() or b"{}")
    except (ValueError, TypeError):
        body = {}

    conv = C.get(tenant_id, cid) if cid else None
    if not conv:
        return _sse_response(_error_stream("not_found"))

    user_text = (body.get("text") or "").strip()
    if not user_text:
        return _sse_response(_error_stream("empty_text"))

    # Persist the incoming user message.
    M.append(cid, "user", {"text": user_text, "modality": "text"})

    # Build history for Anthropic (user + assistant turns from stored messages).
    history = [
        {"role": m["role"], "content": _to_anthropic(m["content"])}
        for m in conv.get("messages", [])
        if m["role"] in ("user", "assistant") and _to_anthropic(m["content"])
    ]
    # Append the new user turn (not yet in conv since we just inserted).
    history.append({"role": "user", "content": user_text})

    # --- stream --------------------------------------------------------
    async def gen():
        assistant_chunks: list[str] = []
        try:
            for ev in stream_messages(prompts.system_for_text(), history):
                if ev["type"] == "text-delta":
                    assistant_chunks.append(ev["text"])
                    yield _sse({"type": "text-delta", "text": ev["text"]})
                elif ev["type"] == "tool-use":
                    yield _sse({
                        "type":  "tool-use",
                        "id":    ev["id"],
                        "name":  ev["name"],
                        "input": ev["input"],
                    })
                elif ev["type"] == "done":
                    yield _sse({"type": "done"})
        except RuntimeError as e:
            print(f"Anthropic stream error: {e}")
            yield _sse({"error": "upstream_failed", "detail": str(e)[:200]})
            # Persist a placeholder assistant message so the conversation
            # history stays consistent — the user message is already stored.
            M.append(cid, "assistant",
                     {"text": "[Error: the assistant could not complete this response]",
                      "modality": "text"})
            return

        # Persist the assembled assistant reply once the stream completes.
        if assistant_chunks:
            M.append(cid, "assistant",
                     {"text": "".join(assistant_chunks), "modality": "text"})

    return _sse_response(gen())


app = Starlette(routes=[
    Route("/v1/conversations/{id}/stream", stream_turn, methods=["POST"]),
])
