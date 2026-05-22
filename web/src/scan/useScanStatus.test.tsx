// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, cleanup } from "@testing-library/react";

const getScanStatus = vi.fn();
vi.mock("../lib/api", () => ({ api: { getScanStatus: (id: string) => getScanStatus(id) } }));

import { useScanStatus } from "./useScanStatus";

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => cleanup());

describe("useScanStatus", () => {
  it("does nothing when scanId is null", async () => {
    const { result } = renderHook(() => useScanStatus(null));
    expect(result.current.scan).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(getScanStatus).not.toHaveBeenCalled();
  });

  it("fetches and stops once the scan is terminal", async () => {
    getScanStatus.mockResolvedValue({
      scan_id: "s1", tier: "quick", status: "completed", phase: "done",
      coverage_map: null, started_at: null, finished_at: null, finding_count: 12,
    });
    const { result } = renderHook(() => useScanStatus("s1", 50));
    await waitFor(() => expect(result.current.scan?.status).toBe("completed"));
    const callsAfterTerminal = getScanStatus.mock.calls.length;
    await new Promise((r) => setTimeout(r, 120));
    // No further polls after a terminal status.
    expect(getScanStatus.mock.calls.length).toBe(callsAfterTerminal);
  });

  it("keeps polling while the scan is running", async () => {
    getScanStatus.mockResolvedValue({
      scan_id: "s2", tier: "quick", status: "running", phase: "crown_jewel",
      coverage_map: null, started_at: null, finished_at: null, finding_count: 3,
    });
    renderHook(() => useScanStatus("s2", 30));
    await waitFor(() => expect(getScanStatus.mock.calls.length).toBeGreaterThan(1));
  });
});
