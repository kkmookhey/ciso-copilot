import { useEffect, useState } from "react";
import { api, type Finding } from "../lib/api";

export function TopRisks() {
  const [findings, setFindings] = useState<Finding[] | null>(null);

  useEffect(() => {
    api.listFindings({ limit: 50 })
      .then((r) => setFindings(r.findings))
      .catch(() => setFindings([]));
  }, []);

  return (
    <div className="max-w-5xl">
      <h1 className="text-3xl font-bold tracking-tight">Top Risks</h1>
      <p className="text-slate-600 mt-1">Highest-priority open findings across your connected clouds.</p>

      <div className="mt-10">
        {findings === null ? (
          <p className="text-slate-500">Loading…</p>
        ) : findings.length === 0 ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center">
            <p className="text-slate-600">No findings yet.</p>
            <p className="text-sm text-slate-500 mt-2">
              Connect a cloud account, run a scan, and findings will appear here.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {findings.map((f) => <FindingRow key={f.finding_id} f={f} />)}
          </ul>
        )}
      </div>
    </div>
  );
}

function FindingRow({ f }: { f: Finding }) {
  return (
    <li className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <SeverityPill severity={f.severity} />
            <span className="text-xs text-slate-500 font-mono">{f.check_id}</span>
          </div>
          <div className="font-semibold mt-2">{f.title}</div>
          {f.description && (
            <div className="text-sm text-slate-600 mt-1">{f.description}</div>
          )}
          {f.resource_arn && (
            <div className="mt-2 text-xs font-mono text-slate-500 break-all">{f.resource_arn}</div>
          )}
        </div>
      </div>
    </li>
  );
}

function SeverityPill({ severity }: { severity: Finding["severity"] }) {
  const style = {
    critical: "bg-red-100 text-red-700",
    high:     "bg-amber-100 text-amber-700",
    medium:   "bg-yellow-100 text-yellow-700",
    low:      "bg-slate-100 text-slate-700",
    info:     "bg-slate-100 text-slate-500",
  }[severity];
  return <span className={`px-2 py-0.5 rounded-full text-xs font-semibold uppercase ${style}`}>{severity}</span>;
}
