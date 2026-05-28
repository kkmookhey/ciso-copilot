import Foundation
import AVFoundation
import WebRTC
import Observation

/// WebRTC-based OpenAI Realtime client. Matches the GA flow:
///   1. Backend mints an ephemeral key via /voice/session (POST /v1/realtime/client_secrets).
///   2. We create an RTCPeerConnection (no STUN/TURN — OpenAI is the SFU).
///   3. Add a local audio track from the RTC audio source (built-in AEC, AGC, NS).
///   4. Open a data channel "oai-events" for Realtime control events.
///   5. Generate an SDP offer, POST it to https://api.openai.com/v1/realtime/calls
///      with the ephemeral key as Bearer, get the answer SDP back.
///   6. Audio flows full-duplex over WebRTC. Events arrive on the data channel.
///
/// We deliberately avoid AVAudioEngine + manual PCM streaming. iPhone speaker
/// at conversation distance overwhelms iOS's standalone AEC, so the prior
/// WebSocket-based implementation produced "jumbled, repeating" output as
/// VAD kept firing on echo. WebRTC's audio stack includes Google's AEC3 which
/// handles this natively.
@MainActor
@Observable
final class VoiceClient: NSObject {

    enum State { case idle, connecting, listening, speaking, error }

    private(set) var state:        State  = .idle
    private(set) var transcript:   [TranscriptLine] = []
    private(set) var errorMessage: String?

    struct TranscriptLine: Identifiable {
        let id = UUID()
        let role: Role
        var text: String
        enum Role { case user, assistant }
    }

    // MARK: - Dependencies

    private weak var api: APIClient?
    func bind(api: APIClient) { self.api = api }

    // MARK: - WebRTC

    private let factory: RTCPeerConnectionFactory
    private var peerConnection: RTCPeerConnection?
    private var dataChannel:    RTCDataChannel?
    private var audioTrack:     RTCAudioTrack?

    /// Accumulators for streaming partials.
    private var assistantPartial = ""
    private var userPartial      = ""

    /// Pending tool-call argument streams keyed by call_id.
    private var pendingToolArgs: [String: String] = [:]
    private var pendingToolNames: [String: String] = [:]

    /// If set before start(), this developer message is injected into the
    /// conversation once the data channel opens, triggering Shasta to speak first.
    private var pendingSeedMessage: String?

    override init() {
        RTCInitializeSSL()
        self.factory = RTCPeerConnectionFactory(
            encoderFactory: RTCDefaultVideoEncoderFactory(),
            decoderFactory: RTCDefaultVideoDecoderFactory(),
        )
        super.init()
    }

    // MARK: - Public API

    /// Start a session and, once the data channel opens, inject a developer
    /// message so Shasta speaks the briefing without waiting for user input.
    func start(seedDeveloperMessage: String) async {
        pendingSeedMessage = seedDeveloperMessage
        await start()
    }

    func start() async {
        guard state == .idle || state == .error else { return }
        state = .connecting
        errorMessage = nil
        assistantPartial = ""
        userPartial      = ""

        let granted = await AVAudioApplication.requestRecordPermission()
        guard granted else { failed("Microphone permission denied."); return }

        guard let api else { failed("Voice client not bound."); return }
        let session: VoiceSessionResponse
        do {
            session = try await api.voiceSession()
        } catch {
            failed("Failed to start voice session. \(error.localizedDescription)")
            return
        }
        guard let clientSecret = session.client_secret else {
            failed("Backend did not return a client secret (is OPENAI_API_KEY configured?).")
            return
        }

        do {
            try configureRtcAudioSession()
            try setupPeerConnection()
            try await sdpExchange(ephemeralKey: clientSecret)
        } catch {
            failed("WebRTC setup failed: \(error.localizedDescription)")
            teardown()
            return
        }

        state = .listening
    }

    func stop() {
        teardown()
        state = .idle
    }

    // MARK: - WebRTC setup

