# Auto-titled Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user sends the first message in a new conversation, generate a 3–7 word title via Haiku 4.5 after the assistant replies, persist it with a default-guard UPDATE, and push the new title to the sidebar over the existing SSE stream — no page refresh, manual rename always wins.

**Architecture:** All work happens inside the existing `ChatStreamFn` Lambda (Function URL + Lambda Web Adapter). One new helper module (`auto_title.py`), one new SQL helper (`patch_title_if_default`), one extra block in `app.py:stream_turn` after the assistant message is persisted, one new SSE event type (`title-updated`), and matching web wiring that reuses the already-existing `setTitle` reducer action. Zero new infrastructure. Hotswap-eligible.

**Tech Stack:** Python 3.12 (Starlette + LWA on Lambda), Anthropic Messages API (Haiku 4.5), Aurora Postgres via RDS Data API, AWS CDK (TypeScript), React + TypeScript + Vite (web).

**Spec:** [`docs/superpowers/specs/2026-06-12-auto-titled-conversations-design.md`](../specs/2026-06-12-auto-titled-conversations-design.md)

**Branch:** `feat/auto-titled-conversations` (already created; spec commit `907ddb4` is its first commit).

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `platform/lambda/chat_session/anthropic_call.py` | Modify | Add optional `model` + `timeout` params to `call()` (backward-compatible defaults) |
| `platform/lambda/chat_session/auto_title.py` | **Create** | One public function `generate_title(user_text, assistant_text) -> str \| None`; calls Haiku, sanitizes output, swallows all errors |
| `platform/lambda/chat_session/conversations.py` | Modify | Add `patch_title_if_default()` — guarded UPDATE that only fires when title is still `'New conversation'` |
| `platform/lambda/chat_session/app.py` | Modify | After the existing assistant-message persistence (line ~248), insert an auto-title block; yield new SSE event before `done`. Update wire-format docstring at top. |
| `platform/lambda/chat_session/tests/test_auto_title.py` | **Create** | 7 unit tests for `auto_title.generate_title()` + `_sanitize()` |
| `platform/lambda/chat_session/tests/test_conversations.py` | Modify | Add 3 tests for `patch_title_if_default()` |
| `platform/lambda/chat_session/tests/test_auto_title_integration.py` | **Create** | 4 integration tests through the Starlette TestClient (mirroring `test_agentic_loop.py` harness) |
| `platform/lib/api-stack.ts` | Modify | Add `ANTHROPIC_TITLE_MODEL: 'claude-haiku-4-5'` to `chatEnv` (line ~1039) |
| `web/src/chat/chatApi.ts` | Modify | Extend `StreamCallbacks` with `onTitleUpdated?`; add `else if` branch in the SSE dispatcher (line ~159) |
| `web/src/chat/Shell.tsx` | Modify | Pass `onTitleUpdated` to `streamMessage`; mirror the existing `onRename` pattern (in-place `setConvs` map + conditional `setTitle` dispatch) |

10 files total. 3 new, 7 modified. No new migrations, no new IAM, no new CFN resources.

---

## Task 1 — Add `model` + `timeout` parameters to `anthropic_call.call()`

**Goal:** Make `call()` overridable for the auto-titler without forking a parallel `call_haiku()`. Backward-compatible: existing call sites must work unchanged.

**Files:**
- Modify: `platform/lambda/chat_session/anthropic_call.py` (lines 33-61)
- Test: covered by Task 2's unit tests (mocks `call`) and existing `test_*` files (`grep -rn 'anthropic_call.call' platform/lambda/chat_session/` to confirm no caller breaks)

- [ ] **Step 1.1: Read the existing `call()` to confirm signature**

Run: `sed -n '33,62p' platform/lambda/chat_session/anthropic_call.py`

Expected: signature is `def call(system: str, user_message: str, max_tokens: int = 2048) -> str`. Body uses module-level `MODEL` constant and `urllib.request.urlopen(req, timeout=45)`.

- [ ] **Step 1.2: Verify zero current callers pass positional arguments past `max_tokens`**

Run: `grep -rn "anthropic_call\.call\|from anthropic_call import call" platform/lambda/chat_session/ --include="*.py"`

