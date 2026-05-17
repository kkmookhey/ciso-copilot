import SwiftUI

/// Phase 0 landing for approved users. Phase A replaces this with the cloud
/// connection wizard + MainTabView (Brief / Alerts / Posture / Profile).
struct WelcomeView: View {
    @Environment(AuthManager.self) private var auth
    @Environment(APIClient.self) private var api
    @State private var me: MeResponse?

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Spacer()

                Image(systemName: "checkmark.shield.fill")
                    .font(.system(size: 72))
                    .foregroundStyle(.green)

                VStack(spacing: 12) {
                    Text("You're in.")
                        .font(.largeTitle.bold())
                    if let email = me?.user?.email {
                        Text("Signed in as \(email)")
                            .foregroundStyle(.secondary)
                    }
                    Text("Next up: connect your first cloud account. Coming in the next build.")
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 32)
                        .padding(.top, 4)
                }

                Spacer()

                Button("Sign out", action: { auth.signOut() })
                    .foregroundStyle(.secondary)

                Spacer().frame(height: 24)
            }
            .task { me = try? await api.fetchMe() }
            .navigationTitle("CISO Copilot")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}
