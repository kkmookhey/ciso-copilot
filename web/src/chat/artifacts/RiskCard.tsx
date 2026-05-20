// web/src/chat/artifacts/RiskCard.tsx
import { CitationChip } from "../Artifact";
import type { Source } from "../tools";

const SEVERITY_PILL: Record<string, { bg: string; color: string }> = {
  critical: { bg: "#FDECEA", color: "#B91C1C" },
  high:     { bg: "#FEF3E2", color: "#C2410C" },
  medium:   { bg: "#FEFCE8", color: "#854D0E" },
  low:      { bg: "#F5F0E6", color: "#7A7268" },
  info:     { bg: "#F0EDE8", color: "#A89B89" },
};

const STATUS_BADGE: Record<string, { bg: string; color: string; label: string }> = {
  open:        { bg: "#FDECEA", color: "#B91C1C",  label: "Open" },
  mitigated:   { bg: "#ECFDF5", color: "#065F46",  label: "Mitigated" },
  accepted:    { bg: "#EFF6FF", color: "#1D4ED8",  label: "Accepted" },
  transferred: { bg: "#F5F3FF", color: "#6D28D9",  label: "Transferred" },
  closed:      { bg: "#F5F0E6", color: "#7A7268",  label: "Closed" },
};

interface RiskCardProps {
  kind:      "risk_card";
  risk_id:   string;
  title:     string;
  severity:  "critical" | "high" | "medium" | "low" | "info";
  status:    "open" | "mitigated" | "accepted" | "transferred" | "closed";
  owner?:    string;
  due_date?: string;
  source?:   Source;
}

export function RiskCard({ title, severity, status, owner, due_date, source }: RiskCardProps) {
  const sev = SEVERITY_PILL[severity] ?? SEVERITY_PILL.info;
  const sts = STATUS_BADGE[status] ?? STATUS_BADGE.closed;

  return (
    <div style={{
      background: "#FFFCF6",
      border: "1px solid #E8DFD0",
      borderRadius: 12,
      padding: 16,
      margin: "8px 0",
      position: "relative",
    }}>
      <div style={{ fontFamily: "Georgia, serif", fontSize: 15,
                    color: "#3A342B", lineHeight: 1.4, marginBottom: 10 }}>
        {title}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
        <span style={{
          fontSize: 11, fontWeight: 600, padding: "2px 8px",
          borderRadius: 99, background: sev.bg, color: sev.color,
          textTransform: "capitalize",
        }}>{severity}</span>
        <span style={{
          fontSize: 11, fontWeight: 600, padding: "2px 8px",
          borderRadius: 99, background: sts.bg, color: sts.color,
        }}>{sts.label}</span>
      </div>

      {(owner || due_date) && (
        <div style={{ display: "flex", gap: 16, fontSize: 12, color: "#7A7268" }}>
          {owner && (
            <span>Owner: <span style={{ color: "#3A342B" }}>{owner}</span></span>
          )}
          {due_date && (
            <span>Due: <span style={{ color: "#3A342B" }}>{due_date}</span></span>
          )}
        </div>
      )}

      {source && <CitationChip source={source} />}
    </div>
  );
}
