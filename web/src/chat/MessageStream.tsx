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
