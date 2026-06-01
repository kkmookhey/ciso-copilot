import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, type Risk } from "../lib/api";

export function RisksDetail() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [risk, setRisk] = useState<Risk | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) {
      setError("No finding ID provided");
      setLoading(false);
      return;
    }

    // Load all risks and find the one matching the ID
    api.listRisks({ status: "all" })
      .then((r) => {
        const found = r.risks.find((risk) => risk.risk_id === id || risk.finding_id === id);
        if (found) {
          setRisk(found);
        } else {
          setError("Risk not found");
        }
        setLoading(false);
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "Failed to load risk");
        setLoading(false);
      });
  }, [id]);

  if (loading) {
    return <div className="p-8 text-neutral-500">Loading…</div>;
  }

  if (error || !risk) {
    return (
      <div className="max-w-2xl">
        <button
          onClick={() => nav("/risks")}
          className="mb-4 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm transition"
        >
          ← Back to risks
        </button>
        <div className="p-4 rounded-lg bg-red-50 text-red-700 text-sm">
          {error || "Risk not found"}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl">
      <button
        onClick={() => nav("/risks")}
        className="mb-4 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm transition"
      >
        ← Back to risks
      </button>

      <div className="rounded-2xl border border-slate-200 bg-white p-6">
        <div className="flex items-start justify-between mb-4">
          <h1 className="text-2xl font-bold tracking-tight">{risk.title}</h1>
          <SeverityPill sev={risk.severity} />
        </div>

        {risk.description && (
          <p className="text-slate-600 mb-6">{risk.description}</p>
        )}

        <div className="space-y-4">
          {risk.finding_id && (
            <div>
              <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Finding ID</h3>
              <code className="text-sm font-mono text-slate-700 bg-slate-50 px-3 py-2 rounded-lg block">
                {risk.finding_id}
              </code>
            </div>
          )}

          <div>
            <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Status</h3>
            <p className="text-sm text-slate-700 capitalize">{risk.status}</p>
          </div>

          {risk.owner && (
            <div>
              <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Owner</h3>
              <p className="text-sm text-slate-700">{risk.owner}</p>
            </div>
          )}

          {risk.due_date && (
            <div>
              <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Due Date</h3>
              <p className="text-sm text-slate-700">{risk.due_date}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SeverityPill({ sev }: { sev: Risk["severity"] }) {
  const cls = {
    critical: "bg-red-100 text-red-700",
    high: "bg-amber-100 text-amber-700",
    medium: "bg-yellow-100 text-yellow-700",
    low: "bg-slate-100 text-slate-700",
    info: "bg-slate-100 text-slate-500",
  }[sev];
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold uppercase ${cls}`}>
      {sev}
    </span>
  );
}
