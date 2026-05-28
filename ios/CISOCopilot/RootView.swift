import SwiftUI

/// Three-state routing for v2 Phase 0:
///   - Signed out                  → SignInView
///   - Signed in + pending/unknown → PendingApprovalView (polls /me)
///   - Signed in + approved        → WelcomeView (Phase A will swap in MainTabView)
struct RootView: View {
    @Environment(AuthManager.self) private var auth
    @Environment(APIClient.self) private var api
    @State private var tenantStatus: String?
    @State private var checkingStatus = false

    var body: some View {
        Group {
            switch auth.state {
            case .signedOut, .error:
                SignInView()
            case .signingIn:
                ProgressView("Signing in…")
            case .signedIn:
                approvedOrPending
            }
        }
        .task(id: auth.state) {
            if case .signedIn = auth.state {
                await refreshTenantStatus()
            } else {
                tenantStatus = nil
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .deviceTokenReady)) { note in
            // AppDelegate captured an APNs token. POST it to /me/device-token
            // so subsequent server-side pushes can reach this device.
            guard let hex = note.object as? String,
                  case .signedIn = auth.state else { return }
            Task {
                do {
                    try await api.registerDeviceToken(hex)
                    print("[apns] token registered (prefix=\(hex.prefix(8)))")
                } catch {
                    print("[apns] token registration failed: \(error)")
                }
            }
        }
    }

    @ViewBuilder
    private var approvedOrPending: some View {
        switch tenantStatus {
        case "approved":
            MainTabView()
        case "rejected":
            SignInView()
        case nil where checkingStatus:
            ProgressView()
        default:
            PendingApprovalView(onApproved: { tenantStatus = "approved" })
        }
    }

    private func refreshTenantStatus() async {
        checkingStatus = true
        defer { checkingStatus = false }
        if let me = try? await api.fetchMe() {
            tenantStatus = me.tenant?.status
        }
    }
}
