import { useState } from "react";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
}

export function EntraScanCardBody({ conn, onScanStarted }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string | null>(null);

  async function startScan() {
    setBusy(true); setErr(null);
    try {
      const { scan_id } = await api.rescanConnection(conn.conn_id, "quick");
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
        The tenant is the scope — no scope picker, no tier choice.
      </div>
      {err && <div className="text-sm text-red-700">{err}</div>}
      <button onClick={startScan} disabled={busy}
        className="px-4 py-1.5 rounded-md bg-orange-600 text-white text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
        {busy ? "Starting…" : "Scan"}
      </button>
    </div>
  );
}
