import SwiftUI

struct TopRisksView: View {
    @Environment(APIClient.self) private var api
    @State private var findings: [Finding]?
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        Group {
            if let findings = findings {
                if findings.isEmpty {
                    ContentUnavailableView(
                        "No findings yet",
                        systemImage: "checkmark.shield.fill",
                        description: Text("Connect a cloud account and run a scan. Findings will appear here.")
                    )
                } else {
                    List {
                        ForEach(findings) { f in
                            NavigationLink(value: f) {
                                FindingRow(f: f)
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                    .navigationDestination(for: Finding.self) { f in
                        FindingDetailView(finding: f)
                    }
                }
            } else if loading {
                ProgressView()
            } else {
                Color.clear
            }
        }
        .navigationTitle("Top Risks")
        .navigationBarTitleDisplayMode(.large)
        .task { await load() }
        .refreshable { await load() }
        .alert("Couldn't load findings", isPresented: .init(
            get: { error != nil },
            set: { if !$0 { error = nil } }
        )) {
            Button("OK") { error = nil }
        } message: {
            Text(error ?? "")
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            findings = try await api.listFindings(severity: "critical,high,medium", limit: 100)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Subviews

struct FindingRow: View {
    let f: Finding

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                SeverityPill(severity: f.severity)
                Spacer()
                Text(f.check_id)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
            }
            Text(f.title).font(.subheadline.weight(.medium))
            if let desc = f.description, !desc.isEmpty {
                Text(desc).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            if let arn = f.resource_arn, !arn.isEmpty {
                Text(arn)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 4)
    }
}

struct SeverityPill: View {
    let severity: String

    private var color: Color {
        switch severity {
        case "critical": .red
        case "high":     .orange
        case "medium":   .yellow
        case "low":      .gray
        default:         .secondary
        }
    }

    var body: some View {
        Text(severity.uppercased())
            .font(.caption2.bold())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.18))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

// MARK: - Finding detail

struct FindingDetailView: View {
    let finding: Finding

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    SeverityPill(severity: finding.severity)
                    Spacer()
                    Text(finding.check_id)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
                Text(finding.title).font(.title3.bold())

                if let desc = finding.description {
                    DetailSection(title: "What we saw") { Text(desc).font(.body) }
                }
                if let arn = finding.resource_arn {
                    DetailSection(title: "Resource") {
                        Text(arn).font(.callout.monospaced()).textSelection(.enabled)
                    }
                }
                if !finding.frameworks.isEmpty {
                    DetailSection(title: "Frameworks") {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(finding.frameworks.sorted(by: { $0.key < $1.key }), id: \.key) { (fw, ctrls) in
                                Text("\(fw.uppercased()): \(ctrls.joined(separator: ", "))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                if let r = finding.remediation, !r.isEmpty {
                    DetailSection(title: "Remediation") { Text(r).font(.body) }
                }
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct DetailSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }
}
