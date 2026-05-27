# SP4 — Chat-First Front Door

> **Status:** drafted 2026-05-19. Ready for KK review.
>
> **Author:** Claude (per KK's direction, brainstorm checkpoint at
> `docs/superpowers/brainstorm-notes/2026-05-19-sp4-brainstorm-checkpoint.md`).
>
> **Predecessor:** SP1 (unified entity + edge model) — assumed merged.
> SP4 reads from `entities`, `edges`, `findings`, `risks`, `policies`,
> `compliance` views.
>
> **Sibling sub-projects:** SP2 Graph viz, SP3 Unified Findings/Risks UX,
> SP4.5 iOS catch-up, SP5 (voice — absorbed into SP4), SP6 (dynamic
> dashboards — absorbed into SP4 as the artifact catalog).

---

## 1. Goal

Replace the stat-tile-and-tabs Welcome page with a **chat-first surface
at `/`** that is the front door to CISO Copilot. The chat composer
supports both text (Anthropic streaming) and voice (OpenAI Realtime
over WebRTC). Conversations persist to Aurora and survive refresh.
Every concrete claim from the assistant is rendered as a typed
artifact card with a citation chip back to its source entity / finding
/ evidence packet. Action proposals (add to risk register, draft a
policy) render as inline approval cards and never auto-execute.

The "classic dashboard" moves to `/dashboard` for users who prefer the
tiles view.

## 2. Non-goals

This sub-project does **not**:

- Ship an iOS chat surface — that's SP4.5 (~1 week, port renderer +
  composer + chat list view to SwiftUI). Voice on iOS stays on its
  current modal until SP4.5.
- Introduce entity-anchored long-term memory across conversations
  ("remember I prefer IAM findings first"). Conversation-scoped only.
- Add an `audit_events` table — deferred until the first retrospective
  report is built. Approval history is recoverable from
  `conversation_messages` JSONB.
- Add Slack / Teams / Email / JIRA integrations. The system prompt
  REDIRECTS to "I can add it to your risk register."
- Expand actions beyond `propose_risk_entry` + `propose_policy_draft`.
  No cloud config changes, no IAM edits, no scanner triggers from
  chat. Determinism + reversibility invariants stay intact.
- Accept image/file uploads (multi-modal). Text + voice only.
- Stream partial tool calls. Tool results materialize as a card only
  when the call completes.
- Support real-time multi-user shared conversations.
- Offer a per-conversation share/export action. Comes after the action
  audit story.
- Render gracefully under 900px width. Desktop only in v1; mobile gets
  native iOS in SP4.5.
- Expose model selection to users. `gpt-realtime` for voice and
  `claude-sonnet-4-6` for text are pinned per the existing invariants.

## 3. Decisions log

| # | Decision | Why |
|---|---|---|
| Q1 | Landing flow = proactive briefing on every sign-in | Sets the "what changed since I last looked" tone. 2-3 cards: top open finding, posture severity_breakdown, risk register summary. |
| Q2 | Chat replaces Welcome at `/`. Welcome demoted to `/dashboard`. Voice modal retired (mic integrated into composer). | Single front door. Voice is an affordance on the composer, not a destination. |
| Q3 | **Fixed component set** with JSON schemas — 8 artifact components. | Closed set is easier to renderer-test, easier to style consistently, and gives the LLM a finite vocabulary that's hard to misuse. Adding a 9th is one type-union variant. |
| Q4 | Anthropic Messages for text + OpenAI Realtime for voice, **single shared TS tool catalog** in `web/src/chat/tools.ts`. | Reuses the working `voice_session` tool dispatch pattern from 2026-05-18. One catalog file, two translators (`toAnthropicTools()` / `toRealtimeTools()`), one `executeTool(name, args)` in the browser. |
| Q5 | Server-side `conversations` + `conversation_messages` tables. Entity-anchored memory parked for later. | The minimum that makes "refresh and resume" work. JSONB content per role keeps the schema small while permitting tool/artifact storage. |
| Q6 | Always-visible subtle citation chips on every card and concrete claim — auto-rendered from `source` field on tool results. | Forces the LLM to ground every assertion; user can audit instantly. No "expand for sources" hide affordance. |
| Q7 | Web first. iOS catches up in SP4.5. | iOS already has a working voice modal + tab UI; replacing both is a separate one-week port. |
| Q8 | Card grows in place with inline editable fields on approval. Save → re-renders proposal card. | No modal stack. Edit + approve are one card transition, not three. |
| Q9 | Audit events on approve/cancel — **deferred**. | KK call: skip until first retrospective report is on the table. `conversation_messages` retains enough state to reconstruct history. |
| Q10 | Combined `chat_session` Lambda (text proxy + voice mint + conversations CRUD). | All three read/write the same conversation row. One env-var bundle, one IAM policy, one prompt-version source. |
| Q11 | Voice transcripts = per-turn POST after `response.done`, fire-and-forget with retry queue + `sendBeacon` on unload. | Best-in-class voice UX (ChatGPT/Claude-equivalent): transcript appears live, refresh resumes, mid-session crash loses at most one turn. WebRTC audio frames are on the native media engine, NOT the JS main thread — `fetch()` cannot block packetization. |

