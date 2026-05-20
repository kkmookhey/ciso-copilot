"""ASGI streaming app for the chat text turn — served under Lambda Web Adapter.

ONE route: POST /v1/conversations/{id}/stream  body {"text": "..."}

Transport: the Lambda Web Adapter layer proxies the Function URL request to
this uvicorn-served Starlette app on AWS_LWA_PORT. Because the Function URL is
InvokeMode=RESPONSE_STREAM and the Lambda has AWS_LWA_INVOKE_MODE=response_stream,
a Starlette StreamingResponse is flushed to the caller chunk-by-chunk — real
token-by-token Server-Sent-Events.

Auth: the Function URL is AuthType=NONE at the AWS layer, so the JWT is verified
here against the Cognito user-pool JWKS (see messages_stream._verify_jwt).

The Anthropic tool-use loop runs ENTIRELY server-side here (SP4 Task 4b.3) — no
browser round-trips. Each round:
  1. stream Anthropic WITH tool definitions
  2. if a tool_use block is emitted, execute it server-side (tenant-scoped,
     against Aurora), append a tool_result, and call Anthropic again
  3. repeat until Anthropic finishes without requesting a tool (max 6 rounds)
Throughout, text deltas + tool-result events are streamed to the browser, and
every turn (user / tool / assistant) is persisted to conversation_messages.

SSE wire format (the web client depends on this exactly):
  data: {"type":"text-delta","text":"..."}\n\n
  data: {"type":"tool-result","tool_name":"...","artifact_hint":{...}}\n\n
  data: {"type":"tool-result","tool_name":"...","artifact_hints":[...]}\n\n
  data: {"type":"tool-result","tool_name":"navigate_to","side_effect":{...}}\n\n
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
import tools_dispatch
from anthropic_call import stream_messages
from messages_stream import _extract_bearer, _resolve_from_claims, _verify_jwt

# Cap on agentic loop rounds — prevents a runaway chain of Anthropic calls.
MAX_TOOL_ROUNDS = 6


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


def _history_for_anthropic(messages: list[dict]) -> list[dict]:
    """Build the Anthropic message history from stored conversation messages.

    Only user + assistant text turns are replayed — stored `tool` messages are
    UI artifacts, not Anthropic protocol turns (the tool_use/tool_result blocks
    that produced them are not persisted, so they cannot be safely replayed).
    The model re-derives tool calls fresh each conversation turn.
    """
    out = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = (m.get("content") or {}).get("text", "")
        if text:
            out.append({"role": role, "content": text})
    return out


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
    history = _history_for_anthropic(conv.get("messages", []))
    history.append({"role": "user", "content": user_text})

    # --- agentic stream ------------------------------------------------
    async def gen():
        # `messages` is the live Anthropic message list — it grows as the loop
        # appends assistant tool_use turns and user tool_result turns.
        messages: list[dict] = list(history)
        final_assistant_text = ""
        system = prompts.system_for_text()
        tool_defs = tools_dispatch.anthropic_tool_defs()

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                round_text = ""
                tool_uses: list[dict] = []  # {id, name, input}

                for ev in stream_messages(system, messages, tools=tool_defs):
                    etype = ev.get("type")
                    if etype == "text-delta":
                        round_text += ev["text"]
                        yield _sse({"type": "text-delta", "text": ev["text"]})
                    elif etype == "tool-use":
                        tool_uses.append({
                            "id":    ev["id"],
                            "name":  ev["name"],
                            "input": ev.get("input") or {},
                        })
                    # "done" — round's stream finished; fall through below

                final_assistant_text += round_text

                # No tool requested this round → the assistant is finished.
                if not tool_uses:
                    break

                # Rebuild the assistant turn with ALL content blocks (text +
                # tool_use) — the Anthropic protocol requires the full block
                # list on the assistant message that the tool_results answer.
                assistant_blocks: list[dict] = []
                if round_text:
                    assistant_blocks.append({"type": "text", "text": round_text})
                for tu in tool_uses:
                    assistant_blocks.append({
                        "type":  "tool_use",
                        "id":    tu["id"],
                        "name":  tu["name"],
                        "input": tu["input"],
                    })
                messages.append({"role": "assistant", "content": assistant_blocks})

                # Execute each tool server-side, stream a tool-result event,
                # persist a `tool` conversation_message, and collect the
                # tool_result blocks for the next Anthropic call.
                tool_result_blocks: list[dict] = []
                for tu in tool_uses:
                    name, args = tu["name"], tu["input"]
                    try:
                        out = tools_dispatch.dispatch(name, tenant_id, args)
                    except KeyError as e:
                        out = {"result": {"error": str(e)}}
                    except Exception as e:  # noqa: BLE001
                        print(f"tool {name} failed: {e}")
                        out = {"result": {"error": f"tool_failed: {str(e)[:200]}"}}

                    result = out.get("result")
                    artifact_hint  = out.get("_artifact_hint")
                    artifact_hints = out.get("_artifact_hints")
                    source = out.get("source")

                    # Stream a tool-result SSE event for the browser renderer.
                    sse_ev: dict = {"type": "tool-result", "tool_name": name}
                    if artifact_hints is not None:
                        sse_ev["artifact_hints"] = artifact_hints
                    if artifact_hint is not None:
                        sse_ev["artifact_hint"] = artifact_hint
                    if source is not None:
                        sse_ev["source"] = source
                    # Side-effect tools (navigate_to / filter_findings_view)
                    # carry no artifact — surface the intent for the browser.
                    if artifact_hint is None and artifact_hints is None \
                            and isinstance(result, dict):
                        sse_ev["side_effect"] = result
                    yield _sse(sse_ev)

                    # Persist a `tool` conversation_message so a reload
                    # reconstitutes the artifact card.
                    M.append(cid, "tool", {
                        "tool_name":       name,
                        "args":            args,
                        "result":          result,
                        "_artifact_hint":  artifact_hint,
                        "_artifact_hints": artifact_hints,
                        "source":          source,
                    })

                    # The tool_result block sent back to Anthropic carries the
                    # JSON result so the model can reason over real data.
                    tool_result_blocks.append({
                        "type":        "tool_result",
                        "tool_use_id": tu["id"],
                        "content":     json.dumps(result, default=str),
                    })

                messages.append({"role": "user", "content": tool_result_blocks})
                # Loop continues — Anthropic is called again with the results.
            else:
                # Loop exhausted MAX_TOOL_ROUNDS without a tool-free finish.
                print(f"agentic loop hit MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS}")

            yield _sse({"type": "done"})

        except Exception as e:  # noqa: BLE001
            print(f"Anthropic stream error: {e}")
            yield _sse({"error": "upstream_failed", "detail": str(e)[:200]})
            # Persist a placeholder assistant message so the conversation
            # history stays consistent — the user message is already stored.
            M.append(cid, "assistant",
                     {"text": "[Error: the assistant could not complete this response]",
                      "modality": "text"})
            return

        # Persist the assembled assistant reply once the loop completes.
        if final_assistant_text:
            M.append(cid, "assistant",
                     {"text": final_assistant_text, "modality": "text"})

    return _sse_response(gen())


app = Starlette(routes=[
    Route("/v1/conversations/{id}/stream", stream_turn, methods=["POST"]),
])
