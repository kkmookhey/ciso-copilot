import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isSignedIn, discoverTenantAndSignIn } from "../lib/cognito";
import { HeroLockup } from "../components/BrandLockup";

export function SignIn() {
  const nav = useNavigate();
  const [email,   setEmail]   = useState("");
  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    if (isSignedIn()) nav("/", { replace: true });
  }, [nav]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await discoverTenantAndSignIn(email.trim().toLowerCase());
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6 bg-slate-50">
      <HeroLockup chapter="Sign in.">
        <form onSubmit={onSubmit} className="space-y-3 max-w-sm mx-auto">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            required
            autoFocus
            disabled={busy}
            className="w-full bg-white border border-slate-300 rounded-lg px-4 py-3.5 text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
          <button
            type="submit"
            disabled={busy || !email}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white font-semibold rounded-lg px-6 py-4 transition"
          >
            {busy ? "Continuing…" : "Continue"}
          </button>
        </form>

        {error && (
          <p className="mt-4 text-sm text-red-600">{error}</p>
        )}

        <p className="mt-6 text-xs text-slate-500">
          Microsoft 365 or Google Workspace. Personal accounts not supported.
        </p>
      </HeroLockup>
    </div>
  );
}
