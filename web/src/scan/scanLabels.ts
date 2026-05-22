import type { Connection, LatestScan, ScanPhase, ScanTier } from "../lib/api";

const TIER_LABEL: Record<ScanTier, string> = {
  quick: "Quick Scan", medium: "Medium Scan", deep: "Deep Scan",
};
export function scanTierLabel(tier: ScanTier): string {
  return TIER_LABEL[tier] ?? "Scan";
}

const TIER_BLURB: Record<ScanTier, string> = {
  quick:  "Crown-jewel checks across your active regions.",
  medium: "Full posture across every region.",
  deep:   "Full posture plus code & vulnerability review.",
};
export function scanTierBlurb(tier: ScanTier): string {
  return TIER_BLURB[tier] ?? "";
}

const TIER_DURATION: Record<ScanTier, string> = {
  quick: "~5 min", medium: "~20 min", deep: "code & vuln review",
};
export function scanTierDuration(tier: ScanTier): string {
  return TIER_DURATION[tier] ?? "";
}

const PHASE_TEXT: Record<ScanPhase, string> = {
  region_discovery: "Discovering regions…",
  first_signal:     "Phase 1: account posture…",
  crown_jewel:      "Phase 2: crown-jewel checks…",
  full:             "Scanning every region…",
  done:             "Scan complete",
};
export function phaseLabel(phase: ScanPhase): string {
  return PHASE_TEXT[phase] ?? phase;
}

export function relativeTime(iso: string): string {
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

/** The newest completed/partial scan across all connections — "the scan
 *  behind" the findings/dashboard views. Returns null if there is none. */
export function mostRecentCompletedScan(connections: Connection[]): LatestScan | null {
  const done = connections
    .map((c) => c.latest_scan)
    .filter((s): s is LatestScan =>
      s != null && (s.status === "completed" || s.status === "partial") && s.started_at != null);
  if (done.length === 0) return null;
  return done.reduce((a, b) =>
    new Date(b.started_at!).getTime() > new Date(a.started_at!).getTime() ? b : a);
}
