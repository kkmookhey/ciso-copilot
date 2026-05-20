// web/src/chat/MessageStream.tsx
import type { ChatMessage } from "./chatApi";
import type { ArtifactHint } from "./tools";
import { Artifact } from "./Artifact";

/** Pull every artifact hint out of a stored `tool` message content. */
function toolHints(content: any): ArtifactHint[] {
  if (!content) return [];
  if (Array.isArray(content._artifact_hints) && content._artifact_hints.length) {
    return content._artifact_hints;
  }
  if (content._artifact_hint) return [content._artifact_hint];
  return [];
}

export function MessageStream({ messages }: { messages: ChatMessage[] }) {
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "24px 32px" }}>
      {messages.map((m, i) => {
        if (m.role === "tool") {
          const hints = toolHints(m.content);
          if (hints.length === 0) return null;  // side-effect tool — nothing to draw
          return (
            <div key={i} style={{ margin: "12px 0", display: "flex",
              flexDirection: "column", gap: 8 }}>
              {hints.map((h, j) => (
                <div key={j} style={{ position: "relative" }}>
                  <Artifact hint={h} />
                </div>
              ))}
            </div>
          );
        }
        return (
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
        );
      })}
    </div>
  );
}
