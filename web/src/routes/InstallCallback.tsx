import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";

export function InstallCallback() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const installationIdRaw = params.get("installation_id");
    const state             = params.get("state");
    const setupAction       = params.get("setup_action") || "";

    if (setupAction === "request") {
      // GitHub redirects here when a non-owner asks an org admin to approve.
      // We can't proceed; tell the user to wait.
      setError("Awaiting admin approval on GitHub. We'll detect the install once approved.");
      return;
    }
    if (!installationIdRaw || !state) {
      setError("Missing installation_id or state. Reopen the Connect flow on the Connect page.");
      return;
    }
    const installationId = parseInt(installationIdRaw, 10);
    if (Number.isNaN(installationId)) {
      setError("Bad installation_id.");
      return;
    }

    api.completeGithubInstall(installationId, state)
       .then(({ connection_id }) => nav(`/ai/connections/${connection_id}/repos`, { replace: true }))
       .catch((e: Error) => setError(e.message || "Install failed."));
  }, [params, nav]);

  return (
    <div className="max-w-xl mx-auto py-20 text-center">
      {error
        ? <>
            <h1 className="text-xl font-semibold text-red-700">Install error</h1>
            <p className="mt-3 text-slate-700">{error}</p>
            <a href="/connect" className="mt-6 inline-block text-blue-700 hover:underline">← Back to Connect</a>
          </>
        : <>
            <h1 className="text-xl font-semibold">Finishing GitHub install…</h1>
            <p className="mt-3 text-slate-600">Hang tight, this takes a second.</p>
          </>}
    </div>
  );
}
