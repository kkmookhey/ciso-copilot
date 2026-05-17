import SwiftUI

struct ConnectCloudsView: View {
    @Environment(APIClient.self) private var api
    @State private var pending = false
    @State private var cfnUrl: URL?
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Connect a cloud")
                    .font(.largeTitle.bold())
                Text("Pick a cloud to start scanning.")
                    .foregroundStyle(.secondary)

                CloudTile(name: "AWS",
                          tagline: "Cross-account read-only role via CloudFormation",
                          enabled: true,
                          loading: pending,
                          action: { Task { await connectAws() } })

                CloudTile(name: "Azure",  tagline: "Coming in Phase B", enabled: false)
                CloudTile(name: "Entra",  tagline: "Coming in Phase C", enabled: false)
                CloudTile(name: "GCP",    tagline: "Coming in Phase D", enabled: false)

                if let cfnUrl = cfnUrl {
                    LaunchCard(url: cfnUrl)
                }

                if let error = error {
                    Text(error).font(.callout).foregroundStyle(.red).padding(.top, 8)
                }
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
    }

    private func connectAws() async {
        pending = true
        error = nil
        defer { pending = false }
        do {
            let r = try await api.initiateAwsOnboarding(displayName: "AWS Account")
            cfnUrl = URL(string: r.cfn_url)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Subviews

struct CloudTile: View {
    let name: String
    let tagline: String
    let enabled: Bool
    var loading: Bool = false
    var action: (() -> Void)? = nil

    var body: some View {
        Button(action: { action?() }) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(name).font(.headline)
                    Text(tagline).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if loading {
                    ProgressView()
                } else if enabled {
                    Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                }
            }
            .padding()
            .background(enabled ? Color(.secondarySystemBackground) : Color(.systemGray6))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .opacity(enabled ? 1 : 0.55)
        }
        .buttonStyle(.plain)
        .disabled(!enabled || loading)
    }
}

struct LaunchCard: View {
    let url: URL

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("One-click AWS connection")
                .font(.headline)
            Text("Tap below to open the AWS CloudFormation console with our template + your one-time external ID pre-filled. Review and click Create. Once the stack completes, you'll see your AWS account in the Brief tab.")
                .font(.callout)
                .foregroundStyle(.secondary)

            Link(destination: url) {
                Label("Launch CloudFormation", systemImage: "arrow.up.right.square.fill")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.blue.opacity(0.08))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color.blue.opacity(0.25), lineWidth: 1)
                )
        )
    }
}
