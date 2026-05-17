import { useState } from "react";
import { api } from "../lib/api";

/// Phase A onboarding wizard: AWS only for now. Azure/Entra/GCP land in
/// Phases B/C/D — surfaced as disabled tiles for visibility.
export function ConnectClouds() {
  const [pending, setPending]   = useState(false);
  const [cfnUrl, setCfnUrl]     = useState<string | null>(null);
  const [error, setError]       = useState<string | null>(null);

  async function connectAws() {
    setPending(true); setError(null);
    try {
      const r = await api.initiateAwsOnboarding("AWS Account");
      setCfnUrl(r.cfn_url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-3xl font-bold tracking-tight">Connect a cloud</h1>
      <p className="text-slate-600 mt-1">Pick a cloud to start scanning.</p>

      <div className="mt-10 grid grid-cols-2 gap-4">
        <CloudTile
          name="AWS"
          tagline="Cross-account read-only role via CloudFormation"
          enabled={true}
          loading={pending}
          onClick={connectAws}
        />
        <CloudTile name="Azure"  tagline="Coming Phase B" enabled={false} />
        <CloudTile name="Entra"  tagline="Coming Phase C" enabled={false} />
        <CloudTile name="GCP"    tagline="Coming Phase D" enabled={false} />
      </div>

      {cfnUrl && (
        <div className="mt-10 p-6 rounded-2xl border-2 border-blue-200 bg-blue-50">
          <h2 className="font-semibold text-lg">One-click AWS connection</h2>
          <p className="text-sm text-slate-700 mt-2">
            Open the link below — it deep-links you into the AWS CloudFormation
            console with our template + your one-time external ID pre-filled.
            Review the resources it creates (an IAM role, an EventBridge rule,
            and optional AWS Config recorder), then click Create.
          </p>
          <a
            href={cfnUrl}
            target="_blank" rel="noopener noreferrer"
            className="mt-4 inline-block bg-blue-600 hover:bg-blue-700 text-white font-medium px-5 py-2.5 rounded-lg"
          >
            Launch CloudFormation →
          </a>
        </div>
      )}

      {error && <p className="mt-6 text-red-600 text-sm">{error}</p>}
    </div>
  );
}

function CloudTile({
  name, tagline, enabled, loading, onClick,
}: {
  name: string; tagline: string; enabled: boolean;
  loading?: boolean; onClick?: () => void;
}) {
  return (
    <button
      disabled={!enabled || loading}
      onClick={onClick}
      className={`text-left rounded-2xl border p-5 transition ${
        enabled
          ? "border-slate-200 bg-white hover:border-blue-300 hover:shadow"
          : "border-slate-100 bg-slate-50 opacity-60 cursor-not-allowed"
      }`}
    >
      <div className="font-semibold text-lg">{name}</div>
      <div className="text-xs text-slate-500 mt-1">{tagline}</div>
      {loading && <div className="text-xs text-blue-600 mt-2">Generating onboarding URL…</div>}
    </button>
  );
}
