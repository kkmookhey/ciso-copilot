import { useState } from "react";
import { useConnectors } from "../../lib/useConnectors";
import { api } from "../../lib/api";

export function ActButtons({
  finding,
}: {
  finding: { finding_id: string; title: string; resource_arn: string | null };
}) {
  const { connectors } = useConnectors();
  const [pending, setPending] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const slackOK = (connectors ?? []).some(
    c => c.provider === "slack" && c.status === "active",
  );

  async function dmOwner() {
    const channel = window.prompt(
      "Slack DM target (channel ID or @user):", "@kk",
    );
    if (!channel) return;
    const text =
      `[Shasta] ${finding.title}` +
      (finding.resource_arn ? ` — ${finding.resource_arn}` : "");
    setPending(true); setMsg(null);
    try {
      await api.callTool("slack__send_message", { channel, text });
      setMsg("Sent ✓");
    } catch (e: any) {
      setMsg(`Failed: ${e.message ?? e}`);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        disabled={!slackOK || pending}
        title={slackOK ? "Send a Slack DM about this finding" : "Connect Slack in Settings to use this"}
        onClick={dmOwner}
        className={
          "text-[12px] rounded-md px-2.5 py-1 border " +
          (slackOK
            ? "border-neutral-300 hover:bg-neutral-50"
            : "border-neutral-200 text-neutral-400 cursor-not-allowed")
        }
      >
        DM via Slack
      </button>
      {msg && <span className="text-[11px] text-neutral-500">{msg}</span>}
    </div>
  );
}