Locked invariants this sub-project respects:

- **Determinism is the spine.** LLMs never write to `findings`, `risks`,
  `policies`, `entities`, or `edges` directly. Only via `propose_*` tools
  + explicit user approval.
- **Every emission carries evidence.** Tool results embed a `source`
  field that the renderer surfaces as a citation chip pointing at the
  source entity / finding / scan / evidence packet.
- **Reversibility non-negotiable.** Actions limited to "add risk
  register row" and "draft policy". Both are additive, both are
  user-deletable from the existing routes.
- **One model version pinned per provider** — `gpt-realtime`,
  `claude-sonnet-4-6`. No user-facing model toggle.
- **Tenant scoping everywhere.** `conversations` and
  `conversation_messages` carry `tenant_id` + `user_id`. Cross-tenant
  reads forbidden at the Lambda layer.
- **iOS / web never call upstream sources.** Only API Gateway / the
  Function URL for streaming.

## 4. Aesthetic

**Quiet Paper + Persimmon.** Mockup reference: `.superpowers/brainstorm/54991-1779221730/content/shell-v3.html`.

| Token | Value |
|---|---|
| Cream base | `#FAF8F3` |
| Card surface | `#FFFCF6` |
| Borders | `#E8DFD0` |
| Dark warm text | `#3A342B` |
| Muted | `#7A7268` |
| Muted lighter | `#A89B89` |
| Persimmon accent | `#D85F3B` |
| Persimmon glow | `0 0 0 4px rgba(216,95,59,0.18)` |
| Tan secondary surface | `#F5E8DB` |
| Tan secondary text | `#85613A` |
| Voice breathing dot | persimmon + glow, 1.6s ease in/out |

Typography:
- **Greetings + H1**: Georgia serif, 28px / 36px, weight 400.
- **Body**: Inter sans, 14px / 21px, weight 400.
- **Code / ARNs / IDs**: SF Mono / Menlo / monospace, 13px.

Icons: **Lucide-style thin-stroke SVGs**, 1.6px stroke, `currentColor`.
No emoji anywhere in the chat surface. Existing emoji in non-chat
routes are out of scope for SP4.

## 5. Surface layout

Four-column desktop shell. Module rail and chat center always visible;
conversation rail and source side-sheet collapsible.

```
┌────────────┬──────────────────┬───────────────────────────────┬──────────┐
│ Module     │ Conversations    │ Chat                          │ Source   │
│ rail       │ rail             │                               │ side-    │
│ (~200px)   │ (~220px)         │  (fills)                      │ sheet    │
│ dark warm  │ cream `#F5F0E6`  │  cream `#FAF8F3`              │ (40px    │
│ bark       │                  │                               │ collapsed│
│ `#3A342B`  │  "+ New          │  ┌───── header ─────────┐     │ → opens  │
│ cream text │   conversation"  │  │ ▌ Conversation title │     │ to       │
│            │   (persimmon)    │  │ ● voice dot (if on)  │     │ ~420px)  │
│ ● Chat     │                  │  └──────────────────────┘     │          │
│   (active) │  Today           │                               │  shows   │
│ Dashboard  │  ─ Posture sweep │  ┌── message stream ───┐      │  the     │
│ Findings   │  ─ IAM drift     │  │ user                │      │  source  │
│ Risk reg.  │  ─ Risk review   │  │   text or transcript│      │  entity  │
│ Policies   │                  │  │                     │      │  /       │
│ Question.  │  Yesterday       │  │ assistant           │      │  finding │
│ Trust ctr  │  ─ Compliance Q  │  │   text + artifacts  │      │  /       │
│ AI inv.    │                  │  │   • finding_card    │      │  evidence│
│ Connect    │  Last week       │  │   • chart_donut     │      │  packet  │
│ Admin      │  ─ Tuesday sync  │  │   ↗ source chip     │      │          │
│            │                  │  └─────────────────────┘      │          │
│ <admin-email-prefix> │                  │                               │          │
│ gmail.com  │                  │  ┌── composer ─────────┐      │          │
│            │                  │  │ 🎤 [Ask anything…] ↑ │     │          │
│            │                  │  └─────────────────────┘      │          │
└────────────┴──────────────────┴───────────────────────────────┴──────────┘
```

**Module rail (`~200px`, always)** — dark warm bark `#3A342B`,
cream text. Items: Chat, Dashboard, Findings, Risk register, Policies,
Questionnaires, Trust center, AI inventory, Connect, Admin (ADMIN
allowlist only). Active route gets a persimmon dot before the label.
User email at the bottom. Auto-collapses to 56px icon-only at narrow
widths.

