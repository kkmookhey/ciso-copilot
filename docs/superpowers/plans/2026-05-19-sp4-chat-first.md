# SP4: Chat-First Front Door — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stat-tile Welcome page with a chat-first surface at `/` that supports text (Anthropic streaming) and voice (OpenAI Realtime over WebRTC), persists conversations to Aurora, renders typed artifact cards with citation chips, and routes action proposals through inline approval cards.

**Architecture:** A single `chat_session` Lambda exposes conversation CRUD + voice key minting on API Gateway REST, plus a streaming text endpoint on a separate Lambda Function URL (REST can't stream). The web app is a four-column shell. Both LLMs share one TypeScript tool catalog; tool results carry an `_artifact_hint` that a single renderer switch turns into one of 8 card components. Determinism invariant: LLMs never mutate data — only `propose_*` tools + user approval.

**Tech Stack:** Python 3.12 Lambdas (Aurora Data API, boto3), AWS CDK (TypeScript), Vite + React + TS + Tailwind, Anthropic Messages API (`claude-sonnet-4-6`), OpenAI Realtime (`gpt-realtime`), WebRTC (`stasel/WebRTC` not needed on web — browser RTCPeerConnection).

---

## Prerequisites

Before starting, verify:

- [ ] `git rev-parse main` → `d288cb3...` (SP1 + Slice 1b merged). If not, stop — SP4 depends on the `entities` table and `entities_api` Lambda.
- [ ] On branch `feat/sp4-chat-first` (branched from main): `git branch --show-current`
- [ ] Anthropic API key exists: `aws secretsmanager describe-secret --secret-id ciso-copilot/anthropic-api-key` returns OK (used today by `/policies`).
- [ ] OpenAI API key exists: `aws secretsmanager describe-secret --secret-id ciso-copilot/openai-api-key` returns OK (used today by `voice_session`).
- [ ] Web dev server runs: `cd web && pnpm dev` serves on localhost.
- [ ] CDK builds: `cd platform && npx cdk synth CisoCopilotApi >/dev/null`.

## File Structure Map

**Backend (new):**

| File | Responsibility |
|---|---|
| `platform/sql/006_conversations.sql` | `conversations` + `conversation_messages` tables |
| `platform/lambda/chat_session/main.py` | Router — dispatches REST + Function URL events |
| `platform/lambda/chat_session/conversations.py` | Conversation CRUD (list/create/get/patch/delete) |
| `platform/lambda/chat_session/messages.py` | Append message; Anthropic streaming for `/stream` |
| `platform/lambda/chat_session/voice.py` | OpenAI Realtime ephemeral key mint |
| `platform/lambda/chat_session/tools_dispatch.py` | Server-side read-only data tools |
| `platform/lambda/chat_session/anthropic_call.py` | Copy of the streaming-capable Anthropic helper |
| `platform/lambda/chat_session/prompts.py` | PERSONA + TOOL_RULES + addenda |
| `platform/lambda/chat_session/_db.py` | Shared `_resp`, `_resolve_tenant_id`, `_q` Data-API helpers |
| `platform/lib/chat-fn-url-stack.ts` | Lambda Function URL (streaming) stack |

**Backend (modified):**

| File | Change |
|---|---|
| `platform/lib/api-stack.ts` | Add `chat_session` Lambda + 6 REST routes |
| `platform/bin/platform.ts` | Instantiate `ChatFnUrlStack` |

**Frontend (new):** `web/src/chat/` — `Shell.tsx`, `ModuleRail.tsx`, `ConversationRail.tsx`, `ChatCenter.tsx`, `Composer.tsx`, `MessageStream.tsx`, `Artifact.tsx`, `artifacts/{KpiCard,EntityList,FindingCard,RiskCard,ChartBar,ChartDonut,SeverityBreakdown,ApprovalCard}.tsx`, `SourceSideSheet.tsx`, `tools.ts`, `prompts.ts`, `voiceClient.ts`, `anthropicClient.ts`, `state.ts`, `chatApi.ts`.

**Frontend (modified):** `web/src/App.tsx` (route table), `web/src/routes/Shell.tsx` → reused or replaced by `chat/Shell.tsx`, `web/src/routes/Welcome.tsx` → renamed `Dashboard.tsx`.

**Removed:** `web/src/voice/VoiceChat.tsx` (voice modal). `web/src/voice/` keeps the WebRTC client class only.

---

## Phase 4a — Shell + Text Chat (~4d)

**Demo at end of 4a:** KK signs in → 3 morning-briefing cards appear → types "what's my IAM posture?" → an Anthropic text reply streams into the conversation. No artifacts, no voice yet.

### Task 4a.1: Aurora migration — conversations + conversation_messages

**Files:**
- Create: `platform/sql/006_conversations.sql`

- [ ] **Step 1: Write the migration**

```sql
-- platform/sql/006_conversations.sql
-- SP4 — chat-first front door. Spec: docs/superpowers/specs/2026-05-19-sp4-chat-first-design.md §7.1

BEGIN;

CREATE TABLE conversations (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id           UUID NOT NULL REFERENCES users(user_id),
  title             TEXT NOT NULL DEFAULT 'New conversation',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at        TIMESTAMPTZ
);

CREATE INDEX conversations_tenant_user_recent_idx
  ON conversations(tenant_id, user_id, last_activity_at DESC)
  WHERE deleted_at IS NULL;

CREATE TABLE conversation_messages (
  id              UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL
                   CHECK (role IN ('user','assistant','tool','system')),
  content         JSONB NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX conversation_messages_conv_created_idx
  ON conversation_messages(conversation_id, created_at);

COMMIT;
```

- [ ] **Step 2: Apply via the Data API**

Run:
```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "$(cat platform/sql/006_conversations.sql)"
```
Expected: a JSON response with no `error`. (The Data API runs the whole batch; `BEGIN/COMMIT` is accepted.)

- [ ] **Step 3: Verify the tables exist**

Run:
```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT table_name FROM information_schema.tables WHERE table_name IN ('conversations','conversation_messages') ORDER BY table_name"
```
Expected: 2 rows — `conversation_messages`, `conversations`.

- [ ] **Step 4: Commit**

```bash
git add platform/sql/006_conversations.sql
git commit -m "feat(sql): 006 — conversations + conversation_messages tables"
```

### Task 4a.2: chat_session Lambda — shared DB helpers

**Files:**
- Create: `platform/lambda/chat_session/_db.py`
- Create: `platform/lambda/chat_session/tests/__init__.py` (empty)
- Create: `platform/lambda/chat_session/tests/conftest.py`
- Test: `platform/lambda/chat_session/tests/test_db.py`

- [ ] **Step 1: Write conftest for bare imports** — copy the pattern from `platform/lambda/ai_scanner/tests/conftest.py`:

```python
# platform/lambda/chat_session/tests/conftest.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

- [ ] **Step 2: Write the failing test**

```python
# platform/lambda/chat_session/tests/test_db.py
from _db import _resp, _claim_value


def test_resp_includes_cors_and_json_body():
    r = _resp(200, {"ok": True})
    assert r["statusCode"] == 200
    assert r["headers"]["access-control-allow-origin"] == "*"
    assert r["headers"]["content-type"] == "application/json"
    assert r["body"] == '{"ok": true}'


def test_claim_value_unwraps_data_api_field():
    assert _claim_value({"stringValue": "abc"}) == "abc"
    assert _claim_value({"isNull": True}) is None
    assert _claim_value({"longValue": 3}) == 3
```

- [ ] **Step 3: Run it — expect failure**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '_db'`.

- [ ] **Step 4: Write `_db.py`**

```python
# platform/lambda/chat_session/_db.py
"""Shared Data-API helpers for the chat_session Lambda.

Mirrors the _resp / tenant-resolution pattern used across the other
v1 Lambdas. CORS header is mandatory on every response — see HANDOFF.md
gotcha 11.
"""
from __future__ import annotations

import json
import os

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
DB_NAME = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")

_CORS = {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
}


def _resp(status: int, body: dict | list) -> dict:
    return {"statusCode": status, "headers": _CORS, "body": json.dumps(body)}


def _claim_value(field: dict):
    """Unwrap one Data-API column value."""
    if field.get("isNull"):
        return None
    for k in ("stringValue", "longValue", "booleanValue", "doubleValue"):
        if k in field:
            return field[k]
    return None


def _q(sql: str, params: dict | None = None) -> list[list[dict]]:
    """Run a parameterized statement; return raw Data-API records."""
    kwargs = {
        "resourceArn": DB_CLUSTER_ARN,
        "secretArn": DB_SECRET_ARN,
        "database": DB_NAME,
        "sql": sql,
    }
    if params:
        kwargs["parameters"] = [
            {"name": k, "value": _wrap(v)} for k, v in params.items()
        ]
    return rds_data.execute_statement(**kwargs).get("records", [])


def _wrap(v):
    if v is None:
        return {"isNull": True}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"longValue": v}
    return {"stringValue": str(v)}


def _resolve_tenant_id(event: dict) -> str | None:
    """Read tenant_id from the Cognito authorizer claims, same as the
    other Lambdas. For Function-URL events the JWT is verified upstream
    in main.py and tenant_id is injected onto event['_tenant_id']."""
    if event.get("_tenant_id"):
        return event["_tenant_id"]
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )
    return claims.get("custom:tenant_id") or None


def _resolve_user_id(event: dict) -> str | None:
    if event.get("_user_id"):
        return event["_user_id"]
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )
    return claims.get("custom:user_id") or claims.get("sub") or None
```

> Note: confirm the actual claim keys by grepping an existing Lambda — `grep -rn "custom:tenant_id\|tenant_id" platform/lambda/me/main.py`. If `me` resolves tenant differently (e.g. a `users` lookup by `sub`), copy that exact logic into `_resolve_tenant_id` instead.

- [ ] **Step 5: Run the test — expect pass**

Run: `cd platform/lambda/chat_session && python -m pytest tests/test_db.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/chat_session/_db.py platform/lambda/chat_session/tests/
git commit -m "feat(platform): chat_session — shared Data-API helpers"
```

### Task 4a.3: chat_session — conversation CRUD

**Files:**
- Create: `platform/lambda/chat_session/conversations.py`
- Test: `platform/lambda/chat_session/tests/test_conversations.py`

- [ ] **Step 1: Write the failing test** (uses a fake `_q` injected via monkeypatch)

```python
# platform/lambda/chat_session/tests/test_conversations.py
import conversations as C


def test_create_conversation_returns_uuid(monkeypatch):
    captured = {}

    def fake_q(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(C, "_q", fake_q)
    out = C.create("tenant-1", "user-1")
    assert "conversation_id" in out
    assert len(out["conversation_id"]) == 36
    assert "INSERT INTO conversations" in captured["sql"]
    assert captured["params"]["tenant_id"] == "tenant-1"


def test_list_filters_deleted(monkeypatch):
    monkeypatch.setattr(C, "_q", lambda sql, params=None: (
        captured.setdefault("sql", sql) and []
    ))
    captured = {}
    monkeypatch.setattr(C, "_q", lambda sql, params=None: captured.update(sql=sql) or [])
    C.list_for("tenant-1", "user-1")
    assert "deleted_at IS NULL" in captured["sql"]
```

- [ ] **Step 2: Run — expect failure** (`ModuleNotFoundError: conversations`).

- [ ] **Step 3: Write `conversations.py`**

```python
# platform/lambda/chat_session/conversations.py
"""Conversation CRUD. All queries tenant+user scoped."""
from __future__ import annotations

import uuid

from _db import _q, _claim_value


def create(tenant_id: str, user_id: str, title: str = "New conversation") -> dict:
    cid = str(uuid.uuid4())
    _q(
        "INSERT INTO conversations (id, tenant_id, user_id, title) "
        "VALUES (:id::uuid, :tenant_id::uuid, :user_id::uuid, :title)",
        {"id": cid, "tenant_id": tenant_id, "user_id": user_id, "title": title},
    )
    return {"conversation_id": cid}


def list_for(tenant_id: str, user_id: str) -> dict:
    rows = _q(
        "SELECT id::text, title, last_activity_at::text "
        "FROM conversations "
        "WHERE tenant_id = :tenant_id::uuid AND user_id = :user_id::uuid "
        "AND deleted_at IS NULL "
        "ORDER BY last_activity_at DESC LIMIT 100",
        {"tenant_id": tenant_id, "user_id": user_id},
    )
    return {
        "conversations": [
            {
                "id": _claim_value(r[0]),
                "title": _claim_value(r[1]),
                "last_activity_at": _claim_value(r[2]),
            }
            for r in rows
        ]
    }


def get(tenant_id: str, conversation_id: str) -> dict | None:
    """Return conversation + ordered messages, or None if not in tenant."""
    head = _q(
        "SELECT id::text, title FROM conversations "
        "WHERE id = :id::uuid AND tenant_id = :tenant_id::uuid "
        "AND deleted_at IS NULL",
        {"id": conversation_id, "tenant_id": tenant_id},
    )
    if not head:
        return None
    msgs = _q(
        "SELECT role, content::text, created_at::text "
        "FROM conversation_messages "
        "WHERE conversation_id = :id::uuid ORDER BY created_at",
        {"id": conversation_id},
    )
    import json

    return {
        "id": _claim_value(head[0][0]),
        "title": _claim_value(head[0][1]),
        "messages": [
            {
                "role": _claim_value(m[0]),
                "content": json.loads(_claim_value(m[1])),
                "created_at": _claim_value(m[2]),
            }
            for m in msgs
        ],
    }


def patch_title(tenant_id: str, conversation_id: str, title: str) -> bool:
    rows = _q(
        "UPDATE conversations SET title = :title, updated_at = NOW() "
        "WHERE id = :id::uuid AND tenant_id = :tenant_id::uuid "
        "RETURNING id::text",
        {"title": title, "id": conversation_id, "tenant_id": tenant_id},
    )
    return bool(rows)


def soft_delete(tenant_id: str, conversation_id: str) -> bool:
    rows = _q(
        "UPDATE conversations SET deleted_at = NOW() "
        "WHERE id = :id::uuid AND tenant_id = :tenant_id::uuid "
        "AND deleted_at IS NULL RETURNING id::text",
        {"id": conversation_id, "tenant_id": tenant_id},
    )
    return bool(rows)
```

- [ ] **Step 4: Run — expect 2 passed.**

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/chat_session/conversations.py platform/lambda/chat_session/tests/test_conversations.py
git commit -m "feat(platform): chat_session — conversation CRUD"
```

### Task 4a.4: chat_session — message append + router

**Files:**
- Create: `platform/lambda/chat_session/messages.py`
- Create: `platform/lambda/chat_session/main.py`
- Test: `platform/lambda/chat_session/tests/test_router.py`

- [ ] **Step 1: Write the failing router test**

```python
# platform/lambda/chat_session/tests/test_router.py
import main


def _evt(method, path, path_params=None, body=None, tenant="t1", user="u1"):
    import json
    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params or {},
        "body": json.dumps(body) if body else None,
        "requestContext": {
            "authorizer": {"claims": {
                "custom:tenant_id": tenant, "custom:user_id": user}}
        },
    }


