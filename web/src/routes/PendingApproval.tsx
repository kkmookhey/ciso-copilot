import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { signOut } from "../lib/cognito";
import { HeroLockup } from "../components/BrandLockup";

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
      <HeroLockup chapter="Pending review.">
        <p className="text-slate-600 max-w-md mx-auto leading-relaxed">
          We're reviewing your access request. You'll get an email when it's
          approved — typically within 24 hours.
        </p>
        <button
          onClick={signOut}
          className="mt-10 text-sm text-slate-500 hover:text-blue-600 hover:underline transition"
        >
          Sign out
        </button>
      </HeroLockup>
    </div>
  );
}
