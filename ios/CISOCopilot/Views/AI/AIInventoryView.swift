import SwiftUI

/// AI assets discovered by the scanner, grouped by repository. Read-only
/// in Slice 1b — relationships and per-asset graph land in 1c.
struct AIInventoryView: View {
    @Environment(APIClient.self) private var api
    @State private var assets: [AIAssetSummary] = []
    @State private var loading = true
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if let errorMessage {
                ContentUnavailableView("Couldn't load AI inventory", systemImage: "exclamationmark.triangle",
                                        description: Text(errorMessage))
            } else if loading {
                ProgressView("Loading AI inventory…")
            } else if assets.isEmpty {
                ContentUnavailableView("No AI assets yet",
                                        systemImage: "brain.head.profile",
                                        description: Text("Connect GitHub and run a scan from the web app to populate this view."))
            } else {
                List {
                    ForEach(groupedByRepo(), id: \.repo) { group in
                        Section(group.repo ?? "Unattached") {
                            ForEach(group.items) { asset in
                                NavigationLink(value: asset) {
                                    AIAssetRow(asset: asset)
                                }
                            }
                        }
                    }
                }
                .refreshable { await load() }
            }
        }
        .navigationTitle("AI Inventory")
        .task { await load() }
        .navigationDestination(for: AIAssetSummary.self) { asset in
            AIAssetDetailView(assetId: asset.id, fallback: asset)
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            assets = try await api.listAIAssets()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func groupedByRepo() -> [RepoGroup] {
        var buckets: [String?: [AIAssetSummary]] = [:]
        for a in assets {
            buckets[a.source_repo?.full_name, default: []].append(a)
        }
        return buckets
            .map { RepoGroup(repo: $0.key, items: $0.value) }
            .sorted { ($0.repo ?? "") < ($1.repo ?? "") }
    }

    private struct RepoGroup: Hashable {
        let repo: String?
        let items: [AIAssetSummary]
    }
}

private struct AIAssetRow: View {
    let asset: AIAssetSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(asset.asset_type)
                    .font(.caption2)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.gray.opacity(0.15), in: Capsule())
                Text(asset.name)
                    .font(.body)
                    .lineLimit(1)
            }
            if let path = asset.source_path {
                Text(path)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
    }
}