def test_unknown_route_400(monkeypatch):
    r = main.handler(_evt("GET", "/v1/nonsense"), None)
    assert r["statusCode"] == 400


def test_no_tenant_401():
    r = main.handler({"httpMethod": "GET", "path": "/v1/conversations",
                       "requestContext": {"authorizer": {"claims": {}}}}, None)
    assert r["statusCode"] == 401
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Write `messages.py`**

```python
# platform/lambda/chat_session/messages.py
"""Append a fully-formed message to a conversation."""
from __future__ import annotations

import json
import uuid

from _db import _q


VALID_ROLES = {"user", "assistant", "tool", "system"}


def append(conversation_id: str, role: str, content: dict) -> dict:
    if role not in VALID_ROLES:
        raise ValueError(f"bad role {role}")
    mid = str(uuid.uuid4())
    _q(
        "INSERT INTO conversation_messages (id, conversation_id, role, content) "
        "VALUES (:id::uuid, :cid::uuid, :role, :content::jsonb)",
        {"id": mid, "cid": conversation_id, "role": role,
         "content": json.dumps(content)},
    )
    _q(
        "UPDATE conversations SET last_activity_at = NOW(), updated_at = NOW() "
        "WHERE id = :cid::uuid",
        {"cid": conversation_id},
    )
    return {"message_id": mid}
```

- [ ] **Step 4: Write `main.py` (REST routes only — `/stream` added in 4a.6)**

