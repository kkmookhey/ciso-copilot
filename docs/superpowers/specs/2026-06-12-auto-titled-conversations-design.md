# Auto-titled conversations — design spec

> Sidebar today is a stack of "New conversation" entries. ChatGPT-style
> auto-naming should be the default. Manual rename already works (kebab
> menu → Rename), so this spec is strictly about *generating* a useful
> title on the first turn and reflecting it in the UI without a refresh.
>
> Brainstormed 2026-06-12 in the post-punch-list session. Deferred from
> `HANDOFF.md` "Open punch-list items deferred" (2026-06-12).
>
> Cross-refs:
> - [`2026-05-19-sp4-chat-first-design.md`](2026-05-19-sp4-chat-first-design.md) — original chat-first design; `conversations` schema (sql/006) + SSE wire format originate there.

## 0. Codebase baseline — verified 2026-06-12

Areas this slice touches, with file paths + key facts:

- **`conversations` table** — migration `006_conversations.sql` (`platform/sql/`, 33 lines). Columns: `id, tenant_id, user_id, title, created_at, updated_at, last_activity_at, deleted_at`. `title TEXT NOT NULL DEFAULT 'New conversation'`. **Already shipped:** tenant+user scoping, soft delete, `last_activity_at DESC` recency index.
- **Conversation CRUD** — `platform/lambda/chat_session/conversations.py` (90 lines). Public functions: `create()`, `list_for()`, `get()`, `patch_title()`, `soft_delete()`. **Manual rename ships today** via `patch_title()` + `PATCH /v1/conversations/{id}` (routed in `main.py:77`).
- **Streaming chat Lambda** — `platform/lambda/chat_session/app.py` (255 lines). One ASGI route: `POST /v1/conversations/{id}/stream`. Function URL with Lambda Web Adapter; AuthType=NONE at AWS layer, JWT verified in-Lambda via Cognito JWKS. The agentic tool-use loop persists `user`/`tool`/`assistant` messages and emits SSE events. Documented wire format: `text-delta`, `tool-result`, `done`, `error`.
- **Anthropic client** — `platform/lambda/chat_session/anthropic_call.py` (182 lines). Two public functions: `call(system, user_message, max_tokens=2048)` (non-streaming, single-turn, returns concatenated text) and `stream_messages(system, messages, tools, max_tokens=4096)` (streaming generator). Model pinned from `ANTHROPIC_MODEL` env var, currently `claude-sonnet-4-6`. **Haiku is NOT wired into chat_session** but is referenced elsewhere (`soc_enrichment/llm.py:25` knows `claude-haiku-4-5`).
- **Web SSE dispatch** — `web/src/chat/chatApi.ts` (177 lines, `streamMessage` at lines 119-177). Handles `text-delta`, `tool-result`, `error`; the `done` frame is a no-op (loop ends on reader completion). `StreamCallbacks` interface: `onDelta` (required), `onToolResult?`, `onSideEffect?`.
- **Web reducer state** — `web/src/chat/state.ts` (109 lines). **The `setTitle` action already exists** (`type: "setTitle"; title: string`, dispatched at lines 23 + 49). Used today by manual rename in `Shell.tsx`. This means the web wiring needed is *only* (a) one new SSE event branch in `chatApi.ts` and (b) the dispatch call in `Shell.tsx`.
- **Sidebar UI** — `web/src/chat/ConversationRail.tsx` (253 lines). Reads `conversations` from `Shell.tsx` state. No polling; updates only via reducer actions.
- **Tests for chat_session** — `platform/lambda/chat_session/tests/` has 9 files including `test_conversations.py`, `test_app.py`, `test_agentic_loop.py`, `test_stream.py`. Existing pattern: mock `_q` (Aurora Data API wrapper from `_db.py`) and assert on emitted SQL + bound params.
- **CFN resource budget** — `CisoCopilotApi` ~494/500. ChatStreamFn lives on a separate Function URL Lambda (`CisoCopilotApi` does NOT route to it), so adding work *inside* ChatStreamFn does not consume CFN resources. Adding a new SQS queue / Lambda trigger would.
- **ChatStreamFn definition** — `platform/lib/api-stack.ts:1135-1149`. Environment block reads `chatEnv` (defined at line 1039, shared with ChatSessionFn). Today's env: `dbEnv + OPENAI_SECRET_NAME + ANTHROPIC_SECRET_NAME + USER_POOL_ID`. **New env var lands here.**
- **Last migration** — `015_mcp_connectors.sql` is the most recent shipped; `016` belongs to AI Security Sub-slice 1.4 (Workspace OAuth). No new migration needed for this spec.

