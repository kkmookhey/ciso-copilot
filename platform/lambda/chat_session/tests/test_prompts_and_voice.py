# platform/lambda/chat_session/tests/test_prompts_and_voice.py
"""Tests for prompts.py (Task 4c.1 Part A) and voice.py (Part B).

All OpenAI HTTP calls are mocked — no network traffic in tests.
"""
from __future__ import annotations

import json
import unittest.mock as mock

import pytest

import prompts
import voice


# ===========================================================================
# prompts.py tests
# ===========================================================================

class TestSystemForVoice:
    def test_returns_string(self):
        result = prompts.system_for_voice()
        assert isinstance(result, str)

    def test_contains_persona(self):
        result = prompts.system_for_voice()
        # The persona block should be present.
        assert "CISO Copilot" in result
        assert "security engineer" in result

    def test_contains_tool_rules(self):
        result = prompts.system_for_voice()
        assert "Never invent data" in result
        assert "propose_" in result

    def test_contains_voice_addendum_not_text(self):
        result = prompts.system_for_voice()
        assert "25 words" in result
        assert "ARNs" in result
        # TEXT_ADDENDUM must NOT be in the voice prompt
        assert "artifact hint cards" not in result

    def test_user_first_name_interpolated(self):
        result = prompts.system_for_voice(user_first_name="Alice")
        assert "for Alice" in result
        assert "{Alice}" not in result
        # The placeholder literal must not leak through
        assert "{user_first_name}" not in result

    def test_default_first_name_is_there(self):
        result = prompts.system_for_voice()
        assert "for there" in result
        assert "{there}" not in result
        assert "{user_first_name}" not in result

    def test_none_first_name_falls_back_to_there(self):
        result = prompts.system_for_voice(user_first_name=None)
        assert "{user_first_name}" not in result
        assert "for there" in result
        assert "{there}" not in result


class TestSystemForText:
    def test_returns_string(self):
        result = prompts.system_for_text()
        assert isinstance(result, str)

    def test_contains_persona(self):
        result = prompts.system_for_text()
        assert "CISO Copilot" in result

    def test_contains_tool_rules(self):
        result = prompts.system_for_text()
        assert "Never invent data" in result

    def test_contains_text_addendum_not_voice(self):
        result = prompts.system_for_text()
        assert "artifact hint cards" in result
        # VOICE_ADDENDUM must NOT be in the text prompt
        assert "25 words" not in result

    def test_user_first_name_interpolated(self):
        result = prompts.system_for_text(user_first_name="Bob")
        assert "for Bob" in result
        assert "{Bob}" not in result
        assert "{user_first_name}" not in result

    def test_default_first_name_is_there(self):
        result = prompts.system_for_text()
        assert "for there" in result
        assert "{there}" not in result
        assert "{user_first_name}" not in result


class TestPromptsDiverge:
    def test_voice_and_text_differ(self):
        """The two paths must produce different prompts."""
        assert prompts.system_for_voice() != prompts.system_for_text()

    def test_voice_shorter_guidance(self):
        """Voice prompt has the 25-word rule; text prompt does not."""
        v = prompts.system_for_voice()
        t = prompts.system_for_text()
        assert "25 words" in v
        assert "25 words" not in t


# ===========================================================================
# voice.py tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Helper: build a minimal Lambda event with the given body dict
# ---------------------------------------------------------------------------

def _event(body: dict | None = None) -> dict:
    """Build a minimal Lambda proxy event for mint()."""
    return {
        "requestContext": {
            "authorizer": {
                "claims": {"sub": "test-subject"},
            }
        },
        "body": json.dumps(body) if body is not None else "",
    }


# ---------------------------------------------------------------------------
# Fixture: mock the OpenAI HTTP call so no real network request is made
# ---------------------------------------------------------------------------

FAKE_OPENAI_RESPONSE = {
    "value": "ek_test_ephemeral_key",
    "expires_at": 9999999999,
    "session": {
        "id": "sess_test123",
        "model": voice.REALTIME_MODEL,
    },
}


@pytest.fixture(autouse=True)
def mock_openai_key(monkeypatch):
    """Stub _openai_api_key so no Secrets Manager call is made."""
    monkeypatch.setattr(voice, "_openai_key", "sk-test-key")


@pytest.fixture()
def mock_openai_http():
    """Patch urllib.request.urlopen to return FAKE_OPENAI_RESPONSE."""
    fake_body = json.dumps(FAKE_OPENAI_RESPONSE).encode()

    class _FakeResp:
        def read(self):
            return fake_body
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    with mock.patch("urllib.request.urlopen", return_value=_FakeResp()) as m:
        yield m


@pytest.fixture()
def mock_db_no_user(monkeypatch):
    """Stub _q to return no rows (simulates no user found → first name 'there')."""
    monkeypatch.setattr(voice, "_q", lambda *a, **kw: [])


@pytest.fixture()
def mock_db_user_alice(monkeypatch):
    """Stub _q to return a row for alice.smith@example.com."""
    def _fake_q(sql, params=None):
        return [[{"stringValue": "alice.smith@example.com"}]]
    monkeypatch.setattr(voice, "_q", _fake_q)
    monkeypatch.setattr(voice, "_claim_value", lambda f: f.get("stringValue"))


# ---------------------------------------------------------------------------
# Test: model constant
# ---------------------------------------------------------------------------

def test_realtime_model_constant():
    assert voice.REALTIME_MODEL == "gpt-realtime-2"


# ---------------------------------------------------------------------------
# Test: tools from body are passed to OpenAI
# ---------------------------------------------------------------------------

