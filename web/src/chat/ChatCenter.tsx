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