**What's genuinely new in this slice:**
1. New `chat_session/auto_title.py` module — one function that calls Haiku to summarize Q+A.
2. `anthropic_call.call()` gains an optional `model` parameter (backward-compatible default).
3. New `conversations.patch_title_if_default()` helper with a guarded UPDATE.
4. `chat_session/app.py:stream_turn` gains an auto-title block after the existing assistant-message persistence.
5. New SSE event type `title-updated` in the documented wire format.
6. Web `chatApi.streamMessage` learns the new event; `Shell.tsx` dispatches the existing `setTitle` reducer action; the conversation rail entry updates in place.
7. ChatStreamFn env var `ANTHROPIC_TITLE_MODEL=claude-haiku-4-5`.

Everything below in the spec must fall under this scope.

---

## 1. Goal and success criteria

**Goal.** Auto-name new conversations from the first user message + first assistant reply, the same way ChatGPT does. Manual rename keeps working and always wins.

**Testable success criteria:**

1. Submitting the first user message in a fresh conversation results in the sidebar entry transitioning from `"New conversation"` to a 3–7 word summary title, with no page refresh, before the user sees the next idle state.
2. The new title is persisted in `conversations.title` and survives reload.
3. If the user manually renames the conversation before or during the first turn (rename commits while the Haiku call is in flight), the manual title is preserved — the auto-title write becomes a no-op.
4. If the Haiku call fails (timeout, 5xx, network) the conversation stays `"New conversation"`, the streaming response completes normally, and no error is surfaced in the SSE stream.
5. Existing conversations with title `"New conversation"` are NOT mass-backfilled. They get auto-titled organically only if someone next posts in a conversation that has no prior messages yet (per the first-turn guard in §5.4); conversations that already have messages stay `"New conversation"` forever absent manual rename.
6. No regression in the existing `text-delta` / `tool-result` / `done` / `error` wire format. Adding a new event type does not break older web clients (they ignore unknown types by virtue of the `if/else if` chain falling through).
7. Unit tests cover: happy-path title generation, output sanitization (quote-stripping, length cap), Haiku failure → `None` return, default-guard UPDATE behavior (default → updates; custom → no-op).
8. End-to-end p50 added latency to the first turn's billed Lambda duration is < 1.5s (Haiku call only; no impact on user-perceived response latency since the title runs after the SSE `done` event has been queued).

## 2. Why this design (and what was reconsidered)

The natural integration point is `stream_turn` in `app.py`. Three architectural alternatives were considered (in the brainstorming dialogue 2026-06-12):

1. **In-Lambda after assistant reply** *(chosen)*. After persisting the assistant message, conditionally call Haiku and UPDATE the title — still inside the same generator, before the `done` event. No new infra, no CFN cap impact, ~1.5s added to billed duration on the first turn only.
2. **Async via SQS / EventBridge**. Decouples titling from the chat Lambda. Adds 4–6 CFN resources (queue + Lambda + IAM + alarm). Rejected because (a) titling is fast and reliable enough that decoupling buys nothing, (b) the current chat Lambda has plenty of duration headroom, and (c) `CisoCopilotApi` is at ~494/500 — even though ChatStreamFn doesn't share that budget, a worker Lambda would, and "free" CFN resources don't exist.
3. **After first user message (no assistant context)**. Faster to land but produces worse titles. A first-turn user prompt is often a question ("How many criticals?"); the assistant reply ("AWS Critical Findings Overview") is the better titling signal.

Model choice: Haiku 4.5 over Sonnet for ~10× cost saving and ~3× latency saving on a 6-word task. Already pre-validated in `soc_enrichment/llm.py`.

Race rule: manual rename always wins via a `WHERE title = 'New conversation'` guard on the UPDATE. Rejected adding a `never_auto_title` boolean column — the auto-titler only ever runs on turn 1, so a flag solves a problem we don't have. YAGNI.

## 3. Scope

**In scope:**
- New `auto_title.py` module, Haiku-backed.
- `anthropic_call.call()` model-override parameter.
- `conversations.patch_title_if_default()` guarded UPDATE helper.
- `app.py:stream_turn` integration block.
- New SSE event type `title-updated`.
- Web SSE dispatch + reducer dispatch wiring.
- ChatStreamFn env var.
- Unit tests for each new function.

