import SwiftUI
import UIKit

struct ConnectCloudsView: View {
    @Environment(APIClient.self) private var api

    @State private var pendingAws   = false
    @State private var pendingAzure = false
    @State private var pendingEntra = false
    @State private var awsCfnUrl: URL?
    @State private var azureCommand: String?
    @State private var entraConsentUrl: URL?
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
                          loading: pendingAws,
                          action: { Task { await connectAws() } })

                CloudTile(name: "Azure",
                          tagline: "Service Principal via Cloud Shell",
                          enabled: true,
                          loading: pendingAzure,
                          action: { Task { await connectAzure() } })

                CloudTile(name: "Entra",
                          tagline: "Microsoft admin consent for Graph API",
                          enabled: true,
                          loading: pendingEntra,
                          action: { Task { await connectEntra() } })

                CloudTile(name: "GCP",    tagline: "Coming in Phase D", enabled: false)

                if let awsCfnUrl = awsCfnUrl {
                    LaunchCard(url: awsCfnUrl)
                }

                if let cmd = azureCommand {
                    AzureCommandCard(command: cmd)
                }

                if let entraConsentUrl = entraConsentUrl {
                    EntraConsentCard(url: entraConsentUrl)
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
        pendingAws = true; error = nil
        defer { pendingAws = false }
        do {
            let r = try await api.initiateAwsOnboarding(displayName: "AWS Account")
            awsCfnUrl = URL(string: r.cfn_url)
        } catch { self.error = error.localizedDescription }
    }

    private func connectAzure() async {
        pendingAzure = true; error = nil
        defer { pendingAzure = false }
        do {
            let r = try await api.initiateAzureOnboarding(displayName: "Azure Subscription")
            azureCommand = r.run_command
        } catch { self.error = error.localizedDescription }
    }

    private func connectEntra() async {
        pendingEntra = true; error = nil
        defer { pendingEntra = false }
        do {
            let r = try await api.initiateEntraOnboarding(displayName: "Entra Tenant")
            entraConsentUrl = URL(string: r.consent_url)
        } catch { self.error = error.localizedDescription }
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
                .font(.callout).foregroundStyle(.secondary)
            Link(destination: url) {
                Label("Launch CloudFormation", systemImage: "arrow.up.right.square.fill")
                    .frame(maxWidth: .infinity).padding(.vertical, 10)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.blue.opacity(0.08))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.blue.opacity(0.25), lineWidth: 1))
        )
    }
}

struct EntraConsentCard: View {
    let url: URL

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Entra admin consent")
                .font(.headline)
            Text("Your tenant admin needs to approve CISO Copilot's Microsoft Graph permissions (Policy.Read.All, Directory.Read.All, IdentityProtection.Read.All). Tap below to open the consent dialog — you'll be redirected to a confirmation page when done.")
                .font(.callout).foregroundStyle(.secondary)
            Link(destination: url) {
                Label("Open admin consent", systemImage: "checkmark.shield.fill")
                    .frame(maxWidth: .infinity).padding(.vertical, 10)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.teal.opacity(0.08))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.teal.opacity(0.25), lineWidth: 1))
        )
    }
}

struct AzureCommandCard: View {
    let command: String

    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Run this in Azure Cloud Shell")
                .font(.headline)
            Text("Opens Cloud Shell with your subscription selected. Paste and run the command below; it creates a Service Principal with Reader + Security Reader, then notifies CISO Copilot. Takes about 30 seconds.")
                .font(.callout).foregroundStyle(.secondary)

            // Command in a monospaced text view (selectable)
            Text(command)
                .font(.system(.caption, design: .monospaced))
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.systemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .textSelection(.enabled)

            HStack {
                Button {
                    UIPasteboard.general.string = command
                    copied = true
                    Task {
                        try? await Task.sleep(nanoseconds: 1_500_000_000)
                        copied = false
                    }
                } label: {
                    Label(copied ? "Copied" : "Copy command",
                          systemImage: copied ? "checkmark" : "doc.on.doc")
                }
                .buttonStyle(.bordered)

                Link(destination: URL(string: "https://shell.azure.com")!) {
                    Label("Open Cloud Shell", systemImage: "arrow.up.right.square.fill")
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.purple.opacity(0.08))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.purple.opacity(0.25), lineWidth: 1))
        )
    }
}
