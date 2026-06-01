import { useEffect, useState } from "react";
import { api, type SlackChannel } from "../../lib/api";

export function ChannelPicker({
  onSave,
  onClose,
}: {
  onSave: (channel: SlackChannel) => void;
  onClose: () => void;
}) {
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SlackChannel | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.listSlackChannels()
      .then((r) => setChannels(r.channels))
      .catch(console.error);
  }, []);

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await api.setBroadcastChannel(selected.id, selected.name);
      onSave(selected);
    } catch (e) {
      console.error(e);
      setSaving(false);
    }
  };

  const filtered = (channels ?? []).filter((c) =>
    c.name.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md max-h-[80vh] flex flex-col shadow-xl">
        <h4 className="text-lg font-semibold">Pick a broadcast channel</h4>
        <input
          className="mt-4 w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="Search channels..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
        <ul className="mt-4 flex-1 overflow-y-auto">
          {channels == null && (
            <li className="px-3 py-2 text-sm text-neutral-500">Loading…</li>
          )}
          {channels != null && filtered.length === 0 && (
            <li className="px-3 py-2 text-sm text-neutral-500">No channels found.</li>
          )}
          {filtered.map((c) => (
            <li
              key={c.id}
              onClick={() => setSelected(c)}
              className={`px-3 py-2 rounded cursor-pointer text-sm ${
                selected?.id === c.id
                  ? "bg-blue-100 text-blue-800"
                  : "hover:bg-neutral-100"
              }`}
            >
              #{c.name}{" "}
              {c.is_private && (
                <span className="text-xs text-neutral-500">(private)</span>
              )}
            </li>
          ))}
        </ul>
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded border border-neutral-300 hover:bg-neutral-50 text-sm"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={!selected || saving}
            className="px-4 py-2 rounded bg-blue-600 text-white text-sm disabled:opacity-50 hover:bg-blue-700"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
