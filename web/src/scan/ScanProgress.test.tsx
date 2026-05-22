// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ScanProgress } from "./ScanProgress";
import type { ScanStatus } from "../lib/api";

afterEach(() => cleanup());

const base: ScanStatus = {
  scan_id: "s1", tier: "quick", status: "running", phase: "crown_jewel",
  coverage_map: null, started_at: null, finished_at: null, finding_count: 7,
};

describe("ScanProgress", () => {
  it("shows the phase text and finding count while running", () => {
    render(<ScanProgress scan={base} />);
    expect(screen.getByText(/Phase 2/i)).toBeTruthy();
    expect(screen.getByText(/7 findings/)).toBeTruthy();
    expect(screen.getByText(/Quick Scan running/i)).toBeTruthy();
  });

  it("shows a failure message for a failed scan", () => {
    render(<ScanProgress scan={{ ...base, status: "failed" }} />);
    // Both the status header and the error div match /failed/i; use getAllByText.
    expect(screen.getAllByText(/failed/i).length).toBeGreaterThan(0);
  });

  it("labels a queued scan as queued, not running", () => {
    render(<ScanProgress scan={{ ...base, status: "queued", phase: "region_discovery" }} />);
    expect(screen.getByText(/Quick Scan queued/i)).toBeTruthy();
  });

  it("shows the region census once the coverage map is populated", () => {
    render(<ScanProgress scan={{
      ...base, status: "completed", phase: "done",
      coverage_map: { regions: {
        "us-east-1": { state: "active" }, "us-west-1": { state: "default_only" },
      } },
    }} />);
    expect(screen.getByText(/2 regions scanned/)).toBeTruthy();
    expect(screen.getByText(/1 active/)).toBeTruthy();
  });
});
