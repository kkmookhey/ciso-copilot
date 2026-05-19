import SwiftUI

/// AI entities discovered by the scanner, grouped by kind. Read-only in SP1 —
/// cross-domain relationships and per-entity graph land in SP2 via the
/// /v1/entities/{id}/graph and /relationships endpoints.
struct AIInventoryView: View {
    @Environment(APIClient.self) private var api
    @State private var entities: [EntitySummary] = []
    @State private var loading = true
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if let errorMessage {
                ContentUnavailableView("Couldn't load AI inventory", systemImage: "exclamationmark.triangle",
                                        description: Text(errorMessage))
            } else if loading {
                ProgressView("Loading AI inventory…")
            } else if entities.isEmpty {
                ContentUnavailableView("No AI entities yet",
                                        systemImage: "brain.head.profile",
                                        description: Text("Connect GitHub and run a scan from the web app to populate this view."))
            } else {
                List {
                    ForEach(groupedByKind(), id: \.kind) { group in
                        Section(prettyKind(group.kind)) {
                            ForEach(group.items) { entity in
                                NavigationLink(value: entity) {
                                    EntityRow(entity: entity)
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
        .navigationDestination(for: EntitySummary.self) { entity in
            AIAssetDetailView(assetId: entity.id, fallback: entity)
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            entities = try await api.listEntities(domain: "ai")
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func groupedByKind() -> [KindGroup] {
        var buckets: [String: [EntitySummary]] = [:]
        for e in entities {
            buckets[e.kind, default: []].append(e)
        }
        return buckets
            .map { KindGroup(kind: $0.key, items: $0.value) }
            .sorted { $0.kind < $1.kind }
    }

    private struct KindGroup: Hashable {
        let kind: String
        let items: [EntitySummary]
    }
}

private struct EntityRow: View {
    let entity: EntitySummary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(prettyKind(entity.kind))
                    .font(.caption2)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.gray.opacity(0.15), in: Capsule())
                Text(entity.display_name)
                    .font(.body)
                    .lineLimit(1)
            }
            if let path = entity.source_path {
                Text(path)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
    }
}

/// Strip the "ai_" prefix for prettier display (e.g. "ai_framework" → "framework").
private func prettyKind(_ kind: String) -> String {
    kind.hasPrefix("ai_") ? String(kind.dropFirst(3)) : kind
}
