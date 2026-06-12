// web/src/routes/AISummary.tsx
//
// AI Exposure dashboard — top-level /ai route.
// Three rows + a per-person table, sourced from GET /ai/summary.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AISummaryResponse, type AIStatusCounts } from "../lib/api";
import { ExportAIBOMButton } from "../components/ExportAIBOMButton";

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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">AI Exposure</h1>
        <ExportAIBOMButton />
      </div>

      {/* Headline: single AI Exposure Score */}
      <ExposureScore score={data.score} byFramework={data.by_framework} />

      {/* Score tiles — supporting detail */}
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

// ---------- AI Exposure Score ----------

const FAIL_WEIGHT    = 3;
const PARTIAL_WEIGHT = 1;
const PASS_WEIGHT    = 1;

export function computeExposureScore(score: AIStatusCounts): number | null {
  const weightedFail = score.fail * FAIL_WEIGHT + score.partial * PARTIAL_WEIGHT;
  const total        = weightedFail + score.pass * PASS_WEIGHT;
  if (total === 0) return null;
  const raw = (1 - weightedFail / total) * 100;
  return Math.max(0, Math.min(100, Math.round(raw)));
}

type VerdictBand = {
  label:  string;
  tone:   "red" | "amber" | "lime" | "green";
  ringHex: string;
  textCls: string;
};

export function verdictForScore(score: number): VerdictBand {
  if (score >= 90) return { label: "Strong AI hygiene",  tone: "green", ringHex: "#16a34a", textCls: "text-green-700" };
  if (score >= 70) return { label: "Looking healthy",    tone: "lime",  ringHex: "#65a30d", textCls: "text-lime-700" };
  if (score >= 50) return { label: "Needs attention",    tone: "amber", ringHex: "#d97706", textCls: "text-amber-700" };
  return             { label: "Critical exposure",       tone: "red",   ringHex: "#dc2626", textCls: "text-red-700" };
}

function ExposureScore({ score, byFramework }: {
  score: AIStatusCounts;
  byFramework: AISummaryResponse["by_framework"];
}) {
  const computed = computeExposureScore(score);
  const frameworksWithFails = Object.values(byFramework)
    .filter((c) => c.fail > 0).length;
  const totalFailures = score.fail + score.partial;

  if (computed === null) {
    return (
      <section className="rounded-2xl border border-slate-200 bg-gradient-to-br from-slate-50 to-white p-6 flex items-center gap-6">
        <ScoreRing score={null} ringHex="#cbd5e1" />
        <div>
          <div className="text-sm uppercase tracking-wider text-slate-500">AI Exposure Score</div>
          <div className="text-xl font-semibold text-slate-700 mt-1">No data yet</div>
          <div className="text-sm text-slate-500 mt-1">
            Connect a source from <Link to="/connect" className="text-blue-700 underline">Connect</Link> to compute your score.
          </div>
        </div>
      </section>
    );
  }

  const verdict = verdictForScore(computed);
  return (
    <section
      className="rounded-2xl border border-slate-200 bg-gradient-to-br from-slate-50 to-white p-6 flex items-center gap-6"
      aria-label="AI Exposure Score"
    >
      <ScoreRing score={computed} ringHex={verdict.ringHex} />
      <div className="flex-1">
        <div className="text-sm uppercase tracking-wider text-slate-500">AI Exposure Score</div>
        <div className={`text-2xl font-bold mt-1 ${verdict.textCls}`}>{verdict.label}</div>
        <div className="text-sm text-slate-600 mt-2">
          <span className="font-semibold text-slate-800">{totalFailures}</span> unresolved {totalFailures === 1 ? "control" : "controls"} across{" "}
          <span className="font-semibold text-slate-800">{frameworksWithFails}</span>{" "}
          {frameworksWithFails === 1 ? "framework" : "frameworks"}.
        </div>
        <div className="text-xs text-slate-400 mt-1" title="Higher is better. Fails weigh 3×, partials 1×.">
          0–49 critical · 50–69 needs attention · 70–89 healthy · 90–100 strong
        </div>
      </div>
    </section>
  );
}

function ScoreRing({ score, ringHex }: { score: number | null; ringHex: string }) {
  const size   = 120;
  const stroke = 12;
  const radius = (size - stroke) / 2;
  const circ   = 2 * Math.PI * radius;
  const pct    = score ?? 0;
  const dash   = (pct / 100) * circ;

  return (
    <svg width={size} height={size} className="shrink-0" role="img" aria-label={score === null ? "no score" : `score ${score} of 100`}>
      <circle cx={size / 2} cy={size / 2} r={radius}
              fill="none" stroke="#e2e8f0" strokeWidth={stroke} />
      {score !== null && (
        <circle cx={size / 2} cy={size / 2} r={radius}
                fill="none" stroke={ringHex} strokeWidth={stroke}
                strokeDasharray={`${dash} ${circ - dash}`}
                strokeLinecap="round"
                transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      )}
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
            className="fill-slate-900" style={{ fontSize: 32, fontWeight: 700 }}>
        {score === null ? "—" : score}
      </text>
    </svg>
  );
}

// ---------- ----------

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
