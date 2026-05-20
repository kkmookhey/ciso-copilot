// web/src/chat/artifacts/KpiCard.tsx
import { CitationChip } from "../Artifact";
import type { Source } from "../tools";

const SEVERITY_TINT: Record<string, string> = {
  critical: "#D85F3B",
  high:     "#C06030",
  medium:   "#B8860B",
  low:      "#7A7268",
  info:     "#A89B89",
};

interface KpiCardProps {
  kind:      "kpi_card";
  label:     string;
  value:     string;
  detail?:   string;
  severity?: "critical" | "high" | "medium" | "low" | "info";
  tags?:     string[];
  source?:   Source;
}

export function KpiCard({ label, value, detail, severity, tags, source }: KpiCardProps) {
  const valueColor = severity ? SEVERITY_TINT[severity] : "#3A342B";

  return (
    <div style={{
      background: "#FFFCF6",
      border: "1px solid #E8DFD0",
      borderRadius: 12,
      padding: 16,
      margin: "8px 0",
      position: "relative",
    }}>
      <div style={{ fontSize: 11, color: "#A89B89", textTransform: "uppercase",
                    letterSpacing: "0.06em", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontFamily: "Georgia, serif", fontSize: 28, color: valueColor,
                    lineHeight: 1.2 }}>
        {value}
      </div>
      {detail && (
        <div style={{ fontSize: 12, color: "#7A7268", marginTop: 4 }}>{detail}</div>
      )}
      {tags && tags.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
          {tags.map(t => (
            <span key={t} style={{
              fontSize: 10, color: "#85613A", background: "#F5E8DB",
              borderRadius: 4, padding: "2px 6px",
            }}>{t}</span>
          ))}
        </div>
      )}
      {source && <CitationChip source={source} />}
    </div>
  );
}
