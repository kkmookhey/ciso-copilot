import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Finding } from "../lib/api";

// ---------------------------------------------------------------------------
// Findings page — every finding (fail / partial / pass), rolled up by check
// into cards, with a user-chosen top-level grouping.
// ---------------------------------------------------------------------------

type GroupDim = "status" | "category" | "cloud" | "framework";
type Status   = "fail" | "partial" | "pass";

const GROUP_DIMS: { key: GroupDim; label: string }[] = [
  { key: "status",    label: "Status" },
  { key: "category",  label: "Category" },
  { key: "cloud",     label: "Cloud" },
  { key: "framework", label: "Compliance Framework" },
];

const STATUS_ORDER: Status[] = ["fail", "partial", "pass"];
const STATUS_LABEL: Record<Status, string> = { fail: "Fail", partial: "Partial", pass: "Pass" };

const SEV_RANK: Record<string, number> = { critical: 1, high: 2, medium: 3, low: 4, info: 5 };

const DOMAIN_LABEL: Record<string, string> = {
  iam: "Identity & Access", organizations: "Organizations", cloudfront: "CDN / Edge",
  logging: "Logging", compute: "Compute", storage: "Storage", networking: "Networking",
  encryption: "Encryption", database: "Databases", databases: "Databases",
  monitoring: "Monitoring", secrets: "Secrets", governance: "Governance",
  appservice: "App Service", backup: "Backup", diagnostic_settings: "Diagnostic settings",
  private_endpoints: "Private endpoints", cloud_run: "Cloud Run", ai: "AI", other: "Other",
};

const FRAMEWORK_LABEL: Record<string, string> = {
  soc2: "SOC 2", cis_aws: "CIS AWS", cis_azure: "CIS Azure", cis_gcp: "CIS GCP",
  mcsb: "MCSB", iso27001: "ISO 27001", hipaa: "HIPAA",
  nist_ai_rmf: "NIST AI RMF", iso_42001: "ISO 42001", eu_ai_act: "EU AI Act",
  owasp_llm_top10: "OWASP LLM Top 10", owasp_agentic: "OWASP Agentic",
  nist_ai_600_1: "NIST AI 600-1", mitre_atlas: "MITRE ATLAS",
};

interface CheckGroup {
  check_id:   string;
  title:      string;                    // generic — from the check-title catalog
  domain:     string;
  cloud:      string;
  severity:   Finding["severity"];       // worst across findings
  status:     Status;                    // worst across findings
  frameworks: Record<string, string[]>;  // merged
  findings:   Finding[];
}

/** Single-cloud today; derive from the ARN so this still works once
 *  Azure/GCP are connected. */
function cloudOf(f: Finding): string {
  const arn = f.resource_arn ?? "";
  if (arn.startsWith("arn:aws:")) return "AWS";
  if (/azure|microsoft\./i.test(arn)) return "Azure";
  if (/googleapis|cloudresourcemanager/i.test(arn)) return "GCP";
  return "AWS";
}

function rollUp(findings: Finding[]): CheckGroup[] {
  const byCheck = new Map<string, Finding[]>();
  for (const f of findings) {
    const arr = byCheck.get(f.check_id);
    if (arr) arr.push(f);
    else byCheck.set(f.check_id, [f]);
  }
  return [...byCheck.values()].map((fs) => {
    const severity = [...fs].sort(
      (a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9),
    )[0].severity;
    const status = STATUS_ORDER.find((s) => fs.some((f) => f.status === s)) ?? "pass";
    const frameworks: Record<string, string[]> = {};
    for (const f of fs) {
      for (const [fw, ctrls] of Object.entries(f.frameworks ?? {})) {
        const set = new Set([...(frameworks[fw] ?? []), ...(Array.isArray(ctrls) ? ctrls : [])]);
        frameworks[fw] = [...set];
      }
    }
    return {
      check_id: fs[0].check_id,
      title:    fs[0].check_title,
      domain:   fs[0].domain || "other",
      cloud:    cloudOf(fs[0]),
      severity, status, frameworks,
      findings: fs,
    };
  });
}

interface Section { key: string; label: string; groups: CheckGroup[] }

