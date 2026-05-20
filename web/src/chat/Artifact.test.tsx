// @vitest-environment jsdom
// web/src/chat/Artifact.test.tsx
// One render assertion per artifact kind + CitationChip presence check.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";
import { Artifact } from "./Artifact";
import type { ArtifactHint } from "./tools";

// Recharts uses ResizeObserver internally — stub it for jsdom.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// Ensure DOM is cleaned up after each test even if auto-cleanup isn't configured.
afterEach(() => cleanup());

describe("Artifact renderer", () => {
  it("renders kpi_card with value", () => {
    const hint: ArtifactHint = {
      kind: "kpi_card",
      label: "Open findings",
      value: "42",
      detail: "across all clouds",
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("Open findings")).toBeTruthy();
  });

  it("renders kpi_card CitationChip when source is present", () => {
    const hint: ArtifactHint = {
      kind: "kpi_card",
      label: "Top finding",
      value: "critical",
      source: { finding_id: "abc-123" },
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("↗ source")).toBeTruthy();
  });

  it("renders entity_list with title and items", () => {
    const hint: ArtifactHint = {
      kind: "entity_list",
      title: "Cloud entities",
      entities: [
        { id: "e1", kind: "aws_s3_bucket", display_name: "my-bucket" },
        { id: "e2", kind: "aws_ec2_instance", display_name: "web-server" },
      ],
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("Cloud entities")).toBeTruthy();
    expect(screen.getByText("my-bucket")).toBeTruthy();
    expect(screen.getByText("web-server")).toBeTruthy();
  });

  it("renders finding_card with title and severity", () => {
    const hint: ArtifactHint = {
      kind: "finding_card",
      finding_id: "f-001",
      check_id: "CIS-1.1",
      title: "Root account has MFA disabled",
      severity: "critical",
      source: { finding_id: "f-001" },
    };
    const { container } = render(<Artifact hint={hint} />);
    const w = within(container);
    expect(w.getByText("Root account has MFA disabled")).toBeTruthy();
    // severity pill (the <span> with text-transform:capitalize)
    expect(container.querySelector("span[style*='capitalize']")?.textContent).toBe("critical");
    // finding_card always has source → CitationChip always present
    expect(w.getByText("↗ source")).toBeTruthy();
  });

  it("renders risk_card with title and status", () => {
    const hint: ArtifactHint = {
      kind: "risk_card",
      risk_id: "r-001",
      title: "Unpatched EC2 exposure",
      severity: "high",
      status: "open",
      owner: "ops-team",
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("Unpatched EC2 exposure")).toBeTruthy();
    expect(screen.getByText("Open")).toBeTruthy();
    expect(screen.getByText("ops-team")).toBeTruthy();
  });

  it("renders chart_bar with title", () => {
    const hint: ArtifactHint = {
      kind: "chart_bar",
      title: "Findings by cloud",
      series: [
        { label: "AWS", value: 10 },
        { label: "Azure", value: 5 },
      ],
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("Findings by cloud")).toBeTruthy();
  });

  it("renders chart_donut with title and legend", () => {
    const hint: ArtifactHint = {
      kind: "chart_donut",
      title: "Compliance posture",
      segments: [
        { label: "SOC 2", value: 80 },
        { label: "CIS AWS", value: 60 },
      ],
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("Compliance posture")).toBeTruthy();
    expect(screen.getByText("SOC 2")).toBeTruthy();
  });

  it("renders severity_breakdown with total", () => {
    const hint: ArtifactHint = {
      kind: "severity_breakdown",
      total: 100,
      critical: 5,
      high: 20,
      medium: 40,
      low: 35,
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("100 total")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
  });

  it("renders approval_card pending state with Approve/Edit/Cancel buttons", () => {
    const hint: ArtifactHint = {
      kind: "approval_card",
      action_kind: "add_risk",
      current_status: "pending",
      payload: { title: "Exposed S3 bucket", severity: "high" },
      edit_fields: [
        { key: "title",    label: "Title",    type: "text" },
        { key: "severity", label: "Severity", type: "select", options: ["high", "low"] },
      ],
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("Approve")).toBeTruthy();
    expect(screen.getByText("Edit")).toBeTruthy();
    expect(screen.getByText("Cancel")).toBeTruthy();
    expect(screen.getByText("Exposed S3 bucket")).toBeTruthy();
  });

  it("renders approval_card approved state with ✓", () => {
    const hint: ArtifactHint = {
      kind: "approval_card",
      action_kind: "add_risk",
      current_status: "approved",
      payload: { title: "Risk approved" },
      edit_fields: [],
      result: { id: "r-999", href: "https://example.com/risks/r-999" },
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText(/approved/)).toBeTruthy();
  });

  it("renders approval_card error state with Retry button", () => {
    const hint: ArtifactHint = {
      kind: "approval_card",
      action_kind: "draft_policy",
      current_status: "error",
      payload: {},
      edit_fields: [],
      error: "Database write failed",
    };
    render(<Artifact hint={hint} />);
    expect(screen.getByText("Retry")).toBeTruthy();
    expect(screen.getByText("Database write failed")).toBeTruthy();
  });
});
