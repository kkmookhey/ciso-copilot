import SwiftUI
import Charts

struct OverviewView: View {
    @Environment(APIClient.self) private var api
    @Environment(AppState.self) private var appState
    @State private var me: MeResponse?
    @State private var connections: [Connection]?
    @State private var findingsCount: Int?
    @State private var alertsCount: Int?
    @State private var recentAlerts: [AlertEvent]?
    @State private var compliance: ComplianceSummaryResponse?
    @State private var findingsSummary: FindingsSummaryResponse?
    @State private var showVoice = false
    @State private var openAlert: AlertEvent?
    @State private var showAllAlerts = false
    @State private var navFramework: String?      // pushes a filtered TopRisksView

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
                    StatCard(
                        label: "Clouds",
                        value: connections.map { "\($0.filter { $0.status == "active" }.count)" } ?? "—",
                        action: { appState.selectedTab = .connect }
                    )
                    StatCard(
                        label: "Findings",
                        value: findingsCount.map { "\($0)" } ?? "—",
                        action: { appState.selectedTab = .risks }
                    )
                    StatCard(
                        label: "Alerts",
                        value: alertsCount.map { "\($0)" } ?? "—",
                        action: { showAllAlerts = true }
                    )
                }

                if let s = findingsSummary, s.total > 0 {
                    Section_(title: "Risk distribution") {
                        SeverityDonut(slices: s.severitySlices, total: s.total)
                    }
                    Section_(title: "By cloud") {
                        CloudBars(bars: s.cloudBars)
                    }
                }

                if let compliance, !compliance.summary.isEmpty {
                    Section_(title: "Compliance posture") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(compliance.summary.sorted(by: { $0.value.total > $1.value.total }), id: \.key) { (fw, agg) in
                                Button { navFramework = fw } label: {
                                    ComplianceRow(framework: fw, agg: agg)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }

                if let recent = recentAlerts, !recent.isEmpty {
                    Section_(title: "Recent activity") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(recent.prefix(5)) { e in
                                Button { openAlert = e } label: {
                                    AlertRow(event: e)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }

                Section_(title: "Your cloud connections") {
                    if let conns = connections {
                        if conns.isEmpty {
                            Button { appState.selectedTab = .connect } label: {
                                HStack {
                                    Text("Nothing connected yet — tap to connect")
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                    Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                                }
                                .padding(.vertical, 8)
                            }
                            .buttonStyle(.plain)
                        } else {
                            ForEach(conns) { c in
                                Button { appState.selectedTab = .connect } label: {
                                    ConnectionRow(c: c)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    } else {
                        ProgressView().padding(.vertical, 8)
                    }
                }
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showVoice = true } label: {
                    Image(systemName: "mic.circle.fill")
                        .font(.title2)
                }
                .accessibilityLabel("Voice chat")
            }
        }
        .sheet(isPresented: $showVoice)     { VoiceChatView() }
        .sheet(item: $openAlert)            { event in AlertDetailSheet(event: event) }
        .sheet(isPresented: $showAllAlerts) { AlertsListSheet() }
        .navigationDestination(item: $navFramework) { fw in
            TopRisksView(initialFramework: fw)
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        async let meTask         = try? await api.fetchMe()
        async let connsTask      = try? await api.listConnections()
        async let totalTask      = try? await api.findingsTotal()
        async let alertsTask     = try? await api.eventsTotal(kind: "alert")
        async let recentTask     = try? await api.listEvents(limit: 5)
        async let complianceTask = try? await api.complianceSummary()
        async let summaryTask    = try? await api.findingsSummary()

        let (m, c, t, a, r, comp, s) = await (meTask, connsTask, totalTask, alertsTask, recentTask, complianceTask, summaryTask)
        me              = m ?? me
        connections     = c ?? []
        findingsCount   = t
        alertsCount     = a
        recentAlerts    = r
        compliance      = comp
        findingsSummary = s
    }
}

// MARK: - Subviews

struct StatCard: View {
    let label: String
    let value: String
    var action: (() -> Void)? = nil

    var body: some View {
        Button {
            action?()
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                Text(label.uppercased())
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .tracking(0.5)
                HStack(alignment: .firstTextBaseline) {
                    Text(value).font(.title2.bold())
                    Spacer()
                    if action != nil {
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding()
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .disabled(action == nil)
    }
}

/// Sheet listing more recent alerts (top 25). Tap any item to view its full
/// detail sheet — the same AlertDetailSheet the Overview row uses.
struct AlertsListSheet: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    @State private var events: [AlertEvent]?
    @State private var openAlert: AlertEvent?
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if let events {
                    if events.isEmpty {
                        ContentUnavailableView(
                            "No alerts",
                            systemImage: "bell.slash",
                            description: Text("Real-time alerts show up here as your connected clouds emit them.")
                        )
                    } else {
                        List(events) { e in
                            Button { openAlert = e } label: { AlertRow(event: e) }
                                .buttonStyle(.plain)
                        }
                        .listStyle(.plain)
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .navigationTitle("Recent alerts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .sheet(item: $openAlert) { event in AlertDetailSheet(event: event) }
            .task {
                do { events = try await api.listEvents(limit: 25) }
                catch { errorMessage = error.localizedDescription; events = [] }
            }
            .alert("Couldn't load alerts", isPresented: .init(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
        }
    }
}

struct SeverityDonut: View {
    let slices: [(name: String, value: Int)]
    let total: Int

    var body: some View {
        Chart {
            ForEach(slices, id: \.name) { s in
                SectorMark(
                    angle: .value("Findings", s.value),
                    innerRadius: .ratio(0.55),
                    angularInset: 1.5
                )
                .foregroundStyle(color(for: s.name))
                .annotation(position: .overlay) {
                    if s.value >= total / 12 {
                        Text("\(s.value)").font(.caption2.bold()).foregroundStyle(.white)
                    }
                }
            }
        }
        .frame(height: 220)
        .chartLegend(position: .bottom, alignment: .center, spacing: 10)
        .chartForegroundStyleScale(domain: slices.map { $0.name },
                                   range: slices.map { color(for: $0.name) })
    }

    private func color(for severity: String) -> Color {
        switch severity {
        case "critical": return .red
        case "high":     return .orange
        case "medium":   return .yellow
        case "low":      return .gray
        default:         return Color(.systemGray3)
        }
    }
}

struct CloudBars: View {
    let bars: [(name: String, value: Int)]

    var body: some View {
        Chart {
            ForEach(bars, id: \.name) { b in
                BarMark(
                    x: .value("Cloud", b.name),
                    y: .value("Findings", b.value)
                )
                .foregroundStyle(color(for: b.name))
                .cornerRadius(6)
            }
        }
        .frame(height: 180)
    }

    private func color(for cloud: String) -> Color {
        switch cloud.lowercased() {
        case "aws":   return .orange
        case "azure": return .blue
        case "gcp":   return Color(red: 0.26, green: 0.52, blue: 0.96)
        default:      return .gray
        }
    }
}

struct ComplianceRow: View {
    let framework: String
    let agg: FrameworkScore

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(.subheadline.weight(.semibold))
                Text("\(agg.passing) passing · \(agg.failing) failing · \(agg.total) controls")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text("\(Int(agg.score_pct))%")
                .font(.title3.bold())
                .foregroundStyle(scoreColor)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 12)
        .background(Color(.tertiarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var label: String {
        switch framework {
        case "soc2":      return "SOC 2"
        case "cis_aws":   return "CIS AWS"
        case "cis_azure": return "CIS Azure"
        case "cis_gcp":   return "CIS GCP"
        case "mcsb":      return "MCSB"
        case "iso27001":  return "ISO 27001"
        case "hipaa":     return "HIPAA"
        default:          return framework.uppercased()
        }
    }

    private var scoreColor: Color {
        if agg.score_pct >= 80 { return .green }
        if agg.score_pct >= 50 { return .orange }
        return .red
    }
}

/// Full-detail sheet for an alert event. Same fields the web modal shows.
struct AlertDetailSheet: View {
    let event: AlertEvent
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    HStack(alignment: .top, spacing: 10) {
                        SeverityBadge(severity: event.severity)
                        Text(event.title).font(.headline).fixedSize(horizontal: false, vertical: true)
                    }

                    if let desc = event.description, !desc.isEmpty {
                        Section_(title: "Description") {
                            Text(desc).font(.body).foregroundStyle(.primary)
                        }
                    }

                    if let arn = event.resource_arn {
                        Section_(title: "Resource") {
                            Text(arn).font(.caption.monospaced()).textSelection(.enabled)
                        }
                    }

                    if let actor = event.actor {
                        Section_(title: "Actor") {
                            Text(actor).font(.caption.monospaced()).textSelection(.enabled)
                        }
                    }

                    Section_(title: "Timeline") {
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Fired \(event.fired_at)", systemImage: "clock")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Label("Ingested \(event.ingested_at)", systemImage: "tray.and.arrow.down")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    Section_(title: "Source") {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(event.kind) · \(event.source)").font(.caption)
                            Text(event.event_id).font(.caption2.monospaced()).foregroundStyle(.tertiary)
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("Alert")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

struct SeverityBadge: View {
    let severity: String
    var body: some View {
        Text(severity.uppercased())
            .font(.caption2.weight(.bold))
            .foregroundStyle(.white)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color)
            .clipShape(Capsule())
    }
    private var color: Color {
        switch severity {
        case "critical": return .red
        case "high":     return .orange
        case "medium":   return .yellow
        case "low":      return .gray
        default:         return Color(.systemGray3)
        }
    }
}

struct AlertRow: View {
    let event: AlertEvent

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(severityColor)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(event.title).font(.subheadline).lineLimit(2)
                Text("\(event.kind) · \(event.source)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(.vertical, 6)
    }

    private var severityColor: Color {
        switch event.severity {
        case "critical": return .red
        case "high":     return .orange
        case "medium":   return .yellow
        case "low":      return .secondary
        default:         return .secondary
        }
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
