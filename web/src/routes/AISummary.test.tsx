// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import AISummary from "./AISummary";

// Mock the api module — the page calls api.aiSummary().
vi.mock("../lib/api", () => ({
  api: {
    aiSummary: vi.fn(async () => ({
      score:        { fail: 12, partial: 5, pass: 21 },
      by_source:    { aws: 7, azure: 4, code: 6, entra: 0 },
      by_framework: {
        nist_ai_rmf: { fail: 4, partial: 1, pass: 8 },
        iso_42001:   { fail: 3, partial: 2, pass: 6 },
        soc2_ai:     { fail: 0, partial: 0, pass: 0 },
        eu_ai_act:   { fail: 0, partial: 0, pass: 0 },
      },
      top_people: [
        { email: "alice@acme.com", fail: 3, partial: 1, sources: ["aws", "code"] },
      ],
    })),
  },
}));

afterEach(() => cleanup());

describe("AISummary", () => {
  it("renders the score tiles, by-source tiles, framework tiles, and top people", async () => {
    render(<AISummary />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("12")).toBeTruthy());
    expect(screen.getAllByText(/fail/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/partial/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/pass/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/azure/i)).toBeTruthy();
    expect(screen.getByText(/nist ai rmf/i)).toBeTruthy();
    expect(screen.getByText("alice@acme.com")).toBeTruthy();
  });

  it("shows the empty-state copy when no people are returned", async () => {
    const { api } = await import("../lib/api");
    (api.aiSummary as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      score:        { fail: 0, partial: 0, pass: 0 },
      by_source:    { aws: 0, azure: 0, code: 0, entra: 0 },
      by_framework: {
        nist_ai_rmf: { fail: 0, partial: 0, pass: 0 },
        iso_42001:   { fail: 0, partial: 0, pass: 0 },
        soc2_ai:     { fail: 0, partial: 0, pass: 0 },
        eu_ai_act:   { fail: 0, partial: 0, pass: 0 },
      },
      top_people:   [],
    });
    render(<AISummary />);
    await waitFor(() =>
      expect(screen.getByText(/No identifiable AI users yet/i)).toBeTruthy(),
    );
  });
});
