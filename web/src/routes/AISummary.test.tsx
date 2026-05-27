// @vitest-environment jsdom
import type { ReactElement } from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AISummary, { computeExposureScore, verdictForScore } from "./AISummary";

// AISummary now uses <Link> (drill-down + connect cross-link), so tests need
// a router. Helper to wrap inline at each render site.
const renderWithRouter = (ui: ReactElement) =>
  render(<MemoryRouter>{ui}</MemoryRouter>);

const AI_FRAMEWORKS_META = {
  nist_ai_rmf:     { name: "NIST AI RMF",     family: "ai" as const, source_url: "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf", version: "1.0" },
  iso_42001:       { name: "ISO/IEC 42001",   family: "ai" as const, source_url: "https://www.iso.org/standard/81230.html",                version: "2023" },
  soc2_ai:         { name: "SOC 2 + AI",      family: "ai" as const, source_url: "",                                                       version: "2024-tbd" },
  eu_ai_act:       { name: "EU AI Act",       family: "ai" as const, source_url: "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",         version: "2024/1689" },
  nist_ai_600_1:   { name: "NIST AI 600-1",   family: "ai" as const, source_url: "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf", version: "1.0" },
  owasp_llm_top10: { name: "OWASP LLM Top 10", family: "ai" as const, source_url: "https://genai.owasp.org/llm-top-10/",                    version: "2025" },
  owasp_agentic:   { name: "OWASP Agentic",   family: "ai" as const, source_url: "https://genai.owasp.org/",                               version: "draft-2025" },
  mitre_atlas:     { name: "MITRE ATLAS",     family: "ai" as const, source_url: "https://atlas.mitre.org/matrices/ATLAS",                 version: "4" },
};

// Mock the api module — the page calls api.aiSummary().
vi.mock("../lib/api", () => ({
  api: {
    aiSummary: vi.fn(async () => ({
      score:        { fail: 12, partial: 5, pass: 21 },
      by_source:    { aws: 7, azure: 4, code: 6, entra: 0 },
      by_framework: {
        nist_ai_rmf:     { fail: 4, partial: 1, pass: 8 },
        iso_42001:       { fail: 3, partial: 2, pass: 6 },
        soc2_ai:         { fail: 0, partial: 0, pass: 0 },
        eu_ai_act:       { fail: 0, partial: 0, pass: 0 },
        nist_ai_600_1:   { fail: 2, partial: 1, pass: 5 },
        owasp_llm_top10: { fail: 3, partial: 0, pass: 7 },
        owasp_agentic:   { fail: 1, partial: 2, pass: 4 },
        mitre_atlas:     { fail: 5, partial: 1, pass: 3 },
      },
      top_people: [
        { email: "alice@acme.com", fail: 3, partial: 1, sources: ["aws", "code"] },
      ],
      frameworks_meta: AI_FRAMEWORKS_META,
    })),
  },
}));

afterEach(() => cleanup());