def test_mint_passes_tools_from_body(mock_openai_http, mock_db_no_user):
    fake_tools = [
        {"type": "function", "name": "query_findings",
         "description": "...", "parameters": {}}
    ]
    event = _event({"tools": fake_tools})
    resp = voice.mint(event, "tenant-uuid-123", "conv-uuid-456")

    assert resp["statusCode"] == 200
    # Inspect what was sent to OpenAI
    call_args = mock_openai_http.call_args
    req_obj = call_args[0][0]  # urllib.request.Request
    sent_payload = json.loads(req_obj.data)
    assert sent_payload["session"]["tools"] == fake_tools
    assert sent_payload["session"]["model"] == "gpt-realtime-2"


# ---------------------------------------------------------------------------
# Test: no tools in body → defaults to []
# ---------------------------------------------------------------------------

def test_mint_no_tools_defaults_empty(mock_openai_http, mock_db_no_user):
    event = _event({})  # no "tools" key
    resp = voice.mint(event, "tenant-uuid-123", "conv-uuid-456")

    assert resp["statusCode"] == 200
    call_args = mock_openai_http.call_args
    req_obj = call_args[0][0]
    sent_payload = json.loads(req_obj.data)
    assert sent_payload["session"]["tools"] == []


# ---------------------------------------------------------------------------
# Test: body is None / empty → defaults to []
# ---------------------------------------------------------------------------

def test_mint_empty_body_defaults_empty(mock_openai_http, mock_db_no_user):
    event = _event(None)
    resp = voice.mint(event, "tenant-uuid-123", "conv-uuid-456")

    assert resp["statusCode"] == 200
    call_args = mock_openai_http.call_args
    req_obj = call_args[0][0]
    sent_payload = json.loads(req_obj.data)
    assert sent_payload["session"]["tools"] == []


# ---------------------------------------------------------------------------
# Test: instructions are populated (not empty)
# ---------------------------------------------------------------------------

def test_mint_instructions_non_empty(mock_openai_http, mock_db_no_user):
    event = _event({})
    voice.mint(event, "tenant-uuid-123", "conv-uuid-456")

    call_args = mock_openai_http.call_args
    req_obj = call_args[0][0]
    sent_payload = json.loads(req_obj.data)
    instructions = sent_payload["session"].get("instructions", "")
    assert len(instructions) > 50  # not empty / placeholder


# ---------------------------------------------------------------------------
# Test: conversation_id bound in metadata
# ---------------------------------------------------------------------------

def test_mint_conversation_id_in_metadata(mock_openai_http, mock_db_no_user):
    event = _event({})
    voice.mint(event, "tenant-uuid-123", "conv-uuid-abc")

    call_args = mock_openai_http.call_args
    req_obj = call_args[0][0]
    sent_payload = json.loads(req_obj.data)
    assert sent_payload["session"]["metadata"]["conversation_id"] == "conv-uuid-abc"


# ---------------------------------------------------------------------------
# Test: response envelope shape
# ---------------------------------------------------------------------------

def test_mint_response_envelope(mock_openai_http, mock_db_no_user):
    resp = voice.mint(_event({}), "tenant-uuid-123", "conv-uuid-456")
    body = json.loads(resp["body"])
    assert body["session_id"] == "sess_test123"
    assert body["client_secret"] == "ek_test_ephemeral_key"
    assert body["conversation_id"] == "conv-uuid-456"
    assert body["model"] == "gpt-realtime-2"


# ---------------------------------------------------------------------------
# Test: user first name resolved from email (alice)
# ---------------------------------------------------------------------------

def test_mint_first_name_from_email(mock_openai_http, mock_db_user_alice):
    event = _event({})
    voice.mint(event, "tenant-uuid-123", "conv-uuid-456")

    call_args = mock_openai_http.call_args
    req_obj = call_args[0][0]
    sent_payload = json.loads(req_obj.data)
    # "alice.smith@example.com" → "Alice"
    assert "Alice" in sent_payload["session"]["instructions"]


# ---------------------------------------------------------------------------
# Test: first name falls back to "there" when no user found
# ---------------------------------------------------------------------------

def test_mint_first_name_fallback_there(mock_openai_http, mock_db_no_user):
    event = _event({})
    voice.mint(event, "tenant-uuid-123", "conv-uuid-456")

    call_args = mock_openai_http.call_args
    req_obj = call_args[0][0]
    sent_payload = json.loads(req_obj.data)
    assert "there" in sent_payload["session"]["instructions"]


# ---------------------------------------------------------------------------
# Test: OpenAI missing key → 503
# ---------------------------------------------------------------------------

def test_mint_missing_key_returns_503(monkeypatch):
    monkeypatch.setattr(voice, "_openai_key", None)

    def _fail_key():
        return None
    monkeypatch.setattr(voice, "_openai_api_key", _fail_key)

    resp = voice.mint(_event({}), "tenant-uuid-123", "conv-uuid-456")
    assert resp["statusCode"] == 503
    body = json.loads(resp["body"])
    assert body["error"] == "openai_not_configured"


# ---------------------------------------------------------------------------
# Test: OpenAI HTTP error → 502
# ---------------------------------------------------------------------------

def test_mint_openai_http_error_returns_502(mock_db_no_user):
    import urllib.error
    import urllib.request

    class _FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__(
                url="https://api.openai.com/v1/realtime/client_secrets",
                code=400,
                msg="Bad Request",
                hdrs={},  # type: ignore[arg-type]
                fp=None,
            )
        def read(self):
            return b'{"error":"invalid_model"}'

    with mock.patch("urllib.request.urlopen", side_effect=_FakeHTTPError()):
        resp = voice.mint(_event({}), "tenant-uuid-123", "conv-uuid-456")

    assert resp["statusCode"] == 502
    body = json.loads(resp["body"])
    assert body["error"] == "openai_failed"
