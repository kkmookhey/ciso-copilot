import type { ScanStatus } from "../lib/api";
import { phaseLabel, scanTierLabel } from "./scanLabels";

/** The in-progress / just-finished scan view. Works from `phase` +
 *  `finding_count` while running; shows the region census only once the
 *  scanner has written the coverage map (it does so after Phase 2). */
export function ScanProgress({ scan }: { scan: ScanStatus }) {
  const done   = scan.status === "completed" || scan.status === "partial";
  const failed = scan.status === "failed";
  const queued = scan.status === "queued";
  const regions = scan.coverage_map?.regions
    ? Object.values(scan.coverage_map.regions)
    : null;
  const activeCount = regions
    ? regions.filter((r) => r.state === "active").length
    : null;

  return (
    <div className="mt-2 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-medium text-blue-800">
          {scanTierLabel(scan.tier)} {done ? "complete" : failed ? "failed" : queued ? "queued" : "running"}
        </span>
        <span className="text-xs text-blue-600">{scan.finding_count} findings</span>
      </div>
      {failed ? (
        <div className="mt-1 text-xs text-red-600">
          Scan failed — retry from the Scan button.
        </div>
      ) : (
        <div className="mt-1 text-xs text-blue-700">{phaseLabel(scan.phase)}</div>
      )}
      {regions && (
        <div className="mt-1 text-xs text-blue-600">
          {regions.length} regions scanned
          {activeCount != null ? ` · ${activeCount} active` : ""}
        </div>
      )}
    </div>
  );
}
