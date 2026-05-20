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

// Matches ChartDonut's palette so bar charts also render with distinct
// per-bar colors when the caller doesn't supply an explicit color.
const BAR_COLORS = [
  "#D85F3B", // persimmon
  "#4A90C4", // slate blue
  "#7BAF5C", // sage green
  "#C4956A", // warm tan
  "#9B6BB5", // muted violet
  "#E8A83C", // amber
  "#5BAAA8", // teal
  "#C46A6A", // dusty rose
];

export function ChartBar({ title, x_label, y_label, series, source }: ChartBarProps) {
  const data = series.map((s, i) => ({
    name:  s.label,
    value: s.value,
    color: s.color ?? BAR_COLORS[i % BAR_COLORS.length],
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

      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -16, bottom: x_label ? 20 : 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E8DFD0" />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 11, fill: "#7A7268" }}
            axisLine={{ stroke: "#E8DFD0" }}
            tickLine={false}
            {...(x_label ? { label: { value: x_label, position: "insideBottom", offset: -4, fontSize: 11, fill: "#A89B89" } } : {})}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "#7A7268" }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
            {...(y_label ? { label: { value: y_label, angle: -90, position: "insideLeft", fontSize: 11, fill: "#A89B89" } } : {})}
          />
          <RTooltip
            contentStyle={{ borderRadius: 8, fontSize: 12,
                            background: "#FFFCF6", border: "1px solid #E8DFD0" }}
          />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {source && <CitationChip source={source} />}
    </div>
  );
}