**Conversation rail (`~220px`, open by default, collapsible)** — soft
cream `#F5F0E6`. "+ New conversation" persimmon button at top.
Conversations grouped: Today / Yesterday / Last week / Older. Active
conversation has a persimmon left-border. Collapses to a 40px vertical
handle with a rotated "Conversations" label.

**Chat center** — message stream + composer. Header shows the
conversation title (editable in-place) and a persimmon breathing dot
when voice is connected.

**Source side-sheet (`40px` collapsed default, expandable to ~420px)** —
opens when the user clicks an `↗ source` chip on any card. Shows the
underlying entity / finding / evidence packet. Closes on outside click
or Esc.

**Composer:** rounded-pill input with placeholder "Ask anything…", a
mic toggle button (off → connecting → on), and a send arrow
(persimmon when input non-empty). Voice ON disables the text input
but keeps the conversation scrollable.

## 6. Tools, LLM bridge, and artifact catalog

### 6.1 Tool catalog (12 tools, one TS source of truth)

`web/src/chat/tools.ts` is the single source. Two translators emit
the per-LLM schema:

```typescript
// tools.ts (simplified)
export type Tool = {
  name: string;
  description: string;
  input_schema: JSONSchema;        // shared input shape
  flavor: 'data' | 'action' | 'side-effect';
  execute(args: any): Promise<ToolResult>;
};

export type ToolResult = {
  result: unknown;                  // the data the LLM sees
  _artifact_hint?: ArtifactHint;    // typed render hint
  source?: Source;                  // citation
};

export function toAnthropicTools(tools: Tool[]) { /* maps to .tools */ }
export function toRealtimeTools(tools: Tool[])  { /* maps to .session.tools */ }
export async function executeTool(name: string, args: any) { /* dispatch */ }
```

| Tool | Flavor | Returns (artifact hint) |
|---|---|---|
| `get_morning_briefing` | data | 2-3 artifacts: a `kpi_card` for top open finding, a `severity_breakdown`, a `kpi_card` for risk register summary |
| `query_entities` | data | `entity_list` |
| `get_entity` | data | `entity_list` of one (renders detail) |
| `query_findings` | data | `entity_list` of finding refs OR multi-card if ≤3 |
| `get_finding` | data | `finding_card` |
| `get_compliance_summary` | data | `chart_donut` + framework tiles (`kpi_card` × N) |
| `get_severity_breakdown` | data | `severity_breakdown` |
| `list_risks` | data | `risk_card` list |
| `propose_risk_entry` | **action** | `approval_card` with `action_kind='add_risk'` |
| `propose_policy_draft` | **action** | `approval_card` with `action_kind='draft_policy'` + content preview |
| `navigate_to` | side-effect | `{ navigated_to: path }` (no card) |
| `filter_findings_view` | side-effect | `{ filtered: params }` (no card) |

Pre-existing voice tools (`get_top_risks`, `list_connected_clouds`,
`list_recent_alerts`, `add_risk`) are **absorbed** into this catalog.
`add_risk` becomes `propose_risk_entry` so voice goes through the same
approval card path — no more silent mutation from voice.

### 6.2 LLM bridge

```
              conversations + conversation_messages (Aurora)
                              ▲ ▼
                  browser conversation state (React Query + useReducer)
                       ▲                       ▲
                       │                       │
                  text path                voice path
            Anthropic Messages          OpenAI Realtime
            (SSE via Lambda             (WebRTC peer + data channel,
             Function URL)               ephemeral key minted server-side)
                       ▲                       ▲
                       │                       │
                       └───── shared ──────────┘
                       executeTool(name, args) → REST
                       tool_result + _artifact_hint → render
```

Single Lambda `chat_session/main.py` exposes routes across **two
invocation surfaces** — one Function URL (streaming SSE) and the
existing API Gateway REST stage (everything else). The Lambda code
itself dispatches by event source; CDK wires each route to the right
surface.

