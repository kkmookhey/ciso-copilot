import { useState } from "react";
import { api } from "../lib/api";

/**
 * Download the tenant's AI-BOM as CycloneDX-ML 1.6 JSON. AI Security
 * Slice 1.2. Filename pattern matches the Lambda's Content-Disposition
 * suggestion: shasta-ai-bom-<YYYY-MM-DD>.cdx.json.
 */
export function ExportAIBOMButton() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onClick = async () => {
    setBusy(true);
    setErr(null);
    try {
      const blob = await api.exportAIBOM();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const date = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `shasta-ai-bom-${date}.cdx.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium border border-slate-300 rounded-md bg-white hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        title="Download CycloneDX-ML 1.6 AI Bill of Materials"
      >
        {busy ? "Exporting…" : "Export AI-BOM"}
      </button>
      {err && (
        <span className="text-xs text-red-600" role="alert">{err}</span>
      )}
    </div>
  );
}
