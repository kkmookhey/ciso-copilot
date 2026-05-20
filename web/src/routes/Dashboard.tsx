import { Link, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { VoiceChat } from "../voice/VoiceChat";
import {
  PieChart, Pie, Cell, Tooltip as RTooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { api, type Connection, type AlertEvent, type ComplianceSummary, type FindingsSummary } from "../lib/api";

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#dc2626",
  high:     "#f97316",
  medium:   "#eab308",
  low:      "#64748b",
  info:     "#94a3b8",
};

const CLOUD_COLORS: Record<string, string> = {
  aws:   "#f97316",
  azure: "#0078d4",
  gcp:   "#4285f4",
  entra: "#737e8c",
};

const FRAMEWORK_LABEL: Record<string, string> = {
  soc2:      "SOC 2",
  cis_aws:   "CIS AWS",
  cis_azure: "CIS Azure",
  cis_gcp:   "CIS GCP",
  mcsb:      "MCSB",
  iso27001:  "ISO 27001",
  hipaa:     "HIPAA",
};

export function Dashboard() {
  const nav = useNavigate();
  const [conns,      setConns]      = useState<Connection[] | null>(null);
  const [findings,   setFindings]   = useState<number | null>(null);
  const [alerts,     setAlerts]     = useState<AlertEvent[] | null>(null);
  const [critical,   setCritical]   = useState<number | null>(null);
  const [compliance, setCompliance] = useState<ComplianceSummary | null>(null);
  const [summary,    setSummary]    = useState<FindingsSummary | null>(null);
  const [showVoice,  setShowVoice]  = useState(false);
  const [openAlert,  setOpenAlert]  = useState<AlertEvent | null>(null);

  useEffect(() => {
    api.listConnections().then((r) => setConns(r.connections)).catch(() => setConns([]));
    api.listFindings({ limit: 1 }).then((r) => setFindings(r.total)).catch(() => setFindings(null));
    api.listEvents({ limit: 5 }).then((r) => setAlerts(r.events)).catch(() => setAlerts([]));
    api.listEvents({ severity: "critical,high", kind: "alert", limit: 1 })
       .then((r) => setCritical(r.total))
       .catch(() => setCritical(null));
    api.complianceSummary().then(setCompliance).catch(() => setCompliance(null));
    api.findingsSummary().then(setSummary).catch(() => setSummary(null));
  }, []);

  const activeConns = conns?.filter((c) => c.status === "active").length;

  const severitySlices = summary
    ? Object.entries(summary.by_severity)
        .filter(([, n]) => n > 0)
        .map(([sev, n]) => ({ name: sev, value: n }))
    : [];

  const cloudBars = summary
    ? Object.entries(summary.by_cloud)
        .filter(([, n]) => n > 0)
        .map(([cloud, n]) => ({ name: cloud.toUpperCase(), key: cloud, value: n }))
    : [];

  return (
    <div className="max-w-6xl">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Welcome</h1>
          <p className="text-slate-600 mt-1">
            Live posture, real-time signals, and compliance snapshot across your connected clouds.
          </p>
        </div>
        <button
          onClick={() => setShowVoice(true)}
          className="mt-1 flex items-center gap-2 px-4 py-2 rounded-full bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium shadow-sm transition"
          aria-label="Open voice chat"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
            <path d="M12 14a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3z" />
            <path d="M19 11a1 1 0 10-2 0 5 5 0 11-10 0 1 1 0 10-2 0 7 7 0 006 6.92V21h-2a1 1 0 100 2h6a1 1 0 100-2h-2v-3.08A7 7 0 0019 11z" />
          </svg>
          Voice
        </button>
      </div>

      {showVoice && <VoiceChat onClose={() => setShowVoice(false)} />}
      {openAlert && <AlertDetailModal event={openAlert} onClose={() => setOpenAlert(null)} />}

      {/* Headline stats */}
      <div className="mt-10 grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatTile label="Connected clouds" value={activeConns ?? "—"} onClick={() => nav("/connect")} />
        <StatTile label="Open findings"    value={findings ?? "—"} onClick={() => nav("/findings")} />
        <StatTile label="Critical alerts"  value={critical ?? "—"} tone="red" />
      </div>

      {/* Risk distribution + by cloud */}
      {summary && summary.total > 0 && (
        <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
          <ChartCard title="Risk distribution" subtitle={`${summary.total} open findings`}>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={severitySlices}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={50}
                  outerRadius={85}
                  paddingAngle={2}
                  onClick={(d: unknown) => {
                    const name = (d as { name?: string })?.name;
                    if (name) nav(`/findings?severity=${name}`);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  {severitySlices.map((s) => (
                    <Cell key={s.name} fill={SEVERITY_COLORS[s.name] ?? "#94a3b8"} />
                  ))}
                </Pie>
                <RTooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
            <Legend items={severitySlices.map((s) => ({ label: s.name, color: SEVERITY_COLORS[s.name], value: s.value }))} />
          </ChartCard>

          <ChartCard title="By cloud" subtitle="Open findings per connected cloud">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={cloudBars} margin={{ top: 8, right: 8, left: -16, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                <RTooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                <Bar
                  dataKey="value"
                  radius={[6, 6, 0, 0]}
                  onClick={(d: unknown) => {
                    const key = (d as { key?: string })?.key;
                    if (key) nav(`/findings?cloud=${key}`);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  {cloudBars.map((b) => (
                    <Cell key={b.key} fill={CLOUD_COLORS[b.key] ?? "#94a3b8"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
      )}

      {/* Compliance posture — clickable framework tiles */}
      {compliance && Object.keys(compliance.summary).length > 0 && (
        <div className="mt-10">
          <h2 className="font-semibold text-lg mb-3">Compliance posture</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {Object.entries(compliance.summary)
              .sort(([, a], [, b]) => b.total - a.total)
              .map(([framework, agg]) => (
                <FrameworkCard
                  key={framework}
                  framework={framework}
                  agg={agg}
                  onClick={() => nav(`/findings?framework=${framework}`)}
                />
              ))}
          </div>
        </div>
      )}

      {/* Recent activity */}
      <div className="mt-10 p-6 rounded-2xl border border-slate-200 bg-white">
        <h2 className="font-semibold text-lg">Recent activity</h2>
        {alerts === null ? (
          <p className="text-slate-500 mt-3 text-sm">Loading…</p>
        ) : alerts.length === 0 ? (
          <p className="text-slate-500 mt-3 text-sm">No alerts yet. Real-time signals show up here as your connected clouds emit them.</p>
        ) : (
          <ul className="mt-4 divide-y divide-slate-100">
            {alerts.map((e) => (
              <li key={e.event_id}>
                <button
                  type="button"
                  onClick={() => setOpenAlert(e)}
                  className="w-full text-left py-3 px-2 -mx-2 rounded-lg flex items-start gap-3 text-sm hover:bg-slate-50 transition"
                >
                  <SeverityDot severity={e.severity} />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate">{e.title}</div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      {e.kind} · {e.source} · {new Date(e.fired_at).toLocaleString()}
                    </div>
                  </div>
                  <span className="text-slate-300 text-xs">→</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Cloud connections */}
      <div className="mt-10 p-6 rounded-2xl border border-slate-200 bg-white">
        <h2 className="font-semibold text-lg">Your cloud connections</h2>
        {conns === null ? (
          <p className="text-slate-500 mt-3 text-sm">Loading…</p>
        ) : conns.length === 0 ? (
          <div className="mt-4">
            <p className="text-slate-600">Nothing connected yet.</p>
            <Link to="/connect" className="inline-block mt-3 text-blue-600 hover:underline">
              Connect your first cloud →
            </Link>
          </div>
        ) : (
          <ul className="mt-4 divide-y divide-slate-100">
            {conns.map((c) => (
              <li key={c.conn_id} className="py-3 flex items-center justify-between text-sm">
                <div>
                  <div className="font-medium">{c.display_name}</div>
                  <div className="text-slate-500 text-xs">
                    {c.cloud_type.toUpperCase()} · {c.account_identifier ?? "—"}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <StatusPill status={c.status} />
                  {(c.status === "pending" || c.status === "error") && (
                    <button
                      type="button"
                      onClick={async () => {
                        if (!window.confirm(
                          `Delete this ${c.status} ${c.cloud_type.toUpperCase()} connection? This cannot be undone.`,
                        )) return;
                        try {
                          await api.deleteConnection(c.conn_id);
                          const r = await api.listConnections();
                          setConns(r.connections);
                        } catch (e) {
                          window.alert(`Delete failed: ${(e as Error).message}`);
                        }
                      }}
                      className="text-xs text-red-600 hover:underline"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function StatTile({ label, value, onClick, tone }: { label: string; value: string | number; onClick?: () => void; tone?: "red" }) {
  const interactive = !!onClick;
  const valueClass = tone === "red" ? "text-red-600" : "text-slate-900";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!interactive}
      className={`text-left rounded-2xl bg-white border border-slate-200 p-5 transition w-full
        ${interactive ? "hover:border-blue-400 cursor-pointer" : "cursor-default"}`}
    >
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className={`text-3xl font-bold mt-1 ${valueClass}`}>{value}</div>
    </button>
  );
}

function ChartCard({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl bg-white border border-slate-200 p-5">
      <div className="flex items-baseline justify-between">
        <h3 className="font-semibold">{title}</h3>
        {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function Legend({ items }: { items: { label: string; color: string; value: number }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-slate-600">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: i.color }} />
          <span className="capitalize">{i.label}</span>
          <span className="text-slate-400">{i.value}</span>
        </li>
      ))}
    </ul>
  );
}

function FrameworkCard({ framework, agg, onClick }: { framework: string; agg: { total: number; passing: number; failing: number; score_pct: number }; onClick?: () => void }) {
  const tone = agg.score_pct >= 80 ? "text-green-600" : agg.score_pct >= 50 ? "text-amber-600" : "text-red-600";
  const label = FRAMEWORK_LABEL[framework] ?? framework.toUpperCase();
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-left rounded-2xl border border-slate-200 bg-white p-5 w-full hover:border-blue-400 transition cursor-pointer"
    >
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className={`text-3xl font-bold mt-1 ${tone}`}>{agg.score_pct}%</div>
      <div className="text-xs text-slate-500 mt-1">
        {agg.passing} passing · {agg.failing} failing · {agg.total} controls
      </div>
    </button>
  );
}

function AlertDetailModal({ event, onClose }: { event: AlertEvent; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[85vh] flex flex-col">
        <div className="flex items-start justify-between gap-4 p-5 border-b border-slate-200">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            <SeverityDot severity={event.severity} />
            <div className="flex-1 min-w-0">
              <h2 className="font-semibold leading-tight">{event.title}</h2>
              <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3">
                <span className="capitalize">{event.severity}</span>
                <span>·</span>
                <span>{event.kind}</span>
                <span>·</span>
                <span className="font-mono">{event.source}</span>
              </div>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-lg">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4 text-sm">
          {event.description && (
            <Section title="Description">
              <p className="text-slate-700 whitespace-pre-wrap">{event.description}</p>
            </Section>
          )}

          {event.resource_arn && (
            <Section title="Resource">
              <code className="font-mono text-xs text-slate-700 break-all bg-slate-50 px-2 py-1 rounded">
                {event.resource_arn}
              </code>
            </Section>
          )}

          {event.actor && (
            <Section title="Actor">
              <code className="font-mono text-xs text-slate-700 break-all bg-slate-50 px-2 py-1 rounded">
                {event.actor}
              </code>
            </Section>
          )}

          <Section title="Timeline">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-slate-500">Fired at</dt>
              <dd>{new Date(event.fired_at).toLocaleString()}</dd>
              <dt className="text-slate-500">Ingested</dt>
              <dd>{new Date(event.ingested_at).toLocaleString()}</dd>
            </dl>
          </Section>

          <Section title="Identifier">
            <code className="font-mono text-xs text-slate-500">{event.event_id}</code>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function SeverityDot({ severity }: { severity: AlertEvent["severity"] }) {
  const color = {
    critical: "bg-red-500",
    high:     "bg-amber-500",
    medium:   "bg-yellow-500",
    low:      "bg-slate-400",
    info:     "bg-slate-300",
  }[severity];
  return <span className={`mt-1.5 w-2 h-2 rounded-full ${color} shrink-0`} />;
}

function StatusPill({ status }: { status: Connection["status"] }) {
  const styles = {
    active:  "bg-green-50 text-green-700",
    pending: "bg-amber-50 text-amber-700",
    error:   "bg-red-50 text-red-700",
    revoked: "bg-slate-100 text-slate-600",
  }[status];
  return <span className={`px-2 py-1 rounded-full text-xs font-medium ${styles}`}>{status}</span>;
}