function sectionize(groups: CheckGroup[], dim: GroupDim): Section[] {
  const bucket = new Map<string, CheckGroup[]>();
  const push = (k: string, g: CheckGroup) => {
    const arr = bucket.get(k);
    if (arr) arr.push(g);
    else bucket.set(k, [g]);
  };

  for (const g of groups) {
    if (dim === "status")        push(g.status, g);
    else if (dim === "category") push(g.domain, g);
    else if (dim === "cloud")    push(g.cloud, g);
    else {
      const fws = Object.keys(g.frameworks).filter((fw) => g.frameworks[fw]?.length);
      if (fws.length === 0) push("__none__", g);
      else for (const fw of fws) push(fw, g);
    }
  }

  const labelFor = (k: string): string => {
    if (dim === "status")    return STATUS_LABEL[k as Status] ?? k;
    if (dim === "category")  return DOMAIN_LABEL[k] ?? k.replace(/_/g, " ");
    if (dim === "cloud")     return k;
    if (k === "__none__")    return "No framework mapping";
    return FRAMEWORK_LABEL[k] ?? k.toUpperCase();
  };

  const sortGroups = (gs: CheckGroup[]) =>
    [...gs].sort((a, b) =>
      (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9)
      || b.findings.length - a.findings.length);

  const sections: Section[] = [...bucket.entries()].map(([key, gs]) => ({
    key, label: labelFor(key), groups: sortGroups(gs),
  }));

  if (dim === "status") {
    sections.sort((a, b) => STATUS_ORDER.indexOf(a.key as Status) - STATUS_ORDER.indexOf(b.key as Status));
  } else {
    sections.sort((a, b) =>
      b.groups.reduce((s, g) => s + g.findings.length, 0)
      - a.groups.reduce((s, g) => s + g.findings.length, 0));
  }
  return sections;
}

