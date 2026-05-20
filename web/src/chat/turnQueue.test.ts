// web/src/chat/turnQueue.test.ts
// Unit tests for TurnQueue (SP4 Task 4c.2).
//
// WebRTC itself is not unit-tested here — it requires a real browser.
// The TurnQueue is factored out to be independently testable against a
// mocked fetch. Tests verify: enqueue ordering, FIFO flush, retry-with-backoff
// on failure, sync-warning after max retries, and the destroyed state.

import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import { TurnQueue } from "./turnQueue";
import type { SealedTurn } from "./turnQueue";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTurn(conversationId = "conv-1", userText = "hello"): SealedTurn {
  return {
    conversation_id: conversationId,
    user:            { text: userText, modality: "voice" },
    assistant:       { text: "response", modality: "voice" },
  };
}

// Wait for all microtasks + macrotasks to drain.
// We use a loop of Promise.resolve() + small timeouts to let the async flush
// worker complete without relying on arbitrary sleep durations.
async function flushAsync(iterations = 5): Promise<void> {
  for (let i = 0; i < iterations; i++) {
    await new Promise(resolve => setTimeout(resolve, 0));
  }
}

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mock cognito to always return a valid token
vi.mock("../lib/cognito", () => ({
  validIdToken: vi.fn().mockResolvedValue("mock-id-token"),
  signOut:      vi.fn(),
}));

// We will mock globalThis.fetch per test
let fetchMock: Mock;

beforeEach(() => {
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock;
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TurnQueue — enqueue and FIFO ordering", () => {
  it("posts a single turn on enqueue", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200 } as Response);

    const q = new TurnQueue();
    q.enqueue(makeTurn("conv-1", "first turn"));

    await flushAsync(10);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = (fetchMock.mock.calls[0] as [string, RequestInit]);
    expect(url).toContain("/conversations/conv-1/messages");
    expect(init.method).toBe("POST");

    const body = JSON.parse(init.body as string) as SealedTurn;
    expect(body.user.text).toBe("first turn");
    expect(body.user.modality).toBe("voice");
    expect(body.assistant.modality).toBe("voice");
  });

  it("posts turns in FIFO order", async () => {
    // Sequence each fetch resolution so the second call only starts after
    // the first completes.
    fetchMock.mockResolvedValue({ ok: true, status: 200 } as Response);

    const q = new TurnQueue();
    q.enqueue(makeTurn("conv-1", "turn-1"));
    q.enqueue(makeTurn("conv-1", "turn-2"));
    q.enqueue(makeTurn("conv-1", "turn-3"));

    await flushAsync(20);

    // All three posted, in order.
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const bodies = (fetchMock.mock.calls as Array<[string, RequestInit]>).map(
      ([, init]) =>
        (JSON.parse(init.body as string) as SealedTurn).user.text,
    );
    expect(bodies).toEqual(["turn-1", "turn-2", "turn-3"]);
  });

  it("pending() returns the unposted queue", async () => {
    // Hold all fetches unresolved so the queue doesn't drain.
    let resolve1: (v: Response) => void;
    const held = new Promise<Response>(r => { resolve1 = r; });
    fetchMock.mockReturnValueOnce(held);

    const q = new TurnQueue();
    q.enqueue(makeTurn("c", "a"));
    q.enqueue(makeTurn("c", "b"));

    // First fetch is in flight; second turn still pending.
    await flushAsync(2);
    expect(q.pending.length).toBeGreaterThanOrEqual(1);

    // Resolve first fetch so the queue can drain.
    resolve1!({ ok: true, status: 200 } as Response);
    fetchMock.mockResolvedValue({ ok: true, status: 200 } as Response);
    await flushAsync(10);
  });
});