```python
# platform/lambda/chat_session/main.py
"""chat_session Lambda — REST router.

Routes (API Gateway REST):
  POST   /v1/conversations
  GET    /v1/conversations
  GET    /v1/conversations/{id}
  PATCH  /v1/conversations/{id}
  DELETE /v1/conversations/{id}
  POST   /v1/conversations/{id}/messages
  POST   /v1/conversations/{id}/voice

The streaming text route POST /v1/conversations/{id}/stream is served
by the SAME deployment artifact but invoked through a Lambda Function
URL — see stream_handler in messages_stream.py (Task 4a.6).
"""
from __future__ import annotations

import json

import conversations as C
import messages as M
from _db import _resp, _resolve_tenant_id, _resolve_user_id


def _body(event: dict) -> dict:
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id:
        return _resp(401, {"error": "no_tenant"})
    user_id = _resolve_user_id(event)

    method = event.get("httpMethod", "GET")
    path = event.get("path") or ""
    cid = (event.get("pathParameters") or {}).get("id")

    if method == "POST" and path.endswith("/voice") and cid:
        import voice
        return voice.mint(event, tenant_id, cid)
    if method == "POST" and path.endswith("/messages") and cid:
        body = _body(event)
        if not C.get(tenant_id, cid):
            return _resp(404, {"error": "not_found"})
        out = M.append(cid, body.get("role", "user"), body.get("content", {}))
        return _resp(200, out)
    if method == "POST" and path.rstrip("/").endswith("/conversations"):
        return _resp(200, C.create(tenant_id, user_id))
    if method == "GET" and path.rstrip("/").endswith("/conversations"):
        return _resp(200, C.list_for(tenant_id, user_id))
    if method == "GET" and cid:
        conv = C.get(tenant_id, cid)
        return _resp(200, conv) if conv else _resp(404, {"error": "not_found"})
    if method == "PATCH" and cid:
        ok = C.patch_title(tenant_id, cid, _body(event).get("title", ""))
        return _resp(200, {"ok": True}) if ok else _resp(404, {"error": "not_found"})
    if method == "DELETE" and cid:
        ok = C.soft_delete(tenant_id, cid)
        return _resp(200, {"ok": True}) if ok else _resp(404, {"error": "not_found"})
    return _resp(400, {"error": "unsupported"})
```

- [ ] **Step 5: Run — expect 2 passed.** (`voice` import is lazy, so the router test passes before `voice.py` exists.)

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/chat_session/messages.py platform/lambda/chat_session/main.py platform/lambda/chat_session/tests/test_router.py
git commit -m "feat(platform): chat_session — message append + REST router"
```

### Task 4a.5: chat_session — voice key mint (stub for now)

**Files:**
- Create: `platform/lambda/chat_session/voice.py`

> The full voice payload (tools, persona) lands in Phase 4c. For 4a, `voice.py` exists only so the lazy import in `main.py` resolves and the Lambda packages cleanly. Port the working logic from `platform/lambda/voice_session/main.py`.

- [ ] **Step 1: Write `voice.py`** by copying the ephemeral-key mint from `platform/lambda/voice_session/main.py`. Keep the function signature `mint(event, tenant_id, conversation_id) -> dict`. Bind `conversation_id` into the session `metadata`. Leave `tools` empty for now (filled in 4c.1).

- [ ] **Step 2: Smoke-import test**

Run: `cd platform/lambda/chat_session && python -c "import voice; print('ok')"`
Expected: `ok` (no syntax/import error).

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/chat_session/voice.py
git commit -m "feat(platform): chat_session — voice key mint (ported from voice_session)"
```

### Task 4a.6: chat_session — Anthropic streaming handler (Function URL)

**Files:**
- Create: `platform/lambda/chat_session/anthropic_call.py` (copy from `platform/lambda/policies/anthropic_call.py`, add streaming)
- Create: `platform/lambda/chat_session/messages_stream.py`
- Create: `platform/lambda/chat_session/prompts.py` (minimal — full version in 4c.3)
- Test: `platform/lambda/chat_session/tests/test_stream.py`

- [ ] **Step 1: Copy the Anthropic helper and add a streaming generator**

Copy `platform/lambda/policies/anthropic_call.py` → `platform/lambda/chat_session/anthropic_call.py`. Add a `stream_messages(system, messages, tools=None)` generator that uses `urllib` against `https://api.anthropic.com/v1/messages` with `"stream": true` and yields parsed SSE events (`content_block_delta`, `message_stop`, `tool_use`).

- [ ] **Step 2: Write a minimal `prompts.py`**

```python
# platform/lambda/chat_session/prompts.py
"""System prompt blocks. Full PERSONA/addenda land in Task 4c.3."""
PERSONA = (
    "You are CISO Copilot. Calm, precise, slightly understated — an "
    "experienced security engineer on a Tuesday afternoon."
)
TOOL_RULES = (
    "Never invent data. For ambiguous questions, default to open + "
    "unresolved findings on the latest scan. For action requests, you "
    "MUST call a propose_* tool — never claim to have changed anything."
)


def system_for_text(user_first_name: str = "there") -> str:
    return f"{PERSONA}\n\n{TOOL_RULES}".replace("{user_first_name}", user_first_name)
```

- [ ] **Step 3: Write the failing test**

```python
# platform/lambda/chat_session/tests/test_stream.py
import messages_stream as MS


def test_verify_jwt_rejects_missing_header():
    evt = {"headers": {}}
    assert MS._extract_bearer(evt) is None


def test_extract_bearer_parses_header():
    evt = {"headers": {"authorization": "Bearer abc.def.ghi"}}
    assert MS._extract_bearer(evt) == "abc.def.ghi"
```

- [ ] **Step 4: Run — expect failure.**

- [ ] **Step 5: Write `messages_stream.py`**

```python
# platform/lambda/chat_session/messages_stream.py
"""Streaming text turn — served via Lambda Function URL (RESPONSE_STREAM).

POST /v1/conversations/{id}/stream  body {text}
  → append user message
  → call Anthropic streaming
  → emit SSE: text-delta / tool-use / done
  → on completion, persist assistant + tool messages

Auth: AuthType=NONE at AWS layer; JWT verified here against the Cognito
JWKS, same as the API Gateway authorizer.
"""
from __future__ import annotations

import json

import conversations as C
import messages as M
import prompts
from anthropic_call import stream_messages
from _db import _resp


def _extract_bearer(event: dict) -> str | None:
    h = (event.get("headers") or {})
    auth = h.get("authorization") or h.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _verify_jwt(token: str) -> dict | None:
    """Verify against Cognito JWKS. Return claims or None.
    Reuse the exact verification used by an existing Lambda — grep for
    'jwks' or 'PyJWT' in platform/lambda/auth_discover/main.py and port
    that verifier here verbatim (it already caches the JWKS)."""
    raise NotImplementedError("port the verifier in Step 6")


# Lambda Function URL streaming entry point.
# CDK sets InvokeMode=RESPONSE_STREAM; the runtime passes a writable
# stream as the second arg via awslambdaric's streaming wrapper.
def handler(event, response_stream):
    token = _extract_bearer(event)
    claims = _verify_jwt(token) if token else None
    if not claims:
        response_stream.write(b'data: {"error":"unauthorized"}\n\n')
        response_stream.end()
        return

    tenant_id = claims.get("custom:tenant_id")
    body = json.loads(event.get("body") or "{}")
    cid = (event.get("pathParameters") or {}).get("id")
    conv = C.get(tenant_id, cid)
    if not conv:
        response_stream.write(b'data: {"error":"not_found"}\n\n')
        response_stream.end()
        return

    user_text = body.get("text", "")
    M.append(cid, "user", {"text": user_text, "modality": "text"})

    history = [
        {"role": m["role"], "content": _to_anthropic(m["content"])}
        for m in conv["messages"]
        if m["role"] in ("user", "assistant")
    ]
    history.append({"role": "user", "content": user_text})

    assistant_text = []
    for ev in stream_messages(prompts.system_for_text(), history):
        if ev["type"] == "text-delta":
            assistant_text.append(ev["text"])
            chunk = json.dumps({"type": "text-delta", "text": ev["text"]})
            response_stream.write(f"data: {chunk}\n\n".encode())
    response_stream.write(b'data: {"type":"done"}\n\n')
    response_stream.end()

    M.append(cid, "assistant",
             {"text": "".join(assistant_text), "modality": "text"})


def _to_anthropic(content: dict) -> str:
    return content.get("text", "")
```

- [ ] **Step 6: Port the JWT verifier.** Open `platform/lambda/auth_discover/main.py`, find the JWKS fetch + `jwt.decode` logic, and replace the `NotImplementedError` body of `_verify_jwt` with that exact code. Confirm `PyJWT[crypto]` is in `requirements.txt` (add it — `PyJWT[crypto]==2.10.1`, matching `ai_scanner`).

