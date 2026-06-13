"""Small Anthropic Messages API client (stdlib-only — no SDK dependency).

Reads ciso-copilot/anthropic-api-key on cold start and caches it. Two public
functions:
  call(system, user_message, max_tokens, model, timeout) — non-streaming, single-turn
  stream_messages(system, messages, tools)               — streaming generator (SSE)
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Generator

import boto3

ANTHROPIC_SECRET_NAME = os.environ.get("ANTHROPIC_SECRET_NAME", "ciso-copilot/anthropic-api-key")
MODEL                 = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_URL         = "https://api.anthropic.com/v1/messages"

_sm     = boto3.client("secretsmanager")
_key:   str | None = None


def _api_key() -> str:
    global _key
    if _key is None:
        _key = _sm.get_secret_value(SecretId=ANTHROPIC_SECRET_NAME)["SecretString"].strip()
    return _key


def call(
    system: str,
    user_message: str,
    max_tokens: int = 2048,
    model: str | None = None,
    timeout: int = 45,
) -> str:
    """Single-turn call to Claude. Returns the model's text output.

    `model` defaults to the module-level MODEL env (Sonnet today); callers
    can override per-call (the auto-titler uses Haiku).
    `timeout` is forwarded to urlopen — auto-titler uses 5s.
    """
    body = json.dumps({
        "model":      model or MODEL,
        "max_tokens": max_tokens,
        "system":     system,
        "messages":   [{"role": "user", "content": user_message}],
    }).encode()

    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={
            "x-api-key":         _api_key(),
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise RuntimeError(f"Anthropic HTTP {e.code}: {detail}")

    blocks = data.get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def stream_messages(
    system: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 4096,
) -> Generator[dict, None, None]:
    """Streaming multi-turn call to Claude via SSE.

    Yields normalised event dicts:
      {"type": "text-delta", "text": "<chunk>"}
      {"type": "tool-use",   "id": ..., "name": ..., "input": ...}
      {"type": "done"}

    Uses urllib + "stream": true — no SDK dependency. Reads the response body
    line-by-line via readline(); Anthropic SSE emits paired `event:` + `data:`
    lines.

    Raises RuntimeError on non-2xx HTTP responses.
    """
    payload: dict = {
        "model":      MODEL,
        "max_tokens": max_tokens,
        "system":     system,
        "messages":   messages,
        "stream":     True,
    }
    if tools:
        payload["tools"] = tools

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={
            "x-api-key":         _api_key(),
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise RuntimeError(f"Anthropic HTTP {e.code}: {detail}")

    # Track tool_use blocks being assembled (input arrives as JSON delta chunks)
    _tool_blocks: dict[int, dict] = {}  # index -> partial block

    try:
        # NOTE: Assumes Anthropic `data:` payloads contain no embedded raw
        # newlines — a literal \n inside a JSON string value would be mis-split
        # by readline() into two lines. Anthropic escapes newlines as \n in
        # JSON, so this holds; revisit if the protocol ever changes.
        for raw_line in resp:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data_str = line[len("data:"):].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    ev = json.loads(data_str)
                except (ValueError, TypeError):
                    continue

                ev_type = ev.get("type")

                if ev_type == "content_block_start":
                    block = ev.get("content_block") or {}
                    idx = ev.get("index", 0)
                    if block.get("type") == "tool_use":
                        _tool_blocks[idx] = {
                            "id":    block.get("id"),
                            "name":  block.get("name"),
                            "_input_chunks": [],
                        }

                elif ev_type == "content_block_delta":
                    delta = ev.get("delta") or {}
                    idx   = ev.get("index", 0)
                    dtype = delta.get("type")

                    if dtype == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield {"type": "text-delta", "text": text}

                    elif dtype == "input_json_delta":
                        # Accumulate partial JSON for tool input
                        partial = delta.get("partial_json", "")
                        if idx in _tool_blocks:
                            _tool_blocks[idx]["_input_chunks"].append(partial)

                elif ev_type == "content_block_stop":
                    idx = ev.get("index", 0)
                    if idx in _tool_blocks:
                        block = _tool_blocks.pop(idx)
                        raw_input = "".join(block["_input_chunks"])
                        try:
                            parsed_input = json.loads(raw_input) if raw_input else {}
                        except (ValueError, TypeError):
                            parsed_input = {"_raw": raw_input}
                        yield {
                            "type":  "tool-use",
                            "id":    block["id"],
                            "name":  block["name"],
                            "input": parsed_input,
                        }

                elif ev_type == "message_stop":
                    yield {"type": "done"}
                    return

    finally:
        resp.close()

    # Emit done if message_stop was never received (shouldn't happen but be safe)
    yield {"type": "done"}
