import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { signOut } from "../lib/cognito";

export function PendingApproval() {
  const nav = useNavigate();

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        if (me.tenant?.status === "approved") nav("/", { replace: true });
        if (me.tenant?.status === "rejected") signOut();
      } catch { /* keep polling */ }
    };
    tick();
    const id = setInterval(tick, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [nav]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6 bg-slate-50">
      <div className="max-w-md w-full text-center">
        <div className="w-16 h-16 mx-auto mb-6 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center text-2xl">
          ⏳
        </div>
        <h1 className="text-2xl font-bold">Access request pending</h1>
        <p className="text-slate-600 mt-2">
          We're reviewing your access request. You'll get an email when it's
          approved — typically within 24 hours.
        </p>
        <button onClick={signOut} className="mt-10 text-sm text-slate-500 hover:underline">
          Sign out
        </button>
      </div>
    </div>
  );
}
