import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { handleCallback } from "../lib/cognito";
import { BrandLockup } from "../components/BrandLockup";

export function Callback() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const errParam = params.get("error");
    const errDesc  = params.get("error_description");
    if (errParam) {
      setError(`Sign-in failed (${errParam}): ${errDesc ?? "no description"}`);
      return;
    }
    const code = params.get("code");
    if (!code) {
      setError("Sign-in was cancelled or returned no code.");
      return;
    }
    handleCallback(code)
      .then(() => {
        const after = params.get("after");
        nav(after ? decodeURIComponent(after) : "/", { replace: true });
      })
      .catch((e) => setError(`Sign-in failed: ${e.message ?? e}`));
  }, [params, nav]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6 bg-slate-50 text-center">
      <BrandLockup className="text-[22px] font-semibold text-slate-900 tracking-tight mb-3" />
      <p className="text-base text-slate-600 mb-8">The Full Stack Security OS</p>
      {error
        ? <span className="text-red-600">{error}</span>
        : <span className="text-slate-500">Completing sign-in…</span>}
    </div>
  );
}
