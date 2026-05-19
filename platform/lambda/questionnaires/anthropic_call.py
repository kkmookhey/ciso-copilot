"""Small Anthropic Messages API client (stdlib-only — no SDK dependency).

Reads ciso-copilot/anthropic-api-key on cold start and caches it. One public
function: `call(system, messages, max_tokens)`.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

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


def call(system: str, user_message: str, max_tokens: int = 2048) -> str:
    """Single-turn call to Claude. Returns the model's text output."""
    body = json.dumps({
        "model":      MODEL,
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
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise RuntimeError(f"Anthropic HTTP {e.code}: {detail}")

    # Concatenate text blocks
    blocks = data.get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
