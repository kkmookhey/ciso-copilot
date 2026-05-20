/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, cleanup } from "@testing-library/react";
import { ApprovalCard } from "./ApprovalCard";

// ── Mock the API modules ─────────────────────────────────────────────────────

const mockCreateRisk = vi.fn();
const mockCreatePolicy = vi.fn();
const mockPatchMessage = vi.fn();

vi.mock("../../lib/api", () => ({
  api: {
    createRisk:   (...args: any[]) => mockCreateRisk(...args),
    createPolicy: (...args: any[]) => mockCreatePolicy(...args),
  },
}));

vi.mock("../chatApi", () => ({
  patchMessage: (...args: any[]) => mockPatchMessage(...args),
}));

// ── Base props ───────────────────────────────────────────────────────────────

const BASE_RISK_PROPS = {
  kind:            "approval_card" as const,
  action_kind:     "add_risk"      as const,
  current_status:  "pending"       as const,
  approval_id:     "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  payload: {
    title:       "Test Risk",
    severity:    "high",
    description: "A test risk",
    owner:       "Alice",
    due_date:    "",
    status:      "open",
  },
  edit_fields: [
    { key: "title",    label: "Title",    type: "text"   as const },
    { key: "severity", label: "Severity", type: "select" as const,
      options: ["critical", "high", "medium", "low"] },
  ],
  conversationId: "conv-123",
  messageId:      "msg-456",
};

// ── Helpers ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockCreateRisk.mockResolvedValue({ risk_id: "rid-999", status: "open" });
  mockCreatePolicy.mockResolvedValue({ policy_id: "pid-888", status: "draft" });
  mockPatchMessage.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
});

// ── Tests ────────────────────────────────────────────────────────────────────

describe("ApprovalCard — add_risk approve flow", () => {
  it("renders pending state initially", () => {
    render(<ApprovalCard {...BASE_RISK_PROPS} />);
    expect(screen.getByText(/pending approval/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /approve/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /edit/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeTruthy();
  });

  it("calls createRisk with source_approval_id and transitions to approved", async () => {
    render(<ApprovalCard {...BASE_RISK_PROPS} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    await waitFor(() => {
      expect(mockCreateRisk).toHaveBeenCalledTimes(1);
    });

    const body = mockCreateRisk.mock.calls[0][0];
    expect(body.title).toBe("Test Risk");
    expect(body.severity).toBe("high");
    expect(body.source_approval_id).toBe("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");

    await waitFor(() => {
      expect(screen.getByText(/approved/i)).toBeTruthy();
    });
  });

  it("calls patchMessage with approved status after successful create", async () => {
    render(<ApprovalCard {...BASE_RISK_PROPS} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    await waitFor(() => {
      expect(mockPatchMessage).toHaveBeenCalledTimes(1);
    });

    const [convId, msgId, content] = mockPatchMessage.mock.calls[0];
    expect(convId).toBe("conv-123");
    expect(msgId).toBe("msg-456");
    expect(content._artifact_hint.current_status).toBe("approved");
    expect(content._artifact_hint.result.id).toBe("rid-999");
    expect(content._artifact_hint.result.href).toBe("/risks");
  });

  it("does NOT double-POST if approve is clicked while approved", async () => {
    render(<ApprovalCard {...BASE_RISK_PROPS} current_status="approved"
                         result={{ id: "rid-999", href: "/risks" }} />);

    // Card should be in approved state — no approve button shown
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(mockCreateRisk).not.toHaveBeenCalled();
  });

  it("shows error state when createRisk fails", async () => {
    mockCreateRisk.mockRejectedValueOnce(new Error("500 Internal Server Error"));
    render(<ApprovalCard {...BASE_RISK_PROPS} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/failed/i)).toBeTruthy();
      expect(screen.getByText(/500 Internal Server Error/i)).toBeTruthy();
    });

    // Retry button should be present
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("retry resets to pending state", async () => {
    mockCreateRisk.mockRejectedValueOnce(new Error("Network error"));
    render(<ApprovalCard {...BASE_RISK_PROPS} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    await waitFor(() => screen.getByRole("button", { name: /retry/i }));

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(screen.getByRole("button", { name: /approve/i })).toBeTruthy();
  });
});

describe("ApprovalCard — cancel flow", () => {
  it("transitions to cancelled and calls patchMessage", async () => {
    render(<ApprovalCard {...BASE_RISK_PROPS} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/cancelled/i)).toBeTruthy();
    });

    expect(mockCreateRisk).not.toHaveBeenCalled();

    await waitFor(() => {
      expect(mockPatchMessage).toHaveBeenCalledTimes(1);
    });
    const [, , content] = mockPatchMessage.mock.calls[0];
    expect(content._artifact_hint.current_status).toBe("cancelled");
  });
});

describe("ApprovalCard — edit/save flow", () => {
  it("opens edit form and saves payload, calls patchMessage", async () => {
    render(<ApprovalCard {...BASE_RISK_PROPS} />);

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    expect(screen.getByText(/Edit · Add Risk/i)).toBeTruthy();

    const titleInput = screen.getByDisplayValue("Test Risk");
    fireEvent.change(titleInput, { target: { value: "Updated Risk" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save/i }));
    });

    // Back to pending view with updated payload shown
    expect(screen.getByText(/pending approval/i)).toBeTruthy();
    expect(screen.getByText("Updated Risk")).toBeTruthy();

    await waitFor(() => {
      expect(mockPatchMessage).toHaveBeenCalledTimes(1);
    });
    const [, , content] = mockPatchMessage.mock.calls[0];
    expect(content._artifact_hint.payload.title).toBe("Updated Risk");
  });

  it("edit cancel returns to pending without persisting", async () => {
    render(<ApprovalCard {...BASE_RISK_PROPS} />);

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));

    fireEvent.click(screen.getAllByRole("button", { name: /cancel/i })[0]);

    expect(screen.getByText(/pending approval/i)).toBeTruthy();
    expect(mockPatchMessage).not.toHaveBeenCalled();
  });
});

