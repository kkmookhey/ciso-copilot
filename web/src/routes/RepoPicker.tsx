import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, type GitHubRepo, type AIScanSummary } from "../lib/api";

type ScanState =
  | { status: "idle" }
  | { status: "starting" }
  | { status: "polling"; scan_id: string; snapshot: AIScanSummary | null }
  | { status: "done";    scan: AIScanSummary }
  | { status: "error";   message: string };

export function RepoPicker() {
  const { id } = useParams<{ id: string }>();
  const [repos,    setRepos]    = useState<GitHubRepo[] | null>(null);
  const [page,     setPage]     = useState(1);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [total,    setTotal]    = useState<number | null>(null);
  const [error,    setError]    = useState<string | null>(null);
  const [scans,    setScans]    = useState<Record<string, ScanState>>({});

  useEffect(() => {
    if (!id) return;
    setRepos(null); setError(null);
    api.listAuthorizedRepos(id, page)
       .then((r) => { setRepos(r.repos); setNextPage(r.next_page); setTotal(r.total_count); })
       .catch((e: Error) => setError(e.message));
  }, [id, page]);

  if (!id) return <div>Missing connection id.</div>;

  async function startScan(repo: GitHubRepo) {
    setScans((s) => ({ ...s, [repo.full_name]: { status: "starting" } }));
    try {
      const { scan_id } = await api.startAIScan(id!, repo.full_name, repo.default_branch ?? undefined);
      setScans((s) => ({ ...s, [repo.full_name]: { status: "polling", scan_id, snapshot: null } }));
      poll(repo.full_name, scan_id);
    } catch (e) {
      setScans((s) => ({ ...s, [repo.full_name]: { status: "error", message: (e as Error).message } }));
    }
  }

  function poll(repoFullName: string, scanId: string) {
    const tick = async () => {
      try {
        const snap = await api.getAIScan(scanId);
        if (snap.status === "queued" || snap.status === "running") {
          setScans((s) => ({ ...s, [repoFullName]: { status: "polling", scan_id: scanId, snapshot: snap } }));
          setTimeout(tick, 3000);
        } else if (snap.status === "success") {
          setScans((s) => ({ ...s, [repoFullName]: { status: "done", scan: snap } }));
        } else {
          setScans((s) => ({ ...s, [repoFullName]: { status: "error", message: snap.error_message ?? "scan failed" } }));
        }
      } catch {
        setTimeout(tick, 5000);
      }
    };
    setTimeout(tick, 1500);
  }

  return (
    <div className="max-w-5xl">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Choose repositories to scan</h1>
          <p className="text-slate-600 mt-1">
            {total !== null ? `${total} authorized.` : "Loading…"}{" "}
            Scanning is per-repo — pick the AI-bearing ones first.
          </p>
        </div>
        <Link to="/connect" className="text-sm text-slate-600 hover:underline">← Back to Connect</Link>
      </div>

      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-50 border border-red-200 text-red-800 text-sm">
          {error}
        </div>
      )}

      <div className="mt-8 rounded-2xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wide">
            <tr>
              <th className="px-4 py-3 text-left">Repository</th>
              <th className="px-4 py-3 text-left">Language</th>
              <th className="px-4 py-3 text-left">Last push</th>
              <th className="px-4 py-3 text-left">Visibility</th>
              <th className="px-4 py-3 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {repos === null && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-slate-500">Loading…</td></tr>
            )}
            {repos?.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-slate-500">
                No repositories authorized. Add some on the GitHub App settings page.
              </td></tr>
            )}
            {repos?.map((r) => (
              <tr key={r.full_name} className="border-t border-slate-100">
                <td className="px-4 py-3">
                  <a className="text-blue-700 hover:underline"
                     href={`https://github.com/${r.full_name}`} target="_blank" rel="noopener noreferrer">
                    {r.full_name}
                  </a>
                </td>
                <td className="px-4 py-3 text-slate-700">{r.primary_language ?? "—"}</td>
                <td className="px-4 py-3 text-slate-700">{formatDate(r.last_pushed_at)}</td>
                <td className="px-4 py-3 text-slate-700">{r.is_private ? "Private" : "Public"}</td>
                <td className="px-4 py-3 text-right">
                  <ScanButton state={scans[r.full_name]} onScan={() => startScan(r)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 flex items-center gap-3 text-sm">
        <button onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
          ← Prev
        </button>
        <span className="text-slate-600">Page {page}</span>
        <button onClick={() => setPage((p) => p + 1)}
                disabled={nextPage === null}
                className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
          Next →
        </button>
      </div>
    </div>
  );
}

function ScanButton({ state, onScan }: { state: ScanState | undefined; onScan: () => void }) {
  if (!state || state.status === "idle") {
    return (
      <button onClick={onScan}
              className="px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-xs">
        Scan
      </button>
    );
  }
  if (state.status === "starting") {
    return <span className="text-xs text-slate-500">Queueing…</span>;
  }
  if (state.status === "polling") {
    const label = state.snapshot?.status === "running" ? "Scanning…" : "Queued…";
    return <span className="text-xs text-slate-500">{label}</span>;
  }
  if (state.status === "done") {
    return (
      <span className="text-xs text-emerald-700">
        ✓ {state.scan.assets_discovered_count} assets, {state.scan.findings_generated_count} findings
      </span>
    );
  }
  return <span className="text-xs text-rose-700" title={state.message}>Failed</span>;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}
