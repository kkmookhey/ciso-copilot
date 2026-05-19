import SwiftUI

struct SignInView: View {
    @Environment(AuthManager.self) private var auth
    @Environment(APIClient.self)   private var api
    @State private var email     = ""
    @State private var working   = false
    @State private var localError: String?
    @FocusState private var emailFocused: Bool

    /// Combine local errors (discovery) with auth-state errors so both survive
    /// across re-mounts triggered by state changes.
    private var errorMessage: String? {
        if let e = localError { return e }
        if case .error(let msg) = auth.state { return msg }
        return nil
    }

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

            VStack(spacing: 14) {
                TextField("work@yourcompany.com", text: $email)
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($emailFocused)
                    .padding(.vertical, 12)
                    .padding(.horizontal, 14)
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .submitLabel(.continue)
                    .onSubmit { Task { await continueSignIn() } }

                Button(action: { Task { await continueSignIn() } }) {
                    HStack(spacing: 10) {
                        if working {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "arrow.right.circle.fill")
                        }
                        Text(working ? "Routing…" : "Continue")
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(working || email.isEmpty)

                Text("Microsoft 365 or Google Workspace. We route you to your company's sign-in automatically.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 32)

            if let error = errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            Spacer().frame(height: 24)
        }
        .onAppear { emailFocused = true }
    }

    private func continueSignIn() async {
        let trimmed = email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard trimmed.contains("@") else {
            localError = "Enter a valid work email."
            return
        }
        working = true
        localError = nil
        defer { working = false }

        do {
            let discovered = try await api.discoverTenant(email: trimmed)
            guard let url = URL(string: discovered.authorize_url) else {
                localError = "Invalid sign-in URL from server."
                return
            }
            await auth.signIn(authorizeURL: url)
        } catch {
            localError = error.localizedDescription
        }
    }
}
