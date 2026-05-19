import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type EntitySummary, type EntityKind } from "../lib/api";

const TYPES: EntityKind[] = [
  "ai_framework", "ai_model", "ai_mcp_server", "ai_tool", "ai_agent",
  "ai_vector_db", "ai_embedding", "ai_prompt",
];

export function AIInventory() {
  const [sp, setSp] = useSearchParams();
  const typeFilter = (sp.get("type") || "") as EntityKind | "";
  const pageStr    = sp.get("page") || "1";
  const page       = Math.max(1, parseInt(pageStr, 10) || 1);

  const [entities, setEntities] = useState<EntitySummary[] | null>(null);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    setEntities(null); setError(null);
    api.listEntities({ domain: "ai", kind: typeFilter || undefined, page })
       .then((r) => { setEntities(r.entities); setNextPage(r.next_page); })
       .catch((e: Error) => setError(e.message));
  }, [typeFilter, page]);

  const groups = groupByKind(entities ?? []);

  return (
    <div className="max-w-6xl">
      <h1 className="text-3xl font-bold tracking-tight">AI Inventory</h1>
      <p className="text-slate-600 mt-1">
        AI entities discovered by the scanner, grouped by kind.
      </p>

      <div className="mt-6 flex flex-wrap gap-2">
        <FilterChip label="All" active={typeFilter === ""}
                    onClick={() => setSp((s) => { s.delete("type"); s.delete("page"); return s; })} />
        {TYPES.map((t) => (
          <FilterChip key={t} label={prettyKind(t)} active={typeFilter === t}
                      onClick={() => setSp((s) => { s.set("type", t); s.delete("page"); return s; })} />
        ))}
      </div>

      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-50 border border-red-200 text-red-800 text-sm">
          {error}
        </div>
      )}

      {entities === null && <div className="mt-10 text-slate-500">Loading…</div>}
      {entities !== null && entities.length === 0 && (
        <div className="mt-10 text-slate-500">
          No AI entities yet. Run a scan from <Link to="/connect" className="text-blue-700 underline">Connect</Link>.
        </div>
      )}

      {groups.map(({ kind, items }) => (
        <section key={kind} className="mt-8 rounded-2xl border border-slate-200 overflow-hidden">
          <header className="px-4 py-3 bg-slate-50 text-sm font-medium text-slate-700">
            {prettyKind(kind)}
          </header>
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2 text-left">Kind</th>
                <th className="px-4 py-2 text-left">Name</th>
                <th className="px-4 py-2 text-left">Source path</th>
                <th className="px-4 py-2 text-left">Detector</th>
                <th className="px-4 py-2 text-left">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-2 text-slate-700">{prettyKind(e.kind)}</td>
                  <td className="px-4 py-2">
                    <Link to={`/ai/inventory/${e.id}`} className="text-blue-700 hover:underline">
                      {e.display_name}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-slate-600 font-mono text-xs">{e.source_path ?? "—"}</td>
                  <td className="px-4 py-2 text-slate-500 text-xs">{e.detector_id}</td>
                  <td className="px-4 py-2 text-slate-500 text-xs">{formatDate(e.last_seen_at)}</td>
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

function groupByKind(entities: EntitySummary[]) {
  const map = new Map<EntityKind, EntitySummary[]>();
  for (const e of entities) {
    const bucket = map.get(e.kind) ?? [];
    bucket.push(e);
    map.set(e.kind, bucket);
  }
  return Array.from(map.entries())
    .map(([kind, items]) => ({ kind, items }))
    .sort((a, b) => a.kind.localeCompare(b.kind));
}

function prettyKind(kind: string): string {
  return kind.startsWith("ai_") ? kind.slice(3) : kind;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}
