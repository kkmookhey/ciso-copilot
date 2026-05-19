import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AIAssetSummary, type AIAssetType } from "../lib/api";

const TYPES: AIAssetType[] = [
  "framework", "model", "mcp_server", "tool", "agent",
  "vector_db", "embedding", "prompt",
];

export function AIInventory() {
  const [sp, setSp] = useSearchParams();
  const typeFilter = (sp.get("type") || "") as AIAssetType | "";
  const pageStr    = sp.get("page") || "1";
  const page       = Math.max(1, parseInt(pageStr, 10) || 1);

  const [assets,   setAssets]   = useState<AIAssetSummary[] | null>(null);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    setAssets(null); setError(null);
    api.listAIAssets({ type: typeFilter || undefined, page })
       .then((r) => { setAssets(r.assets); setNextPage(r.next_page); })
       .catch((e: Error) => setError(e.message));
  }, [typeFilter, page]);

  const groups = groupByRepo(assets ?? []);

  return (
    <div className="max-w-6xl">
      <h1 className="text-3xl font-bold tracking-tight">AI Inventory</h1>
      <p className="text-slate-600 mt-1">
        AI assets discovered by the scanner, grouped by repository.
      </p>

      <div className="mt-6 flex flex-wrap gap-2">
        <FilterChip label="All" active={typeFilter === ""}
                    onClick={() => setSp((s) => { s.delete("type"); s.delete("page"); return s; })} />
        {TYPES.map((t) => (
          <FilterChip key={t} label={t} active={typeFilter === t}
                      onClick={() => setSp((s) => { s.set("type", t); s.delete("page"); return s; })} />
        ))}
      </div>

      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-50 border border-red-200 text-red-800 text-sm">
          {error}
        </div>
      )}

      {assets === null && <div className="mt-10 text-slate-500">Loading…</div>}
      {assets !== null && assets.length === 0 && (
        <div className="mt-10 text-slate-500">
          No AI assets yet. Run a scan from <Link to="/connect" className="text-blue-700 underline">Connect</Link>.
        </div>
      )}

      {groups.map(({ repo, items }) => (
        <section key={repo ?? "_orphan"} className="mt-8 rounded-2xl border border-slate-200 overflow-hidden">
          <header className="px-4 py-3 bg-slate-50 text-sm font-medium text-slate-700">
            {repo ?? "Unattached"}
          </header>
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2 text-left">Type</th>
                <th className="px-4 py-2 text-left">Name</th>
                <th className="px-4 py-2 text-left">Source path</th>
                <th className="px-4 py-2 text-left">Detector</th>
                <th className="px-4 py-2 text-left">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-2 text-slate-700">{a.asset_type}</td>
                  <td className="px-4 py-2">
                    <Link to={`/ai/inventory/${a.id}`} className="text-blue-700 hover:underline">
                      {a.name}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-slate-600 font-mono text-xs">{a.source_path ?? "—"}</td>
                  <td className="px-4 py-2 text-slate-500 text-xs">{a.detector_id}</td>
                  <td className="px-4 py-2 text-slate-500 text-xs">{formatDate(a.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      <div className="mt-6 flex items-center gap-3 text-sm">
        <button onClick={() => setSp((s) => { s.set("page", String(Math.max(1, page - 1))); return s; })}
                disabled={page === 1}
                className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
          ← Prev
        </button>
        <span className="text-slate-600">Page {page}</span>
        <button onClick={() => setSp((s) => { s.set("page", String(page + 1)); return s; })}
                disabled={nextPage === null}
                className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
          Next →
        </button>
      </div>
    </div>
  );
}

function FilterChip({ label, active, onClick }:
  { label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
            className={`px-3 py-1 rounded-full text-xs ${
              active
                ? "bg-blue-600 text-white"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}>
      {label}
    </button>
  );
}

function groupByRepo(assets: AIAssetSummary[]) {
  const map = new Map<string | null, AIAssetSummary[]>();
  for (const a of assets) {
    const key = a.source_repo?.full_name ?? null;
    const bucket = map.get(key) ?? [];
    bucket.push(a);
    map.set(key, bucket);
  }
  return Array.from(map.entries()).map(([repo, items]) => ({ repo, items }));
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}