**Out of scope (explicit):**
- Backfill of existing `"New conversation"` rows (they title organically on next use; see §5.2).
- A `never_auto_title` flag column.
- Re-titling after N turns (topic-drift detection).
- Multi-language / i18n title generation.
- Per-tenant customization of the title prompt.
- iOS app changes (iOS doesn't render the conversation rail; the SP4 chat surface is web-only today).
- Updating the documented wire format in `app.py`'s module docstring (will be done as part of this implementation, but no separate spec needed).

## 4. Components & architecture

```
POST /v1/conversations/{id}/stream
    │
    ▼
chat_session/app.py:stream_turn  (existing)
    │
    ├─ M.append(cid, "user", …)            (existing)
    ├─ agentic loop → text-delta / tool-result SSE events  (existing)
    ├─ M.append(cid, "assistant", …)        (existing)
    │
    ├─ [NEW] if conv.title == "New conversation":
    │       title = auto_title.generate_title(user_text, assistant_text)
    │       if title:
    │           updated = C.patch_title_if_default(tid, cid, title)
    │           if updated:
    │               yield SSE {"type": "title-updated", "conversation_id": cid, "title": title}
    │
    └─ yield SSE {"type": "done"}            (existing)


chat_session/auto_title.py  [NEW]
    └─ generate_title(user_text, assistant_text) -> str | None
            │
            └─ anthropic_call.call(system=TITLE_SYSTEM_PROMPT,
                                   user_message=PROMPT_TEMPLATE.format(…),
                                   max_tokens=32,
                                   model="claude-haiku-4-5",
                                   timeout=5)
            └─ sanitize → trim, strip quotes, cap 60 chars
            └─ return title or None on any failure


chat_session/conversations.py
    └─ patch_title_if_default(tenant_id, cid, title) -> bool   [NEW]
            UPDATE conversations
               SET title = :title, updated_at = NOW()
             WHERE id = :cid::uuid
               AND tenant_id = :tenant_id::uuid
               AND title = 'New conversation'
             RETURNING id::text


web/src/chat/chatApi.ts:streamMessage  (existing function, new branch)
    └─ if ev.type === "title-updated":
            cb.onTitleUpdated?.(ev.conversation_id, ev.title)


web/src/chat/Shell.tsx  (existing, new callback)
    └─ onTitleUpdated: (cid, title) => {
            if (cid === activeId) dispatch({ type: "setTitle", title });
            refreshConversations();   // updates rail entry for non-active too
       }
```

## 5. Per-component design

### 5.1 `auto_title.py`

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
MAX_INPUT_CHARS_PER_TURN = 800   # cap input tokens; titles don't need full context

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


def _sanitize(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    # Strip surrounding quotes (straight + smart)
    for q in ('"', "'", "“", "”", "‘", "’"):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            s = s[1:-1].strip()
    # Reject empty after sanitization
    if not s:
        return None
    # Hard cap on length
    if len(s) > MAX_TITLE_CHARS:
        s = s[:MAX_TITLE_CHARS].rstrip()
    return s


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

### 5.2 `anthropic_call.call()` — backward-compatible model + timeout params

Change the signature from `call(system, user_message, max_tokens=2048)` to `call(system, user_message, max_tokens=2048, model=None, timeout=45)`:

- `model is None` → use the module-level `MODEL` constant (today: Sonnet). Backward-compatible with every existing call site.
- `model="claude-haiku-4-5"` → overrides per-call; the existing module `MODEL` env stays Sonnet.
- `timeout=5` → passed through to `urllib.request.urlopen(req, timeout=…)`. Default stays 45.

This keeps the auto-title path self-contained without forking a parallel `call_haiku()` function.

### 5.3 `conversations.patch_title_if_default()`

```python
def patch_title_if_default(tenant_id: str, conversation_id: str, title: str) -> bool:
    """Set title only if it's still the default 'New conversation'.

    Used by the auto-titler to ensure manual renames always win.
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

This helper is purely a guarded UPDATE — it has no opinion on whether *this* turn is the first one. The caller (§5.4) is responsible for that gating. A "stale conversation with no messages and default title" that the user posts into for the first time *will* get auto-titled, which is the intended organic-backfill behavior. A conversation that already has prior messages and still has the default title (e.g., title generation failed on the original first turn) will NOT be retroactively titled, because §5.4 only enters this code path when `prior_msg_count == 0`.

### 5.4 `app.py:stream_turn` integration

Insertion site: immediately before `yield _sse({"type": "done"})` (line 233), inside the generator's main try block.

```python
# Auto-title the conversation on the first turn (best-effort).
# Guard rails:
#   - only when title is still default ('New conversation')
#   - only when this was the first turn (history was empty before this request)
#   - never raises; never emits an error SSE
prior_msg_count = len(conv.get("messages", []))  # captured before we appended
if (
    prior_msg_count == 0
    and (conv.get("title") or "") == "New conversation"
    and final_assistant_text
):
    try:
        new_title = auto_title.generate_title(user_text, final_assistant_text)
        if new_title and C.patch_title_if_default(tenant_id, cid, new_title):
            yield _sse({
                "type":             "title-updated",
                "conversation_id":  cid,
                "title":            new_title,
            })
    except Exception as e:  # noqa: BLE001 — defence in depth
        print(f"auto_title block failed: {e}")

yield _sse({"type": "done"})
```

`prior_msg_count` must be captured at line 123 (where `history` is built) before any new appends, so we know whether *this* invocation is the first turn. Variable lifts to outer scope inside `gen()` via closure of `conv`.

Wire-format docstring at top of `app.py` is updated to add:

```
  data: {"type":"title-updated","conversation_id":"<uuid>","title":"<str>"}\n\n
```

### 5.5 Web wiring

**`chatApi.ts`** — extend `StreamCallbacks`:

```ts
export interface StreamCallbacks {
  onDelta:          (t: string) => void;
  onToolResult?:    (ev: ToolResultEvent) => void;
  onSideEffect?:    (toolName: string, intent: Record<string, unknown>) => void;
  onTitleUpdated?:  (conversationId: string, title: string) => void;   // NEW
}
```

Add one branch in the SSE dispatcher (after `tool-result`, before `error`):

```ts
} else if (ev.type === "title-updated") {
  cb.onTitleUpdated?.(ev.conversation_id, ev.title);
}
```

**`Shell.tsx`** — pass the callback. When fired:

- If the updated conversation is the active one, dispatch `{ type: "setTitle", title }` so the header and rail both update.
- Always call the existing `refreshConversations()` helper (or equivalent rail-list refetch — confirm during implementation) so non-active conversations also pick up the title.

(If `Shell.tsx` doesn't already have a rail-list refresh function, the dispatch + a manual mutation of the `conversations` array in component state are both acceptable.)

### 5.6 CDK / env vars

`platform/lib/api-stack.ts` ChatStreamFn environment block gets:

```ts
ANTHROPIC_TITLE_MODEL: 'claude-haiku-4-5',
```

No new IAM, no new secrets (the existing `ANTHROPIC_SECRET_NAME` Anthropic API key is reused). Hotswap-eligible deploy: `npx cdk deploy CisoCopilotApi --require-approval never --hotswap`.

## 6. Failure modes & error handling

| Failure | Behavior |
|---|---|
| Haiku 4xx / 5xx | `call()` raises `RuntimeError` → caught in `generate_title()` → returns `None` → no SSE event → title unchanged. |
| Haiku timeout (>5s) | `urllib` raises → same path. |
| Haiku returns empty / whitespace | `_sanitize()` returns `None` → same path. |
| Haiku returns multi-line / 100-word essay | `_sanitize()` strips and caps to 60 chars (truncation acceptable for the very rare verbose-LLM case). |
| Manual rename races to commit while Haiku call is in flight | `patch_title_if_default()` UPDATE WHERE clause fails (title is no longer `'New conversation'`) → returns `False` → no SSE event → manual title preserved. |
| Aurora Data API transient error on UPDATE | `_q` raises → caught in the outer `try/except` in `app.py` → logged → no SSE event → `done` event still fires. Chat completes normally. |
| `ANTHROPIC_TITLE_MODEL` env var not set | `auto_title.py` defaults to `claude-haiku-4-5`. Safe. |

The auto-title block is wrapped in a defensive `try/except` at the integration site **in addition to** the inner handler, so even an unexpected programming error (TypeError, KeyError) cannot break the chat stream.

## 7. Testing

### Unit tests — `platform/lambda/chat_session/tests/test_auto_title.py` (NEW)

1. `test_generate_title_happy_path` — mock `anthropic_call.call` to return `"AWS Critical Findings Overview"`; assert result equals that string.
2. `test_generate_title_strips_surrounding_quotes` — mock returns `'"AWS Critical Findings"'`; assert stripped to `AWS Critical Findings`. Test both straight and smart quotes.
3. `test_generate_title_caps_length` — mock returns 200-char string; assert result is ≤ 60 chars, no trailing whitespace.
4. `test_generate_title_returns_none_on_exception` — mock `call` to raise `RuntimeError("Anthropic HTTP 500")`; assert result is `None`.
5. `test_generate_title_returns_none_on_empty_output` — mock returns `""`; assert `None`. Same for whitespace-only.
6. `test_generate_title_returns_none_on_empty_inputs` — both `user_text` and `assistant_text` empty; assert `None` (no Haiku call made).
7. `test_generate_title_truncates_long_inputs` — pass a 5000-char user_text; assert the value forwarded to `call()` is ≤ MAX_INPUT_CHARS_PER_TURN.

### Unit tests — additions to `platform/lambda/chat_session/tests/test_conversations.py`

1. `test_patch_title_if_default_updates_default_row` — mock `_q` to return `[("id-1",)]`; assert function returns `True` and SQL contains `WHERE … AND title = 'New conversation'`.
2. `test_patch_title_if_default_noop_on_custom_title` — mock `_q` to return `[]`; assert `False`.
3. `test_patch_title_if_default_tenant_scoped` — assert the SQL has `tenant_id = :tenant_id::uuid` (defense against cross-tenant overwrite via a forged cid).

### Unit tests — additions to `platform/lambda/chat_session/tests/test_app.py` (or test_stream.py)

1. `test_stream_turn_emits_title_updated_on_first_turn` — mock conv with empty messages + default title; mock Haiku to return a title; assert one `title-updated` SSE event is yielded before `done`.
2. `test_stream_turn_skips_titling_when_history_not_empty` — mock conv with 4 prior messages; assert no `title-updated` SSE event.
3. `test_stream_turn_skips_titling_when_title_custom` — mock conv title `"My custom name"`; assert no `title-updated` event even on first turn.
4. `test_stream_turn_titling_failure_does_not_break_stream` — mock Haiku to raise; assert `done` event still fires and no error event is emitted.

### Manual smoke (post-hotswap)

1. Open `/chat`, click "+ New conversation", type "show me my AWS criticals", submit.
2. Watch the rail entry: should transition from "New conversation" to a meaningful title (e.g., "AWS Critical Findings Overview") before the page settles into idle.
3. Reload the page; assert the title persisted.
4. Open another new conversation, type a question, then immediately rename via kebab menu before the assistant finishes. Assert the manual rename wins (no auto-overwrite).
5. Open Network panel; in dev console, force the Haiku call to fail (set `ANTHROPIC_TITLE_MODEL` to a bad value via env override, redeploy). Send a first turn; assert chat completes normally and title stays "New conversation".

## 8. Operational notes

- Hotswap deploy on `CisoCopilotApi` (ChatStreamFn lives in that stack but runs on a Function URL, not API GW routes — hotswap updates code + env vars in one shot).
- No migration. No CFN resource delta.
- Cost: Haiku 4.5 at ~$0.80 / Mtok input + $4 / Mtok output. Title call is ~1k input + 32 output tokens → ~$0.0009 per first turn. Negligible.
- Observability: title generation logs `auto_title: Haiku call failed: <err>` on failure (`print` → CloudWatch). No new metric in this slice; revisit if titling failure rate becomes interesting.

## 9. Open follow-ups (NOT in this slice)

- Re-titling after material topic drift (e.g., turn 10 covers a completely different topic). Would need a heuristic for "drift" and an explicit user opt-in. Defer until requested.
- Mass-backfill existing `"New conversation"` rows via a one-shot Lambda. Defer until volume of stale rows justifies it.
- Promote `title-updated` to a generic `conversation-updated` event carrying any conversation-level field change. Defer until a second use case appears.

## 10. References

- `HANDOFF.md` — "Open punch-list items deferred" (2026-06-12) — original problem statement.
- `docs/superpowers/specs/2026-05-19-sp4-chat-first-design.md` §7.1 — `conversations` schema origin.
- `platform/lambda/chat_session/app.py` lines 30 + 23–29 — SSE wire-format docstring (target of update).
- Anthropic Messages API — https://docs.anthropic.com/en/api/messages
