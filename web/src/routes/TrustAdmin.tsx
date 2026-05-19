import { useEffect, useState } from "react";
import { api, type TrustPageSettings } from "../lib/api";

const PUBLIC_BASE = window.location.origin;

export function TrustAdmin() {
  const [settings, setSettings] = useState<TrustPageSettings | null>(null);
  const [loaded,   setLoaded]   = useState(false);
  const [busy,     setBusy]     = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [saved,    setSaved]    = useState(false);

  useEffect(() => {
    api.getTrustPage().then((r) => {
      setSettings(r.page ?? defaultSettings());
      setLoaded(true);
    }).catch((e) => {
      setError(e instanceof Error ? e.message : String(e));
      setSettings(defaultSettings());
      setLoaded(true);
    });
  }, []);

  function defaultSettings(): TrustPageSettings {
    return {
      slug:                 "",
      public_name:          "",
      notes:                null,
      is_published:         false,
      show_compliance:      true,
      show_finding_counts:  true,
      show_clouds:          true,
      show_last_scan:       true,
    };
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!settings) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api.putTrustPage(settings);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!loaded || !settings) {
    return <div className="p-6 text-slate-500">Loading…</div>;
  }

  const publicUrl = settings.slug ? `${PUBLIC_BASE}/public/trust/${settings.slug}` : "";

  return (
    <div className="max-w-3xl">
      <h1 className="text-3xl font-bold tracking-tight">Trust center</h1>
      <p className="text-slate-600 mt-1">
        Publish a redacted posture summary at a public URL. Prospects and
        customers see aggregate scores; specific resources, IPs, and identifiers
        stay private.
      </p>

      {error && <div className="mt-4 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}

      <form onSubmit={save} className="mt-8 space-y-6">
        <Section title="Public URL slug" subtitle="Lowercase letters, numbers, and hyphens. 3–62 chars.">
          <div className="flex items-center gap-2">
            <span className="text-slate-500 text-sm">{PUBLIC_BASE}/public/trust/</span>
            <input
              value={settings.slug}
              onChange={(e) => setSettings({ ...settings, slug: e.target.value.toLowerCase() })}
              placeholder="acme-inc"
              pattern="^[a-z0-9][a-z0-9-]{1,60}[a-z0-9]$"
              required
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
            />
          </div>
        </Section>

        <Section title="Public name" subtitle="Shown as the page heading.">
          <input
            value={settings.public_name}
            onChange={(e) => setSettings({ ...settings, public_name: e.target.value })}
            placeholder="Acme Inc. — Security Posture"
            required
            className="w-full rounded-md border border-slate-300 px-3 py-2"
          />
        </Section>

        <Section title="Public notes" subtitle="Short markdown intro shown above the metrics. Optional.">
          <textarea
            value={settings.notes ?? ""}
            onChange={(e) => setSettings({ ...settings, notes: e.target.value || null })}
            rows={4}
            placeholder="We take security seriously. Below is our live posture, updated daily."
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </Section>

        <Section title="What to expose" subtitle="Everything is aggregate / redacted — no resource IDs or finding details.">
          <div className="space-y-2">
            <Toggle label="Compliance posture (SOC 2, CIS, etc.)" value={settings.show_compliance}
              onChange={(v) => setSettings({ ...settings, show_compliance: v })} />
            <Toggle label="Finding counts (by severity)" value={settings.show_finding_counts}
              onChange={(v) => setSettings({ ...settings, show_finding_counts: v })} />
            <Toggle label="Connected cloud count" value={settings.show_clouds}
              onChange={(v) => setSettings({ ...settings, show_clouds: v })} />
            <Toggle label="Last scan timestamp" value={settings.show_last_scan}
              onChange={(v) => setSettings({ ...settings, show_last_scan: v })} />
          </div>
        </Section>

        <Section title="Publish" subtitle="Off by default. When published, the public URL above starts serving.">
          <Toggle
            label={settings.is_published ? "Published — public URL is live" : "Not published"}
            value={settings.is_published}
            onChange={(v) => setSettings({ ...settings, is_published: v })}
            big
          />
          {publicUrl && (
            <div className="mt-3 text-sm">
              <span className="text-slate-500">Public URL:</span>{" "}
              <a href={publicUrl} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline font-mono">
                {publicUrl}
              </a>
            </div>
          )}
        </Section>

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={busy}
            className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-sm font-medium transition"
          >
            {busy ? "Saving…" : "Save"}
          </button>
          {saved && <span className="text-sm text-green-600">✓ Saved</span>}
        </div>
      </form>
    </div>
  );
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm font-semibold">{title}</div>
      {subtitle && <div className="text-xs text-slate-500 mt-0.5">{subtitle}</div>}
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Toggle({ label, value, onChange, big }: { label: string; value: boolean; onChange: (v: boolean) => void; big?: boolean }) {
  return (
    <label className={`flex items-center gap-3 cursor-pointer ${big ? "" : "text-sm"}`}>
      <button
        type="button"
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
        className={`relative inline-flex shrink-0 ${big ? "h-7 w-12" : "h-5 w-9"} items-center rounded-full transition ${
          value ? "bg-blue-600" : "bg-slate-300"
        }`}
      >
        <span
          className={`inline-block ${big ? "h-5 w-5" : "h-3.5 w-3.5"} transform rounded-full bg-white transition ${
            value ? (big ? "translate-x-6" : "translate-x-5") : "translate-x-1"
          }`}
        />
      </button>
      <span>{label}</span>
    </label>
  );
}
