// web/src/chat/ConversationRail.tsx
import { useState, useRef, useEffect } from "react";
import type { ConversationSummary } from "./chatApi";

export function ConversationRail({ conversations, activeId, onSelect, onNew, onRename, onDelete }: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [hoveredId, setHoveredId]   = useState<string | null>(null);
  const [menuId,    setMenuId]      = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the inline input as soon as it mounts
  useEffect(() => {
    if (renamingId) inputRef.current?.focus();
  }, [renamingId]);

  function startRename(id: string, currentTitle: string) {
    setMenuId(null);
    setRenamingId(id);
    setDraftTitle(currentTitle);
  }

  function commitRename() {
    if (renamingId && draftTitle.trim()) {
      onRename(renamingId, draftTitle.trim());
    }
    setRenamingId(null);
  }

  function cancelRename() {
    setRenamingId(null);
  }

  function handleDelete(id: string) {
    setMenuId(null);
    if (window.confirm("Delete this conversation?")) {
      onDelete(id);
    }
  }

  // Close the kebab menu when clicking elsewhere
  useEffect(() => {
    if (!menuId) return;
    function closeMenu(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-conv-menu]")) setMenuId(null);
    }
    document.addEventListener("mousedown", closeMenu);
    return () => document.removeEventListener("mousedown", closeMenu);
  }, [menuId]);

  return (
    <div style={{ width: 220, background: "#F5F0E6",
                  display: "flex", flexDirection: "column" }}>
      <button onClick={onNew}
        style={{ margin: 12, padding: "8px 12px", borderRadius: 8,
                 border: "none", background: "#D85F3B", color: "#fff",
                 cursor: "pointer", fontSize: 13 }}>+ New conversation</button>
      <div style={{ overflowY: "auto" }}>
        {conversations.map((c) => {
          const isActive   = c.id === activeId;
          const isHovered  = hoveredId === c.id;
          const isRenaming = renamingId === c.id;
          const menuOpen   = menuId === c.id;

          return (
            <div
              key={c.id}
              onMouseEnter={() => setHoveredId(c.id)}
              onMouseLeave={() => setHoveredId(null)}
              onClick={() => { if (!isRenaming) onSelect(c.id); }}
              style={{
                position: "relative",
                display: "flex", alignItems: "center",
                padding: "8px 8px 8px 14px",
                fontSize: 13, cursor: isRenaming ? "default" : "pointer",
                color: "#3A342B",
                borderLeft: isActive ? "3px solid #D85F3B" : "3px solid transparent",
                background: isActive ? "#FFFCF6" : "transparent",
              }}
            >
              {/* Title or inline rename input */}
              {isRenaming ? (
                <input
                  ref={inputRef}
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter")  { e.preventDefault(); commitRename(); }
                    if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
                  }}
                  onBlur={commitRename}
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    flex: 1, minWidth: 0,
                    fontSize: 13, color: "#3A342B",
                    background: "#FFFCF6",
                    border: "1px solid #D85F3B",
                    borderRadius: 4,
                    padding: "2px 4px",
                    outline: "none",
                  }}
                />
              ) : (
                <span style={{
                  flex: 1, minWidth: 0,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  paddingRight: (isHovered || menuOpen) ? 4 : 0,
                }}>
                  {c.title}
                </span>
              )}

              {/* Kebab ⋯ button — visible on hover or when menu is open */}
              {!isRenaming && (isHovered || menuOpen) && (
                <button
                  data-conv-menu
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuId(menuOpen ? null : c.id);
                  }}
                  style={{
                    flexShrink: 0,
                    background: "none", border: "none",
                    cursor: "pointer", padding: "0 4px",
                    color: "#7A7268", fontSize: 15, lineHeight: 1,
                    borderRadius: 4,
                  }}
                  title="More options"
                >
                  ⋯
                </button>
              )}

              {/* Dropdown menu */}
              {menuOpen && (
                <div
                  data-conv-menu
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    position: "absolute", top: "100%", right: 6, zIndex: 100,
                    background: "#FFFCF6",
                    border: "1px solid #E8DFD0",
                    borderRadius: 6,
                    boxShadow: "0 2px 8px rgba(58,52,43,0.12)",
                    minWidth: 110,
                    overflow: "hidden",
                  }}
                >
                  <button
                    onClick={() => startRename(c.id, c.title)}
                    style={menuItemStyle}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F5F0E6")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    Rename
                  </button>
                  <button
                    onClick={() => handleDelete(c.id)}
                    style={{ ...menuItemStyle, color: "#B94040" }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F5F0E6")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const menuItemStyle: React.CSSProperties = {
  display: "block", width: "100%",
  background: "transparent", border: "none",
  textAlign: "left", cursor: "pointer",
  padding: "8px 12px", fontSize: 13,
  color: "#3A342B",
};
