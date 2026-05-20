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
  | { type: "streamDelta"; text: string }
  | { type: "streaming"; on: boolean }
  | { type: "setTitle"; title: string };

export function chatReducer(s: ChatState, a: ChatAction): ChatState {
  switch (a.type) {
    case "load":
      return { ...s, conversationId: a.id, title: a.title, messages: a.messages };
    case "setTitle":
      return { ...s, title: a.title };
    case "append":
      return { ...s, messages: [...s.messages, a.message] };
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
  }
}
