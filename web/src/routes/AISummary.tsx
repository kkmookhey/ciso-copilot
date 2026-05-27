// web/src/routes/AISummary.tsx
//
// AI Exposure dashboard — top-level /ai route.
// Three rows + a per-person table, sourced from GET /ai/summary.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AISummaryResponse, type AIStatusCounts } from "../lib/api";

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
        <ScoreTile label="Fail"    value={data.score.fail}    tone="red"   to="/findings?status=fail"    />
        <ScoreTile label="Partial" value={data.score.partial} tone="amber" to="/findings?status=partial" />
        <ScoreTile label="Pass"    value={data.score.pass}    tone="green" to="/findings?status=pass"    />
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
              to={`/findings?cloud=${s}`}
            />
          ))}
        </div>
      </section>

      {/* By framework — grouped by family (CME-v2 S4) */}
      <section>
        <h2 className="text-lg font-medium mb-2">By framework</h2>
        {(["ai"] as const).map((family) => {
          const keysInFamily = Object.entries(data.frameworks_meta)
            .filter(([, m]) => m.family === family)
            .map(([k]) => k);
          if (keysInFamily.length === 0) return null;
          return (
            <div key={family} className="mb-4">
              <h3 className="text-sm font-medium text-slate-600 mb-2 uppercase tracking-wide">
                {family} frameworks
              </h3>
              <div className="grid grid-cols-4 gap-3">
                {keysInFamily.map((fw) => (
                  <FrameworkTile
                    key={fw}
                    fwKey={fw}
                    label={data.frameworks_meta[fw]?.name ?? fw}
                    counts={data.by_framework[fw as keyof AISummaryResponse["by_framework"]]}
                    sourceUrl={data.frameworks_meta[fw]?.source_url}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </section>

      {/* Top people */}
      <section>
        <h2 className="text-lg font-medium mb-2">Top AI users</h2>
        {data.top_people.length === 0 ? (
          <p className="text-sm text-slate-500">
            No identifiable AI users yet. Connect Entra (see <Link to="/connect" className="text-blue-600 hover:underline">Connect</Link> for any licensing notes) to populate this.
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

function ScoreTile({ label, value, tone, to }: {
  label: string; value: number; tone: "red" | "amber" | "green"; to: string;
}) {
  const colour = tone === "red"   ? "bg-red-50 text-red-800 hover:bg-red-100"
              : tone === "amber" ? "bg-amber-50 text-amber-800 hover:bg-amber-100"
              :                    "bg-green-50 text-green-800 hover:bg-green-100";
  return (
    <Link to={to} className={`rounded-lg p-4 transition-colors ${colour}`}>
      <div className="text-3xl font-bold">{value}</div>
      <div className="text-sm uppercase tracking-wide">{label}</div>
    </Link>
  );
}

function SourceTile({ label, value, to, note }: {
  label: string; value: number; to: string; note?: string;
}) {
  return (
    <Link to={to} className="rounded-lg border p-3 block hover:bg-slate-50 transition-colors">
      <div className="text-xl font-semibold">{value}</div>
      <div className="text-sm">{label}</div>
      {note && <div className="text-xs text-slate-500">{note}</div>}
    </Link>
  );
}

function FrameworkTile({ fwKey, label, counts, sourceUrl }: {
  fwKey:      string;
  label:      string;
  counts:     AIStatusCounts;
  sourceUrl?: string;
}) {
  return (
    <div
      className="rounded-lg border p-3 hover:bg-slate-50"
      title="Mapping only — not a compliance attestation. Verify with your auditor."
    >
      <div className="text-sm font-medium mb-1 flex items-center justify-between gap-2">
        <Link
          to={`/findings?framework=${encodeURIComponent(fwKey)}`}
          className="hover:underline flex-1 truncate"
        >
          {label}
        </Link>
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-slate-400 hover:text-slate-600 text-xs"
            title="Open source document"
            onClick={(e) => e.stopPropagation()}
          >
            ↗
          </a>
        )}
      </div>
      <div className="flex gap-2 text-xs">
        <span className="text-red-700">F: {counts.fail}</span>
        <span className="text-amber-700">P: {counts.partial}</span>
        <span className="text-green-700">Pass: {counts.pass}</span>
      </div>
    </div>
  );
}
