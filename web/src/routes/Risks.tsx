import { useEffect, useState } from "react";
import { api, type Risk } from "../lib/api";

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"] as const;
const STATUS_OPTIONS  = ["open", "mitigated", "accepted", "transferred", "closed"] as const;

type StatusFilter = (typeof STATUS_OPTIONS)[number] | "all";

export function Risks() {
  const [risks,  setRisks]  = useState<Risk[] | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("open");
  const [showNew, setShowNew] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function reload() {
    setRisks(null);
    setErr(null);
    try {
      const r = await api.listRisks(filter === "all" ? {} : { status: filter });
      setRisks(r.risks);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setRisks([]);
    }
  }

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [filter]);

  async function updateStatus(riskId: string, status: string) {
    try {
      await api.updateRisk(riskId, { status });
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="max-w-6xl">
      <div className="flex items-baseline justify-between">
        <h1 className="text-3xl font-bold tracking-tight">Risk register</h1>
        <button
          onClick={() => setShowNew(true)}
          className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition"
        >
          + New risk
        </button>
      </div>
      <p className="text-slate-600 mt-1">
        Track open risks with owner, due date, and disposition. Add directly or convert from a finding.
      </p>

      <div className="mt-6 flex flex-wrap gap-2">
        {(["open", "mitigated", "accepted", "transferred", "closed", "all"] as StatusFilter[]).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1.5 rounded-full text-sm transition ${
              filter === s ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      {err && (
        <div className="mt-4 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{err}</div>
      )}

      <div className="mt-6 rounded-2xl border border-slate-200 bg-white overflow-hidden">
        {risks === null ? (
          <p className="text-slate-500 p-6 text-sm">Loading…</p>
        ) : risks.length === 0 ? (
          <div className="p-6">
            <p className="text-slate-500 text-sm">No risks with status “{filter}”.</p>
            {filter === "open" && (
              <p className="text-slate-400 text-xs mt-2">Click “+ New risk” above or open a finding and convert it.</p>
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="text-left py-3 px-4">Risk</th>
                <th className="text-left py-3 px-4">Sev</th>
                <th className="text-left py-3 px-4">Owner</th>
                <th className="text-left py-3 px-4">Due</th>
                <th className="text-left py-3 px-4">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {risks.sort(bySeverity).map((r) => (
                <tr key={r.risk_id} className="hover:bg-slate-50">
                  <td className="py-3 px-4">
                    <div className="font-medium">{r.title}</div>
                    {r.description && (
                      <div className="text-xs text-slate-500 mt-0.5 line-clamp-2">{r.description}</div>
                    )}
                    {r.finding_id && (
                      <div className="text-xs text-slate-400 mt-0.5 font-mono">↳ finding {r.finding_id.slice(0, 8)}…</div>
                    )}
                  </td>
                  <td className="py-3 px-4">
                    <SeverityPill sev={r.severity} />
                  </td>
                  <td className="py-3 px-4 text-slate-600">{r.owner ?? "—"}</td>
                  <td className="py-3 px-4 text-slate-600 text-xs">{r.due_date ?? "—"}</td>
                  <td className="py-3 px-4">
                    <select
                      value={r.status}
                      onChange={(e) => updateStatus(r.risk_id, e.target.value)}
                      className="text-xs px-2 py-1 rounded-md border border-slate-300 bg-white"
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showNew && (
        <NewRiskModal onClose={() => setShowNew(false)} onCreated={reload} />
      )}
    </div>
  );
}

function bySeverity(a: Risk, b: Risk): number {
  return SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity);
}

function SeverityPill({ sev }: { sev: Risk["severity"] }) {
  const cls = {
    critical: "bg-red-100 text-red-700",
    high:     "bg-amber-100 text-amber-700",
    medium:   "bg-yellow-100 text-yellow-700",
    low:      "bg-slate-100 text-slate-700",
    info:     "bg-slate-100 text-slate-500",
  }[sev];
  return <span className={`px-2 py-0.5 rounded-full text-xs font-semibold uppercase ${cls}`}>{sev}</span>;
}

function NewRiskModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [title,    setTitle]    = useState("");
  const [severity, setSeverity] = useState<Risk["severity"]>("medium");
  const [owner,    setOwner]    = useState("");
  const [dueDate,  setDueDate]  = useState("");
  const [desc,     setDesc]     = useState("");
  const [busy,     setBusy]     = useState(false);
  const [err,      setErr]      = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api.createRisk({
        title:       title.trim(),
        severity,
        description: desc.trim() || undefined,
        owner:       owner.trim() || undefined,
        due_date:    dueDate || undefined,
      });
      onCreated();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <form onSubmit={submit} className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
        <h2 className="text-xl font-bold">New risk</h2>
        <div className="mt-4 space-y-3 text-sm">
          <label className="block">
            <span className="text-slate-600">Title</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              autoFocus
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Severity</span>
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Risk["severity"])}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            >
              {SEVERITY_ORDER.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-slate-600">Owner (email)</span>
            <input
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              placeholder="owner@company.com"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Due date</span>
            <input
              type="date"
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Description</span>
            <textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
        </div>
        {err && <p className="mt-3 text-red-600 text-xs">{err}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-sm">
            Cancel
          </button>
          <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-sm font-medium">
            {busy ? "Creating…" : "Create risk"}
          </button>
        </div>
      </form>
    </div>
  );
}
