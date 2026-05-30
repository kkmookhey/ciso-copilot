import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useConnectors } from "../../lib/useConnectors";
import { ConnectorCard } from "../../components/connectors/ConnectorCard";

export function ConnectorsTab() {
  const { connectors, reload } = useConnectors();
  const [params, setParams] = useSearchParams();

  // ?ok=slack toast after OAuth callback redirect
  useEffect(() => {
    if (params.get("ok")) {
      reload();
      const t = setTimeout(() => {
        const next = new URLSearchParams(params);
        next.delete("ok");
        setParams(next, { replace: true });
      }, 4000);
      return () => clearTimeout(t);
    }
  }, [params, reload, setParams]);

  const byProvider = Object.fromEntries((connectors ?? []).map(c => [c.provider, c]));

  return (
    <div>
      <p className="text-sm text-neutral-600 mb-7 max-w-2xl">
        Connect productivity tools so Shasta can act on your behalf — file
        tickets, send messages, draft email — using your identity in each tool.
        Each analyst connects their own. Revoke anytime.
      </p>

      {params.get("ok") && (
        <div className="mb-5 text-[13px] bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-md px-3 py-2">
          Connected {params.get("ok")} successfully.
        </div>
      )}

      <div className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500 mb-3">
        Your connectors
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
        <ConnectorCard kind="slack"     connector={byProvider.slack}     onChange={reload} />
        <ConnectorCard kind="atlassian" connector={byProvider.atlassian} onChange={reload} />
        <ConnectorCard kind="google"    connector={byProvider.google}    onChange={reload} />
        <ConnectorCard kind="microsoft" connector={byProvider.microsoft} onChange={reload} />
      </div>
    </div>
  );
}