- [ ] **Step 7: Run the test — expect 2 passed.**

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/chat_session/anthropic_call.py platform/lambda/chat_session/messages_stream.py platform/lambda/chat_session/prompts.py platform/lambda/chat_session/requirements.txt platform/lambda/chat_session/tests/test_stream.py
git commit -m "feat(platform): chat_session — Anthropic streaming handler for Function URL"
```

### Task 4a.7: CDK — wire chat_session REST routes + Function URL

**Files:**
- Modify: `platform/lib/api-stack.ts`
- Create: `platform/lib/chat-fn-url-stack.ts`
- Modify: `platform/bin/platform.ts`

- [ ] **Step 1: Add the `chat_session` Lambda + REST routes in `api-stack.ts`**

After the existing Lambda definitions, add:

```typescript
// ========================================================================
// /v1/conversations* — chat_session (SP4)
// ========================================================================
const chatSessionFn = new lambda.Function(this, 'ChatSessionFn', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'main.handler',
  code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'chat_session')),
  timeout: cdk.Duration.seconds(30),
  memorySize: 512,
  environment: {
    ...dbEnv,
    OPENAI_API_KEY_SECRET_ARN: props.openaiApiKeySecret.secretArn,
    ANTHROPIC_API_KEY_SECRET_ARN: 'ciso-copilot/anthropic-api-key',
    USER_POOL_ID: props.userPool.userPoolId,
  },
});
props.dbCluster.grantDataApiAccess(chatSessionFn);
props.openaiApiKeySecret.grantRead(chatSessionFn);
secretsmanager.Secret.fromSecretNameV2(
  this, 'AnthropicKeyForChat', 'ciso-copilot/anthropic-api-key',
).grantRead(chatSessionFn);

const conversations = api.root.addResource('conversations'); // under /v1
conversations.addMethod('GET',  new apigw.LambdaIntegration(chatSessionFn), { authorizer });
conversations.addMethod('POST', new apigw.LambdaIntegration(chatSessionFn), { authorizer });
const convById = conversations.addResource('{id}');
convById.addMethod('GET',    new apigw.LambdaIntegration(chatSessionFn), { authorizer });
convById.addMethod('PATCH',  new apigw.LambdaIntegration(chatSessionFn), { authorizer });
convById.addMethod('DELETE', new apigw.LambdaIntegration(chatSessionFn), { authorizer });
convById.addResource('messages').addMethod('POST', new apigw.LambdaIntegration(chatSessionFn), { authorizer });
convById.addResource('voice').addMethod('POST',    new apigw.LambdaIntegration(chatSessionFn), { authorizer });
```

> Confirm the names of the existing `api` RestApi var and `authorizer` var by reading the surrounding `api-stack.ts` — match them exactly. The resource is added under `/v1` if the existing routes mount under a `/v1` resource; mirror whatever `/findings` does.

- [ ] **Step 2: Export `chatSessionFn` so the Function URL stack can attach to it.** Add a public readonly field on `ApiStack`:

```typescript
public readonly chatSessionFn: lambda.Function;
// ...in constructor, after creating it:
this.chatSessionFn = chatSessionFn;
```

- [ ] **Step 3: Create `chat-fn-url-stack.ts`**

```typescript
// platform/lib/chat-fn-url-stack.ts
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

interface ChatFnUrlStackProps extends cdk.StackProps {
  chatSessionFn: lambda.Function;
}

export class ChatFnUrlStack extends cdk.Stack {
  public readonly streamUrl: string;

  constructor(scope: Construct, id: string, props: ChatFnUrlStackProps) {
    super(scope, id, props);

    const fnUrl = props.chatSessionFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,   // JWT verified in-Lambda
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
      cors: {
        allowedOrigins: ['*'],
        allowedMethods: [lambda.HttpMethod.POST],
        allowedHeaders: ['authorization', 'content-type'],
      },
    });

    this.streamUrl = fnUrl.url;
    new cdk.CfnOutput(this, 'ChatStreamUrl', { value: fnUrl.url });
  }
}
```

> The Function URL invokes `chat_session` with the default `main.handler`. The streaming handler lives in `messages_stream.handler`. Set the Function URL's handler override is not possible per-URL — instead, in `main.handler`, detect a Function-URL event (`event['requestContext']['http']` present + path ends `/stream`) and delegate to `messages_stream.handler`. Add that branch at the TOP of `main.handler`:
>
> ```python
> rc_http = event.get("requestContext", {}).get("http")
> if rc_http and (event.get("rawPath", "")).endswith("/stream"):
>     import messages_stream
>     return messages_stream.handler(event, context)  # context is the stream
> ```
>
> Note the streaming runtime passes the response stream as the 2nd positional arg; verify against AWS docs for Python `RESPONSE_STREAM` at implementation time — if Python streaming requires the `awslambdaric` streaming decorator, apply it to a dedicated entry module instead and point the Function URL there via a separate `lambda.Function` that shares the same code asset with `handler: 'messages_stream.handler'`. Prefer the separate-Function approach if the dual-dispatch is awkward.

- [ ] **Step 4: Instantiate in `platform/bin/platform.ts`**

```typescript
const chatFnUrl = new ChatFnUrlStack(app, 'CisoCopilotChatFnUrl', {
  env,
  chatSessionFn: apiStack.chatSessionFn,
});
```

- [ ] **Step 5: Synth to catch errors**

Run: `cd platform && npx cdk synth CisoCopilotApi CisoCopilotChatFnUrl >/dev/null`
Expected: no errors.

- [ ] **Step 6: Deploy**

Run:
```bash
cd platform
npx cdk deploy CisoCopilotApi CisoCopilotChatFnUrl --require-approval never
```
Expected: both stacks `UPDATE_COMPLETE` / `CREATE_COMPLETE`. Note the `ChatStreamUrl` output.

- [ ] **Step 7: Smoke-test the REST route**

Run (replace `$TOKEN` with a valid id_token from the web app's localStorage):
```bash
curl -s -X POST https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1/conversations \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json'
```
Expected: `{"conversation_id":"<uuid>"}`.

- [ ] **Step 8: Commit**

```bash
git add platform/lib/api-stack.ts platform/lib/chat-fn-url-stack.ts platform/bin/platform.ts platform/lambda/chat_session/main.py
git commit -m "feat(platform): wire chat_session REST routes + streaming Function URL"
```

---

### Task 4a.8: Web — chatApi.ts client

**Files:**
- Create: `web/src/chat/chatApi.ts`

- [ ] **Step 1: Write the client.** It mirrors `web/src/lib/api.ts` (Bearer token via `validIdToken()`), and adds the streaming base URL.

```typescript
// web/src/chat/chatApi.ts
import { validIdToken } from "../lib/cognito";

const REST_BASE = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";
// Set from the ChatFnUrlStack output (Task 4a.7 Step 6):
const STREAM_BASE = import.meta.env.VITE_CHAT_STREAM_URL ?? "";

export type Role = "user" | "assistant" | "tool" | "system";
export interface ChatMessage { role: Role; content: any; created_at?: string; }
export interface ConversationSummary { id: string; title: string; last_activity_at: string; }

async function authedFetch(url: string, init: RequestInit = {}) {
  const token = await validIdToken();
  return fetch(url, {
    ...init,
    headers: { ...(init.headers || {}), Authorization: `Bearer ${token}`,
               "content-type": "application/json" },
  });
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const r = await authedFetch(`${REST_BASE}/conversations`);
  return (await r.json()).conversations;
}

export async function createConversation(): Promise<string> {
  const r = await authedFetch(`${REST_BASE}/conversations`, { method: "POST" });
  return (await r.json()).conversation_id;
}

export async function getConversation(id: string) {
  const r = await authedFetch(`${REST_BASE}/conversations/${id}`);
  return r.json() as Promise<{ id: string; title: string; messages: ChatMessage[] }>;
}

export async function appendMessage(id: string, role: Role, content: any) {
  await authedFetch(`${REST_BASE}/conversations/${id}/messages`,
    { method: "POST", body: JSON.stringify({ role, content }) });
}

export async function patchTitle(id: string, title: string) {
  await authedFetch(`${REST_BASE}/conversations/${id}`,
    { method: "PATCH", body: JSON.stringify({ title }) });
}

export async function deleteConversation(id: string) {
  await authedFetch(`${REST_BASE}/conversations/${id}`, { method: "DELETE" });
}

