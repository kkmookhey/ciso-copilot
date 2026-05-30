import { api, type ConnectorRow, type ProviderKind } from "../../lib/api";

type Catalog = {
  kind: ProviderKind | "coming-soon";
  label: string;
  letter: string;
  bg: string;
  mcpUrl?: string;
  capabilities: string[];
  preview?: boolean;
  previewNote?: string;
};

const CATALOG: Record<ProviderKind | "coming-soon-1" | "coming-soon-2" | "coming-soon-3", Catalog> = {
  slack: {
    kind: "slack", label: "Slack", letter: "S", bg: "#4A154B",
    mcpUrl: "mcp.slack.com",
    capabilities: ["Send DM", "Post in channel", "Search messages"],
  },
  atlassian: {
    kind: "atlassian", label: "Atlassian (Jira) — coming next slice", letter: "J", bg: "#0052CC",
    mcpUrl: "mcp.atlassian.com",
    capabilities: ["Create issue", "Comment", "Transition status"],
  },
  google: {
    kind: "google", label: "Google Workspace — coming next slice", letter: "G", bg: "#ea4335",
    mcpUrl: "gmailmcp.googleapis.com",
    capabilities: ["Send mail", "Draft", "Search inbox"],
  },
  microsoft: {
    kind: "microsoft", label: "Microsoft 365 — coming next slice", letter: "M", bg: "#00a4ef",
    mcpUrl: "graph.microsoft.com/mcp", preview: true,
    previewNote: "Read-only today. Send-mail and Teams DM not yet supported by Microsoft's first-party MCP.",
    capabilities: ["Search Outlook", "Search Teams", "Calendar read"],
  },
  // The three "coming-soon-N" keys are not used — placeholder shape for type completeness
  "coming-soon-1": { kind: "coming-soon", label: "", letter: "", bg: "", capabilities: [] },
  "coming-soon-2": { kind: "coming-soon", label: "", letter: "", bg: "", capabilities: [] },
  "coming-soon-3": { kind: "coming-soon", label: "", letter: "", bg: "", capabilities: [] },
};

export function ConnectorCard({
  kind, connector, onChange,
}: {
  kind: ProviderKind;
  connector: ConnectorRow | undefined;
  onChange: () => void;
}) {
  const cfg = CATALOG[kind];
  const connected = connector?.status === "active";
  const live = kind === "slack"; // Slice 1: only Slack is live

  async function connect() {
    if (!live) return;
    const { authorize_url } = await api.initiateConnectorOAuth(kind);
    window.location.href = authorize_url;
  }

  async function disconnect() {
    if (!connector) return;
    if (!window.confirm(`Disconnect ${cfg.label}?`)) return;
    await api.revokeConnector(connector.conn_id);
    onChange();
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="flex items-center gap-3 mb-3">
        <div
          className="w-9 h-9 rounded-md flex items-center justify-center text-white text-sm font-bold"
          style={{ background: cfg.bg }}
        >
          {cfg.letter}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-[15px]">{cfg.label}</span>
          {cfg.preview && (
            <span className="text-[10px] font-semibold uppercase tracking-wide bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
              Preview
            </span>
          )}
          {cfg.mcpUrl && (
            <span title={`MCP endpoint: ${cfg.mcpUrl}`} className="text-neutral-400 cursor-help text-xs">
              ⓘ
            </span>
          )}
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-1.5">
        {cfg.capabilities.map(c => (
          <span key={c} className="text-[11px] bg-neutral-100 text-neutral-600 px-2 py-0.5 rounded">{c}</span>
        ))}
      </div>

      {cfg.previewNote && (
        <p className="text-[11px] text-amber-700 mb-3">{cfg.previewNote}</p>
      )}

      <div className="flex justify-between items-center pt-3 border-t border-neutral-100">
        <div className="text-[13px] flex items-center">
          <span
            className={
              "inline-block w-2 h-2 rounded-full mr-2 " +
              (connected ? "bg-emerald-500" : "bg-neutral-300")
            }
          />
          <span className="text-neutral-600">
            {connected
              ? <>Connected{connector?.vendor_workspace_id ? ` · ${connector.vendor_workspace_id}` : ""}</>
              : live ? "Not connected" : "Coming in a later slice"}
          </span>
        </div>
        {live && (connected ? (
          <button
            onClick={disconnect}
            className="text-[13px] text-red-600 border border-red-200 rounded-md px-3 py-1.5"
          >
            Disconnect
          </button>
        ) : (
          <button
            onClick={connect}
            className="text-[13px] bg-neutral-900 text-white rounded-md px-3 py-1.5"
          >
            Connect {cfg.label}
          </button>
        ))}
      </div>
    </div>
  );
}
