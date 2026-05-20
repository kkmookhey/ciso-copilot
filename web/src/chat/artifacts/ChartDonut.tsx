// web/src/chat/artifacts/ChartDonut.tsx
import { CitationChip } from "../Artifact";
import type { Source } from "../tools";
import {
  PieChart, Pie, Cell, Tooltip as RTooltip, ResponsiveContainer,
} from "recharts";

interface ChartDonutProps {
  kind:      "chart_donut";
  title:     string;
  segments:  Array<{ label: string; value: number; color?: string }>;
  source?:   Source;
}

// Distinct warm-palette hues — harmonises with Quiet Paper but stays
// visually distinguishable at a glance. Used when a segment has no
// explicit color so any chart_donut benefits automatically.
const SEGMENT_COLORS = [
  "#D85F3B", // persimmon
  "#4A90C4", // slate blue
  "#7BAF5C", // sage green
  "#C4956A", // warm tan
  "#9B6BB5", // muted violet
  "#E8A83C", // amber
  "#5BAAA8", // teal
  "#C46A6A", // dusty rose
];

export function ChartDonut({ title, segments, source }: ChartDonutProps) {
  // Drop zero-value segments — they render as invisible slices and
  // add noise to the legend. Collect zeros separately for a muted footnote.
  const nonZero = segments.filter(s => s.value > 0);
  const zeros   = segments.filter(s => s.value === 0);

  // Resolve a color for each non-zero segment: use explicit color if
  // provided, otherwise cycle through the distinct palette.
  const data = nonZero.map((s, i) => ({
    name:  s.label,
    value: s.value,
    color: s.color ?? SEGMENT_COLORS[i % SEGMENT_COLORS.length],
  }));

  return (
    <div style={{
      background: "#FFFCF6",
      border: "1px solid #E8DFD0",
      borderRadius: 12,
      padding: 16,
      margin: "8px 0",
      position: "relative",
    }}>
      <div style={{ fontSize: 12, color: "#7A7268", fontWeight: 600,
                    marginBottom: 12, textTransform: "uppercase",
                    letterSpacing: "0.05em" }}>
        {title}
      </div>

      <ResponsiveContainer width="100%" height={180}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius={45}
            outerRadius={75}
            paddingAngle={2}
          >
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} />
            ))}
          </Pie>
          <RTooltip
            contentStyle={{ borderRadius: 8, fontSize: 12,
                            background: "#FFFCF6", border: "1px solid #E8DFD0" }}
          />
        </PieChart>
      </ResponsiveContainer>

      {/* Legend — non-zero segments in their distinct colors */}
      <ul style={{ listStyle: "none", margin: "8px 0 0", padding: 0,
                   display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
        {data.map((s) => (
          <li key={s.name} style={{ display: "flex", alignItems: "center",
                                     gap: 5, fontSize: 11, color: "#7A7268" }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
              background: s.color,
            }} />
            <span>{s.name}</span>
            <span style={{ color: "#A89B89" }}>{s.value}</span>
          </li>
        ))}
        {/* Zero-value frameworks: muted, no color dot, clearly "0" */}
        {zeros.map((s) => (
          <li key={s.label} style={{ display: "flex", alignItems: "center",
                                     gap: 5, fontSize: 11, color: "#C4B8AA" }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
              background: "#E8DFD0",
            }} />
            <span>{s.label}</span>
            <span>0</span>
          </li>
        ))}
      </ul>

      {source && <CitationChip source={source} />}
    </div>
  );
}
