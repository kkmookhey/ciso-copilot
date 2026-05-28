import SwiftUI

struct BriefingView: View {
    let incident: IncidentContext
    @EnvironmentObject var router: IncidentRouter
    @Environment(APIClient.self) private var api

    @State private var client: VoiceClient = VoiceClient()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            // Incident header card
            HStack {
                Image(systemName: (incident.payload["kind_label"] as? String) == "AI Supply Chain"
                                  ? "shield.lefthalf.filled.trianglebadge.exclamationmark"
                                  : "exclamationmark.shield.fill")
                    .font(.system(size: 28))
                    .foregroundColor(.orange)
                VStack(alignment: .leading) {
                    Text(payloadString("kind_label", default: "Incident"))
                        .font(.headline)
                    Text(payloadString("speakable_summary", default: incident.findingId))
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
            }
            .padding()
            .background(Color(.systemGray6))
            .cornerRadius(12)

            // Voice state indicator
            HStack(spacing: 8) {
                Circle()
                    .fill(voiceStateColor)
                    .frame(width: 10, height: 10)
                Text(voiceStateLabel)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            // Transcript — shown once Shasta starts speaking
            if !client.transcript.isEmpty {
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
                            withAnimation(.easeOut(duration: 0.2)) {
                                proxy.scrollTo(last.id, anchor: .bottom)
                            }
                        }
                    }
                }
            }

            if let err = client.errorMessage, client.state == .error {
                Text(err)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()
        }
        .padding()
        .navigationTitle("Briefing")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button("Done") {
                    client.stop()
                    router.clear()
                }
            }
        }
        .onAppear {
            client.bind(api: api)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                Task { await client.start(seedDeveloperMessage: buildSeedMessage()) }
            }
        }
        .onDisappear {
            client.stop()
        }
    }

    // MARK: - Seed message

    private func buildSeedMessage() -> String {
        var lines = ["INCIDENT CONTEXT (the user just opened the app from a push notification):"]
        for key in ["finding_id", "kind_label", "speakable_summary"] {
            if let v = incident.payload[key] {
                lines.append("  \(key): \(v)")
            }
        }
        for (k, v) in incident.payload where !["finding_id", "kind_label", "speakable_summary"].contains(k) {
            lines.append("  \(k): \(v)")
        }
        lines.append("")
        lines.append("Open the conversation with a peer-grade briefing on this incident.")
        lines.append("Three to four sentences. Then wait for KK's next question.")
        return lines.joined(separator: "\n")
    }

    // MARK: - Helpers

    private func payloadString(_ key: String, `default` fallback: String) -> String {
        (incident.payload[key] as? String) ?? fallback
    }

    private var voiceStateColor: Color {
        switch client.state {
        case .idle:                 return .gray
        case .connecting:           return .yellow
        case .listening, .speaking: return .green
        case .error:                return .red
        }
    }

    private var voiceStateLabel: String {
        switch client.state {
        case .idle:       return "Disconnected"
        case .connecting: return "Connecting Shasta..."
        case .listening:  return "Shasta is listening"
        case .speaking:   return "Shasta is speaking"
        case .error:      return "Connection error"
        }
    }
}
