import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
  onChanged: () => void;
}

export function AzureScanCardBody({ conn, onScanStarted, onChanged }: Props) {
  const allSubs = conn.scope?.subscriptions ?? [];
  const names   = conn.scope?.subscription_names ?? {};
  const initial = new Set<string>(conn.scope?.selected ?? allSubs);
  const [selected, setSelected] = useState<Set<string>>(initial);
  const [tier, setTier]         = useState<"quick" | "medium">("quick");
  const [busy, setBusy]         = useState(false);
  const [err, setErr]           = useState<string | null>(null);

  function toggle(sub: string) {
    const next = new Set(selected);
    if (next.has(sub)) { next.delete(sub); } else { next.add(sub); }
    setSelected(next);
  }

  const changed = !setsEqual(selected, new Set(conn.scope?.selected ?? allSubs));

  async function startScan() {
    if (selected.size === 0) { setErr("Select at least one subscription"); return; }
    setBusy(true); setErr(null);
    try {
      if (changed) {
        await api.updateConnectionSubscriptions(conn.conn_id, [...selected]);
        onChanged();
      }
      const { scan_id } = await api.rescanConnection(conn.conn_id, tier);
      onScanStarted(scan_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="text-sm font-medium text-stone-700 mb-1">
          Subscriptions ({selected.size} of {allSubs.length})
        </div>
        <div className="max-h-48 overflow-auto border rounded p-2 space-y-1 text-sm">
          {allSubs.map(sub => (
            <label key={sub} className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={selected.has(sub)}
                     onChange={() => toggle(sub)} />
              <span className="text-stone-700">{names[sub] ?? sub}</span>
              {names[sub] && <span className="text-xs text-stone-400">{sub}</span>}
            </label>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-stone-700">Tier</label>
        <select value={tier} onChange={e => setTier(e.target.value as "quick" | "medium")}
                className="px-2 py-1 border rounded">
          <option value="quick">Quick</option>
          <option value="medium">Medium</option>
        </select>
        <Link to="/contact/deep-scan" className="text-xs text-stone-500 underline">
          Deep? Contact us
        </Link>
      </div>
      {err && <div className="text-sm text-red-700">{err}</div>}
      <button onClick={startScan} disabled={busy || selected.size === 0}
        className="px-4 py-1.5 rounded-md bg-orange-600 text-white text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
        {busy ? "Starting…" : "Scan"}
      </button>
    </div>
  );
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}
