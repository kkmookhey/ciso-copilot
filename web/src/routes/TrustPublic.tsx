import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { env } from "../lib/env";

interface PublicPage {
  name:       string;
  notes:      string | null;
  updated_at: string;
  compliance?: Record<string, { score_pct: number; passing: number; failing: number; total: number }>;
  findings?:   { total: number; by_severity: Record<string, number> };
  clouds?:     { total: number; by_cloud: Record<string, number> };
  last_scan?:  string | null;
}

const API_BASE_URL = env.apiBaseUrl;

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-500",
  high:     "bg-amber-500",
  medium:   "bg-yellow-500",
  low:      "bg-slate-400",
  info:     "bg-slate-300",
};

const FRAMEWORK_LABEL: Record<string, string> = {
  soc2:      "SOC 2",
  cis_aws:   "CIS AWS",
  cis_azure: "CIS Azure",
  cis_gcp:   "CIS GCP",
  mcsb:      "MCSB",
  iso27001:  "ISO 27001",
  hipaa:     "HIPAA",
  fedramp:   "FedRAMP",
  pci_dss:   "PCI DSS",
};

export function TrustPublic() {
  const { slug } = useParams<{ slug: string }>();
  const [page,  setPage]  = useState<PublicPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) return;
    fetch(`${API_BASE_URL}/public/trust/${slug}`)
      .then((r) => r.ok ? r.json() : Promise.reject(`${r.status}`))
      .then(setPage)
      .catch(() => setError("not_found"));
  }, [slug]);

  if (error === "not_found") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-slate-700">Trust page not found</h1>
          <p className="text-slate-500 mt-2">This trust page doesn't exist or hasn't been published yet.</p>
        </div>
      </div>
    );
  }

  if (!page) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <p className="text-slate-500">Loading…</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 to-white">
      <div className="max-w-3xl mx-auto px-6 py-12">
        <div className="text-xs text-slate-400 uppercase tracking-wide">Security Posture</div>
        <h1 className="text-4xl font-bold tracking-tight mt-1">{page.name}</h1>
        <p className="text-xs text-slate-500 mt-2">
          Live data · Updated {new Date(page.updated_at).toLocaleString()}
        </p>

        {page.notes && (
          <div className="mt-8 p-5 rounded-2xl bg-white border border-slate-200">
            <p className="text-slate-700 whitespace-pre-wrap text-sm">{page.notes}</p>
          </div>
        )}

        {page.compliance && Object.keys(page.compliance).length > 0 && (
          <Section title="Compliance posture">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {Object.entries(page.compliance)
                .sort(([, a], [, b]) => b.total - a.total)
                .map(([fw, agg]) => (
                  <FrameworkTile key={fw} framework={fw} agg={agg} />
                ))}
            </div>
          </Section>
        )}

        {page.findings && (
          <Section title="Open security findings">
            <div className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="flex items-baseline justify-between">
                <span className="text-3xl font-bold">{page.findings.total}</span>
                <span className="text-xs text-slate-500">across all clouds</span>
              </div>
              <div className="mt-4 space-y-2">
                {(["critical", "high", "medium", "low", "info"] as const).map((sev) => (
                  <SeverityBar
                    key={sev}
                    label={sev}
                    count={page.findings?.by_severity[sev] ?? 0}
                    total={page.findings?.total ?? 1}
                  />
                ))}
              </div>
            </div>
          </Section>
        )}

        {page.clouds && page.clouds.total > 0 && (
          <Section title="Connected clouds">
            <div className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="text-3xl font-bold">{page.clouds.total}</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(page.clouds.by_cloud).map(([cloud, n]) => (
                  <span key={cloud} className="px-2.5 py-1 rounded-full bg-slate-100 text-xs text-slate-700">
                    {cloud.toUpperCase()} · {n}
                  </span>
                ))}
              </div>
            </div>
          </Section>
        )}

        {page.last_scan && (
          <Section title="Last scan">
            <div className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="text-base">{new Date(page.last_scan).toLocaleString()}</div>
            </div>
          </Section>
        )}

        <div className="mt-16 text-center text-xs text-slate-400">
          Powered by CISO Copilot
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-10">
      <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">{title}</h2>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function FrameworkTile({ framework, agg }: { framework: string; agg: { score_pct: number; passing: number; failing: number; total: number } }) {
  const tone = agg.score_pct >= 80 ? "text-green-600" : agg.score_pct >= 50 ? "text-amber-600" : "text-red-600";
  const label = FRAMEWORK_LABEL[framework] ?? framework.toUpperCase();
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${tone}`}>{agg.score_pct}%</div>
      <div className="text-xs text-slate-500 mt-0.5">
        {agg.passing} / {agg.total} controls
      </div>
    </div>
  );
}

function SeverityBar({ label, count, total }: { label: string; count: number; total: number }) {
  const pct = total > 0 ? (count / total) * 100 : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="capitalize text-slate-600 w-16 text-xs">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full ${SEVERITY_COLORS[label]}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-500 w-8 text-right">{count}</span>
    </div>
  );
}
