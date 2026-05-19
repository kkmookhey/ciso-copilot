import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isSignedIn, discoverTenantAndSignIn } from "../lib/cognito";

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
    <div className="min-h-screen flex flex-col items-center justify-center px-6 bg-gradient-to-b from-slate-50 to-slate-100">
      <div className="max-w-md w-full text-center">
        <div className="w-20 h-20 mx-auto mb-6 rounded-2xl bg-blue-500 text-white flex items-center justify-center text-3xl">
          ⛨
        </div>
        <h1 className="text-4xl font-bold tracking-tight">CISO Copilot</h1>
        <p className="text-slate-500 mt-2">Your cloud security, in one place.</p>

        <form onSubmit={onSubmit} className="mt-10 space-y-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            required
            autoFocus
            disabled={busy}
            className="w-full bg-white border border-slate-300 rounded-xl px-4 py-3.5 text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
          <button
            type="submit"
            disabled={busy || !email}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white font-semibold rounded-xl px-6 py-4 transition"
          >
            {busy ? "Continuing…" : "Continue"}
          </button>
        </form>

        {error && (
          <p className="mt-4 text-sm text-red-600">{error}</p>
        )}

        <p className="mt-4 text-xs text-slate-500">
          Microsoft 365 or Google Workspace. Personal accounts not supported.
        </p>
      </div>
    </div>
  );
}
