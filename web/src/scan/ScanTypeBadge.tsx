import type { ScanTier } from "../lib/api";
import { scanTierLabel, relativeTime } from "./scanLabels";

/** A pill naming the scan behind the current view — "Quick Scan · 2h ago".
 *  Renders nothing when there is no scan to attribute. */
export function ScanTypeBadge({ tier, at }: { tier: ScanTier | null; at: string | null }) {
  if (!tier) return null;
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
      {scanTierLabel(tier)}{at ? ` · ${relativeTime(at)}` : ""}
    </span>
  );
}