export function TopRisks() {
  const [params, setParams] = useSearchParams();
  const severity  = params.get("severity")  ?? undefined;
  const cloud     = params.get("cloud")     ?? undefined;
  const framework = params.get("framework") ?? undefined;
  const dim       = (params.get("group") as GroupDim) || "status";
  const initialQ  = params.get("q") ?? "";

  const [search,   setSearch]   = useState(initialQ);
  const [findings, setFindings] = useState<Finding[] | null>(null);

  // Debounce search → URL.
  useEffect(() => {
    const id = setTimeout(() => {
      const p = new URLSearchParams(params);
      if (search) p.set("q", search); else p.delete("q");
      setParams(p, { replace: true });
    }, 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  // Fetch every finding once — grouping + filtering is all client-side.
  useEffect(() => {
    setFindings(null);
    api.listFindings({ status: "fail,partial,pass", limit: 200 })
      .then((r) => setFindings(r.findings))
      .catch(() => setFindings([]));
  }, []);

  const q = params.get("q")?.toLowerCase() ?? "";

  const sections = useMemo(() => {
    if (!findings) return null;
    let rows = findings;
    if (severity)  rows = rows.filter((f) => f.severity === severity);
    if (cloud)     rows = rows.filter((f) => cloudOf(f).toLowerCase() === cloud.toLowerCase());
    if (framework) rows = rows.filter((f) => (f.frameworks?.[framework]?.length ?? 0) > 0);
    if (q) {
      rows = rows.filter((f) =>
        f.title.toLowerCase().includes(q) ||
        f.check_id.toLowerCase().includes(q) ||
        (f.description ?? "").toLowerCase().includes(q));
    }
    return sectionize(rollUp(rows), dim);
  }, [findings, severity, cloud, framework, q, dim]);

  function setDim(next: GroupDim) {
    const p = new URLSearchParams(params);
    p.set("group", next);
    setParams(p, { replace: true });
  }
  function clearFilter(key: string) {
    const p = new URLSearchParams(params);
    p.delete(key);
    setParams(p, { replace: true });
  }

  const filterChips = [
    severity  ? { key: "severity",  label: `severity: ${severity}` }   : null,
    cloud     ? { key: "cloud",     label: `cloud: ${cloud}` }          : null,
    framework ? { key: "framework", label: `framework: ${framework}` } : null,
  ].filter(Boolean) as { key: string; label: string }[];

  const totalFindings = sections?.reduce(
    (s, sec) => s + sec.groups.reduce((n, g) => n + g.findings.length, 0), 0) ?? 0;
  const totalGroups = sections?.reduce((s, sec) => s + sec.groups.length, 0) ?? 0;

  return (
    <div className="max-w-6xl">
      <h1 className="text-3xl font-bold tracking-tight">Findings</h1>
      <p className="text-slate-600 mt-1">
        Every finding across your connected clouds — grouped by status, category, cloud, or framework.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search title, check, or description…"
          className="flex-1 min-w-[240px] rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500 uppercase tracking-wide">Group by</span>
          <div className="flex rounded-lg overflow-hidden border border-slate-300 text-sm">
            {GROUP_DIMS.map((d) => (
              <button
                key={d.key}
                onClick={() => setDim(d.key)}
                className={`px-3 py-1.5 ${
                  dim === d.key ? "bg-blue-600 text-white" : "bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                {d.label}
              </button>
            ))}
          </div>
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

      {sections && (
        <div className="mt-3 text-xs text-slate-500">
          {totalGroups} distinct issues · {totalFindings} findings
        </div>
      )}

      <div className="mt-6">
        {sections === null ? (
          <p className="text-slate-500 text-sm">Loading…</p>
        ) : sections.length === 0 ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center">
            <p className="text-slate-600">No findings match this filter.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {sections.map((sec) => (
              <SectionBlock
                key={sec.key}
                section={sec}
                defaultCollapsed={dim === "status" && sec.key === "pass"}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SectionBlock({ section, defaultCollapsed }: { section: Section; defaultCollapsed: boolean }) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const findingCount = section.groups.reduce((s, g) => s + g.findings.length, 0);

  return (
    <section>
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center gap-2 py-2 text-left"
      >
        <span className={`text-slate-400 text-sm transition-transform ${collapsed ? "" : "rotate-90"}`}>▶</span>
        <h2 className="font-semibold text-base">{section.label}</h2>
        <span className="text-xs text-slate-500">
          {section.groups.length} issues · {findingCount} findings
        </span>
      </button>
      {!collapsed && (
        <ul className="mt-2 space-y-2">
          {section.groups.map((g) => (
            <GroupRow key={g.check_id} group={g} />
          ))}
        </ul>
      )}
    </section>
  );
}

function GroupRow({ group }: { group: CheckGroup }) {
  const [open, setOpen] = useState(false);
  const count = group.findings.length;
  const frameworks = Object.entries(group.frameworks).filter(([, c]) => c?.length);

  return (
    <li className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full p-4 text-left hover:bg-slate-50 transition"
        aria-expanded={open}
      >
        <div className="flex items-start gap-3">
          <SeverityPill severity={group.severity} />
          <StatusBadge status={group.status} />
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="font-semibold">{group.title}</span>
              <span className="text-xs font-mono text-slate-400">{group.check_id}</span>
            </div>
            <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
              <span><strong className="text-slate-700">{count}</strong> affected resource{count === 1 ? "" : "s"}</span>
              {frameworks.length > 0 && (
                <span>
                  {frameworks.map(([fw, ctrls]) => (
                    <span key={fw} className="mr-2">
                      <span className="text-slate-600">{FRAMEWORK_LABEL[fw] ?? fw.toUpperCase()}</span>{" "}
                      <span className="font-mono text-slate-400">{ctrls.join(",")}</span>
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
          <ul className="divide-y divide-slate-200">
            {group.findings.map((f) => (
              <li key={f.finding_id} className="p-4">
                <ResourceRow f={f} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function ResourceRow({ f }: { f: Finding }) {
  return (
    <div className="text-sm">
      <div className="font-medium text-slate-800">{f.title}</div>
      {f.resource_arn ? (
        <div className="font-mono text-xs text-slate-600 break-all mt-0.5">{f.resource_arn}</div>
      ) : (
        <div className="text-slate-500 italic text-xs mt-0.5">No resource ARN</div>
      )}
      <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3">
        <span>status: <StatusText status={f.status} /></span>
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

const STATUS_STYLE: Record<string, string> = {
  fail:    "bg-red-100 text-red-700",
  partial: "bg-amber-100 text-amber-700",
  pass:    "bg-green-100 text-green-700",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`shrink-0 px-2 py-0.5 rounded-full text-xs font-semibold uppercase ${STATUS_STYLE[status] ?? "bg-slate-100 text-slate-600"}`}>
      {status}
    </span>
  );
}

function StatusText({ status }: { status: string }) {
  const tone = status === "fail" ? "text-red-600"
             : status === "partial" ? "text-amber-600"
             : status === "pass" ? "text-green-600" : "text-slate-600";
  return <span className={`font-medium ${tone}`}>{status}</span>;
}

function SeverityPill({ severity }: { severity: Finding["severity"] }) {
  const style = {
    critical: "bg-red-100 text-red-700",
    high:     "bg-amber-100 text-amber-700",
    medium:   "bg-yellow-100 text-yellow-700",
    low:      "bg-slate-100 text-slate-700",
    info:     "bg-slate-100 text-slate-500",
  }[severity];
  return <span className={`shrink-0 px-2 py-0.5 rounded-full text-xs font-semibold uppercase ${style}`}>{severity}</span>;
}
