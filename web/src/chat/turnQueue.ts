// web/src/chat/turnQueue.ts
//
// TurnQueue — per-turn voice transcript persistence (SP4 §9.2).
//
// On each `response.done` the voice client seals the completed user+assistant
// turn and hands it to this queue. A background worker POSTs turns FIFO to
// `POST /v1/conversations/{id}/messages` with `modality: "voice"`. The POST
// is fire-and-forget relative to the audio path — WebRTC frames live on the
// browser's native media engine; a fetch() cannot block packetization.
//
// Failure handling:
//   - Exponential back-off, max 5 attempts, cap 30 s per turn.
//   - After 5 failures the turn is discarded and `onSyncWarning` is called
//     once. Subsequent turns continue to drain normally.
//   - The queue survives a voice disconnect; call `drainAndDestroy()` if you
//     want to stop it permanently.

import { validIdToken, signOut } from "../lib/cognito";
import { env } from "../lib/env";

const REST_BASE = env.apiBaseUrl;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type VoiceModality = "voice";

export interface SealedTurnUser {
  text:     string;
  modality: VoiceModality;
}

export interface SealedTurnAssistant {
  text:     string;
  modality: VoiceModality;
}

export interface SealedTurnToolResult {
  call_id:   string;
  tool_name: string;
  result:    unknown;
}

/**
 * One completed voice turn ready to persist.
 *
 * `user.text`      — transcript of what the user said (may be empty if Whisper
 *                    didn't fire before `response.done`).
 * `assistant.text` — final transcript of what the assistant said.
 * `tool_results`   — all tool calls that happened inside this turn (optional).
 */
export interface SealedTurn {
  conversation_id: string;
  user:            SealedTurnUser;
  assistant:       SealedTurnAssistant;
  tool_results?:   SealedTurnToolResult[];
}

// ---------------------------------------------------------------------------
// TurnQueue
// ---------------------------------------------------------------------------

export interface TurnQueueOptions {
  /** Called once when a turn has exhausted all retries (transcript out of sync). */
  onSyncWarning?: () => void;
}

const MAX_ATTEMPTS = 5;
const BASE_MS      = 500;
const CAP_MS       = 30_000;

function backoffMs(attempt: number): number {
  // Exponential: 500 * 2^n, capped at 30 s, plus ±10 % jitter.
  const raw  = BASE_MS * Math.pow(2, attempt);
  const delay = Math.min(raw, CAP_MS);
  return delay * (0.9 + 0.2 * Math.random());
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export class TurnQueue {
  private queue:    SealedTurn[] = [];
  private flushing: boolean      = false;
  private destroyed: boolean     = false;
  private readonly opts: TurnQueueOptions;

  constructor(opts: TurnQueueOptions = {}) {
    this.opts = opts;
  }

  /**
   * Enqueue a sealed turn for persistence. Non-blocking — returns immediately.
   * The flush worker runs in the background.
   */
  enqueue(turn: SealedTurn): void {
    if (this.destroyed) return;
    this.queue.push(turn);
    // Kick the worker if it's idle.
    if (!this.flushing) this.flush();
  }

  /** Snapshot of the pending queue (for sendBeacon on unload). */
  get pending(): readonly SealedTurn[] {
    return this.queue;
  }

  /** Permanently stop the queue. Any enqueued turns are discarded. */
  drainAndDestroy(): void {
    this.destroyed = true;
    this.queue     = [];
  }

  /**
   * Best-effort flush of the HEAD turn on page unload.
   *
   * Called from `window.beforeunload`. Uses `fetch` with `keepalive: true` so
   * the request survives the page going away AND can carry the Authorization
   * header (navigator.sendBeacon cannot set headers — §9.2 note).
   *
   * Fire-and-forget: no await, no error handling.  Only the head turn is sent;
   * anything beyond the head is acceptable loss (matches spec §9.2).
   */
  flushHeadOnUnload(token: string): void {
    const head = this.queue[0];
    if (!head) return;

    // Intentionally not awaited — the page is going away.
    void fetch(
      `${REST_BASE}/conversations/${head.conversation_id}/messages`,
      {
        method:    "POST",
        keepalive: true,
        headers: {
          Authorization:  `Bearer ${token}`,
          "content-type": "application/json",
        },
        body: JSON.stringify(head),
      },
    );
  }

  // ---------------------------------------------------------------------------
  // Internal
  // ---------------------------------------------------------------------------

  private async flush(): Promise<void> {
    if (this.flushing || this.queue.length === 0 || this.destroyed) return;
    this.flushing = true;

    const turn = this.queue[0];

    const posted = await this.postWithRetry(turn);

    if (posted) {
      this.queue.shift();
    } else {
      // Max retries exhausted — drop the turn and warn.
      this.queue.shift();
      this.opts.onSyncWarning?.();
    }

    this.flushing = false;

    // If more turns are waiting, schedule the next flush.
    if (this.queue.length > 0 && !this.destroyed) {
      setTimeout(() => this.flush(), 0);
    }
  }

  /**
   * POST a single turn with exponential back-off.
   * Returns `true` if the POST succeeded, `false` after MAX_ATTEMPTS failures.
   */
  private async postWithRetry(turn: SealedTurn): Promise<boolean> {
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      try {
        const token = await validIdToken();
        if (!token) { signOut(); return false; }

        const res = await fetch(
          `${REST_BASE}/conversations/${turn.conversation_id}/messages`,
          {
            method:  "POST",
            headers: {
              Authorization:  `Bearer ${token}`,
              "content-type": "application/json",
            },
            body: JSON.stringify(turn),
          },
        );

        if (res.ok) return true;

        // 4xx (except 429) are unlikely to be transient — don't burn retries.
        if (res.status !== 429 && res.status >= 400 && res.status < 500) {
          return false;
        }

        // 5xx or 429 → retry after back-off.
      } catch {
        // Network error → retry.
      }

      if (attempt < MAX_ATTEMPTS - 1) {
        await sleep(backoffMs(attempt));
      }
    }

    return false;
  }
}