Expected: any call sites use keyword args or stop at `max_tokens`. (If you find a positional 4th arg anywhere, switch it to keyword in the same commit. Today's repo has none — verify.)

- [ ] **Step 1.3: Modify the function signature and body**

Replace the entire `call` function (lines 33-61 of `anthropic_call.py`) with:

```python
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
```

- [ ] **Step 1.4: Run the existing chat_session test suite to confirm no regression**

Run: `cd platform/lambda/chat_session && python -m pytest tests/ -x -q`

Expected: all existing tests pass (test_conversations, test_db, test_messages_update, test_app, test_agentic_loop, test_router, test_stream, test_tools_dispatch, test_prompts_and_voice). The new param has a default, so nothing should break.

- [ ] **Step 1.5: Commit**

```bash
git add platform/lambda/chat_session/anthropic_call.py
git commit -m "feat(chat): allow per-call model + timeout overrides in anthropic_call.call

Adds optional model and timeout parameters with backward-compatible
defaults. Auto-titler will use Haiku 4.5 + a 5s budget; no other call
site changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — Create `auto_title.py` module (TDD)

**Goal:** One function, `generate_title(user_text, assistant_text) -> str | None`, that calls Haiku and sanitizes the output. Every failure path returns `None`. Never raises.

**Files:**
- Create: `platform/lambda/chat_session/auto_title.py`
- Test: `platform/lambda/chat_session/tests/test_auto_title.py`

- [ ] **Step 2.1: Write the failing test file**

Create `platform/lambda/chat_session/tests/test_auto_title.py`:

```python
# platform/lambda/chat_session/tests/test_auto_title.py
"""Tests for auto_title.generate_title.

Every test mocks anthropic_call.call so no network or AWS access happens.
"""
import auto_title as AT


def _patch_call(monkeypatch, return_value=None, raises=None):
    """Replace auto_title.call with a stub that returns or raises."""
    calls = []

    def fake_call(*, system, user_message, max_tokens, model, timeout):
        calls.append({
            "system":       system,
            "user_message": user_message,
            "max_tokens":   max_tokens,
            "model":        model,
            "timeout":      timeout,
        })
        if raises is not None:
            raise raises
        return return_value

    monkeypatch.setattr(AT, "call", fake_call)
    return calls


def test_happy_path_returns_title(monkeypatch):
    _patch_call(monkeypatch, return_value="AWS Critical Findings Overview")
    title = AT.generate_title("show me my AWS criticals", "You have 12 critical findings…")
    assert title == "AWS Critical Findings Overview"


def test_strips_surrounding_straight_quotes(monkeypatch):
    _patch_call(monkeypatch, return_value='"AWS Critical Findings"')
    assert AT.generate_title("q", "a") == "AWS Critical Findings"


def test_strips_surrounding_smart_quotes(monkeypatch):
    _patch_call(monkeypatch, return_value="“AWS Critical Findings”")
    assert AT.generate_title("q", "a") == "AWS Critical Findings"


def test_caps_length_at_60_chars(monkeypatch):
    long = "X" * 200
    _patch_call(monkeypatch, return_value=long)
    result = AT.generate_title("q", "a")
    assert result is not None
    assert len(result) <= 60
    assert result == "X" * 60


def test_returns_none_on_exception(monkeypatch):
    _patch_call(monkeypatch, raises=RuntimeError("Anthropic HTTP 500"))
    assert AT.generate_title("q", "a") is None


def test_returns_none_on_empty_model_output(monkeypatch):
    _patch_call(monkeypatch, return_value="")
    assert AT.generate_title("q", "a") is None


def test_returns_none_on_whitespace_only_output(monkeypatch):
    _patch_call(monkeypatch, return_value="   \n  \t")
    assert AT.generate_title("q", "a") is None


def test_returns_none_when_both_inputs_empty(monkeypatch):
    calls = _patch_call(monkeypatch, return_value="never called")
    assert AT.generate_title("", "") is None
    assert calls == []  # the Haiku call must NOT be made


def test_truncates_long_inputs_before_call(monkeypatch):
    calls = _patch_call(monkeypatch, return_value="Some Title")
    user = "U" * 5000
    asst = "A" * 5000
    AT.generate_title(user, asst)
    assert len(calls) == 1
    forwarded = calls[0]["user_message"]
    # Each turn should be capped to MAX_INPUT_CHARS_PER_TURN (800)
    assert "U" * 800 in forwarded
    assert "U" * 801 not in forwarded
    assert "A" * 800 in forwarded
    assert "A" * 801 not in forwarded


def test_uses_haiku_model_and_short_timeout(monkeypatch):
    calls = _patch_call(monkeypatch, return_value="Title Here")
    AT.generate_title("q", "a")
    assert calls[0]["model"] == "claude-haiku-4-5"
    assert calls[0]["timeout"] == 5
    assert calls[0]["max_tokens"] == 32
```

- [ ] **Step 2.2: Run to confirm the test file fails on import**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_auto_title.py -x -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'auto_title'` (or `ImportError`).

- [ ] **Step 2.3: Create the module**

Create `platform/lambda/chat_session/auto_title.py`:

```python
# platform/lambda/chat_session/auto_title.py
"""Generate a short conversation title from the first turn (user + assistant).

Best-effort: every failure path returns None and the caller leaves the
conversation title untouched. Never raises.
"""
from __future__ import annotations

import os

from anthropic_call import call

TITLE_MODEL = os.environ.get("ANTHROPIC_TITLE_MODEL", "claude-haiku-4-5")
MAX_TITLE_CHARS = 60
MAX_INPUT_CHARS_PER_TURN = 800

_SYSTEM = (
    "You name chat conversations for a security analyst dashboard. "
    "Output 3 to 7 words, title case, no quotes, no trailing punctuation. "
    "Output ONLY the title — no preamble, no explanation."
)

_TEMPLATE = (
    "User asked:\n{user}\n\n"
    "Assistant replied:\n{assistant}\n\n"
    "Title:"
)

_QUOTE_CHARS = ('"', "'", "“", "”", "‘", "’")


def _sanitize(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    for q in _QUOTE_CHARS:
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            s = s[1:-1].strip()
    if not s:
        return None
    if len(s) > MAX_TITLE_CHARS:
        s = s[:MAX_TITLE_CHARS].rstrip()
    return s or None


def generate_title(user_text: str, assistant_text: str) -> str | None:
    user_text = (user_text or "")[:MAX_INPUT_CHARS_PER_TURN]
    assistant_text = (assistant_text or "")[:MAX_INPUT_CHARS_PER_TURN]
    if not user_text and not assistant_text:
        return None
    try:
        raw = call(
            system=_SYSTEM,
            user_message=_TEMPLATE.format(user=user_text, assistant=assistant_text),
            max_tokens=32,
            model=TITLE_MODEL,
            timeout=5,
        )
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"auto_title: Haiku call failed: {e}")
        return None
    return _sanitize(raw)
```

- [ ] **Step 2.4: Run the tests to verify all pass**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_auto_title.py -x -q`

Expected: 10 passed.

- [ ] **Step 2.5: Run the full chat_session suite to confirm no regression**

Run: `cd platform/lambda/chat_session && python -m pytest tests/ -x -q`

Expected: all tests pass.

- [ ] **Step 2.6: Commit**

```bash
git add platform/lambda/chat_session/auto_title.py platform/lambda/chat_session/tests/test_auto_title.py
git commit -m "feat(chat): add auto_title module — Haiku-backed title generation

Single public function generate_title(user, assistant) -> str | None.
Sanitizes output (strips quotes, caps at 60 chars), truncates inputs
to 800 chars/turn, swallows every error path. 10 unit tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 — Add `patch_title_if_default()` to conversations.py (TDD)

**Goal:** A tenant-scoped UPDATE that only fires when title is still `'New conversation'`. Returns `True` iff a row was updated. The auto-titler uses this so manual renames always win the race.

**Files:**
- Modify: `platform/lambda/chat_session/conversations.py` (append after `patch_title`, before `soft_delete`)
- Test: `platform/lambda/chat_session/tests/test_conversations.py` (append at end)

- [ ] **Step 3.1: Write the failing tests**

Append to `platform/lambda/chat_session/tests/test_conversations.py`:

```python
def test_patch_title_if_default_returns_true_when_row_updated(monkeypatch):
    captured = {}

    def fake_q(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [[{"stringValue": "conv-uuid"}]]

    monkeypatch.setattr(C, "_q", fake_q)
    assert C.patch_title_if_default("tenant-1", "conv-uuid", "Auto Title") is True
    assert "UPDATE conversations" in captured["sql"]
    assert "title = 'New conversation'" in captured["sql"]
    assert "tenant_id = :tenant_id::uuid" in captured["sql"]
    assert "id = :id::uuid" in captured["sql"]
    assert captured["params"]["title"] == "Auto Title"
    assert captured["params"]["tenant_id"] == "tenant-1"
    assert captured["params"]["id"] == "conv-uuid"


def test_patch_title_if_default_returns_false_when_no_row(monkeypatch):
    """No row returned -> title was already custom or wrong tenant."""
    monkeypatch.setattr(C, "_q", lambda sql, params=None: [])
    assert C.patch_title_if_default("tenant-1", "conv-uuid", "Auto Title") is False


def test_patch_title_if_default_sql_returns_id(monkeypatch):
    """RETURNING id::text is what we use to detect 'a row was updated'."""
    captured = {}
    monkeypatch.setattr(C, "_q",
                        lambda sql, params=None: captured.update(sql=sql) or [])
    C.patch_title_if_default("t", "c", "T")
    assert "RETURNING id::text" in captured["sql"]
```

- [ ] **Step 3.2: Run to confirm the tests fail**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_conversations.py::test_patch_title_if_default_returns_true_when_row_updated tests/test_conversations.py::test_patch_title_if_default_returns_false_when_no_row tests/test_conversations.py::test_patch_title_if_default_sql_returns_id -x -q`

Expected: 3 FAIL with `AttributeError: module 'conversations' has no attribute 'patch_title_if_default'`.

- [ ] **Step 3.3: Add the function to conversations.py**

In `platform/lambda/chat_session/conversations.py`, add this function between `patch_title` (line 73-80) and `soft_delete` (line 83):

```python
def patch_title_if_default(tenant_id: str, conversation_id: str, title: str) -> bool:
    """Set title only if it's still the default 'New conversation'.

    Used by the auto-titler to ensure manual renames always win — if the
    user (or any race) has already set a non-default title, the WHERE
    clause matches zero rows and we return False without overwriting.
    Returns True iff a row was updated.
    """
    rows = _q(
        "UPDATE conversations SET title = :title, updated_at = NOW() "
        "WHERE id = :id::uuid AND tenant_id = :tenant_id::uuid "
        "AND title = 'New conversation' "
        "RETURNING id::text",
        {"title": title, "id": conversation_id, "tenant_id": tenant_id},
    )
    return bool(rows)
```

- [ ] **Step 3.4: Run the tests to verify all pass**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_conversations.py -x -q`

Expected: 8 passed (5 existing + 3 new).

- [ ] **Step 3.5: Commit**

```bash
git add platform/lambda/chat_session/conversations.py platform/lambda/chat_session/tests/test_conversations.py
git commit -m "feat(chat): add patch_title_if_default — guarded title UPDATE

Tenant-scoped UPDATE that only writes when title is still the default
'New conversation'. The auto-titler uses this so manual renames always
win the race. 3 new tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 — Wire auto-titling into `app.py:stream_turn` (TDD)

**Goal:** After the existing assistant-message persistence, conditionally generate a title and emit a new SSE event. Guards: title still default + this was the first turn + assistant produced text. Wrapped in defence-in-depth `try/except` so titling can never break the stream.

**Files:**
- Modify: `platform/lambda/chat_session/app.py` (top docstring + import block + integration site at line ~233-248)
- Test: `platform/lambda/chat_session/tests/test_auto_title_integration.py` (new)

- [ ] **Step 4.1: Write the failing integration tests**

Create `platform/lambda/chat_session/tests/test_auto_title_integration.py`:

```python
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

    def fake_patch(tid, cid, title):
        patched_titles.append((tid, cid, title))
        return patch_holder["return"]

    patch_holder = {"return": True}
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
```

- [ ] **Step 4.2: Run to confirm the tests fail**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_auto_title_integration.py -x -q`

Expected: 6 FAIL with `AttributeError: module 'app' has no attribute 'auto_title'` (or similar — the integration block doesn't exist yet).

- [ ] **Step 4.3: Add the `auto_title` import to app.py**

In `platform/lambda/chat_session/app.py`, after the existing `import conversations as C` (line 40), add:

```python
import auto_title
```

The full import block (lines 40-45) becomes:

```python
import conversations as C
import auto_title
import messages as M
import prompts
import tools_dispatch
from anthropic_call import stream_messages
from messages_stream import _extract_bearer, _resolve_from_claims, _verify_jwt
```

- [ ] **Step 4.4: Update the SSE wire-format docstring at the top of app.py**

Replace lines 22-29 (the SSE wire format block in the module docstring) with:

```python
SSE wire format (the web client depends on this exactly):
  data: {"type":"text-delta","text":"..."}\n\n
  data: {"type":"tool-result","tool_name":"...","artifact_hint":{...}}\n\n
  data: {"type":"tool-result","tool_name":"...","artifact_hints":[...]}\n\n
  data: {"type":"tool-result","tool_name":"...","side_effect":{...}}\n\n
  data: {"type":"title-updated","conversation_id":"<uuid>","title":"<str>"}\n\n
  data: {"type":"done"}\n\n
  data: {"error":"..."}\n\n            (on any failure)
```

- [ ] **Step 4.5: Capture `prior_msg_count` before any new appends**

In `stream_turn`, between line 123 (`history = _history_for_anthropic(...)`) and line 124 (`history.append(...)`), add:

```python
    prior_msg_count = len(conv.get("messages", []))
```

So the block becomes:

```python
    # Build history for Anthropic (user + assistant turns from stored messages).
    history = _history_for_anthropic(conv.get("messages", []))
    prior_msg_count = len(conv.get("messages", []))
    history.append({"role": "user", "content": user_text})
```

(`prior_msg_count` is closed over by `gen()`.)

- [ ] **Step 4.6: Insert the auto-title block before `yield _sse({"type": "done"})`**

In `app.py`, find the existing trailing block (currently lines 245-248):

```python
        # Persist the assembled assistant reply once the loop completes.
        if final_assistant_text:
            M.append(cid, "assistant",
                     {"text": final_assistant_text, "modality": "text"})
```

…and find `yield _sse({"type": "done"})` (currently line 233 — note the file restructure means it's the LAST yield inside the try block, around line 233 today, but the relative ordering matters more than the line number).

Reorder so the auto-title block sits between the assistant-message persistence and the `done` event. The full tail of `gen()` should read:

```python
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
```

Wait — the `done` event currently fires INSIDE the try block (before the persistence). To insert auto-title BEFORE `done`, we need to restructure: move the assistant-message persistence and the auto-title block to fire INSIDE the try block, BEFORE the `done` yield.

Replace the **entire `try/except` body in `gen()`** (the block currently at lines ~135-248) with:

```python
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

                final_assistant_text += round_text

                if not tool_uses:
                    break

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

                    sse_ev: dict = {"type": "tool-result", "tool_name": name}
                    if artifact_hints is not None:
                        sse_ev["artifact_hints"] = artifact_hints
                    if artifact_hint is not None:
                        sse_ev["artifact_hint"] = artifact_hint
                    if source is not None:
                        sse_ev["source"] = source
                    if artifact_hint is None and artifact_hints is None \
                            and isinstance(result, dict):
                        sse_ev["side_effect"] = result
                    yield _sse(sse_ev)

                    M.append(cid, "tool", {
                        "tool_name":       name,
                        "args":            args,
                        "result":          result,
                        "_artifact_hint":  artifact_hint,
                        "_artifact_hints": artifact_hints,
                        "source":          source,
                    })

                    tool_result_blocks.append({
                        "type":        "tool_result",
                        "tool_use_id": tu["id"],
                        "content":     json.dumps(result, default=str),
                    })

                messages.append({"role": "user", "content": tool_result_blocks})
            else:
                print(f"agentic loop hit MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS}")

            # Persist the assembled assistant reply once the loop completes.
            if final_assistant_text:
                M.append(cid, "assistant",
                         {"text": final_assistant_text, "modality": "text"})

            # Auto-title the conversation on the first turn (best-effort).
            # Guards: title still default + this was the first turn (no prior
            # messages) + assistant produced text. Never raises.
            if (
                prior_msg_count == 0
                and (conv.get("title") or "") == "New conversation"
                and final_assistant_text
            ):
                try:
                    new_title = auto_title.generate_title(user_text, final_assistant_text)
                    if new_title and C.patch_title_if_default(tenant_id, cid, new_title):
                        yield _sse({
                            "type":            "title-updated",
                            "conversation_id": cid,
                            "title":           new_title,
                        })
                except Exception as e:  # noqa: BLE001 — defence in depth
                    print(f"auto_title block failed: {e}")

            yield _sse({"type": "done"})

        except Exception as e:  # noqa: BLE001
            print(f"Anthropic stream error: {e}")
            yield _sse({"error": "upstream_failed", "detail": str(e)[:200]})
            M.append(cid, "assistant",
                     {"text": "[Error: the assistant could not complete this response]",
                      "modality": "text"})
            return
```

Note two reorderings vs the original:
1. The "persist the assembled assistant reply" block moved from AFTER the `try/except` to INSIDE the `try` block, right before the auto-title block. This is necessary because the auto-title block needs to see the assistant text but must run BEFORE `done` is emitted.
2. Removed the trailing post-try persistence (now duplicate).

- [ ] **Step 4.7: Run the new integration tests**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_auto_title_integration.py -x -q`

Expected: 6 passed.

- [ ] **Step 4.8: Run the full chat_session suite to confirm no regression**

Run: `cd platform/lambda/chat_session && python -m pytest tests/ -x -q`

Expected: ALL tests pass — most importantly `test_agentic_loop.py` (which patches `C.get` to return `title='T'`, a non-default title, so the auto-title guard skips it) and `test_app.py` (unauth cases short-circuit before `gen()`).

If `test_agentic_loop.py` fails because it doesn't define `patch_title_if_default` or `auto_title` in its monkeypatches: the auto-title block defends against missing/non-existent attribute by using `getattr` indirectly through `APP.C.patch_title_if_default` — but since we added `patch_title_if_default` to `conversations.py` in Task 3, it's a real attribute. And `auto_title` is imported at module top. The guard `(conv.get("title") or "") == "New conversation"` returning `False` (because title is `"T"`) prevents either from being called. Tests should pass unchanged.

- [ ] **Step 4.9: Commit**

```bash
git add platform/lambda/chat_session/app.py platform/lambda/chat_session/tests/test_auto_title_integration.py
git commit -m "feat(chat): wire auto-titling into stream_turn

After the assistant message is persisted, on the first turn of a
default-titled conversation, call Haiku and emit a title-updated SSE
event before done. Guarded by (prior_msg_count == 0 AND title default
AND assistant text non-empty). Defensive try/except ensures titling
can never break the stream. 6 new integration tests.

Reorders the tail of gen() so assistant-message persistence happens
inside the try block — required so auto-title runs before 'done'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5 — Add `ANTHROPIC_TITLE_MODEL` env var to ChatStreamFn

**Goal:** Pin the title model in CDK so the Lambda picks up the right value on deploy.

**Files:**
- Modify: `platform/lib/api-stack.ts` (lines 1039-1044, `chatEnv` block)

- [ ] **Step 5.1: Add the env var to `chatEnv`**

In `platform/lib/api-stack.ts`, change lines 1039-1044 from:

```typescript
    const chatEnv = {
      ...dbEnv,
      OPENAI_SECRET_NAME:    props.openaiApiKeySecret.secretName,
      ANTHROPIC_SECRET_NAME: 'ciso-copilot/anthropic-api-key',
      USER_POOL_ID:          props.userPool.userPoolId,
    };
```

to:

```typescript
    const chatEnv = {
      ...dbEnv,
      OPENAI_SECRET_NAME:    props.openaiApiKeySecret.secretName,
      ANTHROPIC_SECRET_NAME: 'ciso-copilot/anthropic-api-key',
      ANTHROPIC_TITLE_MODEL: 'claude-haiku-4-5',
      USER_POOL_ID:          props.userPool.userPoolId,
    };
```

Note: `chatEnv` is shared between `ChatSessionFn` (CRUD) and `ChatStreamFn` (streaming). `ChatSessionFn` won't use the var; harmless.

- [ ] **Step 5.2: Type-check the CDK app**

Run: `cd platform && npx tsc --noEmit -p .`

Expected: no errors. (If a `tsc` invocation isn't part of the repo, run `cd platform && npx cdk synth -q CisoCopilotApi --output cdk.out.tmp >/dev/null 2>&1` instead — synth will fail loud on TS errors.)

- [ ] **Step 5.3: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "feat(chat): pin ANTHROPIC_TITLE_MODEL=claude-haiku-4-5 on ChatStreamFn

Env var consumed by auto_title.py. Lives on chatEnv (shared with
ChatSessionFn — harmless, the CRUD Lambda doesn't read it).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6 — Extend web SSE dispatcher with `title-updated` branch

**Goal:** Add the new SSE event type to `chatApi.streamMessage` and surface it through a new optional `onTitleUpdated` callback.

**Files:**
- Modify: `web/src/chat/chatApi.ts` (lines 104-109 for `StreamCallbacks`, lines 154-176 for the dispatcher)

- [ ] **Step 6.1: Extend the `StreamCallbacks` interface**

In `web/src/chat/chatApi.ts`, change lines 104-109 from:

```typescript
/** Callbacks for the streaming turn. onDelta is required; the rest optional. */
export interface StreamCallbacks {
  onDelta:        (t: string) => void;
  onToolResult?:  (ev: ToolResultEvent) => void;
  onSideEffect?:  (toolName: string, intent: Record<string, unknown>) => void;
}
```

to:

```typescript
/** Callbacks for the streaming turn. onDelta is required; the rest optional. */
export interface StreamCallbacks {
  onDelta:          (t: string) => void;
  onToolResult?:    (ev: ToolResultEvent) => void;
  onSideEffect?:    (toolName: string, intent: Record<string, unknown>) => void;
  onTitleUpdated?:  (conversationId: string, title: string) => void;
}
```

- [ ] **Step 6.2: Add the dispatcher branch**

In `web/src/chat/chatApi.ts`, change the dispatcher block (lines 157-174 today):

```typescript
      if (ev.type === "text-delta") {
        cb.onDelta(ev.text);
      } else if (ev.type === "tool-result") {
        const tre: ToolResultEvent = {
          tool_name:      ev.tool_name,
          artifact_hint:  ev.artifact_hint,
          artifact_hints: ev.artifact_hints,
          source:         ev.source,
          side_effect:    ev.side_effect,
        };
        cb.onToolResult?.(tre);
        if (ev.side_effect) {
          cb.onSideEffect?.(ev.tool_name, ev.side_effect);
        }
      } else if (ev.error) {
        throw new Error(`stream error: ${ev.error}`);
      }
      // "done" frame: no action needed — loop ends naturally on reader completion
```

to:

```typescript
      if (ev.type === "text-delta") {
        cb.onDelta(ev.text);
      } else if (ev.type === "tool-result") {
        const tre: ToolResultEvent = {
          tool_name:      ev.tool_name,
          artifact_hint:  ev.artifact_hint,
          artifact_hints: ev.artifact_hints,
          source:         ev.source,
          side_effect:    ev.side_effect,
        };
        cb.onToolResult?.(tre);
        if (ev.side_effect) {
          cb.onSideEffect?.(ev.tool_name, ev.side_effect);
        }
      } else if (ev.type === "title-updated") {
        cb.onTitleUpdated?.(ev.conversation_id, ev.title);
      } else if (ev.error) {
        throw new Error(`stream error: ${ev.error}`);
      }
      // "done" frame: no action needed — loop ends naturally on reader completion
```

- [ ] **Step 6.3: Verify the web type-check passes**

Run: `cd web && pnpm tsc --noEmit`

Expected: no errors. (pnpm lint baseline is dirty per project memory — don't run `pnpm lint`. Use `tsc --noEmit` for type-check only.)

- [ ] **Step 6.4: Commit**

```bash
git add web/src/chat/chatApi.ts
git commit -m "feat(web): dispatch title-updated SSE event to onTitleUpdated callback

Extends StreamCallbacks with onTitleUpdated; adds one branch in the
streamMessage SSE dispatcher. No behavior change for callers that
don't pass the new callback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7 — Wire `onTitleUpdated` in Shell.tsx (mirror onRename)

**Goal:** When the auto-titler fires, update the rail list in place and (if the conversation is the active one) dispatch the `setTitle` reducer action. Mirrors the existing `onRename` pattern exactly.

**Files:**
- Modify: `web/src/chat/Shell.tsx` (lines 192-... — the `streamMessage` callbacks object)

- [ ] **Step 7.1: Read the existing onSend block to confirm structure**

Run: `sed -n '186,220p' web/src/chat/Shell.tsx`

Expected: shows the `streamMessage` call with `onDelta`, `onToolResult`, `onSideEffect` callbacks passed. We're adding a fourth.

- [ ] **Step 7.2: Add `onTitleUpdated` to the callbacks object**

In `web/src/chat/Shell.tsx`, locate the `chatApi.streamMessage(state.conversationId, text, { … })` call (starts around line 192). The existing onRename function (lines 162-170) is the reference pattern:

```typescript
  async function onRename(id: string, title: string) {
    await chatApi.patchTitle(id, title);
    // Update the local list in-place — no round-trip needed
    setConvs((prev) => prev.map((c) => c.id === id ? { ...c, title } : c));
    // If this is the currently-open conversation, update the header title too
    if (state.conversationId === id) {
      dispatch({ type: "setTitle", title });
    }
  }
```

Add `onTitleUpdated` to the `streamMessage` callbacks (after `onSideEffect`):

```typescript
        onTitleUpdated: (conversationId, title) => {
          // Same pattern as onRename: in-place rail update + header dispatch.
          // No backend PATCH needed — the server already wrote the title.
          setConvs((prev) => prev.map((c) =>
            c.id === conversationId ? { ...c, title } : c
          ));
          if (state.conversationId === conversationId) {
            dispatch({ type: "setTitle", title });
          }
        },
```

(The exact insertion point is at the closing brace of the callbacks object literal, before the `})` that closes the `streamMessage` call. Read the file to confirm bracket alignment before editing.)

- [ ] **Step 7.3: Type-check**

Run: `cd web && pnpm tsc --noEmit`

Expected: no errors.

- [ ] **Step 7.4: Commit**

```bash
git add web/src/chat/Shell.tsx
git commit -m "feat(web): update rail + header on title-updated SSE event

Passes onTitleUpdated to streamMessage. Handler mirrors the existing
onRename pattern: in-place setConvs map + conditional setTitle dispatch
when the updated conversation is the active one. No backend PATCH
(server already wrote the title before emitting the SSE event).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8 — Deploy + smoke test

**Goal:** Hotswap-deploy ChatStreamFn + push the web bundle. Verify auto-titling works end-to-end and manual rename still wins races.

**Files:** none changed in this task.

- [ ] **Step 8.1: Hotswap-deploy the API stack**

Run from `platform/`:

```bash
set -a && . .env && set +a
npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```

Expected: completes in ~30-60s; `ChatStreamFn` updated. Output mentions `LastModified` timestamp change.

- [ ] **Step 8.2: Build + sync the web bundle**

Run from `web/`:

```bash
pnpm build
aws s3 sync dist/ s3://$WEB_BUCKET/ --delete
aws cloudfront create-invalidation --distribution-id $CLOUDFRONT_DIST_ID --paths '/*'
```

Expected: build succeeds (TS errors block the build); s3 sync uploads ~10 files; CloudFront invalidation queued.

- [ ] **Step 8.3: Smoke test — happy path**

1. Open the web app, sign in.
2. Click "+ New conversation" — rail entry says "New conversation".
3. Type "show me my AWS critical findings" and submit.
4. While the response streams: keep an eye on the sidebar.
5. After the response finishes streaming, the rail entry should change from "New conversation" to a meaningful title (e.g., "AWS Critical Findings Overview", "Reviewing AWS Critical Findings", etc.). Header title should match.
6. Reload the page. Title should persist.

Expected: title changes within ~1–2s of the response settling; no console errors; Network panel shows no extra REST call (the title arrived over the existing SSE stream).

- [ ] **Step 8.4: Smoke test — manual rename wins race**

1. Click "+ New conversation".
2. Type a message and submit.
3. IMMEDIATELY (while assistant is still streaming) open the kebab menu on the new conversation, click Rename, type "My Custom Title", press Enter.
4. Wait for the response to finish.

Expected: rail entry still says "My Custom Title". The auto-titler ran (you may see a `auto_title:` log line in CloudWatch if you tail it) but `patch_title_if_default` returned `False` because the manual rename already changed the title. No `title-updated` SSE event was emitted.

Verify in CloudWatch:

```bash
aws logs tail "/aws/lambda/$CHAT_STREAM_FN_NAME" --since 5m | grep auto_title
```

(Replace `$CHAT_STREAM_FN_NAME` with the actual function name — typically `ciso-copilot-CisoCopilotApi-ChatStreamFn-XXXXXX` from the CDK output. The grep may return nothing if titling succeeded silently — that's also fine; check for absence of error lines.)

- [ ] **Step 8.5: Smoke test — failure path is silent**

1. Open Anthropic dashboard, briefly disable the API key (or note that this step is best-effort — if not easy to simulate, skip and note "manual verification deferred").
2. Open a new conversation, send a message.
3. Expected: chat response should fail too (since chat uses the same key), but if you have a fault-injection mechanism that breaks only the Haiku call (e.g., temporarily set `ANTHROPIC_TITLE_MODEL` to a bogus value via console env edit), the chat response succeeds normally, no title-updated event is emitted, no error event is emitted, and the rail stays at "New conversation".

Practical alternative: skip the live fault-injection and rely on the unit tests in Task 4 (`test_titling_failure_does_not_break_stream`, `test_titling_raises_does_not_break_stream`) — they prove the silent-failure behavior at the code level.

- [ ] **Step 8.6: No commit for this task**

Task 8 is verification only.

---

## Task 9 — Update HANDOFF.md + open PR

**Goal:** Record the shipped slice and open the PR for merge.

**Files:**
- Modify: `HANDOFF.md` (move the auto-titled-conversations bullet from "Open punch-list items deferred" to "Shipped" section, add a one-paragraph summary)

- [ ] **Step 9.1: Read the current HANDOFF.md punch-list section**

Run: `sed -n '1,40p' HANDOFF.md`

Look for the `🐛 Web punch-list — 5 bug fixes shipped to main (2026-06-12)` section and the "Open punch-list items deferred" block at the end.

- [ ] **Step 9.2: Update HANDOFF.md**

Move the **Auto-titled conversations** bullet from "Open punch-list items deferred" to the "shipped" section (or extend the punch-list section title to mention the new addition: "🐛 Web punch-list — 6 fixes shipped (5 on 2026-06-12, 1 on 2026-06-12 evening: auto-titled conversations)").

Add a short summary paragraph in the same style as the existing 5 fixes:

```
**6. Auto-titled conversations (`feat(chat)` `<SHA>`).** Sidebar entries used
to stack as "New conversation"; now ChatGPT-style auto-naming via Haiku 4.5
fires after the first assistant reply. New `chat_session/auto_title.py`
module + `conversations.patch_title_if_default()` + one extra block in
`app.py:stream_turn` that emits a new SSE event `title-updated` before
`done`. Web `chatApi.streamMessage` learns the new event; `Shell.tsx`
mirrors the existing `onRename` pattern (in-place rail update + conditional
`setTitle` dispatch). Hotswap on `CisoCopilotApi` (ChatStreamFn). Manual
rename always wins via `WHERE title='New conversation'` guard on the
UPDATE. Spec: `docs/superpowers/specs/2026-06-12-auto-titled-conversations-design.md`.
**Reusable lesson:** when a streaming Lambda needs to emit a post-response
side-effect to the UI, an extra SSE event in the existing stream is
cheaper than any out-of-band channel — zero new CFN resources, no extra
round-trip, sub-1.5s added billed duration on the first turn only.
```

And from "Open punch-list items deferred" REMOVE the "Auto-titled conversations" bullet (keep the other two deferred items: scan history picker, Trust Center editor expansion).

- [ ] **Step 9.3: Commit the HANDOFF update**

```bash
git add HANDOFF.md
git commit -m "docs(handoff): record auto-titled conversations shipped

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 9.4: Push the branch + open the PR**

```bash
git push -u origin feat/auto-titled-conversations
gh pr create --title "feat(chat): auto-titled conversations (punch-list #1/3)" --body "$(cat <<'EOF'
## Summary
- First of three deferred web punch-list items from 2026-06-12 (chat #1: auto-titling, then Trust Center editor, then scan history picker)
- Replaces "New conversation" sidebar entries with a Haiku 4.5-generated 3–7 word title after the first assistant reply
- Manual rename always wins via a default-guard UPDATE; failure is silent (chat completes normally if Haiku is down)
- Zero new infra: one new SSE event type in the existing stream, no SQS / no Lambda trigger, no CFN budget impact

Spec: [`docs/superpowers/specs/2026-06-12-auto-titled-conversations-design.md`](docs/superpowers/specs/2026-06-12-auto-titled-conversations-design.md)
Plan: [`docs/superpowers/plans/2026-06-12-auto-titled-conversations.md`](docs/superpowers/plans/2026-06-12-auto-titled-conversations.md)

## Test plan
- [ ] All chat_session unit + integration tests pass (`pytest platform/lambda/chat_session/tests/ -q`)
- [ ] Web type-check passes (`pnpm tsc --noEmit` in `web/`)
- [ ] Hotswap-deploy `CisoCopilotApi`; web bundle synced + CloudFront invalidated
- [ ] Smoke 1: new conversation, ask a question, sidebar title updates from "New conversation" to a meaningful 3–7 word title within ~1–2s of response settling; persists across reload
- [ ] Smoke 2: new conversation, manual rename mid-stream — manual title wins, no auto-overwrite
- [ ] Smoke 3 (optional): inject a Haiku failure (bad `ANTHROPIC_TITLE_MODEL` env override) — chat completes normally, no error event, title stays "New conversation"

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. Share it back to KK.

---

## Definition of done

Every checkbox in Tasks 1–9 is ticked. After Task 9:

- 7 commits on `feat/auto-titled-conversations` (one spec commit `907ddb4` + one per task — 6 feature commits + 1 HANDOFF commit). Branch is mergeable. PR is open.
- Live in prod (hotswap-deployed in Task 8); web bundle synced + CloudFront invalidated.
- All spec §1 success criteria verified.
- The "Open punch-list items deferred" list in HANDOFF.md is down to 2 items (scan history picker + Trust Center editor expansion — to be tackled as plans #2 and #3 in subsequent sessions).
