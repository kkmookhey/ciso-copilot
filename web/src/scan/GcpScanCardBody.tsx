import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Connection } from "../lib/api";

interface Props {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
  onChanged: () => void;
}

export function GcpScanCardBody({ conn, onScanStarted, onChanged }: Props) {
  const mode = (conn.scope?.mode as string | undefined) ?? "project";
  const [tier, setTier] = useState<"quick" | "medium">("quick");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string | null>(null);

  const projectsObj = (conn.scope?.projects as Record<string, string> | undefined) ?? {};
  const allProjects = Object.keys(projectsObj);
  const initial     = new Set<string>(conn.scope?.selected ?? allProjects);
  const [selected, setSelected] = useState<Set<string>>(initial);

  function toggle(pid: string) {
    const next = new Set(selected);
    if (next.has(pid)) { next.delete(pid); } else { next.add(pid); }
    setSelected(next);
  }
  const changed = mode === "org" && !setsEqual(selected, new Set(conn.scope?.selected ?? allProjects));

  async function startScan() {
    if (mode === "org" && allProjects.length > 0 && selected.size === 0) {
      setErr("Select at least one project");
      return;
    }
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
      {mode === "org" && allProjects.length === 0 && (
        <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
          Projects are discovered on the first scan. Click Scan to enumerate
          and scan everything in the organisation; you can trim the set
          before subsequent scans.
        </div>
      )}
      {mode === "org" && allProjects.length > 0 && (
        <div>
          <div className="text-sm font-medium text-stone-700 mb-1">
            Projects ({selected.size} of {allProjects.length})
          </div>
          {allProjects.length > 10 && (
            <div className="text-xs text-stone-500 mb-1">
              Trim to your prod projects for a faster first scan.
            </div>
          )}
          <div className="max-h-64 overflow-auto border rounded p-2 space-y-1 text-sm">
            {allProjects.map(pid => (
              <label key={pid} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={selected.has(pid)}
                       onChange={() => toggle(pid)} />
                <span className="text-stone-700">{projectsObj[pid] || pid}</span>
                {projectsObj[pid] && projectsObj[pid] !== pid && (
                  <span className="text-xs text-stone-400">{pid}</span>
                )}
              </label>
            ))}
          </div>
        </div>
      )}
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

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}
