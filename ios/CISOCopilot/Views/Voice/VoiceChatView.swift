import SwiftUI

/// Voice modal — tap mic to start a Realtime session, talk freely (server VAD
/// handles turn-taking), tap again to end. Streamed transcripts show the
/// conversation in real time.
struct VoiceChatView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss)      private var dismiss
    @State private var client: VoiceClient = VoiceClient()

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                statusHeader

                if let err = client.errorMessage, client.state == .error {
                    Text(err)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .padding()
                }

                if client.transcript.isEmpty {
                    Spacer()
                    Text(emptyHint)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 32)
                    Spacer()
                } else {
                    transcriptList
                }

                micButton
                    .padding(.bottom, 32)
            }
            .navigationTitle("Voice")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        client.stop()
                        dismiss()
                    }
                }
            }
            .onAppear { client.bind(api: api) }
            .onDisappear { client.stop() }
        }
    }

    // MARK: - Subviews

    private var statusHeader: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(statusColor)
                .frame(width: 8, height: 8)
            Text(statusLabel)
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
        }
        .padding(.top, 8)
    }

    private var transcriptList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(client.transcript) { line in
                        HStack {
                            if line.role == .user { Spacer(minLength: 40) }
                            Text(line.text)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 10)
                                .background(
                                    line.role == .user
                                        ? Color.blue.opacity(0.15)
                                        : Color(.secondarySystemBackground)
                                )
                                .clipShape(RoundedRectangle(cornerRadius: 16))
                                .id(line.id)
                            if line.role == .assistant { Spacer(minLength: 40) }
                        }
                    }
                }
                .padding()
            }
            .onChange(of: client.transcript.count) { _, _ in
                if let last = client.transcript.last {
                    withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private var micButton: some View {
        Button {
            switch client.state {
            case .idle, .error:                Task { await client.start() }
            case .listening, .speaking, .connecting: client.stop()
            }
        } label: {
            ZStack {
                Circle()
                    .fill(micColor)
                    .frame(width: 80, height: 80)
                Image(systemName: micIcon)
                    .font(.system(size: 32, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
        .buttonStyle(.plain)
        .disabled(client.state == .connecting)
    }

    // MARK: - Status helpers

    private var statusLabel: String {
        switch client.state {
        case .idle:       "Tap the mic to start"
        case .connecting: "Connecting…"
        case .listening:  "Listening"
        case .speaking:   "Speaking"
        case .error:      "Error"
        }
    }

    private var statusColor: Color {
        switch client.state {
        case .idle:       .gray
        case .connecting: .orange
        case .listening:  .green
        case .speaking:   .blue
        case .error:      .red
        }
    }

    private var micColor: Color {
        switch client.state {
        case .idle, .error: .blue
        case .connecting:   .orange
        case .listening:    .red
        case .speaking:     .blue
        }
    }

    private var micIcon: String {
        switch client.state {
        case .idle, .error: "mic.fill"
        case .connecting:   "ellipsis"
        case .listening:    "stop.fill"
        case .speaking:     "speaker.wave.2.fill"
        }
    }

    private var emptyHint: String {
        switch client.state {
        case .idle:
            "Ask anything about your cloud posture.\nExamples: \"What are my top risks?\", \"Which clouds am I connected to?\""
        case .connecting:
            "Connecting to OpenAI Realtime…"
        default:
            "Listening…"
        }
    }
}
