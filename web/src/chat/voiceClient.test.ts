import { describe, it, expect } from "vitest";
import { shouldDropUserTranscript } from "./voiceClient";

// The WebRTC machinery in VoiceClient can't be unit-tested, but the
// empty-transcript guard (Bug 1, client side) is a pure function and can.
describe("shouldDropUserTranscript", () => {
  it("drops an empty string", () => {
    expect(shouldDropUserTranscript("")).toBe(true);
  });

  it("drops whitespace-only transcripts", () => {
    expect(shouldDropUserTranscript("   ")).toBe(true);
    expect(shouldDropUserTranscript("\n\t ")).toBe(true);
  });

  it("drops null / undefined", () => {
    expect(shouldDropUserTranscript(null)).toBe(true);
    expect(shouldDropUserTranscript(undefined)).toBe(true);
  });

  it("keeps a real transcript", () => {
    expect(shouldDropUserTranscript("What is my AWS posture?")).toBe(false);
  });

  it("keeps a short but real word (no hard blocklist)", () => {
    // "Bye" is NOT hard-blocked — the principled fix is the empty filter plus
    // VAD tuning, not a word blocklist. A genuine short utterance survives.
    expect(shouldDropUserTranscript("Bye")).toBe(false);
  });
});
