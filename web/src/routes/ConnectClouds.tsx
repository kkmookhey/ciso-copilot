import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type AIConnection, type Connection } from "../lib/api";
import { useScanStatus } from "../scan/useScanStatus";
import { ScanProgress } from "../scan/ScanProgress";
import { scanTierBlurb, scanTierDuration } from "../scan/scanLabels";

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

  useEffect(() => {
    api.listAIConnections().then((r) => setAiConnections(r.connections)).catch(() => { /* non-fatal */ });
    api.listConnections().then((r) => setCloudConnections(r.connections)).catch(() => { /* non-fatal */ });
  }, []);

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

function ConnectionRow({
  conn, actionMsg, onDelete,
}: {
  conn: Connection;
  actionMsg?: string;
  onDelete: (connId: string, status: Connection["status"]) => void;
}) {
  const navigate = useNavigate();
  const seedId =
    conn.latest_scan &&
    !["completed", "partial", "failed"].includes(conn.latest_scan.status)
      ? conn.latest_scan.scan_id
      : null;
  const [scanId, setScanId] = useState<string | null>(seedId);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const { scan } = useScanStatus(scanId);

  async function startScan(tier: "quick" | "medium") {
    setScanMsg("Queuing scan…");
    try {
      const r = await api.rescanConnection(conn.conn_id, tier);
      setScanId(r.scan_id);
      setScanMsg(null);
    } catch (e) {
      setScanMsg(`Failed: ${(e as Error).message}`);
    }
  }

  const isAws = conn.cloud_type === "aws";

  return (
    <li className="py-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium flex items-center gap-2">
            <span className="uppercase text-xs text-slate-500 font-mono">{conn.cloud_type}</span>
            <span className="truncate">{conn.display_name}</span>
          </div>
          <div className="text-xs text-slate-500 truncate">{conn.account_identifier ?? "—"}</div>
          {actionMsg && <div className="text-xs text-blue-600 mt-1">{actionMsg}</div>}
          {scanMsg  && <div className="text-xs text-blue-600 mt-1">{scanMsg}</div>}
        </div>
        <CloudStatusPill status={conn.status} />
        <div className="flex items-center gap-2 shrink-0">
          {conn.status === "active" && isAws && (
            <ScanPicker
              onPick={(tier) =>
                tier === "deep" ? navigate("/contact/deep-scan") : startScan(tier)}
            />
          )}
          {conn.status === "active" && !isAws && (
            <button
              type="button"
              onClick={() => startScan("medium")}
              className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs"
            >
              Rescan
            </button>
          )}
          <button
            type="button"
            onClick={() => onDelete(conn.conn_id, conn.status)}
            className="px-3 py-1.5 rounded-md bg-red-50 hover:bg-red-100 text-red-700 text-xs"
          >
            Delete
          </button>
        </div>
      </div>
      {scan && <ScanProgress scan={scan} />}
    </li>
  );
}

function ScanPicker({ onPick }: { onPick: (tier: "quick" | "medium" | "deep") => void }) {
  const [open, setOpen] = useState(false);
  const tiers: Array<"quick" | "medium" | "deep"> = ["quick", "medium", "deep"];
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs"
      >
        Scan ▾
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-56 rounded-lg border border-slate-200 bg-white shadow-lg">
          {tiers.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => { setOpen(false); onPick(t); }}
              className="block w-full text-left px-3 py-2 hover:bg-slate-50"
            >
              <div className="text-sm font-medium capitalize">
                {t}{t === "deep" ? " — contact us" : ""}
              </div>
              <div className="text-xs text-slate-500">
                {scanTierBlurb(t)} <span className="text-slate-400">({scanTierDuration(t)})</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
