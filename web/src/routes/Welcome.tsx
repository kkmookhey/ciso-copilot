import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { api, type Connection } from "../lib/api";

export function Welcome() {
  const [conns, setConns] = useState<Connection[] | null>(null);

  useEffect(() => {
    api.listConnections().then((r) => setConns(r.connections)).catch(() => setConns([]));
  }, []);

  return (
    <div className="max-w-4xl">
      <h1 className="text-3xl font-bold tracking-tight">Welcome</h1>
      <p className="text-slate-600 mt-1">
        Connect a cloud account to start seeing live posture and real-time alerts.
      </p>

      <div className="mt-10 grid grid-cols-3 gap-6">
        <Stat label="Connected clouds" value={conns?.length ?? "—"} />
        <Stat label="Open findings"    value="—" />
        <Stat label="Critical alerts"  value="—" />
      </div>

      <div className="mt-10 p-6 rounded-2xl border border-slate-200 bg-white">
        <h2 className="font-semibold text-lg">Your cloud connections</h2>
        {conns === null ? (
          <p className="text-slate-500 mt-3 text-sm">Loading…</p>
        ) : conns.length === 0 ? (
          <div className="mt-4">
            <p className="text-slate-600">Nothing connected yet.</p>
            <Link to="/connect" className="inline-block mt-3 text-blue-600 hover:underline">
              Connect your first cloud →
            </Link>
          </div>
        ) : (
          <ul className="mt-4 divide-y divide-slate-100">
            {conns.map((c) => (
              <li key={c.conn_id} className="py-3 flex items-center justify-between text-sm">
                <div>
                  <div className="font-medium">{c.display_name}</div>
                  <div className="text-slate-500 text-xs">
                    {c.cloud_type.toUpperCase()} · {c.account_identifier ?? "—"}
                  </div>
                </div>
                <StatusPill status={c.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-2xl bg-white border border-slate-200 p-5">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className="text-3xl font-bold mt-1">{value}</div>
    </div>
  );
}

function StatusPill({ status }: { status: Connection["status"] }) {
  const styles = {
    active:  "bg-green-50 text-green-700",
    pending: "bg-amber-50 text-amber-700",
    error:   "bg-red-50 text-red-700",
    revoked: "bg-slate-100 text-slate-600",
  }[status];
  return <span className={`px-2 py-1 rounded-full text-xs font-medium ${styles}`}>{status}</span>;
}
