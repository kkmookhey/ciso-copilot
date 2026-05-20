// web/src/chat/voiceClient.ts
//
// Browser WebRTC voice client for the SP4 chat surface (Phase 4c).
//
// Lifted from web/src/voice/connection.ts (the working voice modal client)
// and adapted to:
//   - Hit POST /v1/conversations/{id}/voice (GA endpoint, returns {value, …})
//     instead of /voice/session.
//   - Use the shared TOOLS catalog + executeTool() from chat/tools.ts.
//   - Persist turns via TurnQueue (chat/turnQueue.ts, spec §9.2).
//   - Expose a clean callback interface for Task 4c.3 (Composer UI).
//
// WebRTC pattern (identical to connection.ts):
//   1. Mic via getUserMedia (echoCancellation + noiseSuppression).
//   2. RTCPeerConnection — local mic track + remote audio sink.
//   3. "oai-events" data channel for Realtime events (bidirectional JSON).
//   4. SDP offer/answer via POST https://api.openai.com/v1/realtime/calls
//      with Authorization: Bearer ek_... and Content-Type: application/sdp.
//
// ---------------------------------------------------------------------------
// INTERFACE FOR 4c.3 (the Composer UI)
// ---------------------------------------------------------------------------
//
// import { VoiceClient, VoiceState } from "./voiceClient";
//
// const client = new VoiceClient({
//   onStateChange:        (s: VoiceState) => { ... },
//   onVoiceMessage:       (m) => { ... },   // upsert a transcript by item_id
//   onToolResult:         (hints: ArtifactHint[]) => { ... },
//   onSpeechStarted:      () => { ... },    // user started speaking
//   onSyncWarning:        () => { ... },    // transcript POST failed after retries
// });
//
// await client.connect(conversationId);   // → state transitions off→connecting→on
// client.cancelAssistantResponse();       // barge-in: cancel mid-audio response
// client.disconnect();                    // → state transitions on→off
//
// ---------------------------------------------------------------------------

import { validIdToken, signOut } from "../lib/cognito";
import { TOOLS, toRealtimeTools, executeTool } from "./tools";
import type { ArtifactHint, ToolResult } from "./tools";
import { TurnQueue } from "./turnQueue";
import type { SealedTurn, SealedTurnToolResult } from "./turnQueue";

const REST_BASE     = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";
const REALTIME_BASE = "https://api.openai.com/v1/realtime/calls";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** The four states the voice client cycles through. */
export type VoiceState = "off" | "connecting" | "on";

/** Callbacks the UI (4c.3) subscribes to. All are optional except onStateChange. */
export interface VoiceClientCallbacks {
  /** Fired when the connection state transitions (off → connecting → on → off). */
  onStateChange:          (state: "off" | "connecting" | "on") => void;
  /**
   * Upsert a voice message keyed by its Realtime conversation `item_id`.
   *
   * Realtime events do NOT arrive in display order — the user's audio is
   * transcribed asynchronously, so the assistant reply can stream in before
   * the user transcript lands. The client therefore emits each message keyed
   * by its stable `item_id`: the first call for an item creates the bubble in
   * stream order (a user placeholder fires the moment the item is created,
   * before the assistant responds); later calls update that same bubble.
   * `final=true` marks the last update for that item.
   */
  onVoiceMessage?:        (m: { itemId: string; role: "user" | "assistant"; text: string; final: boolean; drop?: boolean }) => void;
  /** Tool executed — array of ArtifactHints to surface as cards. */
  onToolResult?:          (hints: ArtifactHint[]) => void;
  /** User started speaking. 4c.3 calls cancelAssistantResponse() if needed. */
  onSpeechStarted?:       () => void;
  /** TurnQueue has permanently failed a turn — transcript is out of sync. */
  onSyncWarning?:         () => void;
}

/**
 * Whisper / Realtime input transcription hallucinates short filler tokens
 * ("Bye", "Thanks", "you") on silent or non-speech audio segments, and also
 * emits empty strings. A transcript that is empty/whitespace-only is always
 * dropped; see shouldDropUserTranscript().
 */
export function shouldDropUserTranscript(transcript: string | null | undefined): boolean {
  return !transcript || transcript.trim().length === 0;
}

// Shape of the ephemeral key response from POST /v1/conversations/{id}/voice
interface VoiceSessionResponse {
  value:      string;   // "ek_..." ephemeral key
  expires_at: number;
  session?:   unknown;
}

