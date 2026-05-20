// @vitest-environment jsdom
// web/src/chat/SourceSideSheet.test.tsx
// Light smoke tests for SourceSideSheet:
//   - renders nothing initially
//   - opens on the "open-source-sheet" custom event
//   - closes on Escape key
//   - closes via the × button

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";
import { SourceSideSheet } from "./SourceSideSheet";
import type { Source } from "./tools";

// Stub api.getEntity and api.listFindings so the component doesn't make real HTTP calls.
vi.mock("../lib/api", () => ({
  api: {
    getEntity:    vi.fn(() => Promise.resolve({
      id: "e-1", kind: "aws_s3_bucket", natural_key: "my-bucket",
      display_name: "my-bucket", domain: "cloud",
      source_path: null, detector_id: "d", first_seen_at: "", last_seen_at: "",
      attributes: {}, evidence_packet: null, connection_id: null,
    })),
    listFindings: vi.fn(() => Promise.resolve({ findings: [], limit: 100, offset: 0, count: 0, total: 0 })),
  },
}));

afterEach(() => cleanup());

function dispatchOpenEvent(source: Source) {
  window.dispatchEvent(new CustomEvent("open-source-sheet", { detail: source }));
}

describe("SourceSideSheet", () => {
  it("renders nothing initially", () => {
    const { container } = render(<SourceSideSheet />);
    expect(container.firstChild).toBeNull();
  });

  it("opens when open-source-sheet event fires", async () => {
    render(<SourceSideSheet />);
    await act(async () => {
      dispatchOpenEvent({ entity_id: "e-1" });
    });
    // The × close button should now be in the DOM
    expect(screen.getByLabelText("Close")).toBeTruthy();
  });

  it("closes on Escape key", async () => {
    render(<SourceSideSheet />);
    await act(async () => {
      dispatchOpenEvent({ entity_id: "e-1" });
    });
    expect(screen.getByLabelText("Close")).toBeTruthy();
    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(screen.queryByLabelText("Close")).toBeNull();
  });

  it("closes via the × button", async () => {
    render(<SourceSideSheet />);
    await act(async () => {
      dispatchOpenEvent({ entity_id: "e-1" });
    });
    const btn = screen.getByLabelText("Close");
    await act(async () => {
      fireEvent.click(btn);
    });
    expect(screen.queryByLabelText("Close")).toBeNull();
  });

  it("opens with raw source fields when no entity_id or finding_id", async () => {
    render(<SourceSideSheet />);
    await act(async () => {
      dispatchOpenEvent({ scan_id: "scan-42", last_scan_at: "2026-05-19T00:00:00Z" });
    });
    expect(screen.getByLabelText("Close")).toBeTruthy();
  });
});
