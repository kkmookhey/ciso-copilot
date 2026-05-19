import SwiftUI

private let DOMAIN_LABEL: [String: String] = [
    "iam":                 "Identity & Access",
    "organizations":       "Organizations",
    "cloudfront":          "CDN / Edge",
    "logging":             "Logging",
    "compute":             "Compute",
    "storage":             "Storage",
    "networking":          "Networking",
    "encryption":          "Encryption",
    "database":            "Databases",
    "databases":           "Databases",
    "monitoring":          "Monitoring",
    "secrets":             "Secrets",
    "governance":          "Governance",
    "appservice":          "App Service",
    "backup":              "Backup",
    "diagnostic_settings": "Diagnostic settings",
    "private_endpoints":   "Private endpoints",
    "cloud_run":           "Cloud Run",
    "other":               "Other",
]

private func domainLabel(_ key: String) -> String {
    DOMAIN_LABEL[key] ?? key
        .replacingOccurrences(of: "_", with: " ")
        .capitalized
}

struct TopRisksView: View {
    @Environment(APIClient.self) private var api
    /// Optional framework filter — set when the view is pushed from a
    /// compliance tile on Overview (e.g. "soc2"). When set, the rollup is
    /// filtered client-side to groups that have ≥1 control in that framework.
    var initialFramework: String? = nil
    @State private var groups:    [FindingGroup]?
    @State private var totals:    (findings: Int, groups: Int)?
    @State private var search:    String = ""
    @State private var debounced: String = ""
    @State private var loading   = false
    @State private var error:     String?
    @State private var framework: String? = nil   // set from initialFramework on appear

