/**
 * @vitest-environment jsdom
 */
// web/src/chat/artifacts/ChartDonut.test.tsx
// Tests for Bug 3 fix: distinct per-segment colors, zero-segment filtering.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ChartDonut } from "./ChartDonut";

// Recharts uses ResizeObserver internally — stub it for jsdom.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

afterEach(() => cleanup());

describe("ChartDonut — distinct colors", () => {
  it("renders all non-zero segments in the legend", () => {
    render(
      <ChartDonut
        kind="chart_donut"
        title="Compliance posture"
        segments={[
          { label: "cis_aws",   value: 2 },
          { label: "cis_azure", value: 4 },
          { label: "soc2",      value: 2 },
        ]}
      />
    );
    expect(screen.getByText("cis_aws")).toBeTruthy();
    expect(screen.getByText("cis_azure")).toBeTruthy();
    expect(screen.getByText("soc2")).toBeTruthy();
  });

  it("assigns distinct colors to the first 3 segments (no two the same)", () => {
    const { container } = render(
      <ChartDonut
        kind="chart_donut"
        title="Test"
        segments={[
          { label: "A", value: 10 },
          { label: "B", value: 20 },
          { label: "C", value: 30 },
        ]}
      />
    );
    // Legend dots are <span> elements with an inline background style.
    // Collect the background colors of the colored (non-zero) dots.
    const dots = Array.from(
      container.querySelectorAll("li span[style*='border-radius: 50%']")
    ) as HTMLElement[];
    const colors = dots.map(d => d.style.background);
    // All three should be defined and distinct
    expect(colors).toHaveLength(3);
    const unique = new Set(colors);
    expect(unique.size).toBe(3);
  });

  it("does NOT default every segment to the same persimmon red", () => {
    const { container } = render(
      <ChartDonut
        kind="chart_donut"
        title="Test"
        segments={[
          { label: "A", value: 5 },
          { label: "B", value: 8 },
          { label: "C", value: 3 },
        ]}
      />
    );
    const dots = Array.from(
      container.querySelectorAll("li span[style*='border-radius: 50%']")
    ) as HTMLElement[];
    const colors = dots.map(d => d.style.background);
    // Should not be all the same color
    const unique = new Set(colors);
    expect(unique.size).toBeGreaterThan(1);
  });

  it("respects explicit per-segment colors when provided", () => {
    const { container } = render(
      <ChartDonut
        kind="chart_donut"
        title="Test"
        segments={[
          { label: "pass",   value: 10, color: "#00FF00" },
          { label: "fail",   value: 5,  color: "#FF0000" },
        ]}
      />
    );
    const dots = Array.from(
      container.querySelectorAll("li span[style*='border-radius: 50%']")
    ) as HTMLElement[];
    const colors = dots.map(d => d.style.background);
    expect(colors[0]).toBe("rgb(0, 255, 0)");   // #00FF00 → rgb form
    expect(colors[1]).toBe("rgb(255, 0, 0)");   // #FF0000 → rgb form
  });
});

describe("ChartDonut — zero-segment filtering", () => {
  it("does NOT render a zero-value segment as a colored legend dot", () => {
    const { container } = render(
      <ChartDonut
        kind="chart_donut"
        title="Compliance posture"
        segments={[
          { label: "cis_aws",   value: 2 },
          { label: "cis_azure", value: 4 },
          { label: "mcsb",      value: 0 },
          { label: "soc2",      value: 2 },
        ]}
      />
    );
    // mcsb is zero — its legend dot should be the muted #E8DFD0, NOT
    // one of the distinct palette colors
    const allListItems = Array.from(container.querySelectorAll("li"));
    const mcsbItem = allListItems.find(li => li.textContent?.includes("mcsb"));
    expect(mcsbItem).toBeTruthy();
    const dot = mcsbItem!.querySelector("span[style*='border-radius: 50%']") as HTMLElement;
    // The muted dot uses background: #E8DFD0 (or rgb equivalent)
    expect(
      dot.style.background === "#E8DFD0" ||
      dot.style.background === "rgb(232, 223, 208)"
    ).toBe(true);
  });

  it("still shows zero-value segments in the legend (muted, not hidden)", () => {
    render(
      <ChartDonut
        kind="chart_donut"
        title="Test"
        segments={[
          { label: "active",  value: 5 },
          { label: "dormant", value: 0 },
        ]}
      />
    );
    // "dormant" should appear in the legend even though value=0
    expect(screen.getByText("dormant")).toBeTruthy();
  });

  it("renders correctly when ALL segments are non-zero", () => {
    render(
      <ChartDonut
        kind="chart_donut"
        title="All active"
        segments={[
          { label: "X", value: 3 },
          { label: "Y", value: 7 },
        ]}
      />
    );
    expect(screen.getByText("X")).toBeTruthy();
    expect(screen.getByText("Y")).toBeTruthy();
  });

  it("renders gracefully when ALL segments are zero", () => {
    render(
      <ChartDonut
        kind="chart_donut"
        title="Empty"
        segments={[
          { label: "A", value: 0 },
          { label: "B", value: 0 },
        ]}
      />
    );
    // Title still renders; no crash
    expect(screen.getByText("Empty")).toBeTruthy();
    // Both appear in muted legend
    expect(screen.getByText("A")).toBeTruthy();
    expect(screen.getByText("B")).toBeTruthy();
  });
});
