import SwiftUI
import SwiftData

struct ProfileView: View {
    @Environment(APIClient.self) private var api
    @Environment(PushManager.self) private var push
    @Environment(\.modelContext) private var modelContext

    @Bindable var profile: StoredProfile
    @State private var savedJustNow = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Stack") {
                    profileRow("Cloud",   profile.cloud)
                    profileRow("Identity", profile.identity)
                    profileRow("EDR",     profile.edr)
                    profileRow("SIEM",    profile.siem)
                    profileRow("SaaS",    profile.saas)
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

                Section("Sync") {
                    Button("Re-sync profile to backend") { Task { await sync() } }
                    if savedJustNow {
                        Text("Synced").font(.caption).foregroundStyle(.green)
                    }
                }
            }
            .navigationTitle("Profile")
        }
    }

    private func profileRow(_ title: String, _ items: [String]) -> some View {
        VStack(alignment: .leading) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(items.isEmpty ? "—" : items.joined(separator: ", "))
        }
    }

    private var pushStatus: String {
        switch push.authorizationStatus {
        case .authorized: return "On"
        case .denied:     return "Off"
        case .notDetermined: return "Not asked"
        default: return "—"
        }
    }

    private func sync() async {
        do {
            try await api.postProfile(profile.toDTO(), deviceToken: push.deviceToken)
            savedJustNow = true
            Task {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                savedJustNow = false
            }
        } catch {
            // ignore
        }
    }
}
