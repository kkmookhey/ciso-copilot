import SwiftUI

struct PendingApprovalView: View {
    @Environment(APIClient.self) private var api
    @Environment(AuthManager.self) private var auth
    let onApproved: () -> Void

    @State private var pollTask: Task<Void, Never>?
    @State private var requestedAt = Date()

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            Image(systemName: "hourglass")
                .font(.system(size: 64))
                .foregroundStyle(.tint)
                .symbolEffect(.pulse)

            VStack(spacing: 12) {
                Text("Access request pending")
                    .font(.title2.bold())
                Text("We're reviewing your access request. You'll get an email when it's approved — typically within 24 hours.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            ProgressView()
                .padding(.top, 8)

            Spacer()

            Button("Sign out", action: { auth.signOut() })
                .foregroundStyle(.secondary)

            Spacer().frame(height: 24)
        }
        .task { startPolling() }
        .onDisappear { pollTask?.cancel() }
    }

    private func startPolling() {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled {
                if let status = try? await api.fetchMe()?.tenant?.status {
                    if status == "approved" {
                        await MainActor.run { onApproved() }
                        return
                    }
                    if status == "rejected" {
                        await MainActor.run { auth.signOut() }
                        return
                    }
                }
                try? await Task.sleep(for: .seconds(30))
            }
        }
    }
}
