import { describe, it, expect } from "vitest";
import {
  scanTierLabel, scanTierBlurb, scanTierDuration, phaseLabel, relativeTime,
  mostRecentCompletedScan,
} from "./scanLabels";
import type { Connection } from "../lib/api";

describe("scanLabels", () => {
  it("labels each tier", () => {
    expect(scanTierLabel("quick")).toBe("Quick Scan");
    expect(scanTierLabel("medium")).toBe("Medium Scan");
    expect(scanTierLabel("deep")).toBe("Deep Scan");
  });

  it("has a blurb and a duration for each tier", () => {
    expect(scanTierBlurb("quick")).toMatch(/crown/i);
    expect(scanTierDuration("medium")).toMatch(/min/);
  });

  it("maps phases to human text", () => {
    expect(phaseLabel("region_discovery")).toMatch(/regions/i);
    expect(phaseLabel("crown_jewel")).toMatch(/phase 2/i);
  });

  it("formats relative time", () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    expect(relativeTime(fiveMinAgo)).toBe("5m ago");
    expect(relativeTime(new Date().toISOString())).toBe("just now");
  });

  it("picks the most recent completed scan across connections", () => {
    const conns = [
      { latest_scan: { scan_id: "a", tier: "quick", status: "completed",
                       phase: "done", started_at: "2026-05-20T00:00:00Z" } },
      { latest_scan: { scan_id: "b", tier: "medium", status: "completed",
                       phase: "done", started_at: "2026-05-21T00:00:00Z" } },
      { latest_scan: { scan_id: "c", tier: "deep", status: "running",
                       phase: "full", started_at: "2026-05-22T00:00:00Z" } },
    ] as Connection[];
    const r = mostRecentCompletedScan(conns);
    expect(r?.scan_id).toBe("b"); // 'c' is still running; 'b' is the newest completed
  });

  it("returns null when no connection has a completed scan", () => {
    expect(mostRecentCompletedScan([] as Connection[])).toBeNull();
  });
});
