import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type AIConnection, type Connection, type InitiateGcpResponse } from "../lib/api";

/// Phase A + B onboarding wizard. AWS = one-click CFN; Azure = Cloud-Shell
/// curl pipe. Entra and GCP land in Phases C and D respectively.
export function ConnectClouds() {
  const [pendingAws,   setPendingAws]   = useState(false);
  const [pendingAzure, setPendingAzure] = useState(false);
  const [pendingEntra, setPendingEntra] = useState(false);
  const [pendingGcp,   setPendingGcp]   = useState(false);
  const [pendingGithub, setPendingGithub] = useState(false);
  const [aiConnections, setAiConnections] = useState<AIConnection[]>([]);
  const [cloudConnections, setCloudConnections] = useState<Connection[]>([]);
  const [cloudActionMsg,   setCloudActionMsg]   = useState<Record<string, string>>({});
  const [toast, setToast] = useState<{ cloud: string } | null>(null);
  const prevStatusesRef = useRef<Record<string, string>>({});
  const nav = useNavigate();

  function reloadConnections() {
    api.listConnections().then((r) => {
      // Detect non-active → active transitions and surface a toast linking to /scan.
      for (const c of r.connections) {
        const prev = prevStatusesRef.current[c.conn_id];
        if (prev && prev !== "active" && c.status === "active") {
          setToast({ cloud: c.cloud_type });
        }
        prevStatusesRef.current[c.conn_id] = c.status;
      }
      setCloudConnections(r.connections);
    }).catch(() => { /* non-fatal */ });
  }

  useEffect(() => {
    api.listAIConnections().then((r) => setAiConnections(r.connections)).catch(() => { /* non-fatal */ });
    reloadConnections();
  }, []);

  // Poll while any connection is still pending/error so we can flip the toast on transition.
  useEffect(() => {
    const hasPending = cloudConnections.some(
      (c) => c.status !== "active" && c.status !== "revoked",
    );
    if (!hasPending) return;
    const id = window.setInterval(reloadConnections, 5000);
    return () => window.clearInterval(id);
  }, [cloudConnections]);

  async function deleteCloud(connId: string, status: Connection["status"]) {
    if (status === "active") {
      const ok = window.confirm(
        "This will revoke the connection. The platform will stop scanning this account and any signals will be ignored. Continue?",
      );
      if (!ok) return;
    }
    try {
      await api.deleteConnection(connId);
      setCloudConnections((cs) => cs.filter((c) => c.conn_id !== connId));
    } catch (e) {
      setCloudActionMsg((m) => ({ ...m, [connId]: `Delete failed: ${(e as Error).message}` }));
    }
  }
  const [cfnUrl,        setCfnUrl]        = useState<string | null>(null);
  const [azureCmd,      setAzureCmd]      = useState<string | null>(null);
  const [entraConsent,  setEntraConsent]  = useState<string | null>(null);
  const [gcpInit,       setGcpInit]       = useState<InitiateGcpResponse | null>(null);
  const [gcpMode,       setGcpMode]       = useState<"project" | "org">("project");
  const [gcpOrgId,      setGcpOrgId]      = useState<string>("");
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
      setGcpInit(r);
    } catch (e) { setError((e as Error).message); }
    finally { setPendingGcp(false); }
  }

  const orgIdValid = /^\d{6,20}$/.test(gcpOrgId.trim());
  const gcpCmd = gcpInit
    ? (gcpMode === "org" && orgIdValid
        ? `${gcpInit.run_command} --org ${gcpOrgId.trim()}`
        : gcpInit.run_command)
    : null;

  async function connectGithub() {
    setPendingGithub(true); setError(null);
    try {
      const r = await api.getGithubInstallUrl();
      // Send the user straight to GitHub — no intermediate panel for now.
      window.location.href = r.install_url;
    } catch (e) { setError((e as Error).message); }
    finally { setPendingGithub(false); }
  }

  return (
    <div className="max-w-3xl">
      {toast && (
        <div className="fixed top-4 right-4 z-50 max-w-sm bg-white border border-orange-300 shadow-lg rounded-md p-4">
          <div className="font-medium text-stone-800">
            Your {toast.cloud.toUpperCase()} connection is ready
          </div>
          <button onClick={() => { setToast(null); nav("/scan"); }}
            className="mt-2 text-orange-700 underline text-sm">
            Run your first scan →
          </button>
          <button onClick={() => setToast(null)}
            className="absolute top-1 right-2 text-stone-400 hover:text-stone-600">×</button>
        </div>
      )}
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
        <CloudTile name="GitHub"
                   tagline="AI inventory via the CISO Copilot GitHub App"
                   enabled={true} loading={pendingGithub} onClick={connectGithub} />
      </div>

      {cloudConnections.filter((c) => c.status !== "revoked").length > 0 && (
        <div className="mt-8 rounded-2xl border border-slate-200 p-5">
          <h2 className="font-semibold">Connected clouds</h2>
          <ul className="mt-3 divide-y divide-slate-100">
            {cloudConnections.filter((c) => c.status !== "revoked").map((c) => (
              <ConnectionRow
                key={c.conn_id}
                conn={c}
                actionMsg={cloudActionMsg[c.conn_id]}
                onDelete={deleteCloud}
              />
            ))}
          </ul>
        </div>
      )}

      {aiConnections.filter((c) => c.provider === "github" && c.status === "active").length > 0 && (
        <div className="mt-8 rounded-2xl border border-slate-200 p-5">
          <h2 className="font-semibold">Connected GitHub installations</h2>
          <ul className="mt-3 divide-y divide-slate-100">
            {aiConnections
              .filter((c) => c.provider === "github" && c.status === "active")
              .map((c) => (
                <li key={c.id} className="flex items-center justify-between py-3 text-sm">
                  <div>
                    <div className="font-medium">{c.github_org_name || "GitHub installation"}</div>
                    <div className="text-xs text-slate-500">Connected {formatDate(c.created_at)}</div>
                  </div>
                  <Link to={`/ai/connections/${c.id}/repos`}
                        className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs">
                    Manage repos →
                  </Link>
                </li>
              ))}
          </ul>
        </div>
      )}

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

      {gcpInit && gcpCmd && (
        <div className="mt-10 p-6 rounded-2xl border-2 border-orange-200 bg-orange-50">
          <h2 className="font-semibold text-lg">Run in Google Cloud Shell</h2>

          <fieldset className="mt-4">
            <legend className="text-sm font-medium text-slate-700">Scope</legend>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:gap-6">
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="gcp-mode"
                  value="project"
                  checked={gcpMode === "project"}
                  onChange={() => setGcpMode("project")}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium">Single project</span>
                  <span className="block text-xs text-slate-600">
                    Onboards the project from <code className="font-mono">gcloud config get-value project</code>.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="gcp-mode"
                  value="org"
                  checked={gcpMode === "org"}
                  onChange={() => setGcpMode("org")}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium">Whole organization</span>
                  <span className="block text-xs text-slate-600">
                    Reader roles bind at the Org node; all projects discover on the first scan. Requires org-admin.
                  </span>
                </span>
              </label>
            </div>
          </fieldset>

          {gcpMode === "org" && (
            <div className="mt-3">
              <label htmlFor="gcp-org-id" className="block text-sm font-medium text-slate-700">
                Organization ID
              </label>
              <input
                id="gcp-org-id"
                type="text"
                inputMode="numeric"
                value={gcpOrgId}
                onChange={(e) => setGcpOrgId(e.target.value)}
                placeholder="123456789012"
                aria-invalid={gcpOrgId.length > 0 && !orgIdValid}
                className="mt-1 w-64 px-3 py-2 rounded-md border border-slate-300 bg-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-orange-300"
              />
              <p className="mt-1 text-xs text-slate-600">
                Find with <code className="font-mono bg-white px-1 rounded">gcloud organizations list</code>.
              </p>
              {gcpOrgId.length > 0 && !orgIdValid && (
                <p className="mt-1 text-xs text-red-700">Must be a numeric ID (6–20 digits).</p>
              )}
            </div>
          )}

          <p className="text-sm text-slate-700 mt-4">
            {gcpMode === "org" ? (
              <>
                The script enables required APIs in your host project, creates a Workload
                Identity Pool + AWS provider + service account, binds reader roles
                (Security Reviewer + Cloud Asset Viewer + Logging Viewer + Browser)
                at the Organization node, and grants our scanner role to impersonate it.
                No keys leave your project — all auth is federated.
              </>
            ) : (
              <>
                Make sure <code className="font-mono text-xs bg-white px-1 rounded">gcloud config get-value project</code> shows
                the project you want to onboard. The script enables required APIs,
                creates a Workload Identity Pool + AWS provider + service account
                (Security Reviewer + Cloud Asset Viewer + Logging Viewer), and
                binds our scanner role to impersonate it. No keys leave your
                project — all auth is federated.
              </>
            )}
          </p>
          <pre className="mt-4 p-3 rounded-lg bg-white text-xs font-mono overflow-x-auto select-all">
            {gcpCmd}
          </pre>
          <div className="mt-4 flex items-center gap-3">
            <button onClick={() => navigator.clipboard.writeText(gcpCmd)}
                    disabled={gcpMode === "org" && !orgIdValid}
                    className="bg-slate-100 hover:bg-slate-200 disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2 rounded-lg text-sm">
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

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}

function CloudStatusPill({ status }: { status: Connection["status"] }) {
  const cls =
    status === "active"  ? "bg-green-100 text-green-700" :
    status === "pending" ? "bg-amber-100 text-amber-700" :
    status === "error"   ? "bg-red-100 text-red-700"     :
                           "bg-slate-100 text-slate-600";
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium capitalize ${cls}`}>
      {status}
    </span>
  );
}

export function ConnectionRow({
  conn, actionMsg, onDelete,
}: {
  conn: Connection;
  actionMsg?: string;
  onDelete: (connId: string, status: Connection["status"]) => void;
}) {
  return (
    <li className="py-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium flex items-center gap-2">
            <span className="uppercase text-xs text-slate-500 font-mono">{conn.cloud_type}</span>
            <span className="truncate">{conn.display_name}</span>
          </div>
          <div className="text-xs text-slate-500 truncate">
            {conn.account_identifier ?? `Added ${formatDate(conn.created_at)}`}
          </div>
          {actionMsg && <div className="text-xs text-blue-600 mt-1">{actionMsg}</div>}
        </div>
        <CloudStatusPill status={conn.status} />
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={() => onDelete(conn.conn_id, conn.status)}
            className="px-3 py-1.5 rounded-md bg-red-50 hover:bg-red-100 text-red-700 text-xs"
          >
            Delete
          </button>
        </div>
      </div>
      {conn.cloud_type === 'entra' && conn.scope?.signin_premium_required === true && (
        <LicensingBanner />
      )}
    </li>
  );
}

function LicensingBanner() {
  return (
    <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
      <div className="font-medium text-amber-900">
        ⚠ Sign-in detection requires Microsoft Entra ID P1 or P2
      </div>
      <p className="mt-1 text-amber-800">
        Microsoft restricts <code className="text-xs">/auditLogs/signIns</code> to
        Premium-licensed tenants. Your tenant is on the Free tier, so AI SaaS
        sign-in events can't be detected. All other Entra checks ran normally.
      </p>
      <a
        href="https://learn.microsoft.com/en-us/entra/fundamentals/whatis"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 inline-block text-amber-900 underline hover:text-amber-700"
      >
        Learn more about Entra ID licensing →
      </a>
    </div>
  );
}
