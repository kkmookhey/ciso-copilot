# platform/lambda/chat_session/tests/test_agentic_loop.py
"""Tests for the server-side agentic loop in app.py.

The loop logic is exercised through the Starlette TestClient with auth, DB
access, and Anthropic streaming all monkeypatched — so this isolates the
tool-use loop: a no-tool response, and a one-tool response.
"""
import json

import pytest
from starlette.testclient import TestClient

import app as APP

STREAM_PATH = "/v1/conversations/conv-1/stream"


def _parse_sse(text: str) -> list[dict]:
    """Parse the SSE body into a list of event dicts."""
    out = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            out.append(json.loads(chunk[len("data: "):]))
    return out


@pytest.fixture
def patched(monkeypatch):
    """Patch auth + DB so the loop runs without AWS."""
    monkeypatch.setattr(APP, "_extract_bearer", lambda h: "tok")
    monkeypatch.setattr(APP, "_verify_jwt", lambda t: {"sub": "u-1"})
    monkeypatch.setattr(APP, "_resolve_from_claims", lambda c: ("tenant-1", "user-1"))
    monkeypatch.setattr(APP.C, "get",
                        lambda tid, cid: {"id": cid, "title": "T", "messages": []})

    appended = []
    monkeypatch.setattr(APP.M, "append",
                        lambda cid, role, content: appended.append((role, content)))
    return appended


def test_loop_no_tool_response(patched, monkeypatch):
    """Anthropic answers with text only — one round, no tool execution."""
    def fake_stream(system, messages, tools=None):
        yield {"type": "text-delta", "text": "Hello "}
        yield {"type": "text-delta", "text": "world"}
        yield {"type": "done"}

    monkeypatch.setattr(APP, "stream_messages", fake_stream)

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "hi"},
                    headers={"authorization": "Bearer tok"})
    assert r.status_code == 200
    evs = _parse_sse(r.text)
    deltas = [e for e in evs if e.get("type") == "text-delta"]
    assert "".join(d["text"] for d in deltas) == "Hello world"
    assert evs[-1] == {"type": "done"}
    assert not [e for e in evs if e.get("type") == "tool-result"]

    # Persisted: user message + final assistant message.
    roles = [role for role, _ in patched]
    assert roles == ["user", "assistant"]
    assert patched[-1][1]["text"] == "Hello world"


def test_loop_one_tool_response(patched, monkeypatch):
    """Anthropic requests a tool in round 1, then answers with text in round 2."""
    rounds = []

    def fake_stream(system, messages, tools=None):
        rounds.append(len(messages))
        if len(rounds) == 1:
            # Round 1: a tool_use request.
            yield {"type": "text-delta", "text": "Checking. "}
            yield {"type": "tool-use", "id": "tu-1",
                   "name": "get_severity_breakdown", "input": {}}
            yield {"type": "done"}
        else:
            # Round 2: final text answer.
            yield {"type": "text-delta", "text": "You have 3 high findings."}
            yield {"type": "done"}

    monkeypatch.setattr(APP, "stream_messages", fake_stream)

    captured_dispatch = {}

    def fake_dispatch(name, tenant_id, args):
        captured_dispatch["name"] = name
        captured_dispatch["tenant_id"] = tenant_id
        return {
            "result": {"total": 3},
            "_artifact_hint": {"kind": "severity_breakdown", "total": 3,
                               "critical": 0, "high": 3, "medium": 0, "low": 0},
        }

    monkeypatch.setattr(APP.tools_dispatch, "dispatch", fake_dispatch)
    monkeypatch.setattr(APP.tools_dispatch, "anthropic_tool_defs", lambda: [])

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "how many high findings"},
                    headers={"authorization": "Bearer tok"})
    assert r.status_code == 200
    evs = _parse_sse(r.text)

    # The tool was dispatched, tenant-scoped.
    assert captured_dispatch["name"] == "get_severity_breakdown"
    assert captured_dispatch["tenant_id"] == "tenant-1"

    # A tool-result SSE event carried the artifact hint.
    tool_evs = [e for e in evs if e.get("type") == "tool-result"]
    assert len(tool_evs) == 1
    assert tool_evs[0]["tool_name"] == "get_severity_breakdown"
    assert tool_evs[0]["artifact_hint"]["kind"] == "severity_breakdown"

    # Final text spans both rounds.
    deltas = [e for e in evs if e.get("type") == "text-delta"]
    assert "".join(d["text"] for d in deltas) == \
        "Checking. You have 3 high findings."
    assert evs[-1] == {"type": "done"}

    # Two Anthropic rounds ran.
    assert len(rounds) == 2

    # Persisted: user, tool, assistant.
    roles = [role for role, _ in patched]
    assert roles == ["user", "tool", "assistant"]
    tool_msg = patched[1][1]
    assert tool_msg["tool_name"] == "get_severity_breakdown"
    assert tool_msg["_artifact_hint"]["kind"] == "severity_breakdown"


def test_loop_max_rounds_cap(patched, monkeypatch):
    """A tool requested every round stops at MAX_TOOL_ROUNDS — no runaway."""
    call_count = {"n": 0}

    def fake_stream(system, messages, tools=None):
        call_count["n"] += 1
        yield {"type": "tool-use", "id": f"tu-{call_count['n']}",
               "name": "get_severity_breakdown", "input": {}}
        yield {"type": "done"}

    monkeypatch.setattr(APP, "stream_messages", fake_stream)
    monkeypatch.setattr(APP.tools_dispatch, "dispatch",
                        lambda n, t, a: {"result": {}})
    monkeypatch.setattr(APP.tools_dispatch, "anthropic_tool_defs", lambda: [])

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "loop"},
                    headers={"authorization": "Bearer tok"})
    assert r.status_code == 200
    # Anthropic was called at most MAX_TOOL_ROUNDS times.
    assert call_count["n"] == APP.MAX_TOOL_ROUNDS
    evs = _parse_sse(r.text)
    assert evs[-1] == {"type": "done"}


def test_loop_side_effect_tool_streams_intent(patched, monkeypatch):
    """A side-effect tool's tool-result event carries the side_effect intent."""
    def fake_stream(system, messages, tools=None):
        if len(messages) == 1:
            yield {"type": "tool-use", "id": "tu-1",
                   "name": "navigate_to", "input": {"path": "/findings"}}
            yield {"type": "done"}
        else:
            yield {"type": "text-delta", "text": "Done."}
            yield {"type": "done"}

    monkeypatch.setattr(APP, "stream_messages", fake_stream)
    monkeypatch.setattr(APP.tools_dispatch, "dispatch",
                        lambda n, t, a: {"result": {"navigated_to": a["path"]}})
    monkeypatch.setattr(APP.tools_dispatch, "anthropic_tool_defs", lambda: [])

    client = TestClient(APP.app)
    r = client.post(STREAM_PATH, json={"text": "go to findings"},
                    headers={"authorization": "Bearer tok"})
    assert r.status_code == 200
    evs = _parse_sse(r.text)
    tool_evs = [e for e in evs if e.get("type") == "tool-result"]
    assert len(tool_evs) == 1
    assert tool_evs[0]["side_effect"] == {"navigated_to": "/findings"}
    assert "artifact_hint" not in tool_evs[0]