describe("ApprovalCard — draft_policy approve flow", () => {
  const POLICY_PROPS = {
    kind:            "approval_card" as const,
    action_kind:     "draft_policy"  as const,
    current_status:  "pending"       as const,
    approval_id:     "11111111-2222-3333-4444-555555555555",
    payload: {
      name:        "Access Control Policy",
      content:     "## Policy\nThis is the content.",
      template_id: "access_control",
      status:      "draft",
    },
    edit_fields: [
      { key: "name",    label: "Name",    type: "text"     as const },
      { key: "content", label: "Content", type: "textarea" as const },
    ],
    conversationId: "conv-789",
    messageId:      "msg-012",
  };

  it("calls createPolicy with correct template_key and source_approval_id", async () => {
    render(<ApprovalCard {...POLICY_PROPS} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    await waitFor(() => {
      expect(mockCreatePolicy).toHaveBeenCalledTimes(1);
    });

    const body = mockCreatePolicy.mock.calls[0][0];
    expect(body.template_key).toBe("access_control");
    expect(body.title).toBe("Access Control Policy");
    expect(body.content_md).toBe("## Policy\nThis is the content.");
    expect(body.source_approval_id).toBe("11111111-2222-3333-4444-555555555555");

    await waitFor(() => {
      expect(screen.getByText(/approved/i)).toBeTruthy();
    });
  });

  it("falls back to access_control template if template_id is unknown", async () => {
    render(<ApprovalCard
      {...POLICY_PROPS}
      payload={{ ...POLICY_PROPS.payload, template_id: "unknown_template" }}
    />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    await waitFor(() => expect(mockCreatePolicy).toHaveBeenCalledTimes(1));
    expect(mockCreatePolicy.mock.calls[0][0].template_key).toBe("access_control");
  });
});
