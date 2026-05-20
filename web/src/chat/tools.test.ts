// web/src/chat/tools.test.ts
// Tests for the 12-tool shared catalog (SP4 Task 4b.1).

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  TOOLS,
  toAnthropicTools,
  toRealtimeTools,
  executeTool,
} from "./tools";

// ---------------------------------------------------------------------------
// Mock api.ts — prevent any real network calls
// ---------------------------------------------------------------------------

vi.mock("../lib/api", () => ({
  api: {
    findingsSummary: vi.fn().mockResolvedValue({
      by_severity: { critical: 3, high: 5, medium: 10, low: 2, info: 1 },
      by_cloud:    { aws: 15, azure: 4, gcp: 1, entra: 1 },
      total:       21,
    }),
    listRisks: vi.fn().mockResolvedValue({
      risks: [
        {
          risk_id:    "risk-1",
          title:      "Exposed S3 bucket",
          severity:   "high",
          status:     "open",
          owner:      "alice@example.com",
          due_date:   "2026-06-01",
          finding_id: "finding-1",
          description: null,
          notes:       null,
          created_at:  "2026-01-01T00:00:00Z",
          updated_at:  "2026-01-01T00:00:00Z",
        },
      ],
      count: 1,
    }),
    listFindings: vi.fn().mockResolvedValue({
      findings: [
        {
          finding_id:    "finding-1",
          check_id:      "aws-s3-public-read",
          title:         "S3 bucket public read",
          severity:      "critical",
          status:        "fail",
          resource_arn:  "arn:aws:s3:::my-bucket",
          resource_type: "AWS::S3::Bucket",
          region:        "us-east-1",
          domain:        "storage",
          description:   "Bucket allows public read",
          frameworks:    { soc2: ["CC6.1"] },
          remediation:   null,
          first_seen:    "2026-01-01T00:00:00Z",
          last_seen:     "2026-05-01T00:00:00Z",
        },
      ],
      limit:  20,
      offset: 0,
      count:  1,
      total:  1,
    }),
    complianceSummary: vi.fn().mockResolvedValue({
      summary: {
        SOC2:   { total: 100, passing: 82, failing: 18, score_pct: 82 },
        ISO27001: { total: 50, passing: 40, failing: 10, score_pct: 80 },
      },
      by_framework_control: [],
    }),
    listEntities: vi.fn().mockResolvedValue({
      entities: [
        {
          id:            "entity-1",
          kind:          "aws_s3_bucket",
          natural_key:   "my-bucket",
          display_name:  "my-bucket",
          domain:        "cloud",
          source_path:   null,
          detector_id:   "s3-detector",
          first_seen_at: "2026-01-01T00:00:00Z",
          last_seen_at:  "2026-05-01T00:00:00Z",
          attributes:    {},
        },
      ],
      next_page: null,
    }),
    getEntity: vi.fn().mockResolvedValue({
      id:              "entity-1",
      kind:            "aws_s3_bucket",
      natural_key:     "my-bucket",
      display_name:    "my-bucket",
      domain:          "cloud",
      source_path:     null,
      detector_id:     "s3-detector",
      first_seen_at:   "2026-01-01T00:00:00Z",
      last_seen_at:    "2026-05-01T00:00:00Z",
      attributes:      {},
      evidence_packet: null,
      connection_id:   null,
    }),
  },
}));

// ---------------------------------------------------------------------------
// Catalog shape
// ---------------------------------------------------------------------------

describe("TOOLS catalog", () => {
  it("has exactly 12 entries", () => {
    expect(TOOLS).toHaveLength(12);
  });

  it("every tool has name, description, input_schema, flavor, execute", () => {
    for (const t of TOOLS) {
      expect(t.name,         `${t.name}: name`)         .toBeTruthy();
      expect(t.description,  `${t.name}: description`)  .toBeTruthy();
      expect(t.input_schema, `${t.name}: input_schema`) .toBeTruthy();
      expect(["data", "action", "side-effect"]).toContain(t.flavor);
      expect(typeof t.execute).toBe("function");
    }
  });

  it("tool names are unique", () => {
    const names = TOOLS.map(t => t.name);
    expect(new Set(names).size).toBe(names.length);
  });
});