| Method | Route | Surface | Purpose |
|---|---|---|---|
| POST | `/v1/conversations` | REST | Create new conversation. Returns `{conversation_id}`. |
| GET | `/v1/conversations` | REST | List conversations for current `(tenant_id, user_id)`, ordered by `last_activity_at DESC`. |
| GET | `/v1/conversations/{id}` | REST | Full message history. |
| PATCH | `/v1/conversations/{id}` | REST | Update `title`. |
| DELETE | `/v1/conversations/{id}` | REST | Soft-delete (`deleted_at`). |
| POST | `/v1/conversations/{id}/messages` | REST | Append a fully-formed message. Body `{role, content}` where `content` matches the JSONB shape in §7. Used by the voice TurnQueue to persist `user` / `assistant` / `tool` messages from a voice turn, and by the landing flow to persist the `get_morning_briefing` tool result. No LLM round-trip; returns `200 {message_id}`. |
| POST | `/v1/conversations/{id}/stream` | **Function URL** | Streaming text turn. Body `{text: string}`. Server appends `user` message, calls Anthropic Messages with streaming, emits SSE chunks (`text-delta`, `tool-use-delta`, `tool-result`, `done`). On stream end, persists the full `assistant` message + any `tool` messages. Browser renders deltas live and the renderer reconstitutes the same artifacts from the persisted rows on next load. |
| POST | `/v1/conversations/{id}/voice` | REST | Mints OpenAI Realtime ephemeral key bound to this conversation. Returns `{value, expires_at, session}`. Replaces the legacy `/voice/session`. |

**Two surfaces, two paths:** API Gateway REST does not support
streaming response bodies. Lambda Function URLs with
`InvokeMode=RESPONSE_STREAM` do. Splitting "stream a new text turn"
into its own path (`/stream`) keeps the routing unambiguous: one
URL → one surface → one purpose. The Function URL is at a separate
hostname (`https://<fn-url-hash>.lambda-url.us-east-1.on.aws`); the
web app uses it only for `POST /v1/conversations/{id}/stream`. Auth
on the Function URL is `AuthType=NONE` at the AWS layer; the Lambda
explicitly verifies the Cognito JWT against the same JWKS the API
Gateway authorizer uses. CORS is set on the Function URL config.

### 6.3 Artifact catalog (8 components)

```typescript
type Source = {
  entity_id?: string;
  finding_id?: string;
  evidence_packet_id?: string;
  scan_id?: string;
  last_scan_at?: string;
};

type ArtifactHint =
  | { kind: 'kpi_card'; label: string; value: string; detail?: string;
      severity?: 'critical'|'high'|'medium'|'low'|'info'; tags?: string[];
      source?: Source }
  | { kind: 'entity_list'; title?: string;
      entities: Array<{ id: string; kind: string; display_name: string;
                        source_path?: string; source?: Source }> }
  | { kind: 'finding_card'; finding_id: string; check_id: string;
      title: string;
      severity: 'critical'|'high'|'medium'|'low'|'info';
      description?: string; resource_arn?: string; region?: string;
      frameworks?: string[]; source: Source }
  | { kind: 'risk_card'; risk_id: string; title: string;
      severity: 'critical'|'high'|'medium'|'low'|'info';
      status: 'open'|'mitigating'|'accepted'|'closed';
      owner?: string; due_date?: string; source?: Source }
  | { kind: 'chart_bar'; title: string; x_label?: string; y_label?: string;
      series: Array<{ label: string; value: number; color?: string }>;
      source?: Source }
  | { kind: 'chart_donut'; title: string;
      segments: Array<{ label: string; value: number; color?: string }>;
      source?: Source }
  | { kind: 'severity_breakdown'; total: number; critical: number;
      high: number; medium: number; low: number;
      delta_since?: string; source?: Source }
  | { kind: 'approval_card';
      action_kind: 'add_risk' | 'draft_policy';
      current_status: 'pending'|'editing'|'approved'|'cancelled'|'error';
      payload: Record<string, unknown>;
      edit_fields: Array<{ key: string; label: string;
                            type: 'text'|'textarea'|'select'|'date';
                            options?: string[] }>;
      result?: { id: string; href: string };
      error?: string };
```

`web/src/chat/Artifact.tsx` is a single switch on `kind`. Adding a 9th
component later: add a type-union variant + a switch case. No schema
migration.

The renderer auto-renders a citation chip in the bottom-right of any
card whose hint carries `source`. Click → opens source side-sheet with
the entity / finding / evidence packet view.

## 7. Persistence

### 7.1 Schema — `006_conversations.sql`

```sql
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
```

Content shape per role:

- `user`: `{ text: string; modality: 'text' | 'voice' }`
- `assistant`: `{ text: string; modality: 'text' | 'voice';
                  tool_calls?: Array<{ name, args, call_id }> }`
- `tool`: `{ tool_name: string; args: any; result: any;
             _artifact_hint?: ArtifactHint; source?: Source;
             call_id: string }`
- `system`: `{ text?: string; seed?: 'morning_briefing'; payload?: any }`
  (only seeded once on conversation create; never user-visible)

### 7.2 Sign-in landing flow