describe("AISummary", () => {
  it("renders the score tiles, by-source tiles, framework tiles, and top people", async () => {
    renderWithRouter(<AISummary />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("12")).toBeTruthy());
    expect(screen.getAllByText(/fail/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/partial/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/pass/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/azure/i)).toBeTruthy();
    expect(screen.getByText(/nist ai rmf/i)).toBeTruthy();
    expect(screen.getByText(/nist ai 600-1/i)).toBeTruthy();
    expect(screen.getByText(/owasp llm top 10/i)).toBeTruthy();
    expect(screen.getByText("alice@acme.com")).toBeTruthy();
  });

  it("renders an 'AI frameworks' subhead grouping the framework tiles by family", async () => {
    renderWithRouter(<AISummary />);
    await waitFor(() => expect(screen.getByText("12")).toBeTruthy());
    // CME-v2 S4: family heading present
    expect(screen.getByText(/ai frameworks/i)).toBeTruthy();
  });

  it("carries the mapping-not-attestation tooltip on framework tiles", async () => {
    const { container } = renderWithRouter(<AISummary />);
    await waitFor(() => expect(screen.getByText("12")).toBeTruthy());
    // CME-v2 §14.1: every framework tile carries the disclaimer in its title attribute
    const tiles = container.querySelectorAll('[title*="Mapping only"]');
    expect(tiles.length).toBeGreaterThanOrEqual(8);
  });

  it("renders the AI Exposure Score with a number and verdict band", async () => {
    renderWithRouter(<AISummary />);
    await waitFor(() => expect(screen.getByText("12")).toBeTruthy());
    // 12 fail, 5 partial, 21 pass → weighted_fail=41, total=62, score=round((1-41/62)*100)=34
    expect(screen.getByText("34")).toBeTruthy();
    expect(screen.getByText(/critical exposure/i)).toBeTruthy();
    expect(screen.getByText(/unresolved/i)).toBeTruthy();
  });

  it("shows the empty-state copy when no people are returned", async () => {
    const { api } = await import("../lib/api");
    (api.aiSummary as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      score:        { fail: 0, partial: 0, pass: 0 },
      by_source:    { aws: 0, azure: 0, code: 0, entra: 0 },
      by_framework: {
        nist_ai_rmf:     { fail: 0, partial: 0, pass: 0 },
        iso_42001:       { fail: 0, partial: 0, pass: 0 },
        soc2_ai:         { fail: 0, partial: 0, pass: 0 },
        eu_ai_act:       { fail: 0, partial: 0, pass: 0 },
        nist_ai_600_1:   { fail: 0, partial: 0, pass: 0 },
        owasp_llm_top10: { fail: 0, partial: 0, pass: 0 },
        owasp_agentic:   { fail: 0, partial: 0, pass: 0 },
        mitre_atlas:     { fail: 0, partial: 0, pass: 0 },
      },
      top_people:   [],
      frameworks_meta: AI_FRAMEWORKS_META,
    });
    renderWithRouter(<AISummary />);
    await waitFor(() =>
      expect(screen.getByText(/No identifiable AI users yet/i)).toBeTruthy(),
    );
    // And: ExposureScore renders the "No data yet" empty state.
    expect(screen.getByText(/No data yet/i)).toBeTruthy();
  });
});

describe("computeExposureScore", () => {
  it("returns null when there are no findings at all", () => {
    expect(computeExposureScore({ fail: 0, partial: 0, pass: 0 })).toBeNull();
  });

  it("returns 100 when everything passes", () => {
    expect(computeExposureScore({ fail: 0, partial: 0, pass: 10 })).toBe(100);
  });

  it("returns 0 when everything fails", () => {
    expect(computeExposureScore({ fail: 10, partial: 0, pass: 0 })).toBe(0);
  });

  it("weights fails 3x heavier than partials", () => {
    // 1 fail (3w) + 1 partial (1w) + 4 pass (4w) → 1 - 4/8 = 0.5 → 50
    expect(computeExposureScore({ fail: 1, partial: 1, pass: 4 })).toBe(50);
  });

  it("matches the worked example from the headline test (12/5/21 → 34)", () => {
    // weighted_fail = 12*3 + 5*1 = 41; total = 41 + 21 = 62; score = round((1-41/62)*100) = 34
    expect(computeExposureScore({ fail: 12, partial: 5, pass: 21 })).toBe(34);
  });
});

describe("verdictForScore", () => {
  it("classifies bands at their boundaries", () => {
    expect(verdictForScore(100).label).toMatch(/strong/i);
    expect(verdictForScore(90).label).toMatch(/strong/i);
    expect(verdictForScore(89).label).toMatch(/healthy/i);
    expect(verdictForScore(70).label).toMatch(/healthy/i);
    expect(verdictForScore(69).label).toMatch(/attention/i);
    expect(verdictForScore(50).label).toMatch(/attention/i);
    expect(verdictForScore(49).label).toMatch(/critical/i);
    expect(verdictForScore(0).label).toMatch(/critical/i);
  });
});