    private func configureRtcAudioSession() throws {
        let s = RTCAudioSession.sharedInstance()
        s.lockForConfiguration()
        defer { s.unlockForConfiguration() }
        // Category options: defaultToSpeaker (avoid earpiece), allow Bluetooth.
        try s.setCategory(
            .playAndRecord,
            with: [.defaultToSpeaker, .allowBluetoothA2DP, .allowBluetooth]
        )
        // .videoChat (FaceTime-style) keeps VPIO echo-cancellation but at
        // a louder output level than .voiceChat. AEC is critical when the
        // phone is on a desk in speaker mode — without it, Shasta hears
        // her own voice and gets into a feedback loop.
        try s.setMode(.videoChat)
        try s.setActive(true)
        // Force speaker AFTER activation — .defaultToSpeaker isn't honored
        // when an established route already points at the receiver. Call
        // this on RTCAudioSession (not AVAudioSession) so it respects the
        // configuration lock WebRTC is holding.
        try s.overrideOutputAudioPort(.speaker)
        // Re-assert the speaker route whenever iOS changes it (AirPods
        // plug/unplug, proximity sensor, etc.). One-shot overrides get
        // reverted by iOS on route changes.
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { _ in
            let rs = RTCAudioSession.sharedInstance()
            rs.lockForConfiguration()
            defer { rs.unlockForConfiguration() }
            try? rs.overrideOutputAudioPort(.speaker)
        }
    }

    private func setupPeerConnection() throws {
        let config = RTCConfiguration()
        config.iceServers    = []                  // OpenAI handles transport — no STUN/TURN needed.
        config.sdpSemantics  = .unifiedPlan
        config.bundlePolicy  = .maxBundle
        config.rtcpMuxPolicy = .require

        let constraints = RTCMediaConstraints(
            mandatoryConstraints: nil,
            optionalConstraints: ["DtlsSrtpKeyAgreement": "true"],
        )

        guard let pc = factory.peerConnection(with: config, constraints: constraints, delegate: self) else {
            throw VoiceError.notConfigured
        }
        peerConnection = pc

        // Local mic → WebRTC audio source (handles AEC/AGC/NS internally).
        let audioSource = factory.audioSource(with: RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: nil))
        let track = factory.audioTrack(with: audioSource, trackId: "audio0")
        pc.add(track, streamIds: ["stream0"])
        audioTrack = track

