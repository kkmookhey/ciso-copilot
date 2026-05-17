import SwiftUI

struct SignInView: View {
    @Environment(AuthManager.self) private var auth
    @State private var signingIn = false
    @State private var error: String?

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            Image(systemName: "shield.lefthalf.filled")
                .font(.system(size: 72))
                .foregroundStyle(.tint)

            VStack(spacing: 8) {
                Text("CISO Copilot")
                    .font(.largeTitle.bold())
                Text("Your cloud security, on your phone.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

            Spacer()

            VStack(spacing: 12) {
                Button(action: { Task { await signIn() } }) {
                    HStack(spacing: 10) {
                        if signingIn {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "person.badge.key.fill")
                        }
                        Text(signingIn ? "Opening sign-in…" : "Sign in with corporate account")
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(signingIn)

                Text("Microsoft 365 or Google Workspace. Personal accounts not supported.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 32)

            if let error = error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            Spacer().frame(height: 24)
        }
    }

    private func signIn() async {
        signingIn = true
        error = nil
        defer { signingIn = false }
        await auth.signIn()
        if case .error(let msg) = auth.state {
            error = msg
        }
    }
}
