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
