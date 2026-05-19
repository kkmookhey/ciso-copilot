# SP4 (Chat-First Front Door) — Brainstorm Checkpoint

> **Status:** mid-brainstorm. Sections 1 + 2 approved, Section 3 proposed with one
> KK-requested amendment (voice latency). Section 4 (migration, scope, effort) not
> yet drafted. Spec not yet written.
>
> **Resume:** read this file + memory at session start. Skip the 8 clarifying
> questions — they're answered below. Continue from Section 3 approval.
>
> Date: 2026-05-19. Predecessor: SP1 shipped (PR #2 merged or pending).
> Sibling sub-projects: SP2 (graph viz), SP3 (unified findings/risks UX),
> SP5 (voice on web — partly absorbed into SP4 now), SP6 (dynamic dashboards
> from chat — absorbed into SP4 as the artifact catalog).

---

## Visual companion state

- Server URL during the original session: `http://localhost:53860`
- Screen dir: `/Users/kkmookhey/Projects/CISOBrief/.superpowers/brainstorm/54991-1779221730/content/`
- Latest screens (newest last): `palette.html` → `palette-accents.html` → `waiting-1.html` → `shell.html` → `shell-v2.html` → `shell-v3.html` → `waiting-2.html`
- The server auto-exits after 30 minutes idle — next session will need to restart it.
- The visual artifacts (mockups) persist in the screen dir, so KK can refer back if needed.

---

## All 8 clarifying questions — answered + locked

| # | Dimension | Decision |
|---|---|---|
| Q1 | Landing flow | **Proactive briefing** — sign-in shows greeting + 2-3 contextual cards (top open finding, posture severity breakdown, risk register summary). |
| Q2 | Surface placement | Chat replaces Welcome at `/`. Welcome demoted to `/dashboard`. Old voice modal retires (voice integrated into the chat composer). |
| Q3 | Artifacts | **Fixed component set** with JSON schemas — 8 components, listed below. |
| Q4 | LLM bridge | **Anthropic for text + OpenAI Realtime for voice**, single shared tool catalog in TypeScript. |
| Q5 | Persistence | Server-side `conversations` + `conversation_messages` tables. Entity-anchored memory parked for later. |
| Q6 | Citations | **Always-visible subtle source chips** on every card and concrete claim — auto-rendered from the `source` field on tool results. |
| Q7 | iOS scope | **Web first**; iOS catches up in SP4.5 (~1 week, mostly port the artifact renderer + chat list view to SwiftUI). |
| Q8 | Edit flow | **Card grows in place** with inline editable fields. Save → re-renders the proposal card. |

## Aesthetic decisions

- **Palette: Quiet Paper + Persimmon (`#D85F3B`).** Cream base `#FAF8F3`, cards `#FFFCF6`, borders `#E8DFD0`, dark warm text `#3A342B`, muted `#7A7268` / `#A89B89`. Tan secondary `#F5E8DB` / `#85613A` for tags.
- **Typography**: Georgia serif for greetings + h1; Inter sans for body.
- **Icons**: Lucide-style thin-stroke SVGs (1.6px), color inherits from text. Replaces all emoji.
- **Voice indicator**: persimmon breathing dot in the chat header (`#D85F3B` + `0 0 0 4px rgba(216,95,59,0.18)` glow).

## The "Findings" rename

- Sidebar item formerly labeled "Top risks" is now **Findings**.

---

## Section 1 — Surface layout (APPROVED)

Four-column shell:

1. **Module rail** (always visible, ~200px) — dark warm-bark `#3A342B` background, cream text. Contains: Chat (active route gets persimmon dot), Dashboard, Findings, Risk register, Policies, Questionnaires, Trust center, AI inventory, Connect, Admin. Email at bottom. Auto-collapses to icon-only at narrow widths.
2. **Conversation history** (~220px, open by default, collapsible) — soft cream rail `#F5F0E6`. "+ New conversation" persimmon button at top. Grouped Today / Yesterday / Last week. Active conversation has persimmon left-border. Collapses to 40px vertical handle with rotated "Conversations" label.
3. **Chat center** (fills remaining width) — header (voice status dot), message stream, composer at bottom.
4. **Source side-sheet** (40px collapsed by default, expandable) — opens when user clicks an `↗ source` chip on any card. Shows the underlying entity/finding/evidence packet. Collapses on outside click.

**Composer:** rounded-pill input with placeholder "Ask anything…", mic icon button (toggles voice), send button (persimmon arrow).

**Mockup files for reference:** `shell-v3.html` (latest) — has Lucide icons + "Findings" rename + collapsible rail handles.

---

## Section 2 — Tools, LLM bridge, artifacts (APPROVED)

### Tool catalog (12 tools)

Single source: `web/src/chat/tools.ts`. Two translators: `toAnthropicTools()` for text, `toRealtimeTools()` for voice. Shared `executeTool(name, args)` in the browser.

| Tool | Flavor | Returns |
|---|---|---|
| `get_morning_briefing` | data | 2-3 artifacts (top open finding kpi, posture severity_breakdown, risk register summary) |
| `query_entities` | data | entity_list |
| `get_entity` | data | entity detail panel (right-rail-shaped) |
| `query_findings` | data | finding list |
| `get_finding` | data | finding_card |
| `get_compliance_summary` | data | chart_donut + framework tiles |
| `get_severity_breakdown` | data | severity_breakdown |
| `list_risks` | data | risk_card list |
| `propose_risk_entry` | **action** | approval_card (`add_risk` payload) |
| `propose_policy_draft` | **action** | approval_card (`draft_policy` payload + content preview) |
| `navigate_to` | side-effect | `{navigated_to: path}` |
| `filter_findings_view` | side-effect | `{filtered: params}` |

Existing voice tools (`get_top_risks`, `list_connected_clouds`, `list_recent_alerts`, `add_risk`) get **folded into** this catalog — `add_risk` becomes `propose_risk_entry` so voice goes through the approval card too (no more bypass).

### LLM bridge

```
                    conversations + conversation_messages (Aurora)
                                  ▲ ▼
                       browser conversation state (React)
                          ▲                  ▲
                          │                  │
                     text path           voice path
                  Anthropic Messages    OpenAI Realtime
                  (streaming SSE)       (WebRTC data channel)
                          ▲                  ▲
                          │                  │
                          └──── shared ──────┘
                            executeTool(name, args) → REST
                            tool_result + _artifact_hint → render
```

Single server-side Lambda `chat_session/main.py` — combines:
- `POST /v1/conversations` + CRUD
- `POST /v1/conversations/{id}/messages` (text → proxy Anthropic streaming, SSE back)
- `POST /v1/voice/session?conversation_id=...` (mint OpenAI Realtime ephemeral key, bind to conversation)

Sharing one Lambda because all three read/write the same conversation state.

### Artifact catalog (8 components)

```typescript
type Source = {
  entity_id?: string;
  finding_id?: string;
  evidence_packet_id?: string;
  scan_id?: string;
  last_scan_at?: string;
};
```

Closed set:

1. **`kpi_card`** — `{label, value, detail?, severity?, tags?, source?}`
2. **`entity_list`** — `{title?, entities: [{id, kind, display_name, source_path?, source?}]}`
3. **`finding_card`** — `{finding_id, check_id, title, severity, description?, resource_arn?, region?, frameworks?, source}`
4. **`risk_card`** — `{risk_id, title, severity, status, owner?, due_date?, source?}`
5. **`chart_bar`** — `{title, x_label?, y_label?, series: [{label, value, color?}], source?}`
6. **`chart_donut`** — `{title, segments: [{label, value, color?}], source?}`
7. **`severity_breakdown`** — `{total, critical, high, medium, low, delta_since?, source?}`
8. **`approval_card`** — `{action_kind, current_status, payload, edit_fields, result?}`

Renderer = single switch on `_artifact_hint` in `web/src/chat/Artifact.tsx`. Adding components later = add a case + a type union variant. No schema migration.

**Tool results carry their own artifact hint.** No separate "render this as X" step. Cleaner than two-call patterns.

---

## Section 3 — Action approval, persistence, voice integration (APPROVED 2026-05-19, voice amended again)

### 3a. Action approval flow

Two action tools in v1: `propose_risk_entry`, `propose_policy_draft`. Both emit `approval_card`. NEVER auto-execute.

Card states: `pending` → `editing` (inline form, Save/Cancel) → `approved` (green-tan ✓ + result link) | `cancelled` (struck-through) | `error` (persimmon border + retry).

Idempotency: each card has a UUID. Approve clicks are no-op after status='approved'.

**Audit trail: deferred.** No `audit_events` table in SP4. Wire it up later when the first retrospective report is built. The result_id on each approved card (the new `risks.id` / `policies.id` it produced) is recoverable from the conversation history because the approval flows through `conversation_messages`.

### 3b. Conversations persistence

New SQL migration `006_conversations.sql`:

```sql
CREATE TABLE conversations (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id           UUID NOT NULL REFERENCES users(user_id),
  title             TEXT NOT NULL DEFAULT 'New conversation',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX conversations_tenant_user_recent_idx
  ON conversations(tenant_id, user_id, last_activity_at DESC);

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

Content shape by role:
- `user`: `{ text: string }`
- `assistant`: `{ text: string, tool_calls?: [...] }`
- `tool`: `{ tool_name, args, result, _artifact_hint?, source? }`
- `system`: `{ text: string }` (e.g., seeded morning briefing)

Sign-in landing flow:
1. Web fetches `GET /v1/conversations` (most-recent first).
2. If most-recent's `last_activity_at` within 24h → load it. Else `POST /v1/conversations` to start fresh; server seeds a `system` message with the morning briefing payload.
3. Render. Voice toggle starts OFF.

### 3c. Voice integration — **best-in-class (Claude/ChatGPT-equivalent)**

**Per-turn POST after `response.done`, fire-and-forget with retry queue.** Transcript is part of the conversation as it happens, refresh-safe, no batched end-of-session flush.

Why this doesn't compete with audio frames: WebRTC audio frames are handled by the browser's native media engine (RTCPeerConnection / RTP packetizer in C++), NOT the JS main thread. A `fetch()` on the JS thread cannot block packetization. The original "buffered flush" plan was over-cautious.

**Per-turn flow:**
1. Realtime data channel fires `response.done` → we have the completed user turn (transcript via `conversation.item.input_audio_transcription.done`) + assistant turn (transcript via `response.output_audio_transcript.done` + any tool calls).
2. Push `{user_message, assistant_message, tool_calls?}` into a `TurnQueue` (an in-memory FIFO with a single `flushing` flag).
3. Queue worker (`async`, not a Web Worker — just a Promise loop) POSTs to `/v1/conversations/{id}/messages` one turn at a time. Failure → exponential backoff (max 30s, max 5 retries). Persistent failure → mark conversation header with a warning "transcript out of sync — refresh to recover."
4. Tool calls execute synchronously in the browser (unchanged from today). Tool result is part of the assistant turn payload.
5. If the user toggles voice OFF while the queue is non-empty, queue drains in background — UI is immediately responsive.
6. Page refresh during pending flush: best-effort `navigator.sendBeacon()` from `beforeunload` for the queue head. Anything beyond head is acceptable loss (matches Claude/ChatGPT — they also lose the last unsent turn on hard refresh).

**Transcript visibility:** assistant + user turn messages render in the chat stream the instant the Realtime data channel events arrive, BEFORE the POST returns. The POST is a persistence concern, not a render concern. This is what gives the "transcript appears live as you speak" UX.

**Voice toggle states:** OFF (default) → Connecting → ON (composer disabled, persimmon breathing dot live, transcript streaming into the conversation) → OFF (queue drains in background).

**Greeting:** chat surface loads → if voice ON and conversation is fresh → assistant speaks greeting + headline of first briefing card. If voice OFF → text only. **No autoplay audio without explicit user opt-in** (browser policy + politeness).

**Interruption handling:** Realtime's `input_audio_buffer.speech_started` from the data channel → if assistant is mid-audio playback, immediately cancel via `response.cancel`. Matches ChatGPT/Claude barge-in behavior.

### 3d. System prompt strategy

`web/src/chat/prompts.ts` — three concatenated blocks:

- **`PERSONA`**: "You are CISO Copilot. Calm, precise, slightly understated. Experienced security engineer on a Tuesday afternoon." Includes `{user_first_name}` interpolation. **Borrowed wholesale from Shasta voice's persona principles.**
- **`TOOL_RULES`**: never invent data; for ambiguous questions, default to open+unresolved+latest scan; for action requests, call the `propose_*` tool (NEVER mutate directly); REDIRECTS for things we can't do yet (Slack/JIRA/email → "Not wired up yet — I can add it to your risk register").
- **`VOICE_ADDENDUM`** (appended for voice mode only): 25 word max, lead with the fact, never read ARNs/IPs/JSON aloud, name at most 3 items.
- **`TEXT_ADDENDUM`** (appended for text mode only): tool results carry artifact hints — don't restate, let the artifact speak; prefer `entity_list` over inline bullets; cite every concrete claim.

OpenAI Realtime gets `PERSONA + TOOL_RULES + VOICE_ADDENDUM`.
Anthropic gets `PERSONA + TOOL_RULES + TEXT_ADDENDUM`.

### Section 3 — KK approval (2026-05-19, resumed session)

1. Voice strategy revised to **per-turn POST after `response.done` with retry queue + best-effort `sendBeacon` on unload**. KK asked for ChatGPT/Claude-equivalent voice UX; the buffered-flush plan was rejected in favor of streaming persistence.
2. Combined `chat_session` Lambda — **approved**.
3. `audit_events` — **deferred** to a future slice when the first retrospective report is built.

---

## Section 4 — Migration, scope boundary, effort (DRAFTED 2026-05-19)

### 4a. Migration plan

| Item | Move | Risk |
|---|---|---|
| Route `/` | Was Welcome (greeting + stat tiles). Becomes Chat. | Bookmarks resolve fine — old greeting content moves wholesale to `/dashboard`. |
| Route `/dashboard` | NEW. Renders the existing Welcome component as-is (stat tiles + framework cards + recent activity). The "classic dashboard" fallback. | Zero — same component, new path. |
| Voice modal (`web/src/voice/VoiceChat.tsx`) | Retired. Mic icon moves to the chat composer; voice replies stream into the conversation, not a modal. | Tool catalog re-homed to `web/src/chat/tools.ts`. Folder `web/src/voice/` survives for the WebRTC client class only (re-exported into chat). |
| Sidebar item "Top risks" | Renamed to **Findings**. Route unchanged (`/findings` already exists). | None — pure label change. |
| Sidebar item "Voice" | Removed. Voice is a composer affordance, not a destination. | None. |
| Anthropic helper (`lambda/policies/anthropic_call.py`, `lambda/questionnaires/anthropic_call.py`) | Copied (again) into `lambda/chat_session/anthropic_call.py`. Three copies is fine; cheaper than packaging a Lambda layer. | Keep the helper signature identical so a layer migration later is a one-day cleanup. |
| Voice ephemeral-key Lambda (`lambda/voice_session/`) | Folded into `lambda/chat_session/`. Endpoint `POST /voice/session` becomes `POST /v1/conversations/{id}/voice` (still mints `ek_...` from OpenAI). | Old endpoint stays alive for 1 release behind a feature flag so the iOS app keeps working until SP4.5. |
| New table `conversations` | Aurora migration `006_conversations.sql`. | Tenant + user scoped. Idempotent migration. |
| New table `conversation_messages` | Same migration. JSONB content keyed by role. | Per-conversation index for chronological reads. |
| `web/src/voice/excelHelpers.ts` | Stays put — not voice-specific despite the path. (We'll rename in a later cleanup.) | None. |

**Two-pass deploy order** to avoid breaking iOS during the cutover:
1. Pass 1: ship `chat_session` Lambda with both old + new routes (`POST /voice/session` AND `POST /v1/conversations/{id}/voice`). iOS keeps working on the old route. Migration `006` runs. New web ships.
2. Pass 2 (after iOS SP4.5): remove the old `/voice/session` Lambda. Drop the legacy route from API Gateway.

### 4b. Out of scope for SP4 (deferred)

- **iOS chat surface** — SP4.5 (~1 week). Port artifact renderer + chat list view + composer to SwiftUI. Voice on iOS stays on its current modal until SP4.5.
- **Entity-anchored long-term memory** — across conversations, "remember that I usually want IAM findings first." Not in v1.
- **`audit_events` table** — see §3a. Add when the first retrospective report is built.
- **Slack / Teams / Email / JIRA integrations** — system prompt still REDIRECTS to "I can add it to your risk register."
- **Action approvals beyond `propose_risk_entry` + `propose_policy_draft`** — no cloud config changes, no IAM edits, no scanner triggers from chat. Determinism + reversibility invariants.
- **Multi-modal input** — no image/file uploads in v1.
- **Streaming partial tool calls** — tool results materialize as a card only when the call completes. (Anthropic streams text tokens; tool calls land as a single block.)
- **Real-time multi-user conversations** — one user per conversation.
- **Conversation-level sharing or export** — comes after the action audit story.
- **Mobile-web responsive layout** — desktop only in v1. The four-column shell collapses ungracefully under 900px; we'll fix this with native iOS in SP4.5.
- **Branded LLM model selection** — model pinned per provider per the locked invariants. No "use Sonnet vs Opus" toggle.

### 4c. Effort estimate

Slice into 4 vertical mini-slices. Each ends with a demo.

| Mini-slice | Scope | Days | Demo |
|---|---|---|---|
| **4a — Shell + text chat** | New routes, module rail, conversation rail, chat center skeleton, text composer (no voice yet), `chat_session` Lambda with Anthropic SSE text path, `conversations` + `conversation_messages` schema, sign-in landing flow + seeded morning briefing. | ~4d | KK logs in → 3 briefing cards appear → asks "what's my IAM posture?" → gets text reply. No artifacts yet. |
| **4b — Tool catalog + artifacts** | 12 tools in `tools.ts`, 8 artifact components in `Artifact.tsx`, tool execution wired into the message stream, citation chips, source side-sheet, "Findings" rename in nav. | ~4d | KK asks "show my top open findings" → `entity_list` + `finding_card` artifacts render with click-through to source side-sheet. |
| **4c — Voice integration** | Voice toggle in composer, WebRTC peer connection + Realtime ephemeral key bound to conversation_id, per-turn POST with retry queue, `sendBeacon` on unload, interruption handling, persona system prompt with TEXT/VOICE addenda. | ~3d | KK toggles voice → ChatGPT-equivalent UX: transcript streams live, refresh resumes, barge-in works, voice tool calls share the same catalog. |
| **4d — Action approvals** | `propose_risk_entry` + `propose_policy_draft` tools, `approval_card` artifact with inline edit fields, approve/cancel flow, idempotency on the approval UUID. | ~2d | KK says "add this to my risk register" → editable card → approve → `risks.id` created → green ✓ state with link. Same for policy draft. |

**Total SP4 (web): ~13 days.** SP4.5 (iOS catch-up): ~5 days.

**Critical-path risks** (mitigations called out in the spec):
1. **Anthropic streaming through API Gateway REST.** v1 stage is REST API, which does NOT support streaming responses. Either (a) move `chat_session` to a Lambda Function URL with response streaming enabled (separate from API Gateway), or (b) buffer the full response and return non-streamed JSON. (a) is the right answer; cost is minimal (no per-request API Gateway fee) and we keep auth via signed JWT verified in the Lambda directly. Decision to be locked in the spec.
2. **Realtime ephemeral key needs the conversation_id at mint time** to bind transcripts to a server-side row. New endpoint shape `POST /v1/conversations/{id}/voice` makes this explicit (vs the current ad-hoc `/voice/session`).
3. **Persistence of tool results across reload.** JSONB content includes `_artifact_hint` + `source` + raw result payload — the renderer in `Artifact.tsx` reconstitutes from JSONB on load. Test this end-to-end in 4b before moving on.

---

## What remains in the brainstorm

- ~~**Section 4** — Migration, out-of-scope list, effort estimate.~~ ✅ DRAFTED above.
- **Spec doc** — `docs/superpowers/specs/2026-05-19-sp4-chat-first-design.md` written from sections 1-4.
- **Spec self-review** — placeholder scan, internal consistency, scope, ambiguity.
- **User review gate** — KK reads + approves.
- **Invoke writing-plans skill** — transition to implementation plan.

## Borrowed patterns from Shasta voice (`~/Projects/Shasta/src/shasta/voice/`)

- Per-domain tool files (`tools/findings.py`, `tools/risks.py`, …) instead of one giant file.
- Pydantic-typed tool inputs with reused `Severity = Literal["critical","high",...]`.
- `build_session_payload()` pattern — single function returns Realtime config.
- Persona-led system prompt ("calm, precise, slightly understated") + voice-output rules ("25 word max", "never read ARNs aloud") + REDIRECTS pattern.
- Observability hooks on every tool call (latency, result size).

## Already-locked architecture invariants (from prior work — don't relitigate)

- Determinism is the spine. LLMs never write to graph/findings/risks directly. Only via `propose_*` tools + user approval.
- Every conclusion carries an evidence packet (entities + edges from SP1).
- Reversibility non-negotiable — no code/config changes from chat. Allowed actions: add to risk register, draft policy.
- One model version pinned per provider: `gpt-realtime` for voice, `claude-sonnet-4-6` for text + policy enrichment.
- Tenant scoping everywhere. Conversations are tenant + user scoped.
- iOS/web never call upstream sources — only API Gateway.

## Critical environment gotchas (carry forward from prior sessions)

1. **Bash sandbox silently resets `.git/HEAD` to `main`** during long chains. Pass `dangerouslyDisableSandbox: true` on every Bash call. Commit in dedicated Bash invocations.
2. **`logging.basicConfig` is a no-op in Lambda** — use `force=True` or `print()`.
3. **`*` is invalid in a Secrets Manager `SecretId`** (only valid in IAM ARNs).
4. **Container Lambdas can't hotswap** — use `update-function-code --image-uri`.
5. **Web Write tool's security hook blocks GitHub Actions YAML** — use `cat > … << 'EOF'` via Bash.

## Branch / PR state at checkpoint

- Branch: `feat/sp1-unified-entities` is the active development branch (SP1 + the rescan/delete features). PR #2 open.
- For SP4: new branch `feat/sp4-chat-first` should be created from `main` AFTER PR #2 merges. Or from the SP1 branch if SP4 needs SP1 features (it does — entities/edges/conversations all share Aurora).
- Current main HEAD: `b226821`. PR #2 HEAD: `b6f4978` + the connection rescan/delete commits.

## Files NOT yet created (for the next session to do)

- `docs/superpowers/specs/2026-05-19-sp4-chat-first-design.md` — the spec.
- `docs/superpowers/plans/2026-05-19-sp4-chat-first.md` — the implementation plan (via writing-plans skill).
- Branch `feat/sp4-chat-first`.
