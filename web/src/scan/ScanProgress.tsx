import type { ScanStatus } from "../lib/api";
import { phaseLabel, scanTierLabel } from "./scanLabels";

/** The in-progress / just-finished scan view. Works from `phase` +
 *  `finding_count` while running; shows the region census only once the
 *  scanner has written the coverage map (it does so after Phase 2). */
export function ScanProgress({ scan }: { scan: ScanStatus }) {
  const done   = scan.status === "completed" || scan.status === "partial";
  const failed = scan.status === "failed";
  const queued = scan.status === "queued";
  // AWS scans carry a region-keyed coverage map; Azure scans a
  // subscription-keyed one. Render whichever is present.
  const census = scan.coverage_map?.regions
    ?? scan.coverage_map?.subscriptions
    ?? null;
  const censusUnit = scan.coverage_map?.subscriptions ? "subscriptions" : "regions";
  const cells = census ? Object.values(census) : null;
  const activeCount = cells
    ? cells.filter((c) => c.state === "active").length
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
      {cells && (
        <div className="mt-1 text-xs text-blue-600">
          {cells.length} {censusUnit} scanned
          {activeCount != null ? ` · ${activeCount} active` : ""}
        </div>
      )}
    </div>
  );
}
