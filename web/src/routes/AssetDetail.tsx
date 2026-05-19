import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type EntityDetail } from "../lib/api";

interface EvidenceSourceEvent {
  kind?: string;
  repo?: string;
  commit_sha?: string;
  path?: string;
  snippet_lines?: [number, number];
  snippet?: string;
}

export function AssetDetail() {
  const { asset_id } = useParams<{ asset_id: string }>();
  const [entity, setEntity] = useState<EntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!asset_id) return;
    setEntity(null); setError(null);
    api.getEntity(asset_id)
       .then(setEntity)
       .catch((e: Error) => setError(e.message));
  }, [asset_id]);

  if (!asset_id) return <div>Missing asset id.</div>;
  if (error) return <div className="text-rose-700">{error}</div>;
  if (!entity) return <div className="text-slate-500">Loading…</div>;

  const evidence = entity.evidence_packet as { source_events?: EvidenceSourceEvent[] } | null;
  const events   = evidence?.source_events ?? [];
  const firstEvent = events[0];
  const githubLink = githubBlobUrl(firstEvent);

  return (
    <div className="max-w-4xl">
      <Link to="/ai/inventory" className="text-sm text-slate-600 hover:underline">← Back to AI Inventory</Link>

      <header className="mt-3">
        <div className="flex items-center gap-3">
          <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-xs uppercase tracking-wide">
            {entity.kind}
          </span>
          <h1 className="text-3xl font-bold tracking-tight">{entity.display_name}</h1>
        </div>
        {entity.source_path && (
          <p className="mt-1 text-slate-600 text-sm">
            {githubLink ? (
              <a className="text-blue-700 hover:underline font-mono text-xs"
                 href={githubLink} target="_blank" rel="noopener noreferrer">
                {entity.source_path}
              </a>
            ) : (
              <span className="font-mono text-xs">{entity.source_path}</span>
            )}
          </p>
        )}
      </header>

      <section className="mt-6 rounded-2xl border border-slate-200 p-4">
        <h2 className="text-sm font-medium text-slate-700">Attributes</h2>
        <pre className="mt-2 text-xs text-slate-700 bg-slate-50 p-3 rounded overflow-x-auto">
{JSON.stringify(entity.attributes, null, 2)}
        </pre>
      </section>

      {firstEvent?.snippet && (
        <section className="mt-6 rounded-2xl border border-slate-200 p-4">
          <h2 className="text-sm font-medium text-slate-700">
            Source snippet
            {firstEvent.path && (
              <span className="text-slate-500 font-normal font-mono ml-2">
                {firstEvent.path}
                {firstEvent.snippet_lines && `:${firstEvent.snippet_lines[0]}`}
              </span>
            )}
          </h2>
          <pre className="mt-2 text-xs text-slate-100 bg-slate-900 p-3 rounded overflow-x-auto">
{firstEvent.snippet}
          </pre>
        </section>
      )}

      <details className="mt-6 rounded-2xl border border-slate-200 p-4">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">
          Evidence packet (raw)
        </summary>
        <pre className="mt-3 text-xs text-slate-700 bg-slate-50 p-3 rounded overflow-x-auto">
{JSON.stringify(entity.evidence_packet, null, 2)}
        </pre>
      </details>

      <section className="mt-6 text-sm text-slate-500">
        Related entities (cross-domain edges) ship in SP2 via the relationships endpoint.
      </section>
    </div>
  );
}

function githubBlobUrl(ev: EvidenceSourceEvent | undefined): string | null {
  if (!ev?.repo || !ev?.path) return null;
  const sha = ev.commit_sha && ev.commit_sha !== "fixture-sha" ? ev.commit_sha : null;
  const ref = sha ?? "HEAD";
  const line = ev.snippet_lines?.[0];
  const hash = line ? `#L${line}` : "";
  return `https://github.com/${ev.repo}/blob/${ref}/${ev.path}${hash}`;
}
