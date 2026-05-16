import SwiftUI

struct ItemDetailView: View {
    @Environment(APIClient.self) private var api
    let item: ItemDTO
    @State private var feedback: String?
    @State private var posting = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header

                if let why = item.whyItMatters {
                    Section_(title: "Why this matters to you") {
                        Text(why)
                    }
                }

                if let questions = item.teamQuestions {
                    AskMyTeamSection(questions: questions)
                }

                if let board = item.boardParagraph {
                    Section_(title: "Board paragraph") {
                        Text(board)
                        ShareButton(text: board)
                    }
                }

                detailsSection

                feedbackSection

                Spacer(minLength: 40)
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
        .navigationTitle(item.cveId)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(Severity(rawValue: item.severity)?.label ?? item.severity)
                    .font(.headline)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(severityColor(item.severity).opacity(0.15))
                    .foregroundStyle(severityColor(item.severity))
                    .clipShape(Capsule())
                Spacer()
                if item.inKev { BadgeView(text: "In KEV", color: .red) }
            }
            Text(item.description)
                .font(.body)
        }
    }

    private var detailsSection: some View {
        Section_(title: "Details") {
            HStack(spacing: 24) {
                Metric(label: "CVSS",  value: String(format: "%.1f", item.cvssScore))
                Metric(label: "EPSS",  value: String(format: "%.2f", item.epssScore))
                Metric(label: "Match", value: item.confidence.capitalized)
            }
            if !item.matchedVendors.isEmpty || !item.matchedProducts.isEmpty {
                Text("Matched: " + (item.matchedVendors + item.matchedProducts).joined(separator: ", "))
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var feedbackSection: some View {
        Section_(title: "Was this useful?") {
            HStack(spacing: 16) {
                FeedbackButton(label: "Useful", icon: "hand.thumbsup.fill", selected: feedback == "up") {
                    Task { await postFeedback("up") }
                }
                FeedbackButton(label: "Not useful", icon: "hand.thumbsdown.fill", selected: feedback == "down") {
                    Task { await postFeedback("down") }
                }
            }
        }
    }

    private func postFeedback(_ sentiment: String) async {
        posting = true
        defer { posting = false }
        do {
            try await api.postFeedback(itemId: item.cveId, sentiment: sentiment, reason: nil)
            feedback = sentiment
        } catch {
            // Quietly ignore; SwiftData queue can retry later.
        }
    }
}

private struct Section_<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            content()
        }
    }
}

private struct Metric: View {
    let label: String
    let value: String
    var body: some View {
        VStack(alignment: .leading) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.bold())
        }
    }
}

private struct FeedbackButton: View {
    let label: String
    let icon: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Image(systemName: icon)
                Text(label)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(selected ? Color.accentColor : Color(.secondarySystemBackground))
            .foregroundStyle(selected ? .white : .primary)
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// Opens the native iOS share sheet — Slack, Teams, Mail, Messages, AirDrop, Copy, etc.
/// One affordance, every destination users care about.
struct ShareButton: View {
    let text: String

    var body: some View {
        ShareLink(item: text) {
            Label("Share", systemImage: "square.and.arrow.up")
                .font(.subheadline)
        }
        .buttonStyle(.bordered)
    }
}
