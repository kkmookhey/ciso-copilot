import { useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { api, type MeResponse } from "../lib/api";
import { isSignedIn, signOut } from "../lib/cognito";
import { ModuleRail } from "../chat/ModuleRail";

/// Auth gate + nav chrome for the post-signin views. Redirects to /signin if
/// not signed in, /pending if tenant status != approved.
export function Shell() {
  const nav = useNavigate();
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
    <div style={{ display: "flex", height: "100vh" }}>
      <ModuleRail
        email={me?.user?.email ?? ""}
        isAdmin={isAdmin(me?.user?.email)}
      />
      <main style={{ flex: 1, overflowY: "auto", background: "#FAF8F3",
                     padding: "32px 40px" }}>
        <Outlet />
      </main>
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

function FullPageSpinner() {
  return (
    <div style={{ minHeight: "100vh", display: "flex",
                  alignItems: "center", justifyContent: "center",
                  color: "#7A7268" }}>
      Loading…
    </div>
  );
}