// ---------------------------------------------------------------------------
// VoiceClient
// ---------------------------------------------------------------------------

export class VoiceClient {
  private readonly callbacks: VoiceClientCallbacks;

  // WebRTC internals
  private pc:           RTCPeerConnection | null = null;
  private dataChannel:  RTCDataChannel    | null = null;
  private audioElement: HTMLAudioElement  | null = null;
  private micTrack:     MediaStreamTrack  | null = null;

  // Current connection state
  private state: "off" | "connecting" | "on" = "off";

  // Active conversation — set at connect() time
  private conversationId: string | null = null;

  // TurnQueue for background persistence
  private turnQueue: TurnQueue | null = null;

  // Cached Bearer token — captured at connect() so the beforeunload handler
  // (which is synchronous) can use it without an async validIdToken() call.
  private cachedToken: string | null = null;

  // beforeunload handler reference — kept so we can remove it on disconnect.
  private readonly unloadHandler = (): void => {
    if (this.turnQueue && this.cachedToken) {
      this.turnQueue.flushHeadOnUnload(this.cachedToken);
    }
  };

  // Per-turn accumulation state
  // These refs are reset at the start of each new response.
  private userTranscriptByItem:      Map<string, string> = new Map();
  private assistantTranscriptByItem: Map<string, string> = new Map();
  private toolCallsThisTurn:         SealedTurnToolResult[] = [];
  private artifactHintsThisTurn:     ArtifactHint[] = [];
  private latestUserItemId:          string | null = null;
  private latestAssistantItemId:     string | null = null;
  // User item_ids for which a placeholder bubble has already been emitted.
  // Created from `conversation.item.created` so the user message lands in the
  // correct stream position BEFORE the assistant's reply streams in.
  private placeholderedUserItems:    Set<string> = new Set();

  // Response-lifecycle guard (same pattern as VoiceChat.tsx).
  // Sending response.create while a response is active triggers
  // "Conversation already has an active response in progress" 400.
  private responseActive:          boolean = false;
  private pendingResponseCreate:   boolean = false;

  // Whether the assistant is currently producing audio (for barge-in).
  private assistantAudioActive: boolean = false;

  constructor(callbacks: VoiceClientCallbacks) {
    this.callbacks = callbacks;
  }

  // ---------------------------------------------------------------------------
  // connect
  // ---------------------------------------------------------------------------

  /**
   * Open a WebRTC connection for the given conversation.
   * Transitions: off → connecting → on.
   *
   * Throws if getUserMedia is denied, the ephemeral key POST fails,
   * or the SDP exchange with OpenAI fails.
   */
  async connect(conversationId: string): Promise<void> {
    if (this.state !== "off") {
      throw new Error("VoiceClient: already connected or connecting");
    }

    this.conversationId = conversationId;
    this.setState("connecting");

    try {
      // 1. Mint ephemeral key.
      const session = await this.fetchEphemeralKey(conversationId);

      // 2. Mic.
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.micTrack = mediaStream.getAudioTracks()[0];

      // 3. RTCPeerConnection.
      this.pc = new RTCPeerConnection();
      this.pc.addTrack(this.micTrack, mediaStream);

      // 4. Remote audio sink (browser plays assistant voice here).
      this.audioElement = document.createElement("audio");
      this.audioElement.autoplay = true;
      this.audioElement.style.display = "none";
      document.body.appendChild(this.audioElement);
      this.pc.ontrack = (ev) => {
        if (this.audioElement) this.audioElement.srcObject = ev.streams[0];
      };

      // 5. "oai-events" data channel.
      this.dataChannel = this.pc.createDataChannel("oai-events");
      this.dataChannel.onmessage = (msg) => this.handleRawEvent(msg.data);
      this.dataChannel.onopen    = () => this.setState("on");

      this.pc.onconnectionstatechange = () => {
        const s = this.pc?.connectionState;
        if (s === "failed" || s === "closed" || s === "disconnected") {
          this.teardown();
        }
      };

      // 6. SDP offer.
      const offer = await this.pc.createOffer();
      await this.pc.setLocalDescription(offer);

      // 7. SDP exchange with OpenAI — lifted verbatim from web/src/voice/connection.ts.
      const sdpRes = await fetch(REALTIME_BASE, {
        method:  "POST",
        body:    offer.sdp ?? "",
        headers: {
          Authorization:  `Bearer ${session.value}`,
          "Content-Type": "application/sdp",
        },
      });
      if (!sdpRes.ok) {
        throw new Error(
          `OpenAI SDP exchange failed: ${sdpRes.status} ${await sdpRes.text()}`,
        );
      }
      const answerSdp = await sdpRes.text();
      await this.pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

      // 8. TurnQueue for background persistence.
      this.turnQueue = new TurnQueue({
        onSyncWarning: () => this.callbacks.onSyncWarning?.(),
      });

      // 9. Register beforeunload flush.
      //    Token was already fetched in fetchEphemeralKey (step 1); it's cached
      //    in this.cachedToken so the synchronous beforeunload handler can use it.
      window.addEventListener("beforeunload", this.unloadHandler);

      // Reset per-turn accumulators.
      this.resetTurnAccumulators();

    } catch (err) {
      this.teardown();
      throw err;
    }
  }