    var body: some View {
        Group {
            if let groups = groups {
                if groups.isEmpty {
                    ContentUnavailableView(
                        "No findings",
                        systemImage: "checkmark.shield.fill",
                        description: Text("Nothing matched the current filter. Connect a cloud or relax the search.")
                    )
                } else {
                    List {
                        if framework != nil {
                            Section {
                                HStack {
                                    Text("Filtered by ")
                                        .foregroundStyle(.secondary)
                                    + Text(frameworkLabel(framework!)).fontWeight(.semibold)
                                    Spacer()
                                    Button("Clear") { framework = nil }
                                        .font(.caption.weight(.medium))
                                }
                                .font(.caption)
                            }
                        }
                        if let t = totals {
                            Section {
                                Text("\(t.groups) distinct issues · \(t.findings) findings")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        ForEach(groupedByDomain(groups), id: \.0) { (domain, gs) in
                            Section(domainLabel(domain)) {
                                ForEach(gs) { g in
                                    NavigationLink(value: g) {
                                        GroupRow(group: g)
                                    }
                                }
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                    .navigationDestination(for: FindingGroup.self) { g in
                        GroupDetailView(group: g)
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
        .searchable(text: $search, prompt: "Search title, check, description")
        .task(id: TaskKey(q: debounced, framework: framework)) { await load() }
        .refreshable { await load() }
        .onAppear {
            if let f = initialFramework, framework == nil { framework = f }
        }
        .onChange(of: search) { _, newValue in
            // Debounce so backend doesn't get hammered on each keystroke.
            Task {
                let snap = newValue
                try? await Task.sleep(nanoseconds: 350_000_000)
                if snap == search { debounced = snap }
            }
        }
        .alert("Couldn't load findings", isPresented: .init(
            get: { error != nil },
            set: { if !$0 { error = nil } }
        )) {
            Button("OK") { error = nil }
        } message: {
            Text(error ?? "")
        }
    }

    private func groupedByDomain(_ groups: [FindingGroup]) -> [(String, [FindingGroup])] {
        var byDomain: [String: [FindingGroup]] = [:]
        for g in groups {
            byDomain[g.domain, default: []].append(g)
        }
        // Sort domains by total finding count desc.
        return byDomain
            .map { ($0.key, $0.value) }
            .sorted { lhs, rhs in
                let lt = lhs.1.reduce(0) { $0 + $1.count }
                let rt = rhs.1.reduce(0) { $0 + $1.count }
                return lt > rt
            }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let r = try await api.findingsRollup(q: debounced.isEmpty ? nil : debounced)
            var gs = r.groups
            // Framework filter is applied client-side — backend rollup
            // doesn't take a framework param (frameworks live inside the JSONB).
            if let fw = framework {
                gs = gs.filter { ($0.frameworks[fw]?.isEmpty == false) }
            }
            groups = gs
            totals = framework == nil
                ? (r.total_findings, r.total_groups)
                : (gs.reduce(0) { $0 + $1.count }, gs.count)
        } catch {
            self.error = error.localizedDescription
            groups = []
        }
    }
}

private struct TaskKey: Hashable {
    let q: String
    let framework: String?
}

private func frameworkLabel(_ key: String) -> String {
    switch key {
    case "soc2":      return "SOC 2"
    case "cis_aws":   return "CIS AWS"
    case "cis_azure": return "CIS Azure"
    case "cis_gcp":   return "CIS GCP"
    case "mcsb":      return "MCSB"
    case "iso27001":  return "ISO 27001"
    case "hipaa":     return "HIPAA"
    default:          return key.uppercased()
    }
}

// MARK: - Rolled-up group row

private struct GroupRow: View {
    let group: FindingGroup

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                SeverityPill(severity: group.severity)
                Spacer()
                Text(group.check_id)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Text(group.title).font(.subheadline.weight(.medium))
            HStack(spacing: 10) {
                Label("\(group.count) resource\(group.count == 1 ? "" : "s")", systemImage: "shippingbox")
                    .labelStyle(.titleAndIcon)
                if let firstFw = group.frameworks.sorted(by: { $0.key < $1.key }).first {
                    Text("\(firstFw.key.uppercased()) \(firstFw.value.joined(separator: ", "))")
                        .lineLimit(1)
                }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Drill-in: list affected resources for a group

private struct GroupDetailView: View {
    @Environment(APIClient.self) private var api
    let group: FindingGroup

    @State private var findings: [Finding]?
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    SeverityPill(severity: group.severity)
                    Spacer()
                    Text(group.check_id)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                }
                Text(group.title).font(.title3.bold())

                if !group.frameworks.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(group.frameworks.sorted(by: { $0.key < $1.key }), id: \.key) { (fw, ctrls) in
                            Text("\(fw.uppercased()): \(ctrls.joined(separator: ", "))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Text("Affected resources")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)

                if let findings = findings {
                    if findings.isEmpty {
                        Text("No resources found.").font(.caption).foregroundStyle(.secondary)
                    } else {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(findings) { f in
                                ResourceCard(finding: f)
                            }
                        }
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding()
        }
        .navigationTitle("Issue")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .alert("Couldn't load resources", isPresented: .init(
            get: { error != nil },
            set: { if !$0 { error = nil } }
        )) {
            Button("OK") { error = nil }
        } message: {
            Text(error ?? "")
        }
    }

    private func load() async {
        do {
            findings = try await api.listFindingsByCheck(group.check_id, limit: 200)
        } catch {
            self.error = error.localizedDescription
            findings = []
        }
    }
}

private struct ResourceCard: View {
    let finding: Finding

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let arn = finding.resource_arn, !arn.isEmpty {
                Text(arn)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("No resource ARN").font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 10) {
                if let region = finding.region { Label(region, systemImage: "globe") }
                if let rt = finding.resource_type { Label(rt, systemImage: "tag") }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)

            if let r = finding.remediation, !r.isEmpty {
                DisclosureGroup("Remediation") {
                    Text(r).font(.caption).fixedSize(horizontal: false, vertical: true)
                }
                .font(.caption.weight(.semibold))
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10))
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
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                ShareLink(item: shareText, subject: Text(finding.title)) {
                    Image(systemName: "square.and.arrow.up")
                }
                .accessibilityLabel("Share finding")
            }
        }
    }

    /// Text content for the iOS share sheet. Picks up Slack, Teams, Mail, Messages,
    /// Notes, etc. automatically; users who want Jira install the Jira app and it
    /// shows up in the sheet too.
    private var shareText: String {
        var lines: [String] = []
        lines.append("[\(finding.severity.uppercased())] \(finding.title)")
        lines.append("Check: \(finding.check_id)")
        if let region = finding.region { lines.append("Region: \(region)") }
        if let arn = finding.resource_arn { lines.append("Resource: \(arn)") }
        if let desc = finding.description, !desc.isEmpty {
            lines.append("")
            lines.append("What we saw:")
            lines.append(desc)
        }
        if let r = finding.remediation, !r.isEmpty {
            lines.append("")
            lines.append("Remediation:")
            lines.append(r)
        }
        if !finding.frameworks.isEmpty {
            lines.append("")
            lines.append("Frameworks: " + finding.frameworks
                .sorted(by: { $0.key < $1.key })
                .map { "\($0.key.uppercased())=\($0.value.joined(separator: ","))" }
                .joined(separator: " · "))
        }
        lines.append("")
        lines.append("— shared from CISO Copilot")
        return lines.joined(separator: "\n")
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
