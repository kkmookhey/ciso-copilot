import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";
import { ScanCard } from "../scan/ScanCard";

export default function Scan() {
  const [conns, setConns] = useState<Connection[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  async function reload() {
    try {
      const { connections } = await api.listConnections();
      setConns(connections);
    } catch (e) {
      setErr(String(e));
    }
  }

  useEffect(() => { reload(); }, []);

  const active   = (conns ?? []).filter(c => c.status === "active");
  const pending  = (conns ?? []).filter(c => c.status !== "active");

  async function launchAll() {
    setLaunching(true);
    try {
      await Promise.allSettled(
        active.map(c => api.rescanConnection(c.conn_id, "quick"))
      );
      await reload();
    } finally {
      setLaunching(false);
    }
  }

  if (conns === null && !err) {
    return <div className="p-8 text-stone-500">Loading…</div>;
  }
  if (err) {
    return <div className="p-8 text-red-700">Failed to load connections: {err}</div>;
  }
  if (active.length === 0 && pending.length === 0) {
    return (
      <div className="max-w-xl mx-auto mt-16 text-center">
        <h1 className="text-2xl font-semibold text-stone-800">No clouds connected yet</h1>
        <p className="mt-3 text-stone-600">
          Connect a cloud to start scanning.
        </p>
        <Link to="/connect"
              className="mt-6 inline-block px-5 py-2 rounded-md bg-orange-600 text-white font-medium hover:bg-orange-700">
          Connect a cloud →
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-stone-800">Scan</h1>
        {active.length > 1 && (
          <button onClick={launchAll} disabled={launching}
            className="px-4 py-2 rounded-md bg-orange-600 text-white font-medium hover:bg-orange-700 disabled:opacity-50">
            {launching ? "Launching…" : "Launch all scans"}
          </button>
        )}
      </div>

      <div className="space-y-4">
        {active.map(conn => (
          <ScanCard key={conn.conn_id} conn={conn} onChanged={reload} />
        ))}
      </div>

      {pending.length > 0 && (
        <div className="mt-8 p-4 border border-stone-300 rounded-md bg-stone-50 text-sm text-stone-600">
          <div className="font-medium mb-1">Not ready to scan</div>
          {pending.map(c => (
            <div key={c.conn_id}>
              {c.cloud_type.toUpperCase()} {c.account_identifier ?? "(pending)"} — {c.status}
            </div>
          ))}
          <Link to="/connect" className="mt-2 inline-block text-orange-700 underline">
            Go to Connect →
          </Link>
        </div>
      )}
    </div>
  );
}
