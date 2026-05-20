// web/src/chat/Artifact.tsx
import type { ArtifactHint, Source } from "./tools";
import { KpiCard } from "./artifacts/KpiCard";
import { EntityList } from "./artifacts/EntityList";
import { FindingCard } from "./artifacts/FindingCard";
import { RiskCard } from "./artifacts/RiskCard";
import { ChartBar } from "./artifacts/ChartBar";
import { ChartDonut } from "./artifacts/ChartDonut";
import { SeverityBreakdown } from "./artifacts/SeverityBreakdown";
import { ApprovalCard } from "./artifacts/ApprovalCard";

export function CitationChip({ source }: { source: Source }) {
  return (
    <button
      onClick={() => window.dispatchEvent(
        new CustomEvent("open-source-sheet", { detail: source }))}
      style={{ position: "absolute", bottom: 8, right: 8, fontSize: 11,
               color: "#85613A", background: "#F5E8DB", border: "none",
               borderRadius: 6, padding: "2px 6px", cursor: "pointer" }}>
      ↗ source
    </button>
  );
}

export function Artifact({
  hint,
  conversationId,
  messageId,
}: {
  hint: ArtifactHint;
  conversationId?: string;
  messageId?: string;
}) {
  switch (hint.kind) {
    case "kpi_card":           return <KpiCard {...hint} />;
    case "entity_list":        return <EntityList {...hint} />;
    case "finding_card":       return <FindingCard {...hint} />;
    case "risk_card":          return <RiskCard {...hint} />;
    case "chart_bar":          return <ChartBar {...hint} />;
    case "chart_donut":        return <ChartDonut {...hint} />;
    case "severity_breakdown": return <SeverityBreakdown {...hint} />;
    case "approval_card":
      return (
        <ApprovalCard
          {...hint}
          conversationId={conversationId}
          messageId={messageId}
        />
      );
    default:                   return null;
  }
}