/** Streaming text turn. Calls onDelta for each token; resolves on done. */
export async function streamMessage(
  conversationId: string, text: string,
  onDelta: (t: string) => void,
): Promise<void> {
  const token = await validIdToken();
  const res = await fetch(`${STREAM_BASE}/v1/conversations/${conversationId}/stream`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.type === "text-delta") onDelta(ev.text);
    }
  }
}
```

- [ ] **Step 2: Add `VITE_CHAT_STREAM_URL` to `web/.env`** with the `ChatStreamUrl` output value from Task 4a.7. (`.env` is gitignored — note the value in `HANDOFF.md` instead.)

- [ ] **Step 3: Typecheck**

Run: `cd web && pnpm tsc --noEmit`
Expected: no errors in `chatApi.ts`.

- [ ] **Step 4: Commit**

```bash
git add web/src/chat/chatApi.ts
git commit -m "feat(web): chat — chatApi client (REST + streaming)"
```

### Task 4a.9: Web — conversation state reducer

**Files:**
- Create: `web/src/chat/state.ts`
- Test: `web/src/chat/state.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// web/src/chat/state.test.ts
import { describe, it, expect } from "vitest";
import { chatReducer, initialState } from "./state";

describe("chatReducer", () => {
  it("appends a user message", () => {
    const s = chatReducer(initialState, {
      type: "append", message: { role: "user", content: { text: "hi" } },
    });
    expect(s.messages).toHaveLength(1);
  });

  it("streamDelta appends to the last assistant message", () => {
    let s = chatReducer(initialState, {
      type: "append", message: { role: "assistant", content: { text: "" } },
    });
    s = chatReducer(s, { type: "streamDelta", text: "Hel" });
    s = chatReducer(s, { type: "streamDelta", text: "lo" });
    expect(s.messages[0].content.text).toBe("Hello");
  });
});
```

- [ ] **Step 2: Run — expect failure** (`cd web && pnpm vitest run src/chat/state.test.ts`).

- [ ] **Step 3: Write `state.ts`**

```typescript
// web/src/chat/state.ts
import type { ChatMessage } from "./chatApi";

export interface ChatState {
  conversationId: string | null;
  title: string;
  messages: ChatMessage[];
  streaming: boolean;
}

export const initialState: ChatState = {
  conversationId: null, title: "New conversation", messages: [], streaming: false,
};

export type ChatAction =
  | { type: "load"; id: string; title: string; messages: ChatMessage[] }
  | { type: "append"; message: ChatMessage }
  | { type: "streamDelta"; text: string }
  | { type: "streaming"; on: boolean };

export function chatReducer(s: ChatState, a: ChatAction): ChatState {
  switch (a.type) {
    case "load":
      return { ...s, conversationId: a.id, title: a.title, messages: a.messages };
    case "append":
      return { ...s, messages: [...s.messages, a.message] };
    case "streamDelta": {
      const msgs = s.messages.slice();
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        msgs[msgs.length - 1] = {
          ...last, content: { ...last.content, text: (last.content.text ?? "") + a.text },
        };
      }
      return { ...s, messages: msgs };
    }
    case "streaming":
      return { ...s, streaming: a.on };
  }
}
```

- [ ] **Step 4: Run — expect 2 passed. Commit.**

```bash
git add web/src/chat/state.ts web/src/chat/state.test.ts
git commit -m "feat(web): chat — conversation state reducer"
```

### Task 4a.10: Web — Dashboard route rename + ModuleRail

**Files:**
- Rename: `web/src/routes/Welcome.tsx` → `web/src/routes/Dashboard.tsx`
- Create: `web/src/chat/ModuleRail.tsx`

- [ ] **Step 1: Rename the file and the exported component**

```bash
git mv web/src/routes/Welcome.tsx web/src/routes/Dashboard.tsx
```
Then edit `Dashboard.tsx`: rename `export function Welcome` → `export function Dashboard`. Leave all content otherwise untouched.

- [ ] **Step 2: Write `ModuleRail.tsx`** — dark warm-bark rail, 10 nav items, active-route persimmon dot. Use the Quiet Paper tokens from spec §4.

```typescript
// web/src/chat/ModuleRail.tsx
import { NavLink } from "react-router-dom";

const ITEMS: Array<{ to: string; label: string }> = [
  { to: "/",               label: "Chat" },
  { to: "/dashboard",      label: "Dashboard" },
  { to: "/findings",       label: "Findings" },
  { to: "/risks",          label: "Risk register" },
  { to: "/policies",       label: "Policies" },
  { to: "/questionnaires", label: "Questionnaires" },
  { to: "/trust",          label: "Trust center" },
  { to: "/ai/inventory",   label: "AI inventory" },
  { to: "/connect",        label: "Connect" },
  { to: "/admin",          label: "Admin" },
];

export function ModuleRail({ email }: { email: string }) {
  return (
    <nav style={{ width: 200, background: "#3A342B", color: "#FAF8F3",
                  display: "flex", flexDirection: "column", padding: "16px 0" }}>
      {ITEMS.map((it) => (
        <NavLink key={it.to} to={it.to} end={it.to === "/"}
          style={({ isActive }) => ({
            padding: "9px 18px", color: isActive ? "#FFFCF6" : "#A89B89",
            textDecoration: "none", fontSize: 14,
            borderLeft: isActive ? "3px solid #D85F3B" : "3px solid transparent",
          })}>
          {it.label}
        </NavLink>
      ))}
      <div style={{ marginTop: "auto", padding: "12px 18px", fontSize: 12,
                    color: "#7A7268" }}>{email}</div>
    </nav>
  );
}
```

- [ ] **Step 3: Typecheck.** `cd web && pnpm tsc --noEmit` — expect `Dashboard` rename surfaces errors in `App.tsx` (fixed next task). `ModuleRail.tsx` itself: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/Dashboard.tsx web/src/chat/ModuleRail.tsx
git commit -m "feat(web): rename Welcome route to Dashboard + add ModuleRail"
```

### Task 4a.11: Web — ConversationRail + ChatCenter + Composer + MessageStream

**Files:**
- Create: `web/src/chat/ConversationRail.tsx`, `ChatCenter.tsx`, `Composer.tsx`, `MessageStream.tsx`

- [ ] **Step 1: `MessageStream.tsx`** — renders the message list. For 4a, only text bubbles (artifacts come in 4b).

```typescript
// web/src/chat/MessageStream.tsx
import type { ChatMessage } from "./chatApi";

export function MessageStream({ messages }: { messages: ChatMessage[] }) {
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "24px 32px" }}>
      {messages.map((m, i) => (
        <div key={i} style={{ margin: "12px 0",
          textAlign: m.role === "user" ? "right" : "left" }}>
          <div style={{ display: "inline-block", maxWidth: "70%",
            background: m.role === "user" ? "#F5E8DB" : "#FFFCF6",
            border: "1px solid #E8DFD0", borderRadius: 12,
            padding: "10px 14px", fontSize: 14, color: "#3A342B",
            whiteSpace: "pre-wrap", textAlign: "left" }}>
            {m.content?.text ?? ""}
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: `Composer.tsx`** — pill input + send. (Mic button added in 4c.)

```typescript
// web/src/chat/Composer.tsx
import { useState } from "react";

export function Composer({ onSend, disabled }: {
  onSend: (text: string) => void; disabled: boolean;
}) {
  const [text, setText] = useState("");
  const send = () => { if (text.trim()) { onSend(text.trim()); setText(""); } };
  return (
    <div style={{ display: "flex", gap: 8, padding: "16px 32px",
                  borderTop: "1px solid #E8DFD0" }}>
      <input value={text} disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") send(); }}
        placeholder="Ask anything…"
        style={{ flex: 1, borderRadius: 9999, border: "1px solid #E8DFD0",
                 padding: "10px 18px", fontSize: 14, background: "#FFFCF6" }} />
      <button onClick={send} disabled={disabled || !text.trim()}
        style={{ borderRadius: 9999, border: "none", padding: "10px 16px",
                 background: "#D85F3B", color: "#fff", cursor: "pointer" }}>↑</button>
    </div>
  );
}
```

- [ ] **Step 3: `ConversationRail.tsx`** — list + "+ New conversation" button. Groups by Today/Yesterday/Last week using `last_activity_at`.

```typescript
// web/src/chat/ConversationRail.tsx
import type { ConversationSummary } from "./chatApi";

