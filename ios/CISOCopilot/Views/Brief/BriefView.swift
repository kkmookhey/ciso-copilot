import SwiftUI

struct BriefView: View {
    @Environment(APIClient.self) private var api
    @State private var brief: BriefDTO?
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if let brief = brief {
                    BriefList(brief: brief)
                } else if loading {
                    ProgressView()
                } else {
                    ContentUnavailableView(
                        "No brief yet",
                        systemImage: "list.bullet.rectangle",
                        description: Text("Pull to refresh once your first brief is generated.")
                    )
                }
            }
            .navigationTitle("Today's Brief")
            .refreshable { await load() }
            .task { await load() }
            .alert("Couldn't load brief", isPresented: .init(
                get: { error != nil },
                set: { if !$0 { error = nil } }
            )) {
                Button("OK") { error = nil }
            } message: {
                Text(error ?? "")
            }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            brief = try await api.getBrief()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct BriefList: View {
    let brief: BriefDTO

    private var bySeverity: [(String, [ItemDTO])] {
        let order = ["act_now", "check_today", "watch", "fyi"]
        return order.compactMap { sev in
            let items = brief.items.filter { $0.severity == sev }
            return items.isEmpty ? nil : (sev, items)
        }
    }

    var body: some View {
        List {
            if brief.items.isEmpty {
                Section {
                    Text("Nothing relevant to your stack today. Quiet is good.")
                        .foregroundStyle(.secondary)
                }
            } else {
                ForEach(bySeverity, id: \.0) { (severity, items) in
                    Section(header: SeverityHeader(severity: severity, count: items.count)) {
                        ForEach(items) { item in
                            NavigationLink(value: item) {
                                BriefRow(item: item)
                            }
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationDestination(for: ItemDTO.self) { item in
            ItemDetailView(item: item)
        }
    }
}

struct SeverityHeader: View {
    let severity: String
    let count: Int

    var body: some View {
        HStack {
            Circle()
                .fill(severityColor(severity))
                .frame(width: 10, height: 10)
            Text(Severity(rawValue: severity)?.label ?? severity)
                .font(.headline)
            Spacer()
            Text("\(count)")
                .foregroundStyle(.secondary)
        }
    }
}

struct BriefRow: View {
    let item: ItemDTO

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.cveId)
                    .font(.subheadline.monospaced())
                Spacer()
                if item.inKev {
                    BadgeView(text: "KEV", color: .red)
                }
                if item.cvssScore >= 7 {
                    BadgeView(text: "CVSS \(String(format: "%.1f", item.cvssScore))", color: .orange)
                }
            }
            Text(item.description)
                .font(.callout)
                .lineLimit(2)
                .foregroundStyle(.primary)
            if !item.matchedVendors.isEmpty || !item.matchedProducts.isEmpty {
                Text("Matches: \((item.matchedVendors + item.matchedProducts).joined(separator: ", "))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

struct BadgeView: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.caption2.bold())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

func severityColor(_ severity: String) -> Color {
    switch severity {
    case "act_now":     return .red
    case "check_today": return .orange
    case "watch":       return .yellow
    case "fyi":         return .gray
    default:            return .gray
    }
}
