// web/src/chat/ConversationRail.tsx
import type { ConversationSummary } from "./chatApi";

export function ConversationRail({ conversations, activeId, onSelect, onNew }: {
  conversations: ConversationSummary[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void;
}) {
  return (
    <div style={{ width: 220, background: "#F5F0E6",
                  display: "flex", flexDirection: "column" }}>
      <button onClick={onNew}
        style={{ margin: 12, padding: "8px 12px", borderRadius: 8,
                 border: "none", background: "#D85F3B", color: "#fff",
                 cursor: "pointer", fontSize: 13 }}>+ New conversation</button>
      <div style={{ overflowY: "auto" }}>
        {conversations.map((c) => (
          <div key={c.id} onClick={() => onSelect(c.id)}
            style={{ padding: "8px 14px", fontSize: 13, cursor: "pointer",
              color: "#3A342B",
              borderLeft: c.id === activeId
                ? "3px solid #D85F3B" : "3px solid transparent",
              background: c.id === activeId ? "#FFFCF6" : "transparent" }}>
            {c.title}
          </div>
        ))}
      </div>
    </div>
  );
}
