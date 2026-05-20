// web/src/chat/artifacts/FindingCard.tsx
import { CitationChip } from "../Artifact";
import type { Source } from "../tools";

const SEVERITY_PILL: Record<string, { bg: string; color: string }> = {
  critical: { bg: "#FDECEA", color: "#B91C1C" },
  high:     { bg: "#FEF3E2", color: "#C2410C" },
  medium:   { bg: "#FEFCE8", color: "#854D0E" },
  low:      { bg: "#F5F0E6", color: "#7A7268" },
  info:     { bg: "#F0EDE8", color: "#A89B89" },
};

interface FindingCardProps {
  kind:          "finding_card";
  finding_id:    string;
  check_id:      string;
  title:         string;
  severity:      "critical" | "high" | "medium" | "low" | "info";
  description?:  string;
  resource_arn?: string;
  region?:       string;
  frameworks?:   string[];
  source:        Source;
}

function SeverityPill({ severity }: { severity: string }) {
  const s = SEVERITY_PILL[severity] ?? SEVERITY_PILL.info;
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: "2px 8px",
      borderRadius: 99, background: s.bg, color: s.color,
      textTransform: "capitalize",
    }}>
      {severity}
    </span>
  );
}

export function FindingCard({
  check_id, title, severity, description, resource_arn, region, frameworks, source,
}: FindingCardProps) {
  return (
    <div style={{
      background: "#FFFCF6",
      border: "1px solid #E8DFD0",
      borderRadius: 12,
      padding: 16,
      margin: "8px 0",
      position: "relative",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start",
                    justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
        <div style={{ fontFamily: "Georgia, serif", fontSize: 15,
                      color: "#3A342B", lineHeight: 1.4, flex: 1 }}>
          {title}
        </div>
        <SeverityPill severity={severity} />
      </div>

      <div style={{ fontSize: 11, color: "#A89B89", marginBottom: description ? 10 : 4 }}>
        {check_id}{region ? ` · ${region}` : ""}
      </div>

      {description && (
        <div style={{ fontSize: 13, color: "#5A5248", lineHeight: 1.6, marginBottom: 10 }}>
          {description}
        </div>
      )}

      {resource_arn && (
        <div style={{ marginBottom: 8 }}>
          <code style={{
            fontSize: 11, fontFamily: "monospace", color: "#5A4A3A",
            background: "#F5F0E6", borderRadius: 4, padding: "3px 7px",
            wordBreak: "break-all", userSelect: "text",
          }}>
            {resource_arn}
          </code>
        </div>
      )}

      {frameworks && frameworks.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
          {frameworks.map(f => (
            <span key={f} style={{
              fontSize: 10, color: "#85613A", background: "#F5E8DB",
              borderRadius: 4, padding: "2px 6px",
            }}>{f}</span>
          ))}
        </div>
      )}

      <CitationChip source={source} />
    </div>
  );
}
