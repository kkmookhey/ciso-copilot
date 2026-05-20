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
});
