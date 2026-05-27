import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type EntitySummary, type EntityKind } from "../lib/api";

const AGENT_KINDS: EntityKind[]  = ["ai_agent", "ai_mcp_server", "ai_tool"];
const MODEL_KINDS: EntityKind[]  = ["ai_model", "ai_framework", "ai_vector_db", "ai_embedding", "ai_prompt"];
const ALL_KINDS:   EntityKind[]  = [...AGENT_KINDS, ...MODEL_KINDS];

const PAGE_SIZE = 200;

export function AIInventory() {
  const [sp, setSp] = useSearchParams();
  const typeFilter = (sp.get("type") || "") as EntityKind | "";

  const [entities, setEntities] = useState<EntitySummary[] | null>(null);
  const [truncated, setTruncated] = useState<boolean>(false);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    setEntities(null); setError(null); setTruncated(false);
    api.listEntities({ domain: "ai", kind: typeFilter || undefined, per_page: PAGE_SIZE })
       .then((r) => { setEntities(r.entities); setTruncated(r.next_page !== null); })
       .catch((e: Error) => setError(e.message));
  }, [typeFilter]);

  const counts = countByKind(entities ?? []);
  const agentItems = (entities ?? []).filter((e) => AGENT_KINDS.includes(e.kind));
  const modelItems = (entities ?? []).filter((e) => MODEL_KINDS.includes(e.kind));

  // When a filter is applied, only render the section the filter belongs to.
  const showAgents = typeFilter === "" || AGENT_KINDS.includes(typeFilter as EntityKind);
  const showModels = typeFilter === "" || MODEL_KINDS.includes(typeFilter as EntityKind);

  return (
    <div className="max-w-6xl">
      <h1 className="text-3xl font-bold tracking-tight">AI Inventory</h1>
      <p className="text-slate-600 mt-1">
        AI surface discovered across your code, clouds, and identity stack.
      </p>

      {/* Hero stat strip */}
      <HeroStats counts={counts} truncated={truncated} loading={entities === null} />

      {/* Filter chips */}
      <div className="mt-6 flex flex-wrap gap-2">
        <FilterChip label="All" active={typeFilter === ""}
                    onClick={() => setSp((s) => { s.delete("type"); s.delete("page"); return s; })} />
        {ALL_KINDS.map((t) => (
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

      {/* Section 1 — Autonomous AI (elevated) */}
      {showAgents && agentItems.length > 0 && (
        <Section
          title="Autonomous AI surface"
          subtitle="Agents, MCP servers and tools — the components that act on data and call APIs on your behalf."
          tone="prominent"
          groups={groupByKind(agentItems, AGENT_KINDS)}
        />
      )}

      {/* Section 2 — Models, data & prompts */}
      {showModels && modelItems.length > 0 && (
        <Section
          title="Models, data & prompts"
          subtitle="The model surfaces, vector stores, embeddings and prompt assets your apps consume."
          tone="standard"
          groups={groupByKind(modelItems, MODEL_KINDS)}
        />
      )}
    </div>
  );
}

// ---------- Hero ----------

function HeroStats({ counts, truncated, loading }: {
  counts: Record<EntityKind, number>; truncated: boolean; loading: boolean;
}) {
  const total = ALL_KINDS.reduce((acc, k) => acc + (counts[k] ?? 0), 0);
  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-gradient-to-br from-slate-50 to-white p-5">
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-500">Discovered AI surface</div>
          <div className="text-3xl font-bold mt-1">
            {loading ? "—" : total}
            <span className="text-base font-normal text-slate-500 ml-2">entities</span>
          </div>
        </div>
        {truncated && (
          <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 px-2 py-1 rounded">
            Showing first {PAGE_SIZE} — paginate or filter to see the rest
          </span>
        )}
      </div>

      {/* Autonomous trio — prominent */}
      <div className="grid grid-cols-3 gap-3 mb-3">
        <BigStat label="Agents"       value={counts.ai_agent      ?? 0} />
        <BigStat label="MCP servers"  value={counts.ai_mcp_server ?? 0} />
        <BigStat label="Tools"        value={counts.ai_tool       ?? 0} />
      </div>

      {/* Models, data, frameworks, prompts — satellites */}
      <div className="grid grid-cols-5 gap-2">
        <SmallStat label="Models"     value={counts.ai_model      ?? 0} />
        <SmallStat label="Vector DBs" value={counts.ai_vector_db  ?? 0} />
        <SmallStat label="Embeddings" value={counts.ai_embedding  ?? 0} />
        <SmallStat label="Prompts"    value={counts.ai_prompt     ?? 0} />
        <SmallStat label="Frameworks" value={counts.ai_framework  ?? 0} />
      </div>
    </div>
  );
}

function BigStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-white border border-slate-200 p-4">
      <div className="text-3xl font-bold text-slate-900">{value}</div>
      <div className="text-xs uppercase tracking-wider text-slate-500 mt-1">{label}</div>
    </div>
  );
}

function SmallStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-white border border-slate-100 px-3 py-2">
      <div className="text-lg font-semibold text-slate-800">{value}</div>
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}

// ---------- Sections ----------

function Section({ title, subtitle, tone, groups }: {
  title: string;
  subtitle: string;
  tone: "prominent" | "standard";
  groups: { kind: EntityKind; items: EntitySummary[] }[];
}) {
  const headerBg = tone === "prominent"
    ? "bg-orange-50 border-orange-200"
    : "bg-slate-50 border-slate-200";
  const accent = tone === "prominent" ? "border-orange-200" : "border-slate-200";

  return (
    <div className={`mt-8 rounded-2xl border ${accent} overflow-hidden`}>
      <header className={`px-5 py-4 border-b ${headerBg}`}>
        <div className="text-lg font-semibold text-slate-900">{title}</div>
        <div className="text-sm text-slate-600 mt-0.5">{subtitle}</div>
      </header>

      {groups.map(({ kind, items }) => (
        <div key={kind} className="border-t border-slate-100 first:border-t-0">
          <div className="px-5 py-2 bg-slate-50/60 text-xs uppercase tracking-wider text-slate-600 font-medium">
            {prettyKind(kind)} <span className="text-slate-400">({items.length})</span>
          </div>
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-2 text-left">Name</th>
                <th className="px-5 py-2 text-left">Source path</th>
                <th className="px-5 py-2 text-left">Discovered in</th>
                <th className="px-5 py-2 text-left">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-5 py-2">
                    <Link to={`/ai/inventory/${e.id}`} className="text-blue-700 hover:underline">
                      {e.display_name}
                    </Link>
                  </td>
                  <td className="px-5 py-2 text-slate-600 font-mono text-xs">{e.source_path ?? "—"}</td>
                  <td className="px-5 py-2">
                    <DiscoveredBadge detectorId={e.detector_id} />
                  </td>
                  <td className="px-5 py-2 text-slate-500 text-xs">{formatDate(e.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function DiscoveredBadge({ detectorId }: { detectorId: string }) {
  const { label, tone } = describeDetector(detectorId);
  const colour = tone === "code"   ? "bg-blue-50 text-blue-800 border-blue-200"
              : tone === "aws"    ? "bg-amber-50 text-amber-800 border-amber-200"
              : tone === "azure"  ? "bg-sky-50 text-sky-800 border-sky-200"
              : tone === "entra"  ? "bg-violet-50 text-violet-800 border-violet-200"
              :                     "bg-slate-50 text-slate-600 border-slate-200";
  return (
    <span className={`inline-block text-[11px] px-2 py-0.5 rounded border ${colour}`}
          title={detectorId}>
      {label}
    </span>
  );
}

// ---------- Helpers ----------

function countByKind(entities: EntitySummary[]): Record<EntityKind, number> {
  const out = {} as Record<EntityKind, number>;
  for (const e of entities) {
    out[e.kind] = (out[e.kind] ?? 0) + 1;
  }
  return out;
}

function groupByKind(entities: EntitySummary[], order: EntityKind[]) {
  const map = new Map<EntityKind, EntitySummary[]>();
  for (const e of entities) {
    const bucket = map.get(e.kind) ?? [];
    bucket.push(e);
    map.set(e.kind, bucket);
  }
  return order
    .filter((k) => map.has(k))
    .map((k) => ({ kind: k, items: map.get(k)! }));
}

function describeDetector(detectorId: string): { label: string; tone: "code" | "aws" | "azure" | "entra" | "other" } {
  if (detectorId.startsWith("ai.detectors."))                    return { label: "Code",    tone: "code" };
  if (detectorId.startsWith("shasta_runner.azure"))              return { label: "Azure",   tone: "azure" };
  if (detectorId.startsWith("shasta_runner.entra"))              return { label: "Entra",   tone: "entra" };
  if (detectorId.startsWith("shasta_runner.gcp"))                return { label: "GCP",     tone: "other" };
  if (detectorId.startsWith("shasta_runner"))                    return { label: "AWS",     tone: "aws" };
  if (detectorId.startsWith("manual."))                          return { label: "Manual",  tone: "other" };
  return { label: detectorId, tone: "other" };
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

function prettyKind(kind: string): string {
  if (!kind.startsWith("ai_")) return kind;
  const tail = kind.slice(3);
  if (tail === "mcp_server")  return "MCP server";
  if (tail === "vector_db")   return "Vector DB";
  return tail.charAt(0).toUpperCase() + tail.slice(1);
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}
