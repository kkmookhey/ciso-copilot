// web/src/chat/artifacts/SeverityBreakdown.tsx
import { CitationChip } from "../Artifact";
import type { Source } from "../tools";

interface SeverityBreakdownProps {
  kind:         "severity_breakdown";
  total:        number;
  critical:     number;
  high:         number;
  medium:       number;
  low:          number;
  delta_since?: string;
  source?:      Source;
}

const SEV_CONFIG = [
  { key: "critical" as const, label: "Critical", color: "#D85F3B" },
  { key: "high"     as const, label: "High",     color: "#C47A3B" },
  { key: "medium"   as const, label: "Medium",   color: "#B8930A" },
  { key: "low"      as const, label: "Low",      color: "#A89B89" },
];

export function SeverityBreakdown({
  total, critical, high, medium, low, delta_since, source,
}: SeverityBreakdownProps) {
  const counts = { critical, high, medium, low };

  return (
    <div style={{
      background: "#FFFCF6",
      border: "1px solid #E8DFD0",
      borderRadius: 12,
      padding: 16,
      margin: "8px 0",
      position: "relative",
    }}>
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "baseline",
                    justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: "#7A7268", fontWeight: 600,
                      textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Severity breakdown
        </div>
        <div style={{ fontSize: 13, color: "#3A342B", fontFamily: "Georgia, serif" }}>
          {total} total
          {delta_since && (
            <span style={{ fontSize: 11, color: "#A89B89", marginLeft: 6 }}>
              since {delta_since}
            </span>
          )}
        </div>
      </div>

      {/* Stacked bar */}
      {total > 0 && (
        <div style={{ display: "flex", height: 12, borderRadius: 6,
                      overflow: "hidden", gap: 1, marginBottom: 12 }}>
          {SEV_CONFIG.map(({ key, color }) => {
            const pct = (counts[key] / total) * 100;
            if (pct === 0) return null;
            return (
              <div
                key={key}
                style={{ width: `${pct}%`, background: color, flexShrink: 0 }}
                title={`${key}: ${counts[key]}`}
              />
            );
          })}
        </div>
      )}
      {total === 0 && (
        <div style={{ height: 12, borderRadius: 6, background: "#F0E8DB",
                      marginBottom: 12 }} />
      )}

      {/* Count row */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 20px" }}>
        {SEV_CONFIG.map(({ key, label, color }) => (
          <div key={key} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{
              width: 8, height: 8, borderRadius: 2, background: color, flexShrink: 0,
            }} />
            <span style={{ fontSize: 11, color: "#7A7268" }}>{label}</span>
            <span style={{ fontSize: 13, color: "#3A342B", fontFamily: "Georgia, serif" }}>
              {counts[key]}
            </span>
          </div>
        ))}
      </div>

      {source && <CitationChip source={source} />}
    </div>
  );
}
