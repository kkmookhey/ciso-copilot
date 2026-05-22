// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ScanTypeBadge } from "./ScanTypeBadge";

afterEach(() => cleanup());

describe("ScanTypeBadge", () => {
  it("renders the tier label and relative time", () => {
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    render(<ScanTypeBadge tier="quick" at={oneHourAgo} />);
    expect(screen.getByText(/Quick Scan/)).toBeTruthy();
    expect(screen.getByText(/1h ago/)).toBeTruthy();
  });

  it("renders nothing when tier is null", () => {
    const { container } = render(<ScanTypeBadge tier={null} at={null} />);
    expect(container.firstChild).toBeNull();
  });
});