1. Web `Shell` mounts → fetches `GET /v1/conversations`.
2. If most-recent's `last_activity_at` within 24h → load it via
   `GET /v1/conversations/{id}`. Voice toggle starts OFF. Done.
3. Else → `POST /v1/conversations` to start fresh. Server appends one
   `system` message with `{seed: 'morning_briefing'}` (used only as a
   marker for the renderer; never shown to the user).
4. Web immediately calls `executeTool('get_morning_briefing', {})`
   client-side. The result lands as a `tool` message via
   `POST /v1/conversations/{id}/messages` (REST surface, no streaming
   needed since there's no LLM call). The renderer paints the 2-3
   briefing cards instantly.
5. The user's first text message is what triggers the first Anthropic
   round-trip. The seeded `system` row tells the LLM "the user has
   already seen the briefing cards; don't repeat them."

## 8. Action approvals

Two action tools: `propose_risk_entry`, `propose_policy_draft`. Both
return an `approval_card` hint and NEVER auto-execute.

**Card state machine:**

```
   pending ──── click ✏️ ─── editing ─── Save ─────┐
       │                        │                  │
       │                        │  Cancel          ▼
       │                        ▼                 (back to pending,
       │                       pending             payload updated)
       │                        ▲
       └── click ✓ Approve ─────┤
                                ▼
                              approved (green-tan ✓ + href to result)

       click ✗ Cancel ──→ cancelled (struck-through)

       network or server error during approve ──→ error (persimmon
       border + "Retry" link → returns to pending)
```

**Idempotency:** every `approval_card` has a UUID. Approve clicks are
no-ops after `current_status='approved'`. The server checks for an
existing `risks` / `policies` row keyed on this UUID before inserting.

**On approve:**
- `add_risk` → `POST /risks` with the payload (title, severity, status,
  owner, due_date). Response → renderer sets `result.id` + `result.href`.
- `draft_policy` → `POST /policies` with `{template_id?, content, name,
  status: 'draft'}`. Response → same shape.

**Edit fields** are static per `action_kind`:

| `action_kind` | Editable fields |
|---|---|
| `add_risk` | title (text), severity (select critical/high/medium/low), status (select), owner (text), due_date (date) |
| `draft_policy` | name (text), content (textarea, monospace, ≥12 rows), status (select draft/approved/retired) |

## 9. Voice integration

**Best-in-class UX target: ChatGPT / Claude voice-equivalent.**

### 9.1 Lifecycle

```
composer mic OFF
    ↓ click
Connecting (UI shows breathing dot, gray)
    ↓ POST /v1/conversations/{id}/voice → {value: ek_..., expires_at, session}
    ↓ create RTCPeerConnection, attach mic track, open "oai-events" data channel
    ↓ POST https://api.openai.com/v1/realtime/calls  (offer SDP)
    ← answer SDP
    ↓ data channel opens
ON (composer disabled, breathing dot persimmon)

events on data channel:
    input_audio_buffer.speech_started      → if assistant audio playing, send response.cancel (barge-in)
    input_audio_buffer.speech_stopped
    conversation.item.input_audio_transcription.done   → user transcript text ready
    response.output_audio_transcript.delta             → render live into the current assistant bubble
    response.output_audio_transcript.done              → assistant transcript text complete
    response.output_audio.delta                        → played by browser RTP track
    response.function_call_arguments.done              → executeTool() → conversation.item.create + response.create
    response.done                                       → SEAL TURN, enqueue for POST

OFF (toggle off, silence timeout, navigate away)
    ↓ close data channel, RTCPeerConnection.close()
    ↓ queue drains in background; UI is responsive immediately
```

### 9.2 Per-turn POST

A `TurnQueue` (in-memory FIFO with a `flushing` boolean) lives in
`web/src/chat/voiceClient.ts`.

```typescript
type SealedTurn = {
  conversation_id: string;
  user: { text: string; modality: 'voice' };
  assistant: { text: string; modality: 'voice'; tool_calls?: any[] };
  tool_results?: ToolResult[];     // for any tool calls within the turn
};

async function flush() {
  if (flushing || queue.length === 0) return;
  flushing = true;
  const turn = queue[0];
  try {
    await retryWithBackoff(() =>
      fetch(`/v1/conversations/${turn.conversation_id}/messages`, {
        method: 'POST', headers: { ... }, body: JSON.stringify(turn),
      })
    , { max: 5, capMs: 30_000 });
    queue.shift();
  } catch (err) {
    setBanner('Transcript out of sync. Refresh to recover.');
  } finally {
    flushing = false;
    if (queue.length > 0) setTimeout(flush, 0);
  }
}
```

**Why this doesn't block audio:** WebRTC audio frames are handled in
the browser's native media engine (C++ RTP packetizer). A `fetch()` on
the JS main thread cannot interpose between RTP packet generation and
the network. Even a 200ms POST during a voice turn is invisible to the
caller.

**On `beforeunload`:** `navigator.sendBeacon('/v1/conversations/{id}/messages',
JSON.stringify(queue[0]))` for best-effort flush of the head. Anything
beyond the head is acceptable loss — Claude and ChatGPT have the same
behavior on hard refresh.

### 9.3 Greeting + autoplay

- Fresh conversation + voice ON at toggle time → assistant speaks
  greeting + the headline of the first briefing card. Reads from a
  pinned 25-word voice-mode reply.
- Voice OFF (the default on landing) → no audio. User must click the
  mic to opt in. Browsers throttle autoplay otherwise; this is also
  the right default for politeness.

### 9.4 Persona + addenda

`web/src/chat/prompts.ts` exposes three concatenated blocks:

- `PERSONA` — calm, precise, slightly understated security engineer.
  Includes `{user_first_name}` interpolation. Borrowed from
  `~/Projects/Shasta/src/shasta/voice/realtime_config.py`.
- `TOOL_RULES` — never invent data; default to open + unresolved +
  latest scan when ambiguous; for action requests call `propose_*`
  (NEVER mutate directly); REDIRECTS for un-wired things (Slack /
  JIRA / email → "Not wired up yet — I can add it to your risk
  register").
- `VOICE_ADDENDUM` (voice only) — 25-word reply max, lead with the
  fact, never read ARNs / IPs / JSON aloud, name at most 3 items.
- `TEXT_ADDENDUM` (text only) — tool results carry artifact hints,
  don't restate, let the artifact speak; prefer `entity_list` over
  inline bullets; cite every concrete claim.

Bindings:
- Anthropic gets `PERSONA + TOOL_RULES + TEXT_ADDENDUM`.
- OpenAI Realtime gets `PERSONA + TOOL_RULES + VOICE_ADDENDUM`.

## 10. Migration plan

| Item | Move | Risk |
|---|---|---|
| Route `/` | Was Welcome. Becomes Chat. | None — Welcome moves to `/dashboard` as-is. |
| Route `/dashboard` | NEW. Existing Welcome component, new path. | Zero. |
| Voice modal `web/src/voice/VoiceChat.tsx` | Retired. Mic moves to composer. | Tool catalog re-homed to `web/src/chat/tools.ts`. `web/src/voice/` survives for the WebRTC client class only (re-exported into chat). |
| Sidebar item "Top risks" | Renamed to **Findings**. Route unchanged. | None. |
| Sidebar item "Voice" | Removed. | None. |
| `lambda/voice_session/` | Folded into `lambda/chat_session/`. New endpoint `POST /v1/conversations/{id}/voice` (mints `ek_...`). | Legacy `POST /voice/session` stays alive behind a feature flag for one release so the existing iOS app keeps working until SP4.5. |
| Anthropic helper | Copied (3rd time) into `lambda/chat_session/anthropic_call.py`. Three copies is cheaper than a Lambda layer. Keep the signature identical. | Layer migration is a one-day cleanup later. |
| Aurora migration `006_conversations.sql` | New tables, no destructive changes. | Idempotent, additive. |
| `web/src/voice/excelHelpers.ts` | Stays put. Not voice-specific despite the path. | None. |

**Two-pass deploy:**
1. Ship `chat_session` Lambda with both old + new routes. Migration
   `006` runs. New web ships. iOS keeps working on the old route.
2. After SP4.5 lands and iOS uses the new route: remove the legacy
   `voice_session` Lambda and drop the old API Gateway route.

## 11. Effort estimate

Four vertical mini-slices. Each ends with a working demo.

| Mini-slice | Scope | Days | Demo |
|---|---|---|---|
| **4a — Shell + text chat** | New routes (`/` Chat, `/dashboard` Welcome), module rail, conversation rail, chat center skeleton, text composer (no voice), `chat_session` Lambda with Anthropic SSE via Function URL, `006_conversations.sql`, sign-in landing flow, seeded morning briefing. | ~4d | KK signs in → 3 briefing cards → asks "what's my IAM posture?" → text reply streams in. No artifacts yet. |
| **4b — Tools + artifacts** | 12 tools in `tools.ts`, 8 artifact components in `Artifact.tsx`, tool execution + artifact rendering wired, citation chips, source side-sheet, "Findings" rename. | ~4d | KK asks "show my top open findings" → `entity_list` + `finding_card` artifacts render with click-through into source side-sheet. |
| **4c — Voice integration** | Voice toggle in composer, WebRTC peer connection + Realtime ephemeral key bound to conversation_id, per-turn POST with retry queue, `sendBeacon` on unload, interruption (barge-in), persona prompt + addenda. | ~3d | KK toggles voice → ChatGPT-equivalent UX: transcript streams live, refresh resumes, barge-in works, voice tool calls share the same catalog. |
| **4d — Action approvals** | `propose_risk_entry` + `propose_policy_draft` tools, `approval_card` artifact with inline edit fields, approve/cancel flow, idempotency on the approval UUID. | ~2d | KK says "add this to my risk register" → editable card → approve → `risks.id` created → green ✓ state. Same for policy draft. |

**Total SP4 (web): ~13 days.** SP4.5 (iOS catch-up): ~5 days (port
renderer + chat list view + composer to SwiftUI, reuse the same
JSON artifact hints).

## 12. Critical-path risks + mitigations

1. **Anthropic streaming via API Gateway REST is not supported.**
   - **Mitigation:** route the streaming path through a **Lambda
     Function URL with response streaming enabled**, separate from API
     Gateway REST. JWT auth handled in-Lambda by verifying the Cognito
     access token (same JWKS as the gateway authorizer uses).
   - All other routes stay on API Gateway REST.
   - Test in 4a end-to-end before moving on.

2. **Ephemeral key binding to conversation_id.**
   - **Mitigation:** the new `POST /v1/conversations/{id}/voice`
     endpoint is the one source. The session payload includes
     `metadata: { conversation_id }` so future OpenAI server-side
     hooks (if any) carry it; meanwhile the browser also tags every
     enqueued `SealedTurn` with the same id.

3. **Persistence of tool results across reload.**
   - **Mitigation:** `conversation_messages.content` for `role='tool'`
     stores the full `{tool_name, args, result, _artifact_hint, source,
     call_id}`. On `GET /v1/conversations/{id}`, the renderer
     reconstitutes each tool message as the same artifact card.
   - Test this in 4b end-to-end — reload mid-conversation and verify
     every card re-renders identically.

4. **Voice transcript queue stalls under poor network.**
   - **Mitigation:** the retry queue caps at 5 attempts with
     exponential backoff to 30s. Persistent failure surfaces a
     non-blocking banner ("Transcript out of sync — refresh to
     recover."). The audio session itself is unaffected because
     WebRTC keeps the data channel alive independently of our
     persistence path.

5. **Cross-tenant data leak in conversations.**
   - **Mitigation:** every `chat_session` route enforces
     `tenant_id = $jwt.tenant_id` in the SQL WHERE clause. Add a
     unit test that proves `GET /v1/conversations/{id}` returns 404
     for another tenant's conversation.

## 13. File map

New files (the spec's footprint):

```
platform/
  sql/
    006_conversations.sql                     # 2 tables + indexes
  lambda/
    chat_session/
      main.py                                  # router + handlers
      anthropic_call.py                        # streaming proxy helper
      prompts.py                               # PERSONA + TOOL_RULES + addenda
      conversations.py                         # CRUD
      messages.py                              # append + Anthropic streaming
      voice.py                                 # ephemeral key mint
      tools_dispatch.py                        # server-side data tools (read-only DB queries)
  lib/
    api-stack.ts                               # +5 routes wired to chat_session
    chat-fn-url-stack.ts                       # Function URL for streaming SSE

web/
  src/
    chat/
      Shell.tsx                                # four-column layout
      ModuleRail.tsx
      ConversationRail.tsx
      ChatCenter.tsx
      Composer.tsx
      MessageStream.tsx
      Artifact.tsx                             # single switch on _artifact_hint
      artifacts/
        KpiCard.tsx
        EntityList.tsx
        FindingCard.tsx
        RiskCard.tsx
        ChartBar.tsx
        ChartDonut.tsx
        SeverityBreakdown.tsx
        ApprovalCard.tsx
      SourceSideSheet.tsx
      tools.ts                                 # the 12-tool source of truth
      prompts.ts                               # PERSONA + addenda (client side, for voice config)
      voiceClient.ts                           # WebRTC + TurnQueue + retry + sendBeacon
      anthropicClient.ts                       # SSE consumer
      state.ts                                 # conversation state (useReducer)
      api.ts                                   # /v1/conversations* + /v1/conversations/{id}/voice
    routes/
      Dashboard.tsx                            # was Welcome.tsx (renamed)
    App.tsx                                    # route table updated
```

Removed / retired:

```
web/src/voice/VoiceChat.tsx                   # voice modal — DELETED
web/src/voice/ConfigureVoice.tsx              # if present — DELETED
platform/lambda/voice_session/main.py         # folded into chat_session; route retained for one release behind feature flag
```

Renamed labels (no file moves):

```
Sidebar item "Top risks"                       → "Findings"
Sidebar item "Voice"                           → removed
Top-of-app heading "Welcome"                   → "Chat" (on /) / "Dashboard" (on /dashboard)
```

## 14. Resolved ambiguities (locked 2026-05-19)

1. **Conversation auto-title** — first user message text, truncated
   at 60 chars. Falls back to "New conversation" until the user has
   sent one message.
2. **Briefing on same-day return** — second visit shows the most-
   recent conversation as-is. No fresh briefing seed.
3. **Module rail order** — Chat → Dashboard → Findings → Risk
   register → Policies → Questionnaires → Trust center → AI
   inventory → Connect → Admin. Editable later if KK wants to
   re-order.
4. **Multi-turn approval edits** — only the final approved payload
   is written to `risks` / `policies`. Intermediate edits live in
   the conversation message stream as state transitions on the
   `approval_card`.

## 15. Cross-LLM consistency (Anthropic text + OpenAI voice)

The dual-LLM architecture is the right tradeoff (best reasoning for
text, best voice UX), but it raises a fair concern: will the user
get inconsistent answers when asking the same question via text vs
voice?

**Why consistency holds in practice:**

1. **Both LLMs are constrained to narrate deterministic tool
   results.** The `TOOL_RULES` block (shared verbatim) forbids
   inventing data. Every concrete claim — finding counts, severities,
   ARNs, compliance scores, risk register contents — comes from a
   tool call that hits Aurora. Aurora returns identical data
   regardless of which LLM asked.
2. **Single tool catalog with single input schemas.** `tools.ts` is
   the source of truth. Both LLMs see the same tool descriptions
   and call them with the same shapes. The `toAnthropicTools()` and
   `toRealtimeTools()` translators only reformat structure; the
   semantic contract (what each tool does and returns) is identical.
3. **Artifact cards are identical across modalities.** A
   `finding_card` rendered from a voice-triggered tool call is the
   same component, same fields, same source chip as the one
   rendered from text. The user's *visual* answer is identical even
   if the *spoken* phrasing differs.
4. **Same PERSONA across both.** Calm, precise, slightly
   understated. Same redirects ("not wired up yet — I can add it to
   your risk register"). Same refusal patterns.

**Where they will legitimately differ — and that's OK:**

- **Length and tone.** `VOICE_ADDENDUM` caps voice at 25 words and
  bans ARNs/JSON. `TEXT_ADDENDUM` defers to artifacts and cites
  every claim. The same question gets a terse spoken reply with a
  visible card AND a fuller text reply with the same card. The
  *facts* are identical; the *narration* differs by modality.
- **Tool-call timing and ordering.** If a question requires three
  tool calls, the two LLMs may serialize vs parallelize differently.
  The end state — three tool results in the conversation — is the
  same.
- **Word choice on subjective phrasing.** "Mostly compliant" vs
  "largely compliant." Acceptable.

**Where consistency could actually break — to guard against:**

- **Hallucinated facts from one LLM.** Mitigation: `TOOL_RULES` is
  explicit; every numeric claim must cite a tool result. Code review
  the prompt before 4a ships. Add a runtime check in `chat_session`:
  if the assistant message text mentions a finding count, severity,
  or compliance score, and no tool result in the conversation
  supports it, log a `consistency_warning` event for review. (Not
  blocking — observability only.)
- **Different action proposals from the same input.** Voice "add
  this risk" vs text "add this risk" should produce *the same
  proposed payload* in the `approval_card`. Mitigation: the
  `propose_*` tool's `input_schema` enumerates required fields; both
  LLMs must fill them. The card UI lets the user edit before
  approving, so any drift is user-correctable.

**Verification gate at end of 4c (voice ships):** ask the same 10
test questions in text and in voice. Compare the persisted tool
results in `conversation_messages`. Same tools called, same
arguments (within sensible variance), same results. Differences in
phrasing are fine. Differences in tool-call outputs are bugs.

---

## 16. Prerequisites

SP4 cannot start until both predecessor PRs are merged:

- **PR #1 — Slice 1b** (`feat/ai-security-slice-1b`): the AI scanner
  + 8 detectors + AI Inventory. Required because SP1 builds on top
  of its scanner emissions.
- **PR #2 — SP1: Unified entity + edge model** (`feat/sp1-unified-entities`):
  required because SP4's `query_entities` / `get_entity` tools call
  the `entities_api` Lambda introduced by SP1.

Merge order: PR #1 → PR #2 → branch `feat/sp4-chat-first` from
`main`.

---

**Ready for KK review.** On approval, next step is to invoke the
`writing-plans` skill to generate
`docs/superpowers/plans/2026-05-19-sp4-chat-first.md` with the
4-slice phased plan + test gates per slice.
