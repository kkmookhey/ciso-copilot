import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Finding, type FindingGroup } from "../lib/api";

type ViewMode = "grouped" | "flat";

const DOMAIN_LABEL: Record<string, string> = {
  iam:                 "Identity & Access",
  organizations:      "Organizations",
  cloudfront:          "CDN / Edge",
  logging:             "Logging",
  compute:             "Compute",
  storage:             "Storage",
  networking:          "Networking",
  encryption:          "Encryption",
  database:            "Databases",
  databases:           "Databases",
  monitoring:          "Monitoring",
  secrets:             "Secrets",
  governance:          "Governance",
  appservice:          "App Service",
  backup:              "Backup",
  diagnostic_settings: "Diagnostic settings",
  private_endpoints:   "Private endpoints",
  cloud_run:           "Cloud Run",
  other:               "Other",
};

export function TopRisks() {
  const [params, setParams] = useSearchParams();
  const severity  = params.get("severity") ?? undefined;
  const cloud     = params.get("cloud")    ?? undefined;
  const framework = params.get("framework") ?? undefined;
  const initialQ  = params.get("q") ?? "";

  const [view,   setView]   = useState<ViewMode>("grouped");
  const [search, setSearch] = useState(initialQ);
  const [groups, setGroups] = useState<FindingGroup[] | null>(null);
  const [flat,   setFlat]   = useState<Finding[] | null>(null);
  const [stats,  setStats]  = useState<{ findings: number; groups: number } | null>(null);

  // Debounce search → URL so refreshes preserve state.
  useEffect(() => {
    const id = setTimeout(() => {
      const p = new URLSearchParams(params);
      if (search) p.set("q", search); else p.delete("q");
      setParams(p, { replace: true });
    }, 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const q = params.get("q") ?? undefined;

  // Fetch based on view + filters.
  useEffect(() => {
    if (view === "grouped") {
      setGroups(null);
      api.findingsRollup({ severity, cloud, q })
        .then((r) => {
          let g = r.groups;
          if (framework) {
            g = g.filter((x) => Array.isArray(x.frameworks?.[framework]) && x.frameworks[framework].length > 0);
          }
          setGroups(g);
          setStats({ findings: r.total_findings, groups: r.total_groups });
        })
        .catch(() => setGroups([]));
    } else {
      setFlat(null);
      api.listFindings({ severity, cloud, limit: 200 })
        .then((r) => {
          let rows = r.findings;
          if (framework) {
            rows = rows.filter((f) => Array.isArray(f.frameworks?.[framework]) && f.frameworks[framework].length > 0);
          }
          if (q) {
            const t = q.toLowerCase();
            rows = rows.filter((f) =>
              f.title.toLowerCase().includes(t) ||
              f.check_id.toLowerCase().includes(t) ||
              (f.description ?? "").toLowerCase().includes(t),
            );
          }
          setFlat(rows);
        })
        .catch(() => setFlat([]));
    }
  }, [view, severity, cloud, framework, q]);

  function clearFilter(key: string) {
    const p = new URLSearchParams(params);
    p.delete(key);
    setParams(p, { replace: true });
  }

  const filterChips = [
    severity  ? { key: "severity",  label: `severity: ${severity}` }   : null,
    cloud     ? { key: "cloud",     label: `cloud: ${cloud}` }         : null,
    framework ? { key: "framework", label: `framework: ${framework}` } : null,
  ].filter(Boolean) as { key: string; label: string }[];

  return (
    <div className="max-w-6xl">
      <h1 className="text-3xl font-bold tracking-tight">Top Risks</h1>
      <p className="text-slate-600 mt-1">
        Open findings across your connected clouds, grouped by what's failing.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search title, check, or description…"
          className="flex-1 min-w-[240px] rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
        <div className="flex rounded-lg overflow-hidden border border-slate-300 text-sm">
          {(["grouped", "flat"] as ViewMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setView(m)}
              className={`px-3 py-1.5 capitalize ${
                view === m ? "bg-blue-600 text-white" : "bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      {filterChips.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2 items-center">
          {filterChips.map((c) => (
            <button
              key={c.key}
              onClick={() => clearFilter(c.key)}
              className="text-xs px-3 py-1 rounded-full bg-blue-50 text-blue-700 hover:bg-blue-100 transition"
              title="Click to clear"
            >
              {c.label} ✕
            </button>
          ))}
        </div>
      )}

      {view === "grouped" && stats && (
        <div className="mt-3 text-xs text-slate-500">
          {stats.groups} distinct issues · {stats.findings} findings
        </div>
      )}

      <div className="mt-6">
        {view === "grouped"
          ? <GroupedView groups={groups} />
          : <FlatView    findings={flat} />}
      </div>
    </div>
  );
}

// ============================================================================
// Grouped view — sections by domain, rows by check_id
// ============================================================================

function GroupedView({ groups }: { groups: FindingGroup[] | null }) {
  const byDomain = useMemo(() => {
    if (!groups) return null;
    const m = new Map<string, FindingGroup[]>();
    for (const g of groups) {
      const dom = g.domain || "other";
      if (!m.has(dom)) m.set(dom, []);
      m.get(dom)!.push(g);
    }
    // Sort each domain's groups by severity then count (already done by backend but keep stable)
    return Array.from(m.entries()).sort(([, a], [, b]) => {
      const aw = a.reduce((s, g) => s + g.count, 0);
      const bw = b.reduce((s, g) => s + g.count, 0);
      return bw - aw;
    });
  }, [groups]);

  if (groups === null) {
    return <p className="text-slate-500 text-sm">Loading…</p>;
  }
  if (groups.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center">
        <p className="text-slate-600">No findings match this filter.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {byDomain!.map(([domain, gs]) => (
        <DomainSection key={domain} domain={domain} groups={gs} />
      ))}
    </div>
  );
}

function DomainSection({ domain, groups }: { domain: string; groups: FindingGroup[] }) {
  const [collapsed, setCollapsed] = useState(false);
  const totalFindings = groups.reduce((s, g) => s + g.count, 0);
  const label = DOMAIN_LABEL[domain] ?? domain.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <section>
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center justify-between py-2 text-left"
      >
        <div className="flex items-center gap-2">
          <span className={`text-slate-400 text-sm transition-transform ${collapsed ? "" : "rotate-90"}`}>▶</span>
          <h2 className="font-semibold text-base">{label}</h2>
          <span className="text-xs text-slate-500">{groups.length} issues · {totalFindings} findings</span>
        </div>
      </button>
      {!collapsed && (
        <ul className="mt-2 space-y-2">
          {groups.map((g) => (
            <GroupRow key={`${g.domain}/${g.check_id}`} group={g} />
          ))}
        </ul>
      )}
    </section>
  );
}

function GroupRow({ group }: { group: FindingGroup }) {
  const [open,      setOpen]      = useState(false);
  const [resources, setResources] = useState<Finding[] | null>(null);

  async function expand() {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (resources !== null) return;
    try {
      const r = await api.listFindings({ check_id: group.check_id, limit: 200 });
      setResources(r.findings);
    } catch {
      setResources([]);
    }
  }

  const frameworks = Object.entries(group.frameworks ?? {});

  return (
    <li className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <button
        onClick={expand}
        className="w-full p-4 text-left hover:bg-slate-50 transition"
        aria-expanded={open}
      >
        <div className="flex items-start gap-4">
          <SeverityPill severity={group.severity} />
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="font-semibold">{group.title}</span>
              <span className="text-xs font-mono text-slate-400">{group.check_id}</span>
            </div>
            <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
              <span><strong className="text-slate-700">{group.count}</strong> affected resource{group.count === 1 ? "" : "s"}</span>
              {frameworks.length > 0 && (
                <span>
                  {frameworks.map(([fw, ctrls]) => (
                    <span key={fw} className="mr-2">
                      <span className="text-slate-600">{fw.toUpperCase()}</span>{" "}
                      <span className="font-mono text-slate-400">{Array.isArray(ctrls) ? ctrls.join(",") : String(ctrls)}</span>
                    </span>
                  ))}
                </span>
              )}
            </div>
          </div>
          <span className={`text-slate-300 transition-transform ${open ? "rotate-180" : ""}`}>⌄</span>
        </div>
      </button>
      {open && (
        <div className="border-t border-slate-200 bg-slate-50">
          {resources === null ? (
            <p className="text-slate-500 text-sm p-4">Loading affected resources…</p>
          ) : resources.length === 0 ? (
            <p className="text-slate-500 text-sm p-4">No resources found.</p>
          ) : (
            <ul className="divide-y divide-slate-200">
              {resources.map((f) => (
                <li key={f.finding_id} className="p-4">
                  <ResourceRow f={f} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

function ResourceRow({ f }: { f: Finding }) {
  return (
    <div className="text-sm">
      {f.resource_arn ? (
        <div className="font-mono text-xs text-slate-700 break-all">{f.resource_arn}</div>
      ) : (
        <div className="text-slate-500 italic text-xs">No resource ARN</div>
      )}
      <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3">
        {f.region && <span>region: <span className="font-mono">{f.region}</span></span>}
        {f.resource_type && <span>type: <span className="font-mono">{f.resource_type}</span></span>}
        <span>last seen: {new Date(f.last_seen).toLocaleString()}</span>
      </div>
      {f.remediation && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-slate-600 hover:text-slate-900">Remediation</summary>
          <p className="mt-2 text-slate-700 whitespace-pre-wrap pl-3 border-l-2 border-slate-200">{f.remediation}</p>
        </details>
      )}
    </div>
  );
}

// ============================================================================
// Flat view — original full list
// ============================================================================

function FlatView({ findings }: { findings: Finding[] | null }) {
  if (findings === null) return <p className="text-slate-500 text-sm">Loading…</p>;
  if (findings.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center">
        <p className="text-slate-600">No findings match this filter.</p>
      </div>
    );
  }
  return (
    <ul className="space-y-3">
      {findings.map((f) => <FlatRow key={f.finding_id} f={f} />)}
    </ul>
  );
}

function FlatRow({ f }: { f: Finding }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <button onClick={() => setOpen(!open)} className="w-full p-5 text-left hover:bg-slate-50 transition">
        <div className="flex items-start gap-4">
          <SeverityPill severity={f.severity} />
          <div className="flex-1 min-w-0">
            <div className="font-semibold">{f.title}</div>
            <div className="text-xs text-slate-500 mt-1 font-mono">{f.check_id}</div>
            {f.resource_arn && (
              <div className="mt-2 text-xs font-mono text-slate-500 break-all">{f.resource_arn}</div>
            )}
          </div>
          <span className={`text-slate-300 transition-transform ${open ? "rotate-180" : ""}`}>⌄</span>
        </div>
      </button>
      {open && (
        <div className="border-t border-slate-200 bg-slate-50 px-5 py-4 text-sm space-y-3">
          {f.description && <p className="text-slate-700">{f.description}</p>}
          {f.remediation && (
            <div>
              <div className="text-xs font-semibold uppercase text-slate-500">Remediation</div>
              <p className="text-slate-700 whitespace-pre-wrap mt-1">{f.remediation}</p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function SeverityPill({ severity }: { severity: Finding["severity"] | FindingGroup["severity"] }) {
  const style = {
    critical: "bg-red-100 text-red-700",
    high:     "bg-amber-100 text-amber-700",
    medium:   "bg-yellow-100 text-yellow-700",
    low:      "bg-slate-100 text-slate-700",
    info:     "bg-slate-100 text-slate-500",
  }[severity];
  return <span className={`shrink-0 px-2 py-0.5 rounded-full text-xs font-semibold uppercase ${style}`}>{severity}</span>;
}