  // ---------------------------------------------------------------------------
  // disconnect
  // ---------------------------------------------------------------------------

  /**
   * Close the voice connection. State transitions: on → off.
   * The TurnQueue continues to drain in the background — it is NOT destroyed
   * on disconnect so in-flight persistence completes.
   */
  disconnect(): void {
    this.teardown();
  }

  // ---------------------------------------------------------------------------
  // cancelAssistantResponse  (barge-in)
  // ---------------------------------------------------------------------------

  /**
   * Cancel the assistant's in-progress response.
   * 4c.3 calls this from `onSpeechStarted` if `assistantAudioActive` is true.
   * Sends `response.cancel` over the data channel.
   */
  cancelAssistantResponse(): void {
    if (this.assistantAudioActive) {
      this.send({ type: "response.cancel" });
      this.assistantAudioActive = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Internal — event handling
  // ---------------------------------------------------------------------------

  private handleRawEvent(raw: string): void {
    let event: { type: string; [k: string]: unknown };
    try {
      event = JSON.parse(raw);
    } catch {
      return;
    }
    this.handleEvent(event);
  }

  private handleEvent(event: { type: string; [k: string]: unknown }): void {
    switch (event.type) {

      // -----------------------------------------------------------------------
      // Session ready
      // -----------------------------------------------------------------------
      case "session.created":
        // Data channel onopen fires first; this is belt-and-suspenders.
        if (this.state !== "on") this.setState("on");
        break;

      // -----------------------------------------------------------------------
      // Response lifecycle
      // -----------------------------------------------------------------------
      case "response.created":
        this.responseActive = true;
        // A new response clears the assistant accumulator for this turn.
        this.assistantAudioActive = true;
        break;

      // -----------------------------------------------------------------------
      // User speech signals
      // -----------------------------------------------------------------------
      case "input_audio_buffer.speech_started":
        this.callbacks.onSpeechStarted?.();
        break;

      case "input_audio_buffer.speech_stopped":
        // No UI action needed — conversation.item.created carries the item_id
        // for the placeholder; transcription.completed carries the text.
        break;

      // -----------------------------------------------------------------------
      // Conversation item created.
      //
      // Fires for every Realtime conversation item the moment it exists. For a
      // user audio item this lands AFTER speech_stopped but BEFORE the
      // assistant's response.created — so emitting the user placeholder here
      // puts the user bubble in the correct stream position before the
      // assistant reply streams in (fixes the "assistant above user" bug). The
      // placeholder is empty until input_audio_transcription.completed fills it.
      // -----------------------------------------------------------------------
      case "conversation.item.created": {
        const ev = event as unknown as {
          item?: { id?: string; role?: string; type?: string };
        };
        const item = ev.item;
        if (
          item?.id &&
          item.role === "user" &&
          item.type === "message" &&
          !this.placeholderedUserItems.has(item.id)
        ) {
          this.placeholderedUserItems.add(item.id);
          this.userTranscriptByItem.set(item.id, "");
          this.latestUserItemId = item.id;
          // Empty placeholder bubble, correctly ordered. Filled when the async
          // transcription.completed event arrives (or dropped if it never
          // carries real text — see the .completed handler below).
          this.callbacks.onVoiceMessage?.({
            itemId: item.id, role: "user", text: "", final: false,
          });
        }
        break;
      }

      // -----------------------------------------------------------------------
      // User transcript (Whisper transcription)
      // GA API delivers the full transcript in a single .completed event.
      // There is no .delta / .done pair for user input transcription.
      //
      // Whisper hallucinates filler ("Bye", "you") on silent/non-speech audio
      // and also emits empty strings — drop those. Bug 1: a dropped transcript
      // also removes its placeholder so no empty user bubble lingers.
      // -----------------------------------------------------------------------
      case "conversation.item.input_audio_transcription.completed": {
        const ev = event as unknown as { item_id: string; transcript: string };
        if (shouldDropUserTranscript(ev.transcript)) {
          this.userTranscriptByItem.delete(ev.item_id);
          this.placeholderedUserItems.delete(ev.item_id);
          if (this.latestUserItemId === ev.item_id) this.latestUserItemId = null;
          // Signal a drop so the UI removes the empty placeholder bubble.
          this.callbacks.onVoiceMessage?.({
            itemId: ev.item_id, role: "user", text: "", final: true, drop: true,
          });
          break;
        }
        this.userTranscriptByItem.set(ev.item_id, ev.transcript);
        this.latestUserItemId = ev.item_id;
        this.callbacks.onVoiceMessage?.({
          itemId: ev.item_id, role: "user", text: ev.transcript, final: true,
        });
        break;
      }

      // -----------------------------------------------------------------------
      // Assistant transcript (audio transcript from Realtime)
      // Keyed by item_id: a turn that spans multiple audio items maps each item
      // to its own stable bubble, and re-deltas for an item update that same
      // bubble — never a duplicate or stray partial bubble (Bug 4).
      // -----------------------------------------------------------------------
      case "response.output_audio_transcript.delta": {
        const ev  = event as unknown as { item_id: string; delta: string };
        const cur  = this.assistantTranscriptByItem.get(ev.item_id) ?? "";
        const next = cur + ev.delta;
        this.assistantTranscriptByItem.set(ev.item_id, next);
        this.latestAssistantItemId = ev.item_id;
        this.callbacks.onVoiceMessage?.({
          itemId: ev.item_id, role: "assistant", text: next, final: false,
        });
        break;
      }

      case "response.output_audio_transcript.done": {
        const ev = event as unknown as { item_id: string; transcript: string };
        this.assistantTranscriptByItem.set(ev.item_id, ev.transcript);
        this.latestAssistantItemId = ev.item_id;
        this.callbacks.onVoiceMessage?.({
          itemId: ev.item_id, role: "assistant", text: ev.transcript, final: true,
        });
        this.assistantAudioActive = false;
        break;
      }

      // -----------------------------------------------------------------------
      // Function call (tool use)
      // -----------------------------------------------------------------------
      case "response.function_call_arguments.done": {
        const ev = event as unknown as {
          call_id:   string;
          name:      string;
          arguments: string;
        };
        // Fire-and-forget — awaited internally but does not block the audio path.
        this.handleToolCall(ev.call_id, ev.name, ev.arguments);
        break;
      }

      // -----------------------------------------------------------------------
      // response.done — seal and enqueue the turn
      // -----------------------------------------------------------------------
      case "response.done": {
        this.responseActive       = false;
        this.assistantAudioActive = false;

        this.sealTurn();

        // Flush any queued response.create (same defensive pattern as VoiceChat.tsx).
        if (this.pendingResponseCreate) {
          this.pendingResponseCreate = false;
          this.send({ type: "response.create" });
        }
        break;
      }

      // -----------------------------------------------------------------------
      // Error
      // -----------------------------------------------------------------------
      case "error":
        // No special handling here; connection-level errors go through
        // pc.onconnectionstatechange → teardown → state=off.
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // handleToolCall — async, fire-and-forget from handleEvent
  // ---------------------------------------------------------------------------

  private async handleToolCall(callId: string, name: string, argsJson: string): Promise<void> {
    let args: unknown;
    try {
      args = JSON.parse(argsJson || "{}");
    } catch {
      args = {};
    }

    let toolResult: ToolResult;
    try {
      toolResult = await executeTool(name, args);
    } catch (err) {
      toolResult = { result: { error: err instanceof Error ? err.message : String(err) } };
    }

    // Accumulate for turn sealing.
    this.toolCallsThisTurn.push({
      call_id:   callId,
      tool_name: name,
      result:    toolResult.result,
    });

    // Surface artifact hints to the UI.
    const hints: ArtifactHint[] = [];
    if (toolResult._artifact_hints) {
      hints.push(...toolResult._artifact_hints);
    } else if (toolResult._artifact_hint) {
      hints.push(toolResult._artifact_hint);
    }
    if (hints.length > 0) {
      this.artifactHintsThisTurn.push(...hints);
      this.callbacks.onToolResult?.(hints);
    }

    // Send function_call_output back to Realtime.
    this.send({
      type: "conversation.item.create",
      item: {
        type:    "function_call_output",
        call_id: callId,
        output:  JSON.stringify(toolResult.result),
      },
    });

    // Guard against sending response.create while a response is active.
    if (this.responseActive) {
      this.pendingResponseCreate = true;
    } else {
      this.send({ type: "response.create" });
    }
  }

  // ---------------------------------------------------------------------------
  // sealTurn — called on response.done
  // ---------------------------------------------------------------------------

  private sealTurn(): void {
    if (!this.conversationId || !this.turnQueue) return;

    // Collect user transcript from the most recent item.
    const userText = this.latestUserItemId
      ? (this.userTranscriptByItem.get(this.latestUserItemId) ?? "")
      : "";

    // Collect assistant transcript — could span multiple audio items in theory;
    // use the most recent for now (covers >99% of turns).
    const assistantText = this.latestAssistantItemId
      ? (this.assistantTranscriptByItem.get(this.latestAssistantItemId) ?? "")
      : "";

    const turn: SealedTurn = {
      conversation_id: this.conversationId,
      user:            { text: userText,      modality: "voice" },
      assistant:       { text: assistantText, modality: "voice" },
      ...(this.toolCallsThisTurn.length > 0
        ? { tool_results: [...this.toolCallsThisTurn] }
        : {}),
    };

    this.turnQueue.enqueue(turn);

    // Reset accumulators for the next turn.
    this.resetTurnAccumulators();
  }

  private resetTurnAccumulators(): void {
    this.userTranscriptByItem      = new Map();
    this.assistantTranscriptByItem = new Map();
    this.placeholderedUserItems    = new Set();
    this.toolCallsThisTurn         = [];
    this.artifactHintsThisTurn     = [];
    this.latestUserItemId          = null;
    this.latestAssistantItemId     = null;
    this.responseActive            = false;
    this.pendingResponseCreate     = false;
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  private send(obj: object): void {
    if (this.dataChannel?.readyState === "open") {
      this.dataChannel.send(JSON.stringify(obj));
    }
  }

  private setState(s: "off" | "connecting" | "on"): void {
    this.state = s;
    this.callbacks.onStateChange(s);
  }

  private teardown(): void {
    try { this.micTrack?.stop(); }         catch { /* ignore */ }
    try { this.dataChannel?.close(); }     catch { /* ignore */ }
    try { this.pc?.close(); }              catch { /* ignore */ }
    try { this.audioElement?.remove(); }   catch { /* ignore */ }

    this.pc           = null;
    this.dataChannel  = null;
    this.audioElement = null;
    this.micTrack     = null;

    // Remove the beforeunload flush listener — voice session is ending cleanly.
    window.removeEventListener("beforeunload", this.unloadHandler);
    this.cachedToken = null;

    // The TurnQueue is intentionally NOT destroyed here — it keeps draining
    // any in-flight turns in the background after disconnect.

    if (this.state !== "off") {
      this.setState("off");
    }
  }

  private async fetchEphemeralKey(conversationId: string): Promise<VoiceSessionResponse> {
    const token = await validIdToken();
    if (!token) { signOut(); throw new Error("Sign in to use voice."); }

    // Cache the Cognito Bearer token so the synchronous beforeunload handler
    // can include it in the keepalive-fetch unload flush (§9.2).
    this.cachedToken = token;

    const res = await fetch(
      `${REST_BASE}/conversations/${conversationId}/voice`,
      {
        method:  "POST",
        headers: {
          Authorization:  `Bearer ${token}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ tools: toRealtimeTools(TOOLS) }),
      },
    );

    if (res.status === 401) { signOut(); throw new Error("unauthorized"); }
    if (!res.ok) {
      throw new Error(
        `Failed to mint voice session: ${res.status} ${await res.text()}`,
      );
    }
    return res.json();
  }
}
