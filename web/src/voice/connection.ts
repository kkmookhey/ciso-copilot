// WebRTC connection to OpenAI Realtime API.
// Lifted from Shasta voice/web/connection.ts and adapted to our authed
// /voice/session endpoint (which mints the ephemeral key via OpenAI's
// `/v1/realtime/client_secrets` GA endpoint).
//
// The browser PC connects directly to OpenAI — our backend is NOT on the
// audio path. The data channel carries Realtime events bidirectionally.

import { parseEvent, type RealtimeServerEvent } from "./events";
import { validIdToken } from "../lib/cognito";

const API_BASE_URL  = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";
const REALTIME_BASE = "https://api.openai.com/v1/realtime/calls";

export interface VoiceConnection {
  pc:           RTCPeerConnection;
  dataChannel:  RTCDataChannel;
  audioElement: HTMLAudioElement;
  micTrack:     MediaStreamTrack;
  send:         (obj: object) => void;
  close:        () => void;
}

export interface VoiceConnectionHooks {
  onEvent:                 (event: RealtimeServerEvent) => void;
  onConnectionStateChange: (state: RTCPeerConnectionState) => void;
}

interface SessionTokenResponse {
  session_id:    string;
  client_secret: string;     // ephemeral "ek_..."
  expires_at:    number;
  model:         string;
}

async function fetchEphemeralToken(): Promise<SessionTokenResponse> {
  const token = await validIdToken();
  if (!token) throw new Error("Sign in to use voice chat.");
  const resp = await fetch(`${API_BASE_URL}/voice/session`, {
    method:  "POST",
    headers: {
      "content-type": "application/json",
      authorization:  `Bearer ${token}`,
    },
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`Failed to mint voice session: ${resp.status} ${detail}`);
  }
  return resp.json();
}

export async function connectVoice(hooks: VoiceConnectionHooks): Promise<VoiceConnection> {
  const token = await fetchEphemeralToken();

  // 1. Mic
  const mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const micTrack = mediaStream.getAudioTracks()[0];

  // 2. Peer connection
  const pc = new RTCPeerConnection();
  pc.addTrack(micTrack, mediaStream);

  // 3. Audio sink — model's voice plays here
  const audioElement = document.createElement("audio");
  audioElement.autoplay = true;
  audioElement.style.display = "none";
  document.body.appendChild(audioElement);
  pc.ontrack = (event) => { audioElement.srcObject = event.streams[0]; };

  // 4. Data channel for Realtime events
  const dataChannel = pc.createDataChannel("oai-events");
  dataChannel.onmessage = (msg) => {
    const event = parseEvent(msg.data);
    if (event) hooks.onEvent(event);
  };

  pc.onconnectionstatechange = () => hooks.onConnectionStateChange(pc.connectionState);

  // 5. SDP offer
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  // 6. Exchange with OpenAI Realtime GA endpoint
  const sdpResponse = await fetch(REALTIME_BASE, {
    method:  "POST",
    body:    offer.sdp ?? "",
    headers: {
      Authorization:  `Bearer ${token.client_secret}`,
      "Content-Type": "application/sdp",
    },
  });
  if (!sdpResponse.ok) {
    pc.close();
    micTrack.stop();
    audioElement.remove();
    throw new Error(`OpenAI SDP exchange failed: ${sdpResponse.status} ${await sdpResponse.text()}`);
  }
  const answerSdp = await sdpResponse.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

  const send = (obj: object) => {
    if (dataChannel.readyState === "open") dataChannel.send(JSON.stringify(obj));
  };

  const close = () => {
    try { micTrack.stop(); } catch {/* ignore */}
    try { dataChannel.close(); } catch {/* ignore */}
    try { pc.close(); } catch {/* ignore */}
    try { audioElement.remove(); } catch {/* ignore */}
  };

  return { pc, dataChannel, audioElement, micTrack, send, close };
}
