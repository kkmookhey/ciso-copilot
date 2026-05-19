import { useEffect, useState } from "react";
import { api, type AdminTenantRow } from "../lib/api";

type StatusFilter = "pending" | "approved" | "rejected" | "all";

export function Admin() {
  const [filter,  setFilter]  = useState<StatusFilter>("pending");
  const [tenants, setTenants] = useState<AdminTenantRow[] | null>(null);
  const [busy,    setBusy]    = useState<string | null>(null);
  const [err,     setErr]     = useState<string | null>(null);

  async function reload(f: StatusFilter = filter) {
    setTenants(null);
    setErr(null);
    try {
      const r = await api.adminListTenants(f);
      setTenants(r.tenants);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg);
      setTenants([]);
    }
  }

  useEffect(() => { reload(filter); /* eslint-disable-next-line */ }, [filter]);

  async function act(tenantId: string, decision: "approve" | "reject") {
    setBusy(tenantId);
    setErr(null);
    try {
      const r = await api.adminTenantAction(tenantId, decision);
      console.log("action result", r);
      await reload();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErr(msg);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="max-w-5xl">
      <h1 className="text-3xl font-bold tracking-tight">Admin — Tenants</h1>
      <p className="text-slate-600 mt-1">
        Approve or reject new tenant sign-ups in-app. Restricted to platform admins.
      </p>

      <div className="mt-6 flex flex-wrap gap-2">
        {(["pending", "approved", "rejected", "all"] as StatusFilter[]).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1.5 rounded-full text-sm transition ${
              filter === s
                ? "bg-blue-600 text-white"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      {err && (
        <div className="mt-4 p-3 rounded-lg bg-red-50 text-red-700 text-sm">
          {err.includes("not_an_admin")
            ? "Your account isn't on the admin list. Ask KK to add you."
            : err}
        </div>
      )}

      <div className="mt-6 rounded-2xl border border-slate-200 bg-white overflow-hidden">
        {tenants === null ? (
          <p className="text-slate-500 p-6 text-sm">Loading…</p>
        ) : tenants.length === 0 ? (
          <p className="text-slate-500 p-6 text-sm">No tenants with status “{filter}”.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="text-left py-3 px-4">Tenant</th>
                <th className="text-left py-3 px-4">First user</th>
                <th className="text-left py-3 px-4">Status</th>
                <th className="text-left py-3 px-4">Created</th>
                <th className="text-right py-3 px-4">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {tenants.map((t) => (
                <tr key={t.tenant_id}>
                  <td className="py-3 px-4">
                    <div className="font-medium">{t.display_name}</div>
                    <div className="text-xs text-slate-500 font-mono">{t.tenant_id.slice(0, 8)}…</div>
                  </td>
                  <td className="py-3 px-4 text-slate-600">{t.first_user ?? "—"}</td>
                  <td className="py-3 px-4"><StatusPill status={t.status} /></td>
                  <td className="py-3 px-4 text-slate-500 text-xs">{new Date(t.created_at).toLocaleString()}</td>
                  <td className="py-3 px-4 text-right space-x-2">
                    {t.status === "pending" ? (
                      <>
                        <button
                          onClick={() => act(t.tenant_id, "approve")}
                          disabled={busy === t.tenant_id}
                          className="px-3 py-1 rounded-md bg-green-600 hover:bg-green-700 disabled:bg-slate-300 text-white text-xs font-medium transition"
                        >
                          {busy === t.tenant_id ? "…" : "Approve"}
                        </button>
                        <button
                          onClick={() => act(t.tenant_id, "reject")}
                          disabled={busy === t.tenant_id}
                          className="px-3 py-1 rounded-md bg-red-600 hover:bg-red-700 disabled:bg-slate-300 text-white text-xs font-medium transition"
                        >
                          Reject
                        </button>
                      </>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: AdminTenantRow["status"] }) {
  const cls = {
    pending:    "bg-amber-50 text-amber-700",
    approved:   "bg-green-50 text-green-700",
    rejected:   "bg-red-50 text-red-700",
    suspended:  "bg-slate-100 text-slate-600",
  }[status];
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{status}</span>;
}
