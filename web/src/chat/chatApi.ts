// Chat API client for SP4 chat-first feature.
// REST conversation CRUD via API Gateway; streaming turns via Lambda Web Adapter Function URL.
// Mirrors web/src/lib/api.ts: Bearer token via validIdToken(), hardcoded base URLs.

import { validIdToken, signOut } from "../lib/cognito";
import type { ArtifactHint, Source } from "./tools";
import { env } from "../lib/env";

const REST_BASE  = env.apiBaseUrl;
const STREAM_BASE = env.streamBaseUrl;

export type Role = "user" | "assistant" | "tool" | "system";

export interface ChatMessage {
  id?:        string;
  role:       Role;
  content:    any;
  created_at?: string;
  /**
   * Client-only: the OpenAI Realtime conversation `item_id` this message was
   * created from, for voice turns. Used by the `voiceUpsert` reducer action to
   * update the correct message when transcript events arrive out of order.
   * Not persisted server-side.
   */
  voiceItemId?: string;
}

export interface ConversationSummary {
  id:               string;
  title:            string;
  last_activity_at: string;
}

async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = await validIdToken();
  if (!token) { signOut(); throw new Error("not_signed_in"); }
  const res = await fetch(url, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      Authorization:  `Bearer ${token}`,
      "content-type": "application/json",
    },
  });
  if (res.status === 401) { signOut(); throw new Error("unauthorized"); }
  return res;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const r = await authedFetch(`${REST_BASE}/conversations`);
  return (await r.json()).conversations;
}

export async function createConversation(): Promise<string> {
  const r = await authedFetch(`${REST_BASE}/conversations`, { method: "POST" });
  return (await r.json()).conversation_id;
}

export async function getConversation(id: string): Promise<{ id: string; title: string; messages: ChatMessage[] }> {
  const r = await authedFetch(`${REST_BASE}/conversations/${id}`);
  return r.json();
}

export async function appendMessage(id: string, role: Role, content: any): Promise<void> {
  await authedFetch(`${REST_BASE}/conversations/${id}/messages`, {
    method: "POST",
    body:   JSON.stringify({ role, content }),
  });
}

export async function patchTitle(id: string, title: string): Promise<void> {
  await authedFetch(`${REST_BASE}/conversations/${id}`, {
    method: "PATCH",
    body:   JSON.stringify({ title }),
  });
}

/** Persist updated card content (approval state, edited payload) to the message row. */
export async function patchMessage(
  conversationId: string,
  messageId: string,
  content: any,
): Promise<void> {
  await authedFetch(
    `${REST_BASE}/conversations/${conversationId}/messages/${messageId}`,
    { method: "PATCH", body: JSON.stringify({ content }) },
  );
}

export async function deleteConversation(id: string): Promise<void> {
  await authedFetch(`${REST_BASE}/conversations/${id}`, { method: "DELETE" });
}

/** A server-side tool-result event surfaced over SSE (SP4 Task 4b.3). */
export interface ToolResultEvent {
  tool_name:       string;
  artifact_hint?:  ArtifactHint;
  artifact_hints?: ArtifactHint[];
  source?:         Source;
  /** Present for side-effect tools (navigate_to / filter_findings_view). */
  side_effect?:    Record<string, unknown>;
}

/** Callbacks for the streaming turn. onDelta is required; the rest optional. */
export interface StreamCallbacks {
  onDelta:        (t: string) => void;
  onToolResult?:  (ev: ToolResultEvent) => void;
  onSideEffect?:  (toolName: string, intent: Record<string, unknown>) => void;
}

/**
 * Streaming text turn via SSE.
 * Runs the server-side agentic tool-use loop — emits text-delta tokens,
 * tool-result events (artifact hints), and a final done frame.
 *
 * Accepts either a bare onDelta callback (legacy) or a StreamCallbacks object.
 * Throws on error frames so callers can surface the message.
 */
export async function streamMessage(
  conversationId: string,
  text: string,
  callbacks: ((t: string) => void) | StreamCallbacks,
): Promise<void> {
  const cb: StreamCallbacks =
    typeof callbacks === "function" ? { onDelta: callbacks } : callbacks;
  const token = await validIdToken();
  const res = await fetch(
    `${STREAM_BASE}/v1/conversations/${conversationId}/stream`,
    {
      method:  "POST",
      headers: {
        Authorization:  `Bearer ${token}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ text }),
    },
  );

  if (res.status === 401) { signOut(); throw new Error("unauthorized"); }
  if (!res.ok) {
    throw new Error(`stream endpoint ${res.status}: ${await res.text()}`);
  }

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
    }
  }
}
