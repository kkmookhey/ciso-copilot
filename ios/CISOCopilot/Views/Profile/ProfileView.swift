import SwiftUI
import SwiftData

struct ProfileView: View {
    @Environment(APIClient.self) private var api
    @Environment(PushManager.self) private var push
    @Environment(\.modelContext) private var modelContext

    @Bindable var profile: StoredProfile
    @State private var editing: EditTarget?

    enum EditTarget: Identifiable {
        case cloud, identity, edr, siem, saas, regulatedData, sector, employeeBand
        var id: String { String(describing: self) }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Stack") {
                    editRow("Cloud",          profile.cloud,         target: .cloud)
                    editRow("Identity",       profile.identity,      target: .identity)
                    editRow("EDR",            profile.edr,           target: .edr)
                    editRow("SIEM",           profile.siem,          target: .siem)
                    editRow("SaaS",           profile.saas,          target: .saas)
                    editRow("Regulated data", profile.regulatedData, target: .regulatedData)
                }

                Section("Org") {
                    singleEditRow("Sector",    profile.sector,       target: .sector)
                    singleEditRow("Employees", profile.employeeBand, target: .employeeBand)
                }

                Section("Notifications") {
                    HStack {
                        Text("Status")
                        Spacer()
                        Text(pushStatus).foregroundStyle(.secondary)
                    }
                    if push.authorizationStatus != .authorized {
                        Button("Enable push") {
                            Task { _ = await push.requestAuthorization() }
                        }
                    }
                }
            }
            .navigationTitle("Profile")
            .sheet(item: $editing) { target in editor(for: target) }
        }
    }

    // MARK: - Row builders

    private func editRow(_ title: String, _ items: [String], target: EditTarget) -> some View {
        Button(action: { editing = target }) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.caption).foregroundStyle(.secondary)
                    Text(items.isEmpty ? "Tap to add" : items.joined(separator: ", "))
                        .foregroundStyle(.primary)
                        .multilineTextAlignment(.leading)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.footnote)
                    .foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func singleEditRow(_ title: String, _ value: String?, target: EditTarget) -> some View {
        Button(action: { editing = target }) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.caption).foregroundStyle(.secondary)
                    Text(value ?? "Tap to set")
                        .foregroundStyle(.primary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.footnote)
                    .foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - Editor routing

    @ViewBuilder
    private func editor(for target: EditTarget) -> some View {
        switch target {
        case .cloud:
            CategoryEditView(
                title: "Cloud",
                subtitle: "Where do you run production workloads?",
                options: StackOptions.cloud,
                selection: profile.cloud
            ) { new in
                profile.cloud = new
                await sync()
            }
        case .identity:
            CategoryEditView(
                title: "Identity",
                subtitle: "Who does SSO / MFA / directory?",
                options: StackOptions.identity,
                selection: profile.identity
            ) { new in
                profile.identity = new
                await sync()
            }
        case .edr:
            CategoryEditView(
                title: "EDR",
                subtitle: "What's on the endpoints?",
                options: StackOptions.edr,
                selection: profile.edr
            ) { new in
                profile.edr = new
                await sync()
            }
        case .siem:
            CategoryEditView(
                title: "SIEM",
                subtitle: "Where do logs land?",
                options: StackOptions.siem,
                selection: profile.siem
            ) { new in
                profile.siem = new
                await sync()
            }
        case .saas:
            CategoryEditView(
                title: "SaaS",
                subtitle: "Top SaaS that hold sensitive data or run critical workflows.",
                options: StackOptions.saas,
                selection: profile.saas
            ) { new in
                profile.saas = new
                await sync()
            }
        case .regulatedData:
            CategoryEditView(
                title: "Regulated data",
                subtitle: "What kinds of sensitive data do you store?",
                options: StackOptions.regulatedData,
                selection: profile.regulatedData
            ) { new in
                profile.regulatedData = new
                await sync()
            }
        case .sector:
            SingleSelectEditView(
                title: "Sector",
                subtitle: "Industry / vertical.",
                options: StackOptions.sector,
                selection: profile.sector
            ) { new in
                profile.sector = new
                await sync()
            }
        case .employeeBand:
            SingleSelectEditView(
                title: "Employees",
                subtitle: "How many people work at your org?",
                options: StackOptions.employeeBand,
                selection: profile.employeeBand
            ) { new in
                profile.employeeBand = new
                await sync()
            }
        }
    }

    // MARK: - Helpers

    private var pushStatus: String {
        switch push.authorizationStatus {
        case .authorized:    return "On"
        case .denied:        return "Off"
        case .notDetermined: return "Not asked"
        default:             return "—"
        }
    }

    private func sync() async {
        profile.updatedAt = Date()
        try? modelContext.save()
        try? await api.postProfile(profile.toDTO(), deviceToken: push.deviceToken)
    }
}
