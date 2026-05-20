import type { ChatMessage } from "./chatApi";

export interface ChatState {
  conversationId: string | null;
  title: string;
  messages: ChatMessage[];
  streaming: boolean;
}

export const initialState: ChatState = {
  conversationId: null,
  title: "New conversation",
  messages: [],
  streaming: false,
};

export type ChatAction =
  | { type: "load"; id: string; title: string; messages: ChatMessage[] }
  | { type: "append"; message: ChatMessage }
  | { type: "appendTool"; content: any }
  | { type: "streamDelta"; text: string }
  | { type: "streaming"; on: boolean }
  | { type: "setTitle"; title: string }
  /**
   * Voice-path: upsert a message keyed by its Realtime conversation `item_id`.
   *
   * Realtime data-channel events do NOT arrive in display order: a user's
   * audio is transcribed asynchronously, so the assistant's reply can stream
   * in before the user's `input_audio_transcription.completed` event lands.
   * Appending in event-arrival order therefore produces wrong ordering
   * (assistant above user) and split/duplicate bubbles.
   *
   * Instead the voice client creates a placeholder message the moment a
   * conversation item exists (in correct stream order) and identifies it by
   * `itemId`. Every subsequent transcript event for that item updates THAT
   * message in place — wherever it sits in the stream — rather than touching
   * "the last bubble". A late user transcript fills its placeholder; a
   * multi-event assistant turn keeps mapping to one stable message.
   *
   * `drop: true` removes the message for `itemId` entirely — used when a user
   * placeholder's transcription turns out empty/hallucinated (Bug 1).
   */
  | { type: "voiceUpsert"; itemId: string; role: "user" | "assistant"; text: string; drop?: boolean };

export function chatReducer(s: ChatState, a: ChatAction): ChatState {
  switch (a.type) {
    case "load":
      return { ...s, conversationId: a.id, title: a.title, messages: a.messages };
    case "setTitle":
      return { ...s, title: a.title };
    case "append":
      return { ...s, messages: [...s.messages, a.message] };
    case "appendTool": {
      // Insert the tool message BEFORE a trailing assistant bubble so the
      // streaming assistant text (which keeps arriving as text deltas) stays
      // the last message and streamDelta keeps landing on it.
      const msgs = s.messages.slice();
      const toolMsg: ChatMessage = { role: "tool", content: a.content };
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        msgs.splice(msgs.length - 1, 0, toolMsg);
      } else {
        msgs.push(toolMsg);
      }
      return { ...s, messages: msgs };
    }
    case "streamDelta": {
      const msgs = s.messages.slice();
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        msgs[msgs.length - 1] = {
          ...last,
          content: { ...last.content, text: (last.content.text ?? "") + a.text },
        };
      }
      return { ...s, messages: msgs };
    }
    case "streaming":
      return { ...s, streaming: a.on };
    case "voiceUpsert": {
      const idx = s.messages.findIndex((m) => m.voiceItemId === a.itemId);
      if (a.drop) {
        // Hallucinated/empty transcript — remove the placeholder if present.
        if (idx === -1) return s;
        const msgs = s.messages.slice();
        msgs.splice(idx, 1);
        return { ...s, messages: msgs };
      }
      // Find an existing voice message for this Realtime item_id and update it
      // in place, wherever it sits. The message keeps its stream position, so a
      // late-arriving user transcript fills the placeholder created earlier and
      // a multi-event assistant turn maps to one stable bubble — never a
      // duplicate or stray partial bubble, never wrong ordering.
      if (idx !== -1) {
        const msgs = s.messages.slice();
        msgs[idx] = { ...msgs[idx], content: { ...msgs[idx].content, text: a.text } };
        return { ...s, messages: msgs };
      }
      // No message for this item yet — append a new one in arrival order.
      return {
        ...s,
        messages: [
          ...s.messages,
          { role: a.role, content: { text: a.text }, voiceItemId: a.itemId },
        ],
      };
    }
  }
}
