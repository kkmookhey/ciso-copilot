import SwiftUI

struct OverviewView: View {
    @Environment(APIClient.self) private var api
    @State private var me: MeResponse?
    @State private var connections: [Connection]?
    @State private var findingsCount: Int?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if let tenant = me?.tenant?.display_name {
                    Text(tenant)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text("Brief")
                    .font(.largeTitle.bold())

                HStack(spacing: 12) {
                    StatCard(label: "Clouds",   value: connections.map { "\($0.count)" } ?? "—")
                    StatCard(label: "Findings", value: findingsCount.map { "\($0)" } ?? "—")
                    StatCard(label: "Alerts",   value: "—")
                }

                Section_(title: "Your cloud connections") {
                    if let conns = connections {
                        if conns.isEmpty {
                            Text("Nothing connected yet.")
                                .foregroundStyle(.secondary)
                                .padding(.vertical, 8)
                        } else {
                            ForEach(conns) { c in ConnectionRow(c: c) }
                        }
                    } else {
                        ProgressView().padding(.vertical, 8)
                    }
                }
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        async let meTask       = try? await api.fetchMe()
        async let connsTask    = try? await api.listConnections()
        async let findingsTask = try? await api.listFindings(limit: 1)

        let (m, c, f) = await (meTask, connsTask, findingsTask)
        me            = m ?? me
        connections   = c ?? []
        findingsCount = f?.count
    }
}

// MARK: - Subviews

struct StatCard: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
                .tracking(0.5)
            Text(value)
                .font(.title2.bold())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct ConnectionRow: View {
    let c: Connection

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(c.cloud_type.uppercased())
                        .font(.caption2.bold())
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color(.tertiarySystemBackground))
                        .clipShape(Capsule())
                    Text(c.display_name).font(.subheadline.weight(.medium))
                }
                if let acct = c.account_identifier {
                    Text(acct).font(.caption.monospaced()).foregroundStyle(.secondary)
                }
            }
            Spacer()
            StatusPill(status: c.status)
        }
        .padding(.vertical, 8)
    }
}

struct StatusPill: View {
    let status: String

    private var color: Color {
        switch status {
        case "active":  .green
        case "pending": .orange
        case "error":   .red
        default:        .gray
        }
    }

    var body: some View {
        Text(status)
            .font(.caption2.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

/// Section card wrapper to keep styling DRY.
struct Section_<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            VStack(alignment: .leading, spacing: 0) { content() }
                .padding(16)
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }
}
