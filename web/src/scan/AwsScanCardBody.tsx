import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
}

export function AwsScanCardBody({ conn, onScanStarted }: Props) {
  const [tier, setTier] = useState<"quick" | "medium">("quick");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string | null>(null);

  async function startScan() {
    setBusy(true); setErr(null);
    try {
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
      <div className="text-sm text-stone-600">
        Regions are auto-discovered by the scanner — no scope picker for AWS.
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
      <button onClick={startScan} disabled={busy}
        className="px-4 py-1.5 rounded-md bg-orange-600 text-white text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
        {busy ? "Starting…" : "Scan"}
      </button>
    </div>
  );
}
