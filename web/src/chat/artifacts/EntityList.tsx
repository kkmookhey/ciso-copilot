// web/src/chat/artifacts/EntityList.tsx
import type { Source } from "../tools";

interface Entity {
  id:           string;
  kind:         string;
  display_name: string;
  source_path?: string;
  source?:      Source;
}

interface EntityListProps {
  kind:      "entity_list";
  title?:    string;
  entities:  Entity[];
}

export function EntityList({ title, entities }: EntityListProps) {
  function handleRowClick(source?: Source) {
    if (!source) return;
    window.dispatchEvent(new CustomEvent("open-source-sheet", { detail: source }));
  }

  return (
    <div style={{
      background: "#FFFCF6",
      border: "1px solid #E8DFD0",
      borderRadius: 12,
      padding: 16,
      margin: "8px 0",
      position: "relative",
    }}>
      {title && (
        <div style={{ fontSize: 12, color: "#7A7268", fontWeight: 600,
                      marginBottom: 10, textTransform: "uppercase",
                      letterSpacing: "0.05em" }}>
          {title}
        </div>
      )}
      {entities.length === 0 ? (
        <div style={{ fontSize: 13, color: "#A89B89" }}>No items</div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {entities.map((e, i) => (
            <li key={e.id}>
              <button
                onClick={() => handleRowClick(e.source)}
                disabled={!e.source}
                style={{
                  width: "100%", textAlign: "left", background: "none",
                  border: "none", padding: "7px 6px", cursor: e.source ? "pointer" : "default",
                  borderTop: i === 0 ? "none" : "1px solid #F0E8DB",
                  display: "flex", alignItems: "center", gap: 8,
                  borderRadius: 6,
                  transition: "background 0.12s",
                }}
                onMouseEnter={e_ => { if (e.source) (e_.currentTarget as HTMLButtonElement).style.background = "#F5F0E6"; }}
                onMouseLeave={e_ => { (e_.currentTarget as HTMLButtonElement).style.background = "none"; }}
              >
                <span style={{
                  fontSize: 10, color: "#A89B89", background: "#F0E8DB",
                  borderRadius: 4, padding: "2px 6px", whiteSpace: "nowrap",
                  minWidth: 60, textAlign: "center",
                }}>
                  {e.kind}
                </span>
                <span style={{ fontSize: 13, color: "#3A342B", flex: 1,
                                overflow: "hidden", textOverflow: "ellipsis",
                                whiteSpace: "nowrap" }}>
                  {e.display_name}
                </span>
                {e.source_path && (
                  <span style={{ fontSize: 10, color: "#A89B89",
                                  fontFamily: "monospace", flexShrink: 0 }}>
                    {e.source_path}
                  </span>
                )}
                {e.source && (
                  <span style={{ fontSize: 11, color: "#A89B89", flexShrink: 0 }}>→</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
