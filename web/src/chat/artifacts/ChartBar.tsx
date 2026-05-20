// web/src/chat/artifacts/ChartBar.tsx
import { CitationChip } from "../Artifact";
import type { Source } from "../tools";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  Cell, ResponsiveContainer,
} from "recharts";

interface ChartBarProps {
  kind:     "chart_bar";
  title:    string;
  x_label?: string;
  y_label?: string;
  series:   Array<{ label: string; value: number; color?: string }>;
  source?:  Source;
}

const DEFAULT_BAR_COLOR = "#D85F3B";

export function ChartBar({ title, series, source }: ChartBarProps) {
  const data = series.map(s => ({ name: s.label, value: s.value, color: s.color }));

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

      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E8DFD0" />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 11, fill: "#7A7268" }}
            axisLine={{ stroke: "#E8DFD0" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "#7A7268" }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <RTooltip
            contentStyle={{ borderRadius: 8, fontSize: 12,
                            background: "#FFFCF6", border: "1px solid #E8DFD0" }}
          />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((entry, i) => (
              <Cell
                key={i}
                fill={entry.color ?? DEFAULT_BAR_COLOR}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {source && <CitationChip source={source} />}
    </div>
  );
}