// ---------------------------------------------------------------------------
// toAnthropicTools
// ---------------------------------------------------------------------------

describe("toAnthropicTools", () => {
  it("returns same length as input", () => {
    expect(toAnthropicTools(TOOLS)).toHaveLength(TOOLS.length);
  });

  it("each entry has name, description, input_schema", () => {
    for (const t of toAnthropicTools(TOOLS)) {
      expect(t.name).toBeTruthy();
      expect(t.description).toBeTruthy();
      expect(t.input_schema).toBeTruthy();
    }
  });

  it("does NOT include execute or flavor", () => {
    for (const t of toAnthropicTools(TOOLS)) {
      expect((t as any).execute).toBeUndefined();
      expect((t as any).flavor).toBeUndefined();
    }
  });
});

// ---------------------------------------------------------------------------
// toRealtimeTools
// ---------------------------------------------------------------------------

describe("toRealtimeTools", () => {
  it("returns same length as input", () => {
    expect(toRealtimeTools(TOOLS)).toHaveLength(TOOLS.length);
  });

  it("each entry has type:'function', name, description, parameters", () => {
    for (const t of toRealtimeTools(TOOLS)) {
      expect(t.type).toBe("function");
      expect(t.name).toBeTruthy();
      expect(t.description).toBeTruthy();
      expect(t.parameters).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// executeTool — unknown name
// ---------------------------------------------------------------------------

describe("executeTool", () => {
  it("throws on an unknown tool name", async () => {
    await expect(executeTool("does_not_exist", {})).rejects.toThrow(/Unknown tool/);
  });
});

// ---------------------------------------------------------------------------
// propose_risk_entry — action tool, pending approval_card, no mutation
// ---------------------------------------------------------------------------

describe("propose_risk_entry", () => {
  it("returns approval_card with pending status without throwing", async () => {
    const result = await executeTool("propose_risk_entry", {
      title:    "Overly permissive IAM policy",
      severity: "high",
    });
    expect(result._artifact_hint).toBeDefined();
    expect(result._artifact_hint!.kind).toBe("approval_card");
    const hint = result._artifact_hint as { kind: "approval_card"; action_kind: string; current_status: string };
    expect(hint.action_kind).toBe("add_risk");
    expect(hint.current_status).toBe("pending");
  });

  it("payload contains the provided args", async () => {
    const result = await executeTool("propose_risk_entry", {
      title:    "Exposed RDS",
      severity: "critical",
      owner:    "bob@example.com",
    });
    const hint = result._artifact_hint as any;
    expect(hint.payload.title).toBe("Exposed RDS");
    expect(hint.payload.severity).toBe("critical");
    expect(hint.payload.owner).toBe("bob@example.com");
  });

  it("has ADD_RISK edit_fields (title, severity, status, owner, due_date)", async () => {
    const result = await executeTool("propose_risk_entry", {
      title: "Test risk", severity: "low",
    });
    const hint = result._artifact_hint as any;
    const keys = hint.edit_fields.map((f: any) => f.key);
    expect(keys).toContain("title");
    expect(keys).toContain("severity");
    expect(keys).toContain("status");
    expect(keys).toContain("owner");
    expect(keys).toContain("due_date");
  });

  it("does NOT call any mutating API endpoint", async () => {
    const { api } = await import("../lib/api");
    const createRisk = vi.fn();
    (api as any).createRisk = createRisk;

    await executeTool("propose_risk_entry", { title: "Risk A", severity: "medium" });
    expect(createRisk).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// propose_policy_draft — action tool, pending approval_card, no mutation
// ---------------------------------------------------------------------------

describe("propose_policy_draft", () => {
  it("returns approval_card with pending status", async () => {
    const result = await executeTool("propose_policy_draft", {
      name:    "Access Control Policy",
      content: "# Access Control\n\nAll access must be reviewed quarterly.",
    });
    const hint = result._artifact_hint as any;
    expect(hint.kind).toBe("approval_card");
    expect(hint.action_kind).toBe("draft_policy");
    expect(hint.current_status).toBe("pending");
  });

  it("has DRAFT_POLICY edit_fields (name, content, status)", async () => {
    const result = await executeTool("propose_policy_draft", { name: "Policy B" });
    const hint = result._artifact_hint as any;
    const keys = hint.edit_fields.map((f: any) => f.key);
    expect(keys).toContain("name");
    expect(keys).toContain("content");
    expect(keys).toContain("status");
  });
});

// ---------------------------------------------------------------------------
// Data tools — smoke tests via mock
// ---------------------------------------------------------------------------

describe("get_morning_briefing", () => {
  it("returns a kpi_card _artifact_hint with correct kind", async () => {
    const result = await executeTool("get_morning_briefing", {});
    expect(result._artifact_hint?.kind).toBe("kpi_card");
  });

  it("result._artifact_hints contains 3 hints", async () => {
    const result = await executeTool("get_morning_briefing", {});
    const hints = (result.result as any)._artifact_hints;
    expect(hints).toHaveLength(3);
    expect(hints[0].kind).toBe("kpi_card");
    expect(hints[1].kind).toBe("severity_breakdown");
    expect(hints[2].kind).toBe("kpi_card");
  });
});

describe("query_entities", () => {
  it("returns entity_list hint", async () => {
    const result = await executeTool("query_entities", { domain: "cloud" });
    expect(result._artifact_hint?.kind).toBe("entity_list");
  });
});

describe("get_entity", () => {
  it("returns entity_list hint with one entry and source", async () => {
    const result = await executeTool("get_entity", { entity_id: "entity-1" });
    expect(result._artifact_hint?.kind).toBe("entity_list");
    expect(result.source?.entity_id).toBe("entity-1");
  });
});

describe("query_findings", () => {
  it("returns finding_card for ≤3 results", async () => {
    const result = await executeTool("query_findings", { severity: "critical" });
    // mock returns 1 finding → finding_card
    expect(result._artifact_hint?.kind).toBe("finding_card");
  });
});

describe("get_finding", () => {
  it("returns finding_card hint with source.finding_id", async () => {
    const result = await executeTool("get_finding", { check_id: "aws-s3-public-read" });
    expect(result._artifact_hint?.kind).toBe("finding_card");
    expect(result.source?.finding_id).toBe("finding-1");
  });
});

describe("get_compliance_summary", () => {
  it("returns chart_donut as primary hint", async () => {
    const result = await executeTool("get_compliance_summary", {});
    expect(result._artifact_hint?.kind).toBe("chart_donut");
  });

  it("result._artifact_hints includes donut + kpi_cards per framework", async () => {
    const result = await executeTool("get_compliance_summary", {});
    const hints = (result.result as any)._artifact_hints;
    expect(hints[0].kind).toBe("chart_donut");
    expect(hints.some((h: any) => h.kind === "kpi_card")).toBe(true);
  });
});

describe("get_severity_breakdown", () => {
  it("returns severity_breakdown hint", async () => {
    const result = await executeTool("get_severity_breakdown", {});
    expect(result._artifact_hint?.kind).toBe("severity_breakdown");
    const h = result._artifact_hint as any;
    expect(typeof h.total).toBe("number");
    expect(typeof h.critical).toBe("number");
  });
});

describe("list_risks", () => {
  it("returns risk_card hint", async () => {
    const result = await executeTool("list_risks", {});
    expect(result._artifact_hint?.kind).toBe("risk_card");
  });

  it("result._artifact_hints carries all risk cards", async () => {
    const result = await executeTool("list_risks", {});
    const hints = (result.result as any)._artifact_hints;
    expect(hints.length).toBeGreaterThan(0);
    expect(hints[0].kind).toBe("risk_card");
  });
});

// ---------------------------------------------------------------------------
// Side-effect tools — no artifact, just intent
// ---------------------------------------------------------------------------

describe("navigate_to", () => {
  it("returns navigated_to path without throwing", async () => {
    const result = await executeTool("navigate_to", { path: "/findings" });
    expect((result.result as any).navigated_to).toBe("/findings");
    expect(result._artifact_hint).toBeUndefined();
  });
});

describe("filter_findings_view", () => {
  it("returns filtered params without throwing", async () => {
    const result = await executeTool("filter_findings_view", { severity: "critical", cloud: "aws" });
    expect((result.result as any).filtered).toMatchObject({ severity: "critical", cloud: "aws" });
    expect(result._artifact_hint).toBeUndefined();
  });
});
