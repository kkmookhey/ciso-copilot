# platform/lambda/chat_session/tests/test_auto_title_integration.py
"""Integration tests for the auto-title block in app.py:stream_turn.

Mirrors the harness from test_agentic_loop.py — JWT, DB, Anthropic stream,
and the auto_title module are all monkeypatched; the request is driven
through the Starlette TestClient.
"""
import json

import pytest
from starlette.testclient import TestClient

import app as APP

STREAM_PATH = "/v1/conversations/conv-1/stream"


def _parse_sse(text: str) -> list[dict]:
    out = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            out.append(json.loads(chunk[len("data: "):]))
    return out


@pytest.fixture
def patched(monkeypatch):
    """Patch auth, DB conversation/message helpers, and the Anthropic stream
    so the loop runs without AWS. Returns mutable controls the tests use."""
    monkeypatch.setattr(APP, "_extract_bearer", lambda h: "tok")
    monkeypatch.setattr(APP, "_verify_jwt", lambda t: {"sub": "u-1"})
    monkeypatch.setattr(APP, "_resolve_from_claims", lambda c: ("tenant-1", "user-1"))

    # Conversation state — tests override conv via the fixture return value.
    conv_holder = {"value": {"id": "conv-1", "title": "New conversation", "messages": []}}
    monkeypatch.setattr(APP.C, "get", lambda tid, cid: conv_holder["value"])

    appended = []
    monkeypatch.setattr(APP.M, "append",
                        lambda cid, role, content: appended.append((role, content)))

    # patch_title_if_default — tests override return value via the fixture.
    patched_titles = []
    patch_holder = {"return": True}

    def fake_patch(tid, cid, title):
        patched_titles.append((tid, cid, title))
        return patch_holder["return"]

    monkeypatch.setattr(APP.C, "patch_title_if_default", fake_patch)

    # Anthropic stream — single text-only round (no tool use).
    def fake_stream(system, messages, tools=None):
        yield {"type": "text-delta", "text": "Hello "}
        yield {"type": "text-delta", "text": "world"}
        yield {"type": "done"}
    monkeypatch.setattr(APP, "stream_messages", fake_stream)

    return {
        "conv":           conv_holder,
        "appended":       appended,
        "patched_titles": patched_titles,
        "patch_holder":   patch_holder,
    }


def test_emits_title_updated_on_first_turn_with_default_title(patched, monkeypatch):
    monkeypatch.setattr(APP.auto_title, "generate_title",
                        lambda u, a: "Hello World Conversation")

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "hi"},
                    headers={"authorization": "Bearer tok"})
    assert r.status_code == 200
    evs = _parse_sse(r.text)
    title_events = [e for e in evs if e.get("type") == "title-updated"]
    assert len(title_events) == 1
    assert title_events[0]["conversation_id"] == "conv-1"
    assert title_events[0]["title"] == "Hello World Conversation"
    # title-updated must come BEFORE done
    title_idx = next(i for i, e in enumerate(evs) if e.get("type") == "title-updated")
    done_idx = next(i for i, e in enumerate(evs) if e.get("type") == "done")
    assert title_idx < done_idx
    # patch_title_if_default was called with the new title
    assert patched["patched_titles"] == [("tenant-1", "conv-1", "Hello World Conversation")]


def test_skips_titling_when_history_not_empty(patched, monkeypatch):
    """Prior messages exist -> not the first turn -> no auto-title."""
    patched["conv"]["value"] = {
        "id": "conv-1",
        "title": "New conversation",
        "messages": [
            {"role": "user", "content": {"text": "earlier q"}},
            {"role": "assistant", "content": {"text": "earlier a"}},
        ],
    }
    called = []
    monkeypatch.setattr(APP.auto_title, "generate_title",
                        lambda u, a: called.append((u, a)) or "Should Not Be Used")

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "follow-up"},
                    headers={"authorization": "Bearer tok"})
    evs = _parse_sse(r.text)
    assert not [e for e in evs if e.get("type") == "title-updated"]
    assert called == []
    assert patched["patched_titles"] == []


def test_skips_titling_when_title_already_custom(patched, monkeypatch):
    """Title is non-default -> no auto-title even on first turn."""
    patched["conv"]["value"] = {
        "id": "conv-1",
        "title": "My Custom Name",
        "messages": [],
    }
    called = []
    monkeypatch.setattr(APP.auto_title, "generate_title",
                        lambda u, a: called.append("yes") or "Should Not Be Used")

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "hi"},
                    headers={"authorization": "Bearer tok"})
    evs = _parse_sse(r.text)
    assert not [e for e in evs if e.get("type") == "title-updated"]
    assert called == []


def test_titling_failure_does_not_break_stream(patched, monkeypatch):
    """auto_title returning None -> no SSE event, but done still fires."""
    monkeypatch.setattr(APP.auto_title, "generate_title", lambda u, a: None)

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "hi"},
                    headers={"authorization": "Bearer tok"})
    evs = _parse_sse(r.text)
    assert not [e for e in evs if e.get("type") == "title-updated"]
    assert evs[-1] == {"type": "done"}
    assert not [e for e in evs if e.get("error")]


def test_titling_raises_does_not_break_stream(patched, monkeypatch):
    """Even an unexpected exception in auto_title is caught at the integration site."""
    def boom(u, a):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(APP.auto_title, "generate_title", boom)

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "hi"},
                    headers={"authorization": "Bearer tok"})
    evs = _parse_sse(r.text)
    assert evs[-1] == {"type": "done"}
    assert not [e for e in evs if e.get("error")]


def test_no_event_when_patch_title_returns_false(patched, monkeypatch):
    """patch_title_if_default returned False (manual rename won the race)
    -> no SSE event."""
    monkeypatch.setattr(APP.auto_title, "generate_title", lambda u, a: "Race Loser")
    patched["patch_holder"]["return"] = False

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "hi"},
                    headers={"authorization": "Bearer tok"})
    evs = _parse_sse(r.text)
    assert not [e for e in evs if e.get("type") == "title-updated"]
    # We DID call patch — proves the guard is the SQL WHERE, not the caller
    assert patched["patched_titles"] == [("tenant-1", "conv-1", "Race Loser")]
