# platform/lambda/chat_session/voice.py
"""Voice ephemeral-key mint for chat_session Lambda.

Ported from platform/lambda/voice_session/main.py.

mint() is called by the main.py router for:
  POST /v1/conversations/{id}/voice

It mints an OpenAI Realtime ephemeral key and binds conversation_id into
the session metadata. Tools are intentionally empty here — the full
catalog + persona land in Phase 4c (Task 4c.1).
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

import boto3

from _db import _resp

OPENAI_SECRET_NAME = os.environ.get("OPENAI_SECRET_NAME", "ciso-copilot/openai-api-key")

sm = boto3.client("secretsmanager")

# Module-level cache (Lambda container reuse).
_openai_key: str | None = None


def mint(event: dict, tenant_id: str, conversation_id: str) -> dict:
    """Mint an OpenAI Realtime ephemeral key bound to conversation_id.

    Returns a Lambda-proxy response dict via _resp.
    tools is intentionally empty — filled in Phase 4c.
    """
    key = _openai_api_key()
    if not key:
        return _resp(503, {
            "error":   "openai_not_configured",
            "message": (
                "OpenAI API key missing — run: aws secretsmanager put-secret-value "
                "--secret-id ciso-copilot/openai-api-key --secret-string '{\"api_key\":\"<KEY>\"}'"
            ),
        })

    # OpenAI Realtime GA shape: POST /v1/realtime/client_secrets with the
    # session config nested under "session". Response carries the ephemeral
    # key in "value". Mirrors voice_session/main.py exactly.
    payload = {
        "session": {
            "type":              "realtime",
            "model":             "gpt-realtime",
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format":         {"type": "audio/pcm", "rate": 24000},
                    "transcription":  {"model": "whisper-1"},
                    "turn_detection": {
                        "type":                "server_vad",
                        "threshold":           0.5,
                        "prefix_padding_ms":   300,
                        "silence_duration_ms": 500,
                        "create_response":     True,
                        "interrupt_response":  False,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice":  "alloy",
                },
            },
            # Phase 4c (Task 4c.1) fills this in with the full tool catalog.
            "tools":       [],
            "tool_choice": "auto",
            # Bind the conversation so voice events can be correlated back.
            "metadata": {"conversation_id": conversation_id},
        },
    }

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/realtime/client_secrets",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        print(f"OpenAI session mint failed: {e.code} {detail}")
        return _resp(502, {"error": "openai_failed", "status": e.code, "detail": detail})
    except Exception as e:
        print(f"OpenAI session mint exception: {e}")
        return _resp(502, {"error": "openai_failed", "detail": str(e)[:200]})

    session = body.get("session") or {}
    return _resp(200, {
        "session_id":       session.get("id"),
        "client_secret":    body.get("value"),      # ephemeral key, prefix "ek_"
        "expires_at":       body.get("expires_at"),
        "model":            session.get("model"),
        "conversation_id":  conversation_id,
    })


# ============================================================================
# Helpers
# ============================================================================

def _openai_api_key() -> str | None:
    global _openai_key
    if _openai_key is None:
        try:
            v = sm.get_secret_value(SecretId=OPENAI_SECRET_NAME)
            raw = v["SecretString"]
            # Stored either as raw string or as {"api_key": "..."} JSON.
            if raw.startswith("{"):
                parsed = json.loads(raw)
                _openai_key = parsed.get("api_key") or ""
            else:
                _openai_key = raw
            if not _openai_key:
                return None
        except Exception as e:
            print(f"OpenAI key load failed: {e}")
            return None
    return _openai_key