describe("TurnQueue — retry on failure", () => {
  it("retries 5xx responses up to MAX_ATTEMPTS then calls onSyncWarning", async () => {
    // All fetches return 500.
    fetchMock.mockResolvedValue({ ok: false, status: 500 } as Response);

    const onSyncWarning = vi.fn();
    const q = new TurnQueue({ onSyncWarning });
    q.enqueue(makeTurn());

    // MAX_ATTEMPTS = 5; each retry sleeps with exponential backoff.
    // We use fake timers so the test doesn't actually wait seconds.
    vi.useFakeTimers();

    // Kick the flush and let the first attempt settle.
    await Promise.resolve();

    // Fast-forward timers to burn through all back-off delays.
    for (let i = 0; i < 10; i++) {
      await vi.runAllTimersAsync();
      await Promise.resolve();
    }

    vi.useRealTimers();

    expect(fetchMock.mock.calls.length).toBe(5);
    expect(onSyncWarning).toHaveBeenCalledTimes(1);
  });

  it("succeeds on a retry after transient failure", async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: false, status: 503 } as Response)
      .mockResolvedValueOnce({ ok: false, status: 503 } as Response)
      .mockResolvedValue({ ok: true,  status: 200 } as Response);

    vi.useFakeTimers();

    const onSyncWarning = vi.fn();
    const q = new TurnQueue({ onSyncWarning });
    q.enqueue(makeTurn("c", "transient-fail"));

    for (let i = 0; i < 10; i++) {
      await vi.runAllTimersAsync();
      await Promise.resolve();
    }

    vi.useRealTimers();

    expect(onSyncWarning).not.toHaveBeenCalled();
    // Posted 3 times: 2 failures + 1 success.
    expect(fetchMock.mock.calls.length).toBe(3);
  });

  it("does NOT retry 4xx errors (except 429)", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 400 } as Response);

    const onSyncWarning = vi.fn();
    const q = new TurnQueue({ onSyncWarning });
    q.enqueue(makeTurn());

    await flushAsync(10);

    // Should abort after 1 attempt (non-retryable 4xx).
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // And still warn (turn discarded).
    expect(onSyncWarning).toHaveBeenCalledTimes(1);
  });

  it("retries on 429", async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: false, status: 429 } as Response)
      .mockResolvedValue({ ok: true, status: 200 } as Response);

    vi.useFakeTimers();
    const q = new TurnQueue();
    q.enqueue(makeTurn());

    for (let i = 0; i < 5; i++) {
      await vi.runAllTimersAsync();
      await Promise.resolve();
    }

    vi.useRealTimers();

    // Retried and succeeded on second attempt.
    expect(fetchMock.mock.calls.length).toBe(2);
  });
});

describe("TurnQueue — destroyed state", () => {
  it("ignores enqueue after drainAndDestroy", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200 } as Response);

    const q = new TurnQueue();
    q.drainAndDestroy();
    q.enqueue(makeTurn());

    await flushAsync(10);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears pending queue on drainAndDestroy", () => {
    const q = new TurnQueue();
    q.enqueue(makeTurn("c", "1"));
    q.enqueue(makeTurn("c", "2"));
    q.drainAndDestroy();
    expect(q.pending.length).toBe(0);
  });
});

describe("TurnQueue — network error handling", () => {
  it("retries on network error (fetch throws)", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("Network failure"))
      .mockResolvedValue({ ok: true, status: 200 } as Response);

    vi.useFakeTimers();
    const q = new TurnQueue();
    q.enqueue(makeTurn());

    for (let i = 0; i < 5; i++) {
      await vi.runAllTimersAsync();
      await Promise.resolve();
    }

    vi.useRealTimers();

    expect(fetchMock.mock.calls.length).toBe(2);
  });
});

describe("TurnQueue — tool_results in SealedTurn", () => {
  it("persists tool_results in the POST body", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200 } as Response);

    const turn: SealedTurn = {
      conversation_id: "conv-x",
      user:            { text: "run a scan", modality: "voice" },
      assistant:       { text: "I found 3 critical findings.", modality: "voice" },
      tool_results: [
        { call_id: "call-1", tool_name: "get_morning_briefing", result: { total: 3 } },
      ],
    };

    const q = new TurnQueue();
    q.enqueue(turn);

    await flushAsync(10);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(
      (fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string,
    ) as SealedTurn;
    expect(body.tool_results).toHaveLength(1);
    expect(body.tool_results![0].tool_name).toBe("get_morning_briefing");
  });
});