        // Data channel for Realtime control + event stream.
        let dcInit = RTCDataChannelConfiguration()
        dcInit.isOrdered = true
        if let dc = pc.dataChannel(forLabel: "oai-events", configuration: dcInit) {
            dc.delegate = self
            dataChannel = dc
        }
    }

    private func sdpExchange(ephemeralKey: String) async throws {
        guard let pc = peerConnection else { throw VoiceError.notConfigured }

        let offerConstraints = RTCMediaConstraints(
            mandatoryConstraints: ["OfferToReceiveAudio": "true"],
            optionalConstraints: nil,
        )

        let offer: RTCSessionDescription = try await withCheckedThrowingContinuation { cont in
            pc.offer(for: offerConstraints) { sdp, err in
                if let err { cont.resume(throwing: err); return }
                guard let sdp else { cont.resume(throwing: VoiceError.notConfigured); return }
                cont.resume(returning: sdp)
            }
        }
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            pc.setLocalDescription(offer) { err in
                if let err { cont.resume(throwing: err) } else { cont.resume() }
            }
        }

        // POST the offer SDP to OpenAI's /calls endpoint.
        guard let url = URL(string: "https://api.openai.com/v1/realtime/calls") else {
            throw VoiceError.notConfigured
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(ephemeralKey)", forHTTPHeaderField: "Authorization")
        req.setValue("application/sdp",        forHTTPHeaderField: "Content-Type")
        req.httpBody = offer.sdp.data(using: .utf8)

        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw VoiceError.sdpExchangeFailed(body.prefix(200).description)
        }
        guard let answerSDP = String(data: data, encoding: .utf8), !answerSDP.isEmpty else {
            throw VoiceError.notConfigured
        }

        let answer = RTCSessionDescription(type: .answer, sdp: answerSDP)
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            pc.setRemoteDescription(answer) { err in
                if let err { cont.resume(throwing: err) } else { cont.resume() }
            }
        }
    }

    // MARK: - Teardown

    private func teardown() {
        dataChannel?.close()
        dataChannel = nil
        peerConnection?.close()
        peerConnection = nil
        audioTrack = nil

        let s = RTCAudioSession.sharedInstance()
        s.lockForConfiguration()
        try? s.setActive(false)
        s.unlockForConfiguration()
    }

    // MARK: - Realtime event handling

    fileprivate func handleEvent(_ event: [String: Any]) {
        guard let type = event["type"] as? String else { return }

        switch type {

        case "session.created", "session.updated":
            break

        case "input_audio_buffer.speech_started":
            state = .listening

        case "input_audio_buffer.speech_stopped":
            break

        case "conversation.item.input_audio_transcription.delta":
            if let d = event["delta"] as? String { userPartial += d }

        case "conversation.item.input_audio_transcription.completed",
             "conversation.item.input_audio_transcription.done":
            let final = (event["transcript"] as? String) ?? userPartial
            if !final.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                transcript.append(.init(role: .user, text: final))
            }
            userPartial = ""

        case "response.output_audio_transcript.delta":
            state = .speaking
            if let d = event["delta"] as? String { assistantPartial += d }

        case "response.output_audio_transcript.done":
            let final = (event["transcript"] as? String) ?? assistantPartial
            if !final.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                transcript.append(.init(role: .assistant, text: final))
            }
            assistantPartial = ""

        case "response.function_call_arguments.delta":
            if let id    = event["call_id"] as? String,
               let name  = event["name"]    as? String,
               let delta = event["delta"]   as? String {
                pendingToolNames[id] = name
                pendingToolArgs[id, default: ""] += delta
            }

        case "response.function_call_arguments.done":
            if let callId = event["call_id"] as? String,
               let name   = event["name"]    as? String {
                let args = (event["arguments"] as? String) ?? pendingToolArgs[callId] ?? "{}"
                pendingToolArgs.removeValue(forKey: callId)
                pendingToolNames.removeValue(forKey: callId)
                Task { await self.dispatchTool(callId: callId, name: name, argsJson: args) }
            }

        case "response.done":
            state = .listening

        case "error":
            let err = (event["error"] as? [String: Any])?["message"] as? String ?? "unknown"
            failed("OpenAI: \(err)")

        default:
            break
        }
    }

    private func sendEvent(_ event: [String: Any]) {
        guard let dc = dataChannel, dc.readyState == .open,
              let data = try? JSONSerialization.data(withJSONObject: event) else { return }
        dc.sendData(RTCDataBuffer(data: data, isBinary: false))
    }

    /// If a seed message is pending and the data channel is now open, send it
    /// as a user-role conversation item and trigger a response so Shasta speaks first.
    private func sendSeedIfPending() {
        guard let seed = pendingSeedMessage,
              dataChannel?.readyState == .open else { return }
        pendingSeedMessage = nil
        sendEvent([
            "type": "conversation.item.create",
            "item": [
                "type":    "message",
                "role":    "user",
                "content": [["type": "input_text", "text": seed]],
            ],
        ])
        sendEvent(["type": "response.create"])
    }

    // MARK: - Tool dispatch

    private func dispatchTool(callId: String, name: String, argsJson: String) async {
        let args = (try? JSONSerialization.jsonObject(with: Data(argsJson.utf8))) as? [String: Any] ?? [:]
        let result: String
        do {
            switch name {
            case "get_top_risks":
                let limit    = (args["limit"] as? Int) ?? 5
                let severity = (args["severity"] as? String) ?? "critical,high"
                let findings = try await (api?.listFindings(severity: severity, cloud: nil, limit: limit) ?? [])
                let trimmed  = findings.prefix(limit).map { f in
                    [
                        "severity": f.severity,
                        "title":    f.title,
                        "resource": f.resource_arn ?? "",
                        "region":   f.region ?? "",
                        "cloud":    f.domain,
                    ]
                }
                result = try jsonString(["findings": Array(trimmed), "count": findings.count])

            case "list_connected_clouds":
                let conns = try await (api?.listConnections() ?? [])
                let slim  = conns.map { c in
                    [
                        "cloud":   c.cloud_type,
                        "name":    c.display_name,
                        "account": c.account_identifier ?? "",
                        "status":  c.status,
                    ]
                }
                result = try jsonString(["clouds": slim])

            default:
                // Forward unknown tools to the tools dispatcher Lambda
                // (POST /v1/tools/{name}). Covers all wow-demo tools:
                // slack_dm, create_jira_ticket, revoke_oauth_grant,
                // create_pr_with_bump, tail_lambda_logs_for_pattern,
                // run_forensic_scan.
                result = try await callToolsDispatcher(name: name, args: args)
            }
        } catch {
            result = #"{"error":"\#(error.localizedDescription)"}"#
        }

        sendEvent([
            "type": "conversation.item.create",
            "item": [
                "type":    "function_call_output",
                "call_id": callId,
                "output":  result,
            ],
        ])
        sendEvent(["type": "response.create"])
    }

    private func jsonString(_ obj: Any) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: obj)
        return String(data: data, encoding: .utf8) ?? "{}"
    }

    /// Forward a tool call to the server-side dispatcher
    /// (POST /v1/tools/{name}). The response body is returned to OpenAI as
    /// the function_call_output so the model can narrate the result.
    private func callToolsDispatcher(name: String, args: [String: Any]) async throws -> String {
        guard let api else { return #"{"error":"voice client not bound"}"# }
        return try await api.callTool(name: name, args: args)
    }

    // MARK: - Errors

    private func failed(_ message: String) {
        errorMessage = message
        state = .error
    }

    enum VoiceError: Error, LocalizedError {
        case notConfigured
        case sdpExchangeFailed(String)
        var errorDescription: String? {
            switch self {
            case .notConfigured:           "WebRTC not configured"
            case .sdpExchangeFailed(let s): "SDP exchange failed: \(s)"
            }
        }
    }
}

