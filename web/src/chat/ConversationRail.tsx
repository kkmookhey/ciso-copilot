// web/src/chat/ConversationRail.tsx
import { useState, useRef, useEffect } from "react";
import type { ConversationSummary } from "./chatApi";

// Approximate height of the dropdown menu (2 rows × ~36px + border)
const MENU_HEIGHT = 80;
const MENU_WIDTH  = 130;

interface MenuPos { top: number; left: number; }

export function ConversationRail({ conversations, activeId, onSelect, onNew, onRename, onDelete }: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [hoveredId,  setHoveredId]  = useState<string | null>(null);
  const [menuId,     setMenuId]     = useState<string | null>(null);
  const [menuPos,    setMenuPos]    = useState<MenuPos | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the inline input as soon as it mounts
  useEffect(() => {
    if (renamingId) inputRef.current?.focus();
  }, [renamingId]);

  function openMenu(id: string, buttonEl: HTMLButtonElement) {
    const rect = buttonEl.getBoundingClientRect();
    // Default: open below the button, right-aligned
    let top  = rect.bottom + 4;
    const left = rect.right - MENU_WIDTH;
    // Flip upward if not enough space below
    if (top + MENU_HEIGHT > window.innerHeight) {
      top = rect.top - MENU_HEIGHT - 4;
    }
    setMenuPos({ top, left });
    setMenuId(id);
  }

  function closeMenu() {
    setMenuId(null);
    setMenuPos(null);
  }

  function startRename(id: string, currentTitle: string) {
    closeMenu();
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
    closeMenu();
    if (window.confirm("Delete this conversation?")) {
      onDelete(id);
    }
  }

  // Close the kebab menu when clicking elsewhere
  useEffect(() => {
    if (!menuId) return;
    function onMouseDown(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-conv-menu]")) closeMenu();
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") closeMenu();
    }
    // Close on scroll or resize — fixed menu won't follow the rail
    function onScrollOrResize() { closeMenu(); }

    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown",   onKeyDown);
    window.addEventListener("scroll",  onScrollOrResize, { capture: true });
    window.addEventListener("resize",  onScrollOrResize);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown",   onKeyDown);
      window.removeEventListener("scroll",  onScrollOrResize, { capture: true } as EventListenerOptions);
      window.removeEventListener("resize",  onScrollOrResize);
    };
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
                    if (menuOpen) {
                      closeMenu();
                    } else {
                      openMenu(c.id, e.currentTarget as HTMLButtonElement);
                    }
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
            </div>
          );
        })}
      </div>

      {/* Fixed-position dropdown — rendered outside the scroll container so it
          cannot be clipped by overflowY: auto */}
      {menuId && menuPos && (() => {
        const conv = conversations.find((c) => c.id === menuId);
        if (!conv) return null;
        return (
          <div
            data-conv-menu
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              top: menuPos.top,
              left: menuPos.left,
              width: MENU_WIDTH,
              zIndex: 1000,
              background: "#FFFCF6",
              border: "1px solid #E8DFD0",
              borderRadius: "6px 6px 8px 8px",
              boxShadow: "0 4px 16px rgba(58,52,43,0.12)",
              overflow: "hidden",
            }}
          >
            <button
              onClick={() => startRename(conv.id, conv.title)}
              style={menuItemStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = "#F5F0E6")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
                   style={{ marginRight: 8, verticalAlign: "middle", flexShrink: 0 }}
                   stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8.5 1.5a1.414 1.414 0 0 1 2 2L3.5 10.5 1 11l.5-2.5L8.5 1.5z"/>
              </svg>
              Rename
            </button>
            <button
              onClick={() => handleDelete(conv.id)}
              style={{ ...menuItemStyle, color: "#B94040" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "#F5F0E6")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
                   style={{ marginRight: 8, verticalAlign: "middle", flexShrink: 0 }}
                   stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="1,3 11,3"/>
                <path d="M2.5 3l.5 7.5a1 1 0 0 0 1 .5h5a1 1 0 0 0 1-.5L10.5 3"/>
                <path d="M4.5 3V1.5h3V3"/>
              </svg>
              Delete
            </button>
          </div>
        );
      })()}
    </div>
  );
}

const menuItemStyle: React.CSSProperties = {
  display: "flex", alignItems: "center", width: "100%",
  background: "transparent", border: "none",
  textAlign: "left", cursor: "pointer",
  padding: "8px 12px", fontSize: 13,
  color: "#3A342B",
};
