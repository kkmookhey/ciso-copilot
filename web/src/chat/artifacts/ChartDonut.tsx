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

const FALLBACK_COLORS = [
  "#D85F3B", "#B8502F", "#85613A", "#A89B89", "#7A7268",
  "#E8A87C", "#C4956A", "#F5E8DB", "#3A342B", "#5A4A3A",
];

export function ChartDonut({ title, segments, source }: ChartDonutProps) {
  const data = segments.map(s => ({ name: s.label, value: s.value, color: s.color }));

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
              <Cell
                key={i}
                fill={entry.color ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length]}
              />
            ))}
          </Pie>
          <RTooltip
            contentStyle={{ borderRadius: 8, fontSize: 12,
                            background: "#FFFCF6", border: "1px solid #E8DFD0" }}
          />
        </PieChart>
      </ResponsiveContainer>

      {/* Legend */}
      <ul style={{ listStyle: "none", margin: "8px 0 0", padding: 0,
                   display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
        {data.map((s, i) => (
          <li key={s.name} style={{ display: "flex", alignItems: "center",
                                     gap: 5, fontSize: 11, color: "#7A7268" }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
              background: s.color ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length],
            }} />
            <span>{s.name}</span>
            <span style={{ color: "#A89B89" }}>{s.value}</span>
          </li>
        ))}
      </ul>

      {source && <CitationChip source={source} />}
    </div>
  );
}