// MARK: - RTCPeerConnectionDelegate

extension VoiceClient: RTCPeerConnectionDelegate {
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didChange _: RTCSignalingState) {}
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didAdd _: RTCMediaStream) {}
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didRemove _: RTCMediaStream) {}
    nonisolated func peerConnectionShouldNegotiate(_ pc: RTCPeerConnection) {}
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didChange _: RTCIceConnectionState) {}
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didChange _: RTCIceGatheringState) {}
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didGenerate _: RTCIceCandidate) {}
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didRemove _: [RTCIceCandidate]) {}
    nonisolated func peerConnection(_ pc: RTCPeerConnection, didOpen dc: RTCDataChannel) {
        Task { @MainActor [weak self] in
            self?.dataChannel = dc
            dc.delegate = self
        }
    }
}

// MARK: - RTCDataChannelDelegate

extension VoiceClient: RTCDataChannelDelegate {
    nonisolated func dataChannelDidChangeState(_ dataChannel: RTCDataChannel) {
        guard dataChannel.readyState == .open else { return }
        Task { @MainActor [weak self] in self?.sendSeedIfPending() }
    }
    nonisolated func dataChannel(_ dataChannel: RTCDataChannel, didReceiveMessageWith buffer: RTCDataBuffer) {
        guard let event = try? JSONSerialization.jsonObject(with: buffer.data) as? [String: Any] else { return }
        Task { @MainActor [weak self] in self?.handleEvent(event) }
    }
}
