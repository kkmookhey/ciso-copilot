import { useEffect, useState } from "react";
import { api, type PolicySummary, type Policy, type PolicyTemplate } from "../lib/api";

export function Policies() {
  const [list,      setList]      = useState<PolicySummary[] | null>(null);
  const [templates, setTemplates] = useState<PolicyTemplate[] | null>(null);
  const [openId,    setOpenId]    = useState<string | null>(null);
  const [openTitle, setOpenTitle] = useState<string | null>(null);
  const [showNew,   setShowNew]   = useState(false);
  const [showBulk,  setShowBulk]  = useState(false);
  const [err,       setErr]       = useState<string | null>(null);

  async function reload() {
    setList(null);
    setErr(null);
    try {
      const [l, t] = await Promise.all([api.listPolicies(), api.listPolicyTemplates()]);
      setList(l.policies);
      setTemplates(t.templates);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setList([]);
    }
  }

  useEffect(() => { reload(); }, []);

  return (
    <div className="max-w-6xl">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <h1 className="text-3xl font-bold tracking-tight">Policies</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowBulk(true)}
            title="Generate all 8 standard policies, pre-personalized to your posture"
            className="px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-medium transition"
          >
            ✨ Generate all
          </button>
          <button
            onClick={() => setShowNew(true)}
            className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition"
          >
            + From template
          </button>
        </div>
      </div>
      <p className="text-slate-600 mt-1">
        Policy documents required for SOC 2 / ISO 27001. Generate from template, edit inline, approve.
      </p>

      {err && <div className="mt-4 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{err}</div>}

      <div className="mt-6 rounded-2xl border border-slate-200 bg-white overflow-hidden">
        {list === null ? (
          <p className="text-slate-500 p-6 text-sm">Loading…</p>
        ) : list.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-slate-600">No policies yet.</p>
            <p className="text-xs text-slate-400 mt-2">
              Click “+ From template” to spin up an Access Control / Incident Response /
              Data Classification / Vendor Management / Change Management doc.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {list.map((p) => (
              <li key={p.policy_id}>
                <button
                  onClick={() => { setOpenId(p.policy_id); setOpenTitle(p.title); }}
                  className="w-full text-left p-4 hover:bg-slate-50 transition flex items-center justify-between"
                >
                  <div>
                    <div className="font-semibold">{p.title}</div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      v{p.version} · {p.soc2_controls.join(", ")} · updated {new Date(p.updated_at).toLocaleString()}
                    </div>
                  </div>
                  <StatusPill status={p.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {openId && (
        <PolicyEditor
          policyId={openId}
          initialTitle={openTitle ?? undefined}
          onClose={() => { setOpenId(null); setOpenTitle(null); }}
          onSaved={reload}
        />
      )}
      {showNew && templates && (
        <NewPolicyModal
          templates={templates}
          onClose={() => setShowNew(false)}
          onCreated={(id) => { setShowNew(false); setOpenId(id); setOpenTitle(null); reload(); }}
        />
      )}
      {showBulk && (
        <BulkGenerateModal
          onClose={() => setShowBulk(false)}
          onDone={() => { setShowBulk(false); reload(); }}
        />
      )}
    </div>
  );
}

function BulkGenerateModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [companyName,   setCompanyName]   = useState("Acme Inc.");
  const [effectiveDate, setEffectiveDate] = useState(new Date().toISOString().split("T")[0]);
  const [approver,      setApprover]      = useState("");
  const [busy,          setBusy]          = useState(false);
  const [err,           setErr]           = useState<string | null>(null);
  const [progress,      setProgress]      = useState<string>("");

  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setProgress("Generating 8 policies. AI personalization runs in parallel — this takes ~30–90s.");
    try {
      const r = await api.generateAllPolicies({
        company_name:   companyName.trim(),
        effective_date: effectiveDate,
        approver:       approver.trim() || undefined,
      });
      const enriched = r.policies.filter((p) => p.enriched).length;
      setProgress(`✓ Created ${r.count} policies (${enriched} AI-enriched).`);
      // Give the user a moment to read the success state.
      setTimeout(onDone, 700);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <form onSubmit={go} className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
        <h2 className="text-xl font-bold">Generate all policies</h2>
        <p className="text-sm text-slate-600 mt-1">
          Spins up all 8 standard policies (Access Control, Incident Response,
          Data Classification, Vendor Management, Change Management, Security
          Awareness, BCP/DR, Vulnerability Management). Each is AI-personalized
          to your actual cloud footprint + open findings.
        </p>
        <div className="mt-4 space-y-3 text-sm">
          <label className="block">
            <span className="text-slate-600">Company name</span>
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              required
              disabled={busy}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Effective date</span>
            <input
              type="date"
              value={effectiveDate}
              onChange={(e) => setEffectiveDate(e.target.value)}
              disabled={busy}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Approver (optional)</span>
            <input
              value={approver}
              onChange={(e) => setApprover(e.target.value)}
              placeholder="e.g. Jane Doe, CISO"
              disabled={busy}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
        </div>
        {progress && <p className="mt-3 text-sm text-slate-600">{progress}</p>}
        {err && <p className="mt-3 text-red-600 text-xs">{err}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className="px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-sm">
            Cancel
          </button>
          <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 disabled:bg-slate-300 text-white text-sm font-medium">
            {busy ? "Generating…" : "✨ Generate all 8"}
          </button>
        </div>
      </form>
    </div>
  );
}

function StatusPill({ status }: { status: PolicySummary["status"] }) {
  const cls = {
    draft:    "bg-amber-50 text-amber-700",
    approved: "bg-green-50 text-green-700",
    retired:  "bg-slate-100 text-slate-600",
  }[status];
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{status}</span>;
}

function NewPolicyModal({ templates, onClose, onCreated }: {
  templates: PolicyTemplate[];
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [key,           setKey]           = useState(templates[0]?.key ?? "");
  const [companyName,   setCompanyName]   = useState("Acme Inc.");
  const [effectiveDate, setEffectiveDate] = useState(new Date().toISOString().split("T")[0]);
  const [approver,      setApprover]      = useState("");
  const [busy,          setBusy]          = useState(false);
  const [err,           setErr]           = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.createPolicy({
        template_key: key,
        vars: { company_name: companyName, effective_date: effectiveDate, approver },
      });
      onCreated(r.policy_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
      <form onSubmit={submit} className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
        <h2 className="text-xl font-bold">New policy from template</h2>
        <div className="mt-4 space-y-3 text-sm">
          <label className="block">
            <span className="text-slate-600">Template</span>
            <select
              value={key}
              onChange={(e) => setKey(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            >
              {templates.map((t) => (
                <option key={t.key} value={t.key}>{t.title}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-slate-600">Company name</span>
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              required
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Effective date</span>
            <input
              type="date"
              value={effectiveDate}
              onChange={(e) => setEffectiveDate(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block">
            <span className="text-slate-600">Approver</span>
            <input
              value={approver}
              onChange={(e) => setApprover(e.target.value)}
              placeholder="e.g. Jane Doe, CISO"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
        </div>
        {err && <p className="mt-3 text-red-600 text-xs">{err}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-sm">
            Cancel
          </button>
          <button type="submit" disabled={busy} className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-sm font-medium">
            {busy ? "Generating…" : "Generate"}
          </button>
        </div>
      </form>
    </div>
  );
}

function PolicyEditor({ policyId, initialTitle, onClose, onSaved }: {
  policyId: string;
  initialTitle?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [body,   setBody]   = useState("");
  const [status, setStatus] = useState<Policy["status"]>("draft");
  const [busy,   setBusy]   = useState(false);
  const [err,    setErr]    = useState<string | null>(null);

  useEffect(() => {
    api.getPolicy(policyId).then((p) => {
      setPolicy(p);
      setBody(p.content_md);
      setStatus(p.status);
    }).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [policyId]);

  async function save() {
    if (!policy) return;
    setBusy(true);
    setErr(null);
    try {
      const patch: { content_md?: string; status?: string } = {};
      if (body   !== policy.content_md) patch.content_md = body;
      if (status !== policy.status)     patch.status     = status;
      if (Object.keys(patch).length > 0) {
        await api.updatePolicy(policyId, patch);
        onSaved();
      }
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  async function enrich() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.enrichPolicy(policyId);
      setBody(r.content_md);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-stretch justify-center p-4 z-50">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-4xl flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-slate-200">
          <div>
            <h2 className="font-semibold">{policy?.title ?? initialTitle ?? "Loading…"}</h2>
            {policy && (
              <p className="text-xs text-slate-500 mt-0.5">
                v{policy.version} · {policy.soc2_controls.join(", ")}
              </p>
            )}
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">✕</button>
        </div>

        {err && <div className="mx-4 mt-3 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{err}</div>}

        <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-3 p-4 overflow-y-auto">
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="font-mono text-xs p-3 border border-slate-200 rounded-lg w-full min-h-[400px] resize-vertical"
            placeholder="Markdown source"
          />
          <div className="prose prose-sm max-w-none border border-slate-100 rounded-lg p-3 bg-slate-50 overflow-y-auto whitespace-pre-wrap font-sans text-sm">
            {body}
          </div>
        </div>

        <div className="flex items-center justify-between p-4 border-t border-slate-200 flex-wrap gap-3">
          <label className="text-sm flex items-center gap-2">
            Status:
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as Policy["status"])}
              className="rounded-md border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="draft">draft</option>
              <option value="approved">approved</option>
              <option value="retired">retired</option>
            </select>
          </label>
          <div className="flex gap-2">
            <button
              onClick={enrich}
              disabled={busy}
              title="Rewrite the policy specifically for your posture, using Claude"
              className="px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 disabled:bg-slate-300 text-white text-sm font-medium transition"
            >
              {busy ? "…" : "✨ Enrich with AI"}
            </button>
            <button onClick={onClose} className="px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-sm">
              Cancel
            </button>
            <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-sm font-medium">
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