export function ConversationRail({ conversations, activeId, onSelect, onNew }: {
  conversations: ConversationSummary[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void;
}) {
  return (
    <div style={{ width: 220, background: "#F5F0E6",
                  display: "flex", flexDirection: "column" }}>
      <button onClick={onNew}
        style={{ margin: 12, padding: "8px 12px", borderRadius: 8,
                 border: "none", background: "#D85F3B", color: "#fff",
                 cursor: "pointer", fontSize: 13 }}>+ New conversation</button>
      <div style={{ overflowY: "auto" }}>
        {conversations.map((c) => (
          <div key={c.id} onClick={() => onSelect(c.id)}
            style={{ padding: "8px 14px", fontSize: 13, cursor: "pointer",
              color: "#3A342B",
              borderLeft: c.id === activeId
                ? "3px solid #D85F3B" : "3px solid transparent",
              background: c.id === activeId ? "#FFFCF6" : "transparent" }}>
            {c.title}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: `ChatCenter.tsx`** — header + MessageStream + Composer.

```typescript
// web/src/chat/ChatCenter.tsx
import { MessageStream } from "./MessageStream";
import { Composer } from "./Composer";
import type { ChatState } from "./state";

export function ChatCenter({ state, onSend }: {
  state: ChatState; onSend: (t: string) => void;
}) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column",
                  background: "#FAF8F3" }}>
      <div style={{ padding: "14px 32px", borderBottom: "1px solid #E8DFD0",
                    fontFamily: "Georgia, serif", fontSize: 18,
                    color: "#3A342B" }}>{state.title}</div>
      <MessageStream messages={state.messages} />
      <Composer onSend={onSend} disabled={state.streaming} />
    </div>
  );
}
```

- [ ] **Step 5: Typecheck.** `cd web && pnpm tsc --noEmit` — no errors in the 4 new files.

- [ ] **Step 6: Commit**

```bash
git add web/src/chat/ConversationRail.tsx web/src/chat/ChatCenter.tsx web/src/chat/Composer.tsx web/src/chat/MessageStream.tsx
git commit -m "feat(web): chat — ConversationRail + ChatCenter + Composer + MessageStream"
```

### Task 4a.12: Web — Shell wiring + landing flow + routes

**Files:**
- Create: `web/src/chat/Shell.tsx` (the chat-route shell — distinct from the legacy `routes/Shell.tsx`)
- Modify: `web/src/App.tsx`

- [ ] **Step 1: Write `chat/Shell.tsx`** — the four-column shell, owns state, runs the landing flow.

```typescript
// web/src/chat/Shell.tsx
import { useEffect, useReducer, useState } from "react";
import { ModuleRail } from "./ModuleRail";
import { ConversationRail } from "./ConversationRail";
import { ChatCenter } from "./ChatCenter";
import { chatReducer, initialState } from "./state";
import * as api from "./chatApi";

export function ChatShell({ email }: { email: string }) {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const [convs, setConvs] = useState<api.ConversationSummary[]>([]);

  async function openConversation(id: string) {
    const c = await api.getConversation(id);
    dispatch({ type: "load", id: c.id, title: c.title, messages: c.messages });
  }

  // Landing flow — spec §7.2
  useEffect(() => {
    (async () => {
      const list = await api.listConversations();
      setConvs(list);
      const recent = list[0];
      const within24h = recent &&
        Date.now() - new Date(recent.last_activity_at).getTime() < 86_400_000;
      if (within24h) {
        await openConversation(recent.id);
      } else {
        const id = await api.createConversation();
        dispatch({ type: "load", id, title: "New conversation", messages: [] });
        setConvs(await api.listConversations());
        // Briefing cards are fetched here in Task 4b.x via executeTool.
      }
    })();
  }, []);

  async function onSend(text: string) {
    if (!state.conversationId) return;
    dispatch({ type: "append", message: { role: "user", content: { text } } });
    dispatch({ type: "append", message: { role: "assistant", content: { text: "" } } });
    dispatch({ type: "streaming", on: true });
    await api.streamMessage(state.conversationId, text,
      (t) => dispatch({ type: "streamDelta", text: t }));
    dispatch({ type: "streaming", on: false });
  }

  async function onNew() {
    const id = await api.createConversation();
    dispatch({ type: "load", id, title: "New conversation", messages: [] });
    setConvs(await api.listConversations());
  }

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <ModuleRail email={email} />
      <ConversationRail conversations={convs} activeId={state.conversationId}
        onSelect={openConversation} onNew={onNew} />
      <ChatCenter state={state} onSend={onSend} />
    </div>
  );
}
```

- [ ] **Step 2: Update `App.tsx`** — `/` → chat, `/dashboard` → the renamed `Dashboard`, fix the `Welcome` import.

Change the import line `import { Welcome } from "./routes/Welcome";` → `import { Dashboard } from "./routes/Dashboard";`. Add `import { ChatShell } from "./chat/Shell";`. In the route table:

```tsx
<Route element={<Shell />}>
  <Route path="/"          element={<ChatShell email={/* from auth ctx */ ""} />} />
  <Route path="/dashboard" element={<Dashboard />} />
  {/* ...rest unchanged... */}
```

> The legacy `routes/Shell.tsx` provides the auth gate + outlet. `ChatShell` renders its own three columns and does NOT need the legacy chrome. Decide at implementation time: either (a) render `ChatShell` OUTSIDE the legacy `<Shell>` wrapper (cleanest — it has its own ModuleRail), or (b) keep it inside and have the legacy Shell render only `<Outlet/>` for the `/` route. Prefer (a): move the `/` route out of the `<Route element={<Shell/>}>` group, and have `ChatShell` read auth/email from the same hook the legacy Shell uses (grep `routes/Shell.tsx` for the auth/me hook and reuse it).

- [ ] **Step 3: Typecheck + build**

Run: `cd web && pnpm tsc --noEmit && pnpm build`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add web/src/chat/Shell.tsx web/src/App.tsx
git commit -m "feat(web): chat — four-column Shell + landing flow + route table"
```

### Task 4a.13: Phase 4a demo gate

- [ ] **Step 1: Deploy web**

```bash
cd web && pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
```

- [ ] **Step 2: Manual demo in a browser** — sign in at `https://shasta.transilience.cloud/`:
  - Lands on `/` showing the four-column chat shell (module rail, conversation rail, empty chat center).
  - Type "what's my IAM posture?" → an assistant bubble appears and text streams in token-by-token.
  - Refresh → the conversation reloads with both messages intact.
  - Click "+ New conversation" → a fresh conversation; the old one stays in the rail.
  - `/dashboard` still shows the classic stat-tile view.

- [ ] **Step 3: Fix any defects, re-deploy, re-verify.** Do not proceed to 4b until all five checks pass.

- [ ] **Step 4: Update HANDOFF.md** — add a "Phase 4a shipped" section noting the new tables, the `chat_session` Lambda, the Function URL, and the `VITE_CHAT_STREAM_URL` value. Commit.

```bash
git add HANDOFF.md
git commit -m "docs: HANDOFF — SP4 Phase 4a shipped"
```

---

## Phase 4b — Tools + Artifacts (~4d)

**Demo at end of 4b:** KK asks "show my top open findings" → assistant calls `query_findings` → an `entity_list` + `finding_card` artifacts render in the stream with citation chips → clicking a chip opens the source side-sheet.

### Task 4b.1: tools.ts — shared tool catalog

**Files:**
- Create: `web/src/chat/tools.ts`
- Test: `web/src/chat/tools.test.ts`

- [ ] **Step 1: Failing test** — assert the catalog has 12 tools and both translators produce the right shape.

```typescript
// web/src/chat/tools.test.ts
import { describe, it, expect } from "vitest";
import { TOOLS, toAnthropicTools, toRealtimeTools } from "./tools";

describe("tool catalog", () => {
  it("has 12 tools", () => expect(TOOLS).toHaveLength(12));
  it("anthropic translation carries name + input_schema", () => {
    const t = toAnthropicTools(TOOLS)[0];
    expect(t).toHaveProperty("name");
    expect(t).toHaveProperty("input_schema");
  });
  it("realtime translation uses type:function", () => {
    const t = toRealtimeTools(TOOLS)[0];
    expect(t.type).toBe("function");
  });
});
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Write `tools.ts`** with the 12 tools from spec §6.1. Each `Tool` has `name`, `description`, `input_schema`, `flavor`, and an `execute(args)` that calls the relevant REST endpoint via `chatApi`-style authed fetch. The 8 data tools call existing endpoints (`/findings`, `/findings/rollup`, `/compliance/summary`, `/risks`, `/entities`, `/events`, `/findings/summary`); `get_morning_briefing` fans out to 3 of them; `navigate_to` / `filter_findings_view` are browser side-effects; `propose_*` are stubbed here and completed in Phase 4d.

```typescript
// web/src/chat/tools.ts  (skeleton — fill execute bodies per the endpoint map)
export type Flavor = "data" | "action" | "side-effect";
export interface ToolResult { result: unknown; _artifact_hint?: any; source?: any; }
export interface Tool {
  name: string; description: string; input_schema: object; flavor: Flavor;
  execute: (args: any) => Promise<ToolResult>;
}

export const TOOLS: Tool[] = [
  /* get_morning_briefing, query_entities, get_entity, query_findings,
     get_finding, get_compliance_summary, get_severity_breakdown,
     list_risks, propose_risk_entry, propose_policy_draft, navigate_to,
     filter_findings_view — 12 total. Each execute() returns a ToolResult
     whose _artifact_hint matches the spec §6.1 table. */
];

export function toAnthropicTools(tools: Tool[]) {
  return tools.map((t) => ({
    name: t.name, description: t.description, input_schema: t.input_schema,
  }));
}

export function toRealtimeTools(tools: Tool[]) {
  return tools.map((t) => ({
    type: "function", name: t.name, description: t.description,
    parameters: t.input_schema,
  }));
}

const byName = new Map(TOOLS.map((t) => [t.name, t]));
export async function executeTool(name: string, args: any): Promise<ToolResult> {
  const t = byName.get(name);
  if (!t) throw new Error(`unknown tool ${name}`);
  return t.execute(args);
}
```

> Implementation note: each `execute` body must set `_artifact_hint.kind` exactly per spec §6.1, and populate `source` from the endpoint's response (entity_id / finding_id / scan_id / last_scan_at). The endpoint map for each tool is in spec §6.1.

- [ ] **Step 4: Run — expect 3 passed. Commit.**

```bash
git add web/src/chat/tools.ts web/src/chat/tools.test.ts
git commit -m "feat(web): chat — 12-tool shared catalog + translators"
```

### Task 4b.2: Artifact.tsx renderer + 8 components

**Files:**
- Create: `web/src/chat/Artifact.tsx`
- Create: `web/src/chat/artifacts/{KpiCard,EntityList,FindingCard,RiskCard,ChartBar,ChartDonut,SeverityBreakdown,ApprovalCard}.tsx`
- Test: `web/src/chat/Artifact.test.tsx`

- [ ] **Step 1: Failing test** — render each `kind` and assert it doesn't throw + shows a citation chip when `source` present.

```typescript
// web/src/chat/Artifact.test.tsx
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Artifact } from "./Artifact";

describe("Artifact", () => {
  it("renders a kpi_card", () => {
    const { getByText } = render(
      <Artifact hint={{ kind: "kpi_card", label: "Open", value: "42" }} />);
    expect(getByText("42")).toBeTruthy();
  });
  it("renders a citation chip when source present", () => {
    const { getByText } = render(
      <Artifact hint={{ kind: "kpi_card", label: "x", value: "1",
                        source: { finding_id: "f1" } }} />);
    expect(getByText(/source/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Write the 8 components.** Each is a small presentational component taking its slice of the `ArtifactHint` union from spec §6.3. Keep them styled with the Quiet Paper tokens. Pattern (KpiCard shown — the other 7 follow the same shape, one file each):

```typescript
// web/src/chat/artifacts/KpiCard.tsx
import { CitationChip } from "../Artifact";

export function KpiCard({ label, value, detail, severity, source }: any) {
  return (
    <div style={{ background: "#FFFCF6", border: "1px solid #E8DFD0",
                  borderRadius: 12, padding: 16, margin: "8px 0",
                  position: "relative", maxWidth: 320 }}>
      <div style={{ fontSize: 12, color: "#7A7268" }}>{label}</div>
      <div style={{ fontSize: 28, fontFamily: "Georgia, serif",
                    color: "#3A342B" }}>{value}</div>
      {detail && <div style={{ fontSize: 12, color: "#A89B89" }}>{detail}</div>}
      {source && <CitationChip source={source} />}
    </div>
  );
}
```

- [ ] **Step 4: Write `Artifact.tsx`** — the switch + the shared `CitationChip` (which dispatches a `open-source-sheet` custom event).

```typescript
// web/src/chat/Artifact.tsx
import { KpiCard } from "./artifacts/KpiCard";
import { EntityList } from "./artifacts/EntityList";
import { FindingCard } from "./artifacts/FindingCard";
import { RiskCard } from "./artifacts/RiskCard";
import { ChartBar } from "./artifacts/ChartBar";
import { ChartDonut } from "./artifacts/ChartDonut";
import { SeverityBreakdown } from "./artifacts/SeverityBreakdown";
import { ApprovalCard } from "./artifacts/ApprovalCard";

export function CitationChip({ source }: { source: any }) {
  return (
    <button
      onClick={() => window.dispatchEvent(
        new CustomEvent("open-source-sheet", { detail: source }))}
      style={{ position: "absolute", bottom: 8, right: 8, fontSize: 11,
               color: "#85613A", background: "#F5E8DB", border: "none",
               borderRadius: 6, padding: "2px 6px", cursor: "pointer" }}>
      ↗ source
    </button>
  );
}

export function Artifact({ hint }: { hint: any }) {
  switch (hint.kind) {
    case "kpi_card":           return <KpiCard {...hint} />;
    case "entity_list":        return <EntityList {...hint} />;
    case "finding_card":       return <FindingCard {...hint} />;
    case "risk_card":          return <RiskCard {...hint} />;
    case "chart_bar":          return <ChartBar {...hint} />;
    case "chart_donut":        return <ChartDonut {...hint} />;
    case "severity_breakdown": return <SeverityBreakdown {...hint} />;
    case "approval_card":      return <ApprovalCard {...hint} />;
    default:                   return null;
  }
}
```

- [ ] **Step 5: Run — expect 2 passed.** Add `@testing-library/react` to devDeps if absent (`cd web && pnpm add -D @testing-library/react`).

- [ ] **Step 6: Commit**

```bash
git add web/src/chat/Artifact.tsx web/src/chat/artifacts/ web/src/chat/Artifact.test.tsx web/package.json
git commit -m "feat(web): chat — Artifact renderer + 8 card components"
```

### Task 4b.3: Wire tools into the stream + tool-result messages

**Files:**
- Modify: `web/src/chat/MessageStream.tsx` (render `tool` messages as `<Artifact>`)
- Modify: `web/src/chat/anthropicClient.ts` / streaming path to dispatch `tool-use` events to `executeTool`
- Modify: `platform/lambda/chat_session/messages_stream.py` (emit `tool-use` SSE, accept tool results, loop)

- [ ] **Step 1: Extend the stream protocol.** `messages_stream.py` must, when Anthropic emits a `tool_use` block, send `data: {"type":"tool-use","name":...,"args":...,"id":...}` and then PAUSE the Anthropic call. The browser executes the tool, POSTs the result back, and the Lambda resumes the Anthropic call with the `tool_result`. Implement this as the standard Anthropic tool-use loop. Persist each completed tool call as a `tool` role message via `M.append`.

- [ ] **Step 2: Browser side** — on a `tool-use` SSE event, call `executeTool(name, args)`, dispatch `{type:"append", message:{role:"tool", content:{tool_name, args, result, _artifact_hint, source}}}`, and POST the result to continue the turn.

- [ ] **Step 3: `MessageStream.tsx`** — for `role === "tool"` messages, render `<Artifact hint={m.content._artifact_hint} />` instead of a text bubble.

- [ ] **Step 4: Test** — unit-test the reducer handles a `tool` message; manual end-to-end deferred to the demo gate.

- [ ] **Step 5: Commit** `git commit -m "feat(web+platform): chat — tool-use loop + artifact rendering in stream"`.

### Task 4b.4: SourceSideSheet + landing briefing

**Files:**
- Create: `web/src/chat/SourceSideSheet.tsx`
- Modify: `web/src/chat/Shell.tsx` (mount the sheet; fire `get_morning_briefing` on fresh-conversation landing)

- [ ] **Step 1: `SourceSideSheet.tsx`** — listens for the `open-source-sheet` event, fetches the entity/finding/evidence-packet, renders it in a 420px right panel, closes on outside click / Esc.

- [ ] **Step 2: Landing briefing** — in `Shell.tsx`, after creating a fresh conversation, call `executeTool("get_morning_briefing", {})`, then `appendMessage(id, "tool", {...})` for each of the 2-3 returned artifacts, and dispatch them into state so the cards paint immediately.

- [ ] **Step 3: Commit** `git commit -m "feat(web): chat — SourceSideSheet + landing morning briefing"`.

### Task 4b.5: "Findings" rename + Phase 4b demo gate

- [ ] **Step 1:** `ModuleRail.tsx` already says "Findings" (done in 4a.10). Confirm no other nav file still says "Top risks" — `grep -rn "Top risks" web/src`. Fix any stragglers.

- [ ] **Step 2: Deploy web** (same commands as 4a.13 Step 1).

- [ ] **Step 3: Demo** — sign in: landing shows 3 briefing cards; ask "show my top open findings" → `finding_card`s render; click `↗ source` → side-sheet opens with the finding detail; ask "compliance posture" → `chart_donut` renders.

- [ ] **Step 4:** Fix defects, re-verify. Update HANDOFF.md. Commit.

---

## Phase 4c — Voice Integration (~3d)

**Demo at end of 4c:** KK toggles the mic → ChatGPT-equivalent voice: transcript streams into the conversation live, refresh resumes it, barge-in works, voice tool calls render the same artifacts.

### Task 4c.1: chat_session voice payload — tools + persona

**Files:**
- Modify: `platform/lambda/chat_session/voice.py`, `prompts.py`

- [ ] **Step 1:** Fill `prompts.py` with the full PERSONA + TOOL_RULES + `VOICE_ADDENDUM` + `TEXT_ADDENDUM` per spec §9.4. Add `system_for_voice(name)`.
- [ ] **Step 2:** In `voice.py`, build the Realtime session payload with `instructions = system_for_voice(...)`, `tools = ` the 12-tool catalog in Realtime shape (the server mirrors `toRealtimeTools`; keep a Python copy of the tool schemas or pass them from the client at mint time — prefer client-passes-schemas to keep one source of truth).
- [ ] **Step 3:** Commit `git commit -m "feat(platform): chat_session — full voice session payload + persona"`.

### Task 4c.2: voiceClient.ts — WebRTC + TurnQueue

**Files:**
- Create: `web/src/chat/voiceClient.ts`
- Test: `web/src/chat/voiceClient.test.ts`

- [ ] **Step 1: Failing test** for the `TurnQueue` — enqueue, flush order, retry-on-failure with backoff cap.
- [ ] **Step 2:** Implement `TurnQueue` (FIFO, single `flushing` flag, exponential backoff to 30s / max 5 retries, banner on persistent failure) per spec §9.2.
- [ ] **Step 3:** Port the WebRTC peer + data-channel setup from `~/Projects/shasta-ios-poc` pattern adapted to browser `RTCPeerConnection` (reference the existing `web/src/voice/` client — most of this already exists from the 2026-05-18 web-voice work; lift and adapt).
- [ ] **Step 4:** On `response.done`, seal the turn → `TurnQueue.enqueue`. The queue worker POSTs to `/v1/conversations/{id}/messages` (REST).
- [ ] **Step 5:** Run tests — expect pass. Commit.

### Task 4c.3: Composer mic toggle + breathing dot + interruption

**Files:**
- Modify: `web/src/chat/Composer.tsx`, `ChatCenter.tsx`, `Shell.tsx`

- [ ] **Step 1:** Add the mic toggle button to `Composer.tsx` (OFF → Connecting → ON). ON disables the text input.
- [ ] **Step 2:** Persimmon breathing dot in `ChatCenter.tsx` header when voice connected.
- [ ] **Step 3:** Interruption — on `input_audio_buffer.speech_started`, if assistant audio is playing, send `response.cancel`.
- [ ] **Step 4:** Voice tool calls — reuse `executeTool` (shared with text); send `conversation.item.create` + `response.create` back to Realtime.
- [ ] **Step 5:** Transcript renders live into the stream as data-channel events arrive (before the POST returns).
- [ ] **Step 6:** Commit `git commit -m "feat(web): chat — voice mic toggle + breathing dot + barge-in"`.

### Task 4c.4: sendBeacon on unload + Phase 4c demo gate

- [ ] **Step 1:** `beforeunload` handler → `navigator.sendBeacon` for `TurnQueue` head.
- [ ] **Step 2: Deploy web. Demo:** toggle mic, hold a spoken conversation, see transcript stream live; refresh mid-conversation → transcript resumes; interrupt the assistant mid-sentence → it stops; ask a voice question that triggers a tool → the artifact card renders.
- [ ] **Step 3: Verification gate (spec §15).** Ask the same 10 questions in text and in voice. Compare persisted `tool` messages in `conversation_messages` — same tools called, same args, same results. Phrasing differences OK; tool-output differences are bugs to fix.
- [ ] **Step 4:** Fix defects, update HANDOFF.md, commit.

---

## Phase 4d — Action Approvals (~2d)

**Demo at end of 4d:** KK says "add this to my risk register" → an editable `approval_card` appears → edit a field → Approve → a `risks` row is created → card shows the green ✓ + link. Same for "draft a policy".

### Task 4d.1: propose_* tools + ApprovalCard behavior

**Files:**
- Modify: `web/src/chat/tools.ts` (complete `propose_risk_entry`, `propose_policy_draft`)
- Modify: `web/src/chat/artifacts/ApprovalCard.tsx`

- [ ] **Step 1:** `propose_risk_entry` / `propose_policy_draft` `execute()` returns an `approval_card` hint with `current_status: "pending"`, `payload`, and `edit_fields` per spec §8. They do NOT mutate anything.
- [ ] **Step 2:** `ApprovalCard.tsx` state machine: `pending → editing → pending` (Save) and `pending → approved | cancelled | error` per spec §8. Inline editable fields by `action_kind`.
- [ ] **Step 3:** Each card carries a UUID; Approve is a no-op once `approved`.
- [ ] **Step 4:** Commit.

### Task 4d.2: Approve action → POST /risks or /policies

**Files:**
- Modify: `web/src/chat/artifacts/ApprovalCard.tsx`

- [ ] **Step 1:** On Approve: `add_risk` → `POST /risks` with the payload; `draft_policy` → `POST /policies` with `{content, name, status:"draft"}`. Set `result.id` + `result.href` from the response.
- [ ] **Step 2:** Server-side idempotency — before insert, check for an existing `risks`/`policies` row keyed on the approval UUID (add a nullable `source_approval_id` column to `risks` and `policies` via a small migration `007_approval_idempotency.sql`, or store the UUID in an existing JSONB column if one exists — check the schema first).
- [ ] **Step 3:** Persist the approved-card state transition into the conversation `tool` message so a reload re-renders the `approved` state.
- [ ] **Step 4:** Commit.

### Task 4d.3: Phase 4d demo gate + SP4 wrap

- [ ] **Step 1: Deploy.** Demo both action paths end-to-end (risk + policy), including edit-before-approve and the idempotent double-click.
- [ ] **Step 2:** Confirm voice "add a risk" also routes through the `approval_card` (no silent mutation).
- [ ] **Step 3:** Retire the voice modal — delete `web/src/voice/VoiceChat.tsx`, remove the "Voice" nav item (already absent from `ModuleRail`), confirm nothing imports the deleted modal (`grep -rn VoiceChat web/src`).
- [ ] **Step 4:** Update HANDOFF.md with the full SP4-shipped section. Commit.
- [ ] **Step 5:** Open the PR: `gh pr create --base main --head feat/sp4-chat-first --title "SP4: Chat-first front door" --body "..."`.

---

## Self-Review

**Spec coverage:** §1–§16 of the spec map to tasks — §4a Shell+text (4a.1–4a.13), §6 tools+artifacts (4b.1–4b.5), §9 voice (4c.1–4c.4), §8 approvals (4d.1–4d.3), §10 migration (route rename in 4a.10, voice modal retired in 4d.3), §12 risks (Function URL in 4a.7, idempotency in 4d.2), §15 consistency gate (4c.4 Step 3). §16 prerequisites → the Prerequisites section.

**Known deliberate abbreviations** (not placeholders — flagged for the executing engineer):
- Task 4b.1 Step 3, 4c.1, 4c.2, 4c.3, 4d.1, 4d.2 give task-level structure rather than full code. Reason: the artifact components are 8 near-identical presentational files (one full example given in 4b.2), the tool `execute` bodies are mechanical REST calls against documented endpoints (spec §6.1 has the map), and the WebRTC client is largely a lift of existing `web/src/voice/` code. An executing engineer should expand each into per-step TDD before implementing — or run these phases via subagent-driven-development where each subagent expands its own task.
- The Function URL streaming dispatch (4a.7 Step 3) has an explicit decision point — the plan names the preferred fallback (separate Lambda Function sharing the code asset).

**Carry-forward gotchas baked into tasks:** CORS header on every `_resp` (4a.2), `print()` over `logging` in Lambda (note in any Lambda task), Secrets Manager `SecretId` no `*` (4a.7), `dangerouslyDisableSandbox` for git commands (use on every Bash commit step), container-Lambda hotswap caveat (n/a — `chat_session` is a zip Lambda, hotswap works).

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-19-sp4-chat-first.md`.**
