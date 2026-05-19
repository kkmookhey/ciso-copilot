import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, type GitHubRepo } from "../lib/api";

export function RepoPicker() {
  const { id } = useParams<{ id: string }>();
  const [repos,    setRepos]    = useState<GitHubRepo[] | null>(null);
  const [page,     setPage]     = useState(1);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [total,    setTotal]    = useState<number | null>(null);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setRepos(null); setError(null);
    api.listAuthorizedRepos(id, page)
       .then((r) => { setRepos(r.repos); setNextPage(r.next_page); setTotal(r.total_count); })
       .catch((e: Error) => setError(e.message));
  }, [id, page]);

  if (!id) return <div>Missing connection id.</div>;

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
                  <button disabled
                          title="Scanning will be enabled in the next release."
                          className="px-3 py-1.5 rounded-md bg-slate-100 text-slate-400 text-xs cursor-not-allowed">
                    Scan
                  </button>
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

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}
