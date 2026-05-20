import { describe, it, expect } from "vitest";
import { chatReducer, initialState } from "./state";

describe("chatReducer", () => {
  it("appends a user message", () => {
    const s = chatReducer(initialState, {
      type: "append", message: { role: "user", content: { text: "hi" } },
    });
    expect(s.messages).toHaveLength(1);
  });

  it("streamDelta appends to the last assistant message", () => {
    let s = chatReducer(initialState, {
      type: "append", message: { role: "assistant", content: { text: "" } },
    });
    s = chatReducer(s, { type: "streamDelta", text: "Hel" });
    s = chatReducer(s, { type: "streamDelta", text: "lo" });
    expect(s.messages[0].content.text).toBe("Hello");
  });

  it("load replaces conversation state", () => {
    const msgs = [{ role: "user" as const, content: { text: "test" } }];
    const s = chatReducer(initialState, {
      type: "load", id: "conv-1", title: "My convo", messages: msgs,
    });
    expect(s.conversationId).toBe("conv-1");
    expect(s.title).toBe("My convo");
    expect(s.messages).toHaveLength(1);
  });

  it("streaming toggles the streaming flag", () => {
    const s = chatReducer(initialState, { type: "streaming", on: true });
    expect(s.streaming).toBe(true);
    const s2 = chatReducer(s, { type: "streaming", on: false });
    expect(s2.streaming).toBe(false);
  });

  it("appendTool inserts a tool message before a trailing assistant bubble", () => {
    // user + (empty) assistant — the streaming layout before any tool result.
    let s = chatReducer(initialState, {
      type: "append", message: { role: "user", content: { text: "hi" } },
    });
    s = chatReducer(s, {
      type: "append", message: { role: "assistant", content: { text: "" } },
    });
    s = chatReducer(s, {
      type: "appendTool",
      content: { tool_name: "get_severity_breakdown",
                 _artifact_hint: { kind: "severity_breakdown", total: 3 } },
    });
    expect(s.messages.map((m) => m.role)).toEqual(["user", "tool", "assistant"]);
    // streamDelta still lands on the trailing assistant message.
    s = chatReducer(s, { type: "streamDelta", text: "done" });
    expect(s.messages[2].content.text).toBe("done");
  });

  it("appendTool pushes to the end when there is no trailing assistant", () => {
    let s = chatReducer(initialState, {
      type: "append", message: { role: "user", content: { text: "hi" } },
    });
    s = chatReducer(s, {
      type: "appendTool",
      content: { tool_name: "navigate_to" },
    });
    expect(s.messages.map((m) => m.role)).toEqual(["user", "tool"]);
  });

  it("setTitle updates the title without touching messages", () => {
    const base = chatReducer(initialState, {
      type: "load", id: "conv-1", title: "Old title",
      messages: [{ role: "user", content: { text: "hi" } }],
    });
    const s = chatReducer(base, { type: "setTitle", title: "New title" });
    expect(s.title).toBe("New title");
    expect(s.conversationId).toBe("conv-1");
    expect(s.messages).toHaveLength(1);
  });

  describe("voiceUpsert", () => {
    it("appends a new message keyed by item_id on first upsert", () => {
      const s = chatReducer(initialState, {
        type: "voiceUpsert", itemId: "item-a", role: "user", text: "hello there",
      });
      expect(s.messages).toHaveLength(1);
      expect(s.messages[0].role).toBe("user");
      expect(s.messages[0].voiceItemId).toBe("item-a");
      expect(s.messages[0].content.text).toBe("hello there");
    });

    it("updates the existing message in place when the item_id matches", () => {
      let s = chatReducer(initialState, {
        type: "voiceUpsert", itemId: "item-a", role: "assistant", text: "So",
      });
      s = chatReducer(s, {
        type: "voiceUpsert", itemId: "item-a", role: "assistant", text: "Something",
      });
      expect(s.messages).toHaveLength(1);
      expect(s.messages[0].content.text).toBe("Something");
    });

    it("multi-event assistant turn maps to ONE stable bubble (Bug 4)", () => {
      // A stub delta followed by the full transcript for the SAME item must
      // not produce a partial + a full bubble — just one bubble, updated.
      let s = chatReducer(initialState, {
        type: "voiceUpsert", itemId: "asst-1", role: "assistant",
        text: "MCSB provides only",
      });
      s = chatReducer(s, {
        type: "voiceUpsert", itemId: "asst-1", role: "assistant",
        text: "MCSB provides only counts, no descriptions: AM-1 with 2.",
      });
      expect(s.messages.filter((m) => m.role === "assistant")).toHaveLength(1);
      expect(s.messages[0].content.text).toBe(
        "MCSB provides only counts, no descriptions: AM-1 with 2.",
      );
    });

    it("late user transcript fills its placeholder, keeping order (Bug 5)", () => {
      // Realtime order: user item created (placeholder) → assistant streams →
      // async user transcription.completed arrives LAST. The user bubble must
      // stay ABOVE the assistant bubble and get filled in place.
      let s = chatReducer(initialState, {
        type: "voiceUpsert", itemId: "user-1", role: "user", text: "",
      });
      s = chatReducer(s, {
        type: "voiceUpsert", itemId: "asst-1", role: "assistant", text: "The answer is 42.",
      });
      // Late-arriving user transcript — fills placeholder, does NOT append.
      s = chatReducer(s, {
        type: "voiceUpsert", itemId: "user-1", role: "user",
        text: "What is the answer?",
      });
      expect(s.messages).toHaveLength(2);
      expect(s.messages[0].role).toBe("user");
      expect(s.messages[0].content.text).toBe("What is the answer?");
      expect(s.messages[1].role).toBe("assistant");
      expect(s.messages[1].content.text).toBe("The answer is 42.");
    });

    it("drop removes the placeholder for a hallucinated transcript (Bug 1)", () => {
      let s = chatReducer(initialState, {
        type: "voiceUpsert", itemId: "user-x", role: "user", text: "",
      });
      expect(s.messages).toHaveLength(1);
      s = chatReducer(s, {
        type: "voiceUpsert", itemId: "user-x", role: "user", text: "", drop: true,
      });
      expect(s.messages).toHaveLength(0);
    });

    it("drop on an unknown item_id is a no-op", () => {
      const base = chatReducer(initialState, {
        type: "append", message: { role: "user", content: { text: "hi" } },
      });
      const s = chatReducer(base, {
        type: "voiceUpsert", itemId: "never-seen", role: "user", text: "", drop: true,
      });
      expect(s.messages).toHaveLength(1);
    });

    it("drop removes the correct placeholder when an assistant bubble follows", () => {
      let s = chatReducer(initialState, {
        type: "voiceUpsert", itemId: "user-1", role: "user", text: "",
      });
      s = chatReducer(s, {
        type: "voiceUpsert", itemId: "asst-1", role: "assistant", text: "reply",
      });
      s = chatReducer(s, {
        type: "voiceUpsert", itemId: "user-1", role: "user", text: "", drop: true,
      });
      expect(s.messages).toHaveLength(1);
      expect(s.messages[0].role).toBe("assistant");
      expect(s.messages[0].content.text).toBe("reply");
    });
  });
});
