import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, type Risk, type Finding } from "../lib/api";

export function RisksDetail() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [risk, setRisk] = useState<Risk | null>(null);
  const [finding, setFinding] = useState<Finding | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) {
      setError("No ID provided");
      setLoading(false);
      return;
    }

    let cancelled = false;

    (async () => {
      try {
        // 1. Try risks first — they're the analyst-curated layer.
        const risksResp = await api.listRisks({ status: "all" });
        const matched = risksResp.risks.find(
          (r) => r.risk_id === id || r.finding_id === id,
        );
        if (matched) {
          if (!cancelled) {
            setRisk(matched);
            setLoading(false);
          }
          return;
        }

        // 2. Fall back to findings — Slack-card "View details" buttons link
        // to /risks/{finding_id}, and a finding that hasn't been promoted to
        // a risk yet won't match above. Scan the first page of findings;
        // good enough for the autonomous-broadcast critical-finding case.
        const findingsResp = await api.listFindings({ limit: 200 });
        const matchedFinding = findingsResp.findings.find(
          (f) => f.finding_id === id,
        );
        if (matchedFinding) {
          if (!cancelled) {
            setFinding(matchedFinding);
            setLoading(false);
          }
          return;
        }

        if (!cancelled) {
          setError("Not found in risks or findings");
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load");
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [id]);

  if (loading) {
    return <div className="p-8 text-neutral-500">Loading…</div>;
  }

  if (error || (!risk && !finding)) {
    return (
      <div className="max-w-2xl">
        <button
          onClick={() => nav("/risks")}
          className="mb-4 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm transition"
        >
          ← Back to risks
        </button>
        <div className="p-4 rounded-lg bg-red-50 text-red-700 text-sm">
          {error || "Not found"}
        </div>
      </div>
    );
  }

  if (finding && !risk) {
    return (
      <div className="max-w-2xl">
        <button
          onClick={() => nav("/findings")}
          className="mb-4 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm transition"
        >
          ← Back to findings
        </button>
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <div className="flex items-start justify-between mb-4">
            <h1 className="text-2xl font-bold tracking-tight">{finding.title}</h1>
            <SeverityPill sev={finding.severity} />
          </div>
          {finding.description && (
            <p className="text-slate-600 mb-6">{finding.description}</p>
          )}
          <div className="space-y-4">
            <div>
              <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Finding ID</h3>
              <code className="text-sm font-mono text-slate-700 bg-slate-50 px-3 py-2 rounded-lg block">
                {finding.finding_id}
              </code>
            </div>
            <div>
              <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Status</h3>
              <p className="text-sm text-slate-700 capitalize">{finding.status}</p>
            </div>
            {finding.resource_arn && (
              <div>
                <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Resource</h3>
                <code className="text-sm font-mono text-slate-700 bg-slate-50 px-3 py-2 rounded-lg block">
                  {finding.resource_arn}
                </code>
              </div>
            )}
            {finding.check_id && (
              <div>
                <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Check</h3>
                <p className="text-sm text-slate-700">{finding.check_id}</p>
              </div>
            )}
            {finding.domain && (
              <div>
                <h3 className="text-xs uppercase text-slate-500 font-semibold mb-1">Scanner</h3>
                <p className="text-sm text-slate-700 capitalize">{finding.domain}</p>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (!risk) return null;

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
