import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { handleCallback } from "../lib/cognito";

export function Callback() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = params.get("code");
    if (!code) {
      setError("Sign-in was cancelled or returned no code.");
      return;
    }
    handleCallback(code)
      .then(() => nav("/", { replace: true }))
      .catch((e) => setError(`Sign-in failed: ${e.message ?? e}`));
  }, [params, nav]);

  return (
    <div className="min-h-screen flex items-center justify-center text-slate-600">
      {error ? <span className="text-red-600">{error}</span> : <span>Completing sign-in…</span>}
    </div>
  );
}
