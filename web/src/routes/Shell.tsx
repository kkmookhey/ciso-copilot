import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, type MeResponse } from "../lib/api";
import { isSignedIn, signOut } from "../lib/cognito";

/// Auth gate + nav chrome for the post-signin views. Redirects to /signin if
/// not signed in, /pending if tenant status != approved.
export function Shell() {
  const nav = useNavigate();
  const loc = useLocation();
  const [me, setMe]   = useState<MeResponse | null>(null);
  const [loading, set] = useState(true);

  useEffect(() => {
    if (!isSignedIn()) { nav("/signin", { replace: true }); return; }
    api.me().then((r) => {
      setMe(r); set(false);
      if (r.tenant?.status === "pending")  nav("/pending", { replace: true });
      if (r.tenant?.status === "rejected") signOut();
    }).catch(() => signOut());
  }, [nav]);

  if (loading) return <FullPageSpinner />;

  return (
    <div className="min-h-screen flex">
      <aside className="w-64 bg-white border-r border-slate-200 p-6 flex flex-col">
        <div className="font-bold text-lg mb-1">CISO Copilot</div>
        <div className="text-xs text-slate-500 mb-8">{me?.tenant?.display_name ?? "—"}</div>

        <nav className="flex flex-col gap-1 text-sm">
          <NavItem to="/"          label="Overview"        active={loc.pathname === "/"} />
          <NavItem to="/findings"  label="Top Risks"       active={loc.pathname.startsWith("/findings")} />
          <NavItem to="/risks"     label="Risk register"   active={loc.pathname.startsWith("/risks")} />
          <NavItem to="/policies"       label="Policies"       active={loc.pathname.startsWith("/policies")} />
          <NavItem to="/questionnaires" label="Questionnaires" active={loc.pathname.startsWith("/questionnaires")} />
          <NavItem to="/trust"          label="Trust center"   active={loc.pathname.startsWith("/trust")} />
          <NavItem to="/connect"        label="Connect clouds" active={loc.pathname === "/connect"} />
          {isAdmin(me?.user?.email) && (
            <NavItem to="/admin"   label="Admin"           active={loc.pathname.startsWith("/admin")} />
          )}
        </nav>

        <div className="mt-auto pt-6 border-t border-slate-200 text-xs text-slate-500">
          <div>{me?.user?.email}</div>
          <button onClick={signOut} className="mt-2 hover:underline">Sign out</button>
        </div>
      </aside>
      <main className="flex-1 p-10 overflow-y-auto"><Outlet /></main>
    </div>
  );
}

const ADMIN_EMAILS = new Set([
  "kkmookhey@gmail.com",
  "kkmookhey@transilience.ai",
  "kkmookhey@networkintelligence.ai",
]);
function isAdmin(email: string | null | undefined): boolean {
  return !!email && ADMIN_EMAILS.has(email.toLowerCase());
}

function NavItem({ to, label, active }: { to: string; label: string; active: boolean }) {
  return (
    <Link
      to={to}
      className={`px-3 py-2 rounded-lg transition ${
        active ? "bg-blue-50 text-blue-700 font-medium" : "text-slate-700 hover:bg-slate-100"
      }`}
    >
      {label}
    </Link>
  );
}

function FullPageSpinner() {
  return (
    <div className="min-h-screen flex items-center justify-center text-slate-500">
      Loading…
    </div>
  );
}
