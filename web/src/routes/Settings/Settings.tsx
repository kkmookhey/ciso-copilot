import { useSearchParams } from "react-router-dom";
import { ConnectorsTab } from "./ConnectorsTab";

type TabKey = "profile" | "cloud" | "connectors" | "team" | "billing";

const TABS: { key: TabKey; label: string }[] = [
  { key: "profile",    label: "Profile" },
  { key: "cloud",      label: "Cloud connections" },
  { key: "connectors", label: "Connectors" },
  { key: "team",       label: "Team" },
  { key: "billing",    label: "Billing" },
];

export function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as TabKey) || "connectors";

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="text-2xl font-semibold mb-1">Settings</h1>
      <nav className="flex gap-6 border-b border-neutral-200 mb-7">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setParams({ tab: t.key })}
            className={
              "py-3 -mb-px text-sm " +
              (tab === t.key
                ? "text-neutral-900 font-semibold border-b-2 border-[#d2552b]"
                : "text-neutral-500 hover:text-neutral-700")
            }
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "connectors" && <ConnectorsTab />}
      {tab === "cloud" && (
        <div className="text-sm text-neutral-500">
          The existing cloud onboarding flow will move into this tab in a follow-on PR.
          For now visit <a className="text-[#d2552b]" href="/connect">/connect</a>.
        </div>
      )}
      {tab === "profile" && <Placeholder name="Profile" />}
      {tab === "team" && <Placeholder name="Team" />}
      {tab === "billing" && <Placeholder name="Billing" />}
    </div>
  );
}

function Placeholder({ name }: { name: string }) {
  return <div className="text-sm text-neutral-500">{name} settings — coming later.</div>;
}
