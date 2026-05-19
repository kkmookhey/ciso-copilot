import SwiftUI

/// Risk register — tracked items with owner, due date, and disposition.
/// Backed by GET/POST/PATCH /risks. Mirrors web /risks route.
struct RisksRegisterView: View {
    @Environment(APIClient.self) private var api
    @State private var risks: [Risk]?
    @State private var filter: StatusFilter = .open
    @State private var showNew = false
    @State private var errorMessage: String?

    enum StatusFilter: String, CaseIterable, Identifiable {
        case open, mitigated, accepted, transferred, closed, all
        var id: String { rawValue }
        var label: String { rawValue.capitalized }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Risk register")
                    .font(.largeTitle.bold())
                Text("Tracked items with owner, due date, disposition.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)

                Picker("Status", selection: $filter) {
                    ForEach(StatusFilter.allCases) { f in
                        Text(f.label).tag(f)
                    }
                }
                .pickerStyle(.segmented)

                if let risks = risks {
                    if risks.isEmpty {
                        ContentUnavailableView(
                            "No risks",
                            systemImage: "list.bullet.clipboard",
                            description: Text("Tap + to add one, or convert a finding into a risk on the Risks tab.")
                        )
                        .padding(.top, 40)
                    } else {
                        ForEach(risks) { r in
                            RiskRow(risk: r) { newStatus in
                                await updateStatus(r.risk_id, status: newStatus)
                            }
                        }
                    }
                } else {
                    ProgressView().padding(.top, 40).frame(maxWidth: .infinity)
                }
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showNew = true } label: {
                    Image(systemName: "plus.circle.fill")
                }
                .accessibilityLabel("New risk")
            }
        }
        .sheet(isPresented: $showNew) {
            NewRiskSheet { title, severity, description, owner, due in
                await createRisk(title: title, severity: severity,
                                 description: description, owner: owner, dueDate: due)
            }
        }
        .alert("Couldn't load risks", isPresented: .init(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } })
        ) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
        .task(id: filter) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        risks = nil
        do {
            let statusParam = filter == .all ? nil : filter.rawValue
            risks = try await api.listRisks(status: statusParam)
        } catch {
            risks = []
            errorMessage = error.localizedDescription
        }
    }

    private func updateStatus(_ riskId: String, status: String) async {
        do {
            try await api.updateRiskStatus(riskId, status: status)
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func createRisk(title: String, severity: String,
                            description: String?, owner: String?, dueDate: String?) async {
        do {
            _ = try await api.createRisk(
                title: title, severity: severity,
                description: description, owner: owner, dueDate: dueDate
            )
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct RiskRow: View {
    let risk: Risk
    let onStatusChange: (String) async -> Void

    private let statuses = ["open", "mitigated", "accepted", "transferred", "closed"]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                RiskSeverityBadge(severity: risk.severity)
                VStack(alignment: .leading, spacing: 2) {
                    Text(risk.title).font(.body.weight(.semibold))
                    if let d = risk.description, !d.isEmpty {
                        Text(d).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
                Spacer()
            }

            HStack(spacing: 12) {
                if let owner = risk.owner, !owner.isEmpty {
                    Label(owner, systemImage: "person.fill")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let due = risk.due_date {
                    Label(due, systemImage: "calendar")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Menu(risk.status.capitalized) {
                    ForEach(statuses, id: \.self) { s in
                        Button(s.capitalized) {
                            Task { await onStatusChange(s) }
                        }
                    }
                }
                .font(.caption.weight(.medium))
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background(Color(.tertiarySystemBackground))
                .clipShape(Capsule())
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

private struct RiskSeverityBadge: View {
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

private struct NewRiskSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onSubmit: (_ title: String, _ severity: String, _ description: String?, _ owner: String?, _ dueDate: String?) async -> Void

    @State private var title = ""
    @State private var severity = "medium"
    @State private var description = ""
    @State private var owner = ""
    @State private var dueDate: Date?
    @State private var busy = false

    private let severities = ["critical", "high", "medium", "low", "info"]
    private let isoFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f
    }()

    var body: some View {
        NavigationStack {
            Form {
                Section("Risk") {
                    TextField("Title", text: $title)
                    Picker("Severity", selection: $severity) {
                        ForEach(severities, id: \.self) { Text($0.capitalized).tag($0) }
                    }
                    TextField("Description (optional)", text: $description, axis: .vertical)
                        .lineLimit(2...5)
                }
                Section("Assignment") {
                    TextField("Owner email (optional)", text: $owner)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                    DatePicker(
                        "Due date",
                        selection: Binding(
                            get: { dueDate ?? Date() },
                            set: { dueDate = $0 }
                        ),
                        displayedComponents: .date
                    )
                }
            }
            .navigationTitle("New risk")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Create") {
                        Task {
                            busy = true
                            await onSubmit(
                                title.trimmingCharacters(in: .whitespaces),
                                severity,
                                description.isEmpty ? nil : description,
                                owner.isEmpty ? nil : owner,
                                dueDate.map { isoFormatter.string(from: $0) }
                            )
                            busy = false
                            dismiss()
                        }
                    }
                    .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty || busy)
                }
            }
        }
    }
}
