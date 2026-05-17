import { useState } from "react";
import { api } from "../lib/api";

/// Phase A + B onboarding wizard. AWS = one-click CFN; Azure = Cloud-Shell
/// curl pipe. Entra and GCP land in Phases C and D respectively.
export function ConnectClouds() {
  const [pendingAws,   setPendingAws]   = useState(false);
  const [pendingAzure, setPendingAzure] = useState(false);
  const [pendingEntra, setPendingEntra] = useState(false);
  const [pendingGcp,   setPendingGcp]   = useState(false);
  const [cfnUrl,        setCfnUrl]        = useState<string | null>(null);
  const [azureCmd,      setAzureCmd]      = useState<string | null>(null);
  const [entraConsent,  setEntraConsent]  = useState<string | null>(null);
  const [gcpCmd,        setGcpCmd]        = useState<string | null>(null);
  const [error,         setError]         = useState<string | null>(null);

  async function connectAws() {
    setPendingAws(true); setError(null);
    try {
      const r = await api.initiateAwsOnboarding("AWS Account");
      setCfnUrl(r.cfn_url);
    } catch (e) { setError((e as Error).message); }
    finally { setPendingAws(false); }
  }

  async function connectAzure() {
    setPendingAzure(true); setError(null);
    try {
      const r = await api.initiateAzureOnboarding("Azure Subscription");
      setAzureCmd(r.run_command);
    } catch (e) { setError((e as Error).message); }
    finally { setPendingAzure(false); }
  }

  async function connectEntra() {
    setPendingEntra(true); setError(null);
    try {
      const r = await api.initiateEntraOnboarding("Entra Tenant");
      setEntraConsent(r.consent_url);
    } catch (e) { setError((e as Error).message); }
    finally { setPendingEntra(false); }
  }

  async function connectGcp() {
    setPendingGcp(true); setError(null);
    try {
      const r = await api.initiateGcpOnboarding("GCP Project");
      setGcpCmd(r.run_command);
    } catch (e) { setError((e as Error).message); }
    finally { setPendingGcp(false); }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-3xl font-bold tracking-tight">Connect a cloud</h1>
      <p className="text-slate-600 mt-1">Pick a cloud to start scanning.</p>

      <div className="mt-10 grid grid-cols-2 gap-4">
        <CloudTile name="AWS"
                   tagline="Cross-account read-only role via CloudFormation"
                   enabled={true} loading={pendingAws} onClick={connectAws} />
        <CloudTile name="Azure"
                   tagline="Service Principal via Cloud Shell"
                   enabled={true} loading={pendingAzure} onClick={connectAzure} />
        <CloudTile name="Entra"
                   tagline="Microsoft admin consent for Graph API"
                   enabled={true} loading={pendingEntra} onClick={connectEntra} />
        <CloudTile name="GCP"
                   tagline="Workload Identity Federation via Cloud Shell"
                   enabled={true} loading={pendingGcp} onClick={connectGcp} />
      </div>

      {cfnUrl && (
        <div className="mt-10 p-6 rounded-2xl border-2 border-blue-200 bg-blue-50">
          <h2 className="font-semibold text-lg">One-click AWS connection</h2>
          <p className="text-sm text-slate-700 mt-2">
            Open the link below — it deep-links you into the AWS CloudFormation
            console with our template + your one-time external ID pre-filled.
            Review the resources it creates (IAM role, EventBridge rule, AWS
            Config recorder), then click Create.
          </p>
          <a href={cfnUrl} target="_blank" rel="noopener noreferrer"
             className="mt-4 inline-block bg-blue-600 hover:bg-blue-700 text-white font-medium px-5 py-2.5 rounded-lg">
            Launch CloudFormation →
          </a>
        </div>
      )}

      {azureCmd && (
        <div className="mt-10 p-6 rounded-2xl border-2 border-purple-200 bg-purple-50">
          <h2 className="font-semibold text-lg">Run in Azure Cloud Shell</h2>
          <p className="text-sm text-slate-700 mt-2">
            Open Cloud Shell with your subscription selected. Paste and run
            the command below — it creates a Service Principal with Reader +
            Security Reader and notifies CISO Copilot. Takes about 30 seconds.
          </p>
          <pre className="mt-4 p-3 rounded-lg bg-white text-xs font-mono overflow-x-auto select-all">
            {azureCmd}
          </pre>
          <div className="mt-4 flex items-center gap-3">
            <button onClick={() => navigator.clipboard.writeText(azureCmd)}
                    className="bg-slate-100 hover:bg-slate-200 px-4 py-2 rounded-lg text-sm">
              Copy command
            </button>
            <a href="https://shell.azure.com" target="_blank" rel="noopener noreferrer"
               className="bg-purple-600 hover:bg-purple-700 text-white font-medium px-5 py-2.5 rounded-lg">
              Open Cloud Shell →
            </a>
          </div>
        </div>
      )}

      {entraConsent && (
        <div className="mt-10 p-6 rounded-2xl border-2 border-teal-200 bg-teal-50">
          <h2 className="font-semibold text-lg">Entra admin consent</h2>
          <p className="text-sm text-slate-700 mt-2">
            Your tenant admin needs to approve CISO Copilot's Microsoft Graph
            permissions (Policy.Read.All, Directory.Read.All,
            IdentityProtection.Read.All). Click below — you'll be redirected
            to a confirmation page when done.
          </p>
          <a href={entraConsent} target="_blank" rel="noopener noreferrer"
             className="mt-4 inline-block bg-teal-600 hover:bg-teal-700 text-white font-medium px-5 py-2.5 rounded-lg">
            Open admin consent →
          </a>
        </div>
      )}

      {gcpCmd && (
        <div className="mt-10 p-6 rounded-2xl border-2 border-orange-200 bg-orange-50">
          <h2 className="font-semibold text-lg">Run in Google Cloud Shell</h2>
          <p className="text-sm text-slate-700 mt-2">
            Make sure <code className="font-mono text-xs bg-white px-1 rounded">gcloud config get-value project</code> shows
            the project you want to onboard. The script enables required APIs,
            creates a Workload Identity Pool + AWS provider + service account
            (Security Reviewer + Cloud Asset Viewer + Logging Viewer), and
            binds our scanner role to impersonate it. No keys leave your
            project — all auth is federated.
          </p>
          <pre className="mt-4 p-3 rounded-lg bg-white text-xs font-mono overflow-x-auto select-all">
            {gcpCmd}
          </pre>
          <div className="mt-4 flex items-center gap-3">
            <button onClick={() => navigator.clipboard.writeText(gcpCmd)}
                    className="bg-slate-100 hover:bg-slate-200 px-4 py-2 rounded-lg text-sm">
              Copy command
            </button>
            <a href="https://shell.cloud.google.com" target="_blank" rel="noopener noreferrer"
               className="bg-orange-600 hover:bg-orange-700 text-white font-medium px-5 py-2.5 rounded-lg">
              Open Cloud Shell →
            </a>
          </div>
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
