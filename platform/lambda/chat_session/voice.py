# platform/lambda/chat_session/voice.py
"""Voice ephemeral-key mint for chat_session Lambda.

Ported from platform/lambda/voice_session/main.py.

mint() is called by the main.py router for:
  POST /v1/conversations/{id}/voice

It mints an OpenAI Realtime ephemeral key bound to conversation_id, with
the full CISO Copilot persona (from prompts.py) and the tool catalog
supplied by the browser in the POST body.

Task 4c.1: model upgraded to gpt-realtime-2 (GPT-5-class reasoning,
+15% Big Bench Audio vs gpt-realtime, drop-in payload shape).
Confirmed via live curl to POST /v1/realtime/client_secrets 2026-05-19:
  {"value":"ek_...","session":{"model":"gpt-realtime-2",...}} → 200 OK.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

import boto3

import prompts
from _db import _resp, _q, _claim_value

OPENAI_SECRET_NAME = os.environ.get("OPENAI_SECRET_NAME", "ciso-copilot/openai-api-key")

# Pinned model — single constant, changed in ONE place. Validated 2026-05-19.
REALTIME_MODEL = "gpt-realtime-2"

sm = boto3.client("secretsmanager")

# Module-level cache (Lambda container reuse).
_openai_key: str | None = None


def mint(event: dict, tenant_id: str, conversation_id: str) -> dict:
    """Mint an OpenAI Realtime ephemeral key bound to conversation_id.

    Reads tool definitions from the POST body (key: "tools") — the browser
    supplies these from web/src/chat/tools.ts toRealtimeTools(). If the body
    has no tools key, defaults to [] (graceful degradation, not a crash).

    Voice tool execution is browser-side: the Realtime data channel delivers
    function_call events to the browser, which runs executeTool(). The server
    only needs to include the tool *definitions* in the session config.

    Returns a Lambda-proxy response dict via _resp().
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

    # Parse body — tools supplied by the browser, default [] if absent.
    body: dict = {}
    raw_body = event.get("body") or ""
    if raw_body:
        try:
            body = json.loads(raw_body)
        except (ValueError, TypeError):
            body = {}
    tools: list = body.get("tools") or []
    if not isinstance(tools, list):
        tools = []

    # Resolve user first name for persona interpolation. Derive from email
    # local-part if available, otherwise "there".
    user_first_name = _resolve_first_name(tenant_id, event)

    # OpenAI Realtime GA shape: POST /v1/realtime/client_secrets with the
    # session config nested under "session". Response carries the ephemeral
    # key in "value". Audio/VAD/transcription config mirrors voice_session/main.py.
    # interrupt_response=False: iOS speakerphone AEC isn't strong enough at
    # volume to safely interrupt on echo — keep False for web as well
    # (barge-in is handled browser-side via response.cancel on
    # input_audio_buffer.speech_started; see spec §9.1).
    payload = {
        "session": {
            "type":              "realtime",
            "model":             REALTIME_MODEL,
            "instructions":      prompts.system_for_voice(user_first_name),
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
            "tools":       tools,
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
            body_resp = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        print(f"OpenAI session mint failed: {e.code} {detail}")
        return _resp(502, {"error": "openai_failed", "status": e.code, "detail": detail})
    except Exception as e:
        print(f"OpenAI session mint exception: {e}")
        return _resp(502, {"error": "openai_failed", "detail": str(e)[:200]})

    session = body_resp.get("session") or {}
    return _resp(200, {
        "session_id":       session.get("id"),
        "client_secret":    body_resp.get("value"),     # ephemeral key, prefix "ek_"
        "expires_at":       body_resp.get("expires_at"),
        "model":            session.get("model"),
        "conversation_id":  conversation_id,
    })


# ============================================================================
# Helpers
# ============================================================================

def _resolve_first_name(tenant_id: str, event: dict) -> str:
    """Derive a first name from the authenticated user's email.

    Reads the sso_subject from the JWT claims, looks up the email in the
    users table, and returns the local-part before the first dot or '@'.
    Falls back to "there" on any failure (safe for persona interpolation).
    """
    try:
        claims = (
            (event.get("requestContext") or {})
            .get("authorizer", {})
            .get("claims") or {}
        )
        raw = claims.get("identities")
        sso_subject = None
        if raw:
            try:
                ids = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(ids, dict):
                    ids = [ids]
                if ids:
                    sso_subject = ids[0].get("userId") or claims.get("sub")
            except (TypeError, ValueError):
                pass
        if not sso_subject:
            sso_subject = claims.get("sub")
        if not sso_subject:
            return "there"

        rows = _q(
            "SELECT u.email FROM users u "
            "WHERE u.sso_subject = :s AND u.tenant_id = CAST(:t AS UUID) LIMIT 1",
            {"s": sso_subject, "t": tenant_id},
        )
        if not rows:
            return "there"
        email = _claim_value(rows[0][0]) or ""
        # "alice.smith@example.com" → "alice"
        local = email.split("@")[0].split(".")[0]
        return local.capitalize() if local else "there"
    except Exception:  # noqa: BLE001
        return "there"


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
