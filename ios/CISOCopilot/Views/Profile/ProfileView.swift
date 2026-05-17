import SwiftUI

struct ProfileView: View {
    @Environment(AuthManager.self) private var auth
    @Environment(APIClient.self) private var api
    @State private var me: MeResponse?

    var body: some View {
        Form {
            Section("Account") {
                row("Email",  me?.user?.email)
                row("Tenant", me?.tenant?.display_name)
                row("Role",   me?.user?.role)
                row("Status", me?.tenant?.status)
            }

            Section {
                Button(role: .destructive, action: auth.signOut) {
                    Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                }
            }

            Section("About") {
                row("Build", "Phase A")
                row("Backend", "us-east-1")
            }
        }
        .navigationTitle("Profile")
        .navigationBarTitleDisplayMode(.inline)
        .task { me = try? await api.fetchMe() }
    }

    private func row(_ label: String, _ value: String?) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value ?? "—")
        }
    }
}
