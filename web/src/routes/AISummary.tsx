// web/src/routes/AISummary.tsx
//
// AI Exposure dashboard — top-level /ai route.
// Three rows + a per-person table, sourced from GET /ai/summary.

import { useEffect, useState } from "react";
import { api, type AISummaryResponse, type AIStatusCounts } from "../lib/api";

const FRAMEWORK_LABELS: Record<string, string> = {
  nist_ai_rmf:     "NIST AI RMF",
  iso_42001:       "ISO 42001",
  soc2_ai:         "SOC 2 AI",
  eu_ai_act:       "EU AI Act",
  nist_ai_600_1:   "NIST AI 600-1",
  owasp_llm_top10: "OWASP LLM Top 10",
  owasp_agentic:   "OWASP Agentic",
  mitre_atlas:     "MITRE ATLAS",
};

const SOURCE_LABELS: Record<string, string> = {
  aws:   "AWS",
  azure: "Azure",
  code:  "Code",
  entra: "Entra",
};

export default function AISummary() {
  const [data,  setData]  = useState<AISummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.aiSummary()
       .then((d) => setData(d))
       .catch((e: Error) => setError(e.message || "failed to load"));
  }, []);

  if (error) return <div className="p-6 text-red-700">Failed to load AI summary: {error}</div>;
  if (!data) return <div className="p-6">Loading…</div>;

  return (
    <div className="p-6 space-y-8">
      <h1 className="text-2xl font-semibold">AI Exposure</h1>

      {/* Score tiles */}
      <section className="grid grid-cols-3 gap-4">
        <ScoreTile label="Fail"    value={data.score.fail}    tone="red"   />
        <ScoreTile label="Partial" value={data.score.partial} tone="amber" />
        <ScoreTile label="Pass"    value={data.score.pass}    tone="green" />
      </section>

      {/* By-source */}
      <section>
        <h2 className="text-lg font-medium mb-2">By source</h2>
        <div className="grid grid-cols-4 gap-3">
          {(Object.keys(SOURCE_LABELS) as (keyof AISummaryResponse["by_source"])[]).map((s) => (
            <SourceTile
              key={s}
              label={SOURCE_LABELS[s]}
              value={data.by_source[s]}
              note={s === "entra" ? "coming in S2" : undefined}
            />
          ))}
        </div>
      </section>

      {/* By framework */}
      <section>
        <h2 className="text-lg font-medium mb-2">By framework</h2>
        <div className="grid grid-cols-4 gap-3">
          {Object.keys(FRAMEWORK_LABELS).map((fw) => (
            <FrameworkTile
              key={fw}
              label={FRAMEWORK_LABELS[fw]}
              counts={data.by_framework[fw as keyof AISummaryResponse["by_framework"]]}
            />
          ))}
        </div>
      </section>

      {/* Top people */}
      <section>
        <h2 className="text-lg font-medium mb-2">Top AI users</h2>
        {data.top_people.length === 0 ? (
          <p className="text-sm text-slate-500">
            No identifiable AI users yet — connect Entra (S2) to populate.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b">
                <th className="py-1">Person</th>
                <th className="py-1">Fail</th>
                <th className="py-1">Partial</th>
                <th className="py-1">Sources</th>
              </tr>
            </thead>
            <tbody>
              {data.top_people.map((p) => (
                <tr key={p.email} className="border-b last:border-0">
                  <td className="py-1">{p.email}</td>
                  <td className="py-1">{p.fail}</td>
                  <td className="py-1">{p.partial}</td>
                  <td className="py-1">{p.sources.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function ScoreTile({ label, value, tone }: {
  label: string; value: number; tone: "red" | "amber" | "green";
}) {
  const colour = tone === "red"   ? "bg-red-50 text-red-800"
              : tone === "amber" ? "bg-amber-50 text-amber-800"
              :                    "bg-green-50 text-green-800";
  return (
    <div className={`rounded-lg p-4 ${colour}`}>
      <div className="text-3xl font-bold">{value}</div>
      <div className="text-sm uppercase tracking-wide">{label}</div>
    </div>
  );
}

function SourceTile({ label, value, note }: {
  label: string; value: number; note?: string;
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xl font-semibold">{value}</div>
      <div className="text-sm">{label}</div>
      {note && <div className="text-xs text-slate-500">{note}</div>}
    </div>
  );
}

function FrameworkTile({ label, counts }: { label: string; counts: AIStatusCounts }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-sm font-medium mb-1">{label}</div>
      <div className="flex gap-2 text-xs">
        <span className="text-red-700">F: {counts.fail}</span>
        <span className="text-amber-700">P: {counts.partial}</span>
        <span className="text-green-700">Pass: {counts.pass}</span>
      </div>
    </div>
  );
}
