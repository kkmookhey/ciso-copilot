import { useEffect, useState } from "react";
import { api, type AdminBotStatus } from "../../lib/api";
import { ChannelPicker } from "./ChannelPicker";

export function ConnectorAdminBlock() {
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [status, setStatus] = useState<AdminBotStatus | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.me()
      .then((r) => {
        const admin = r.user?.role === "admin" || r.user?.is_admin === true;
        setIsAdmin(admin);
        if (admin) {
          api.getAdminBotStatus().then(setStatus).catch(console.error);
        }
      })
      .catch(console.error);
  }, []);

  // Render nothing for non-admins or while loading.
  if (!isAdmin || !status) return null;

  const install = async () => {
    setBusy(true);
    try {
      const { authorize_url } = await api.initiateSlackWorkspaceBot();
      window.location.href = authorize_url;
    } catch (e) {
      console.error(e);
      setBusy(false);
    }
  };

  const onToggle = async () => {
    const next = !status.autonomous_rule_enabled;
    await api.toggleAutonomousRule(next);
    setStatus({ ...status, autonomous_rule_enabled: next });
  };

  const onRevoke = async () => {
    if (!window.confirm("Disconnect Shasta's bot from your Slack workspace?")) return;
    setBusy(true);
    await api.revokeSlackBot();
    setStatus({
      installed: false,
      broadcast_channel_id: null,
      broadcast_channel_name: null,
      autonomous_rule_enabled: false,
    });
    setBusy(false);
  };

  return (
    <section className="mt-8 rounded-lg border border-neutral-200 p-6">
      <h3 className="text-lg font-semibold">Admin · Slack workspace bot</h3>
      <p className="text-sm text-neutral-600 mt-1">
        Posts a Block Kit card to your chosen channel whenever a CRITICAL finding lands.
      </p>

      {!status.installed && (
        <button
          className="mt-4 px-4 py-2 rounded bg-[#4A154B] text-white disabled:opacity-50"
          onClick={install}
          disabled={busy}
        >
          Install Shasta to your Slack workspace
        </button>
      )}

      {status.installed && !status.broadcast_channel_id && (
        <div className="mt-4">
          <button
            className="px-4 py-2 rounded border border-neutral-300 hover:bg-neutral-50"
            onClick={() => setShowPicker(true)}
          >
            Pick a broadcast channel
          </button>
          {showPicker && (
            <ChannelPicker
              onSave={(ch) => {
                setStatus({
                  ...status,
                  broadcast_channel_id: ch.id,
                  broadcast_channel_name: ch.name,
                });
                setShowPicker(false);
              }}
              onClose={() => setShowPicker(false)}
            />
          )}
        </div>
      )}

      {status.installed && status.broadcast_channel_id && (
        <div className="mt-4 flex items-center gap-4">
          <span className="text-sm">
            Installed · <span className="font-mono">#{status.broadcast_channel_name}</span>
          </span>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={status.autonomous_rule_enabled}
              onChange={onToggle}
            />
            Autonomous broadcasts ON
          </label>
          <button
            className="text-sm text-red-600 hover:underline disabled:opacity-50"
            onClick={onRevoke}
            disabled={busy}
          >
            Disconnect
          </button>
        </div>
      )}
    </section>
  );
}
