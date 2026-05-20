// Chat API client for SP4 chat-first feature.
// REST conversation CRUD via API Gateway; streaming turns via Lambda Web Adapter Function URL.
// Mirrors web/src/lib/api.ts: Bearer token via validIdToken(), hardcoded base URLs.

import { validIdToken } from "../lib/cognito";

const REST_BASE  = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";
const STREAM_BASE = "https://otc43ep2sidkuyv5uaxpclljsu0rkvbr.lambda-url.us-east-1.on.aws";

export type Role = "user" | "assistant" | "tool" | "system";

export interface ChatMessage {
  role:       Role;
  content:    any;
  created_at?: string;
}

export interface ConversationSummary {
  id:               string;
  title:            string;
  last_activity_at: string;
}

async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = await validIdToken();
  return fetch(url, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      Authorization:  `Bearer ${token}`,
      "content-type": "application/json",
    },
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

export async function deleteConversation(id: string): Promise<void> {
  await authedFetch(`${REST_BASE}/conversations/${id}`, { method: "DELETE" });
}

/**
 * Streaming text turn via SSE.
 * Calls onDelta for each text-delta token.
 * Throws on error frames so callers can surface the message.
 */
export async function streamMessage(
  conversationId: string,
  text: string,
  onDelta: (t: string) => void,
): Promise<void> {
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
        onDelta(ev.text);
      } else if (ev.error) {
        throw new Error(`stream error: ${ev.error}`);
      }
      // "done" frame: no action needed — loop ends naturally on reader completion
    }
  }
}
