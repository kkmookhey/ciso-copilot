// Subset of OpenAI Realtime server event types we care about for the web
// voice client. Lifted from Shasta voice/web/events.ts and adapted to the
// Realtime GA event names (response.output_audio_*).
//
// Full reference: https://platform.openai.com/docs/api-reference/realtime-server-events

export type RealtimeServerEvent =
  | { type: "session.created"; [k: string]: unknown }
  | { type: "session.updated"; [k: string]: unknown }
  | { type: "input_audio_buffer.speech_started"; item_id?: string; [k: string]: unknown }
  | { type: "input_audio_buffer.speech_stopped";  item_id?: string; [k: string]: unknown }
  | { type: "conversation.item.input_audio_transcription.completed";
      item_id: string; transcript: string }
  | { type: "response.created"; response: { id: string } }
  | { type: "response.output_audio_transcript.delta";
      response_id: string; item_id: string; delta: string }
  | { type: "response.output_audio_transcript.done";
      response_id: string; item_id: string; transcript: string }
  | { type: "response.function_call_arguments.delta";
      response_id: string; item_id: string; call_id: string; delta: string }
  | { type: "response.function_call_arguments.done";
      response_id: string; item_id: string; call_id: string; name: string; arguments: string }
  | { type: "response.done"; response: { id: string; status: string } }
  | { type: "error"; error: { type: string; message: string } }
  | { type: string; [k: string]: unknown };

export function parseEvent(raw: string): RealtimeServerEvent | null {
  try {
    return JSON.parse(raw) as RealtimeServerEvent;
  } catch {
    return null;
  }
}

export function buildFunctionCallOutput(callId: string, output: string) {
  return {
    type: "conversation.item.create",
    item: {
      type:    "function_call_output",
      call_id: callId,
      output,
    },
  };
}

export function buildResponseCreate() {
  return { type: "response.create" };
}
