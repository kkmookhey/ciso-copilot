import SwiftUI

/// Detail view for a single AI asset. Renders attributes inline and the full
/// evidence packet inside a collapsible DisclosureGroup so the deterministic
/// trust trail is one tap away.
struct AIAssetDetailView: View {
    let assetId: String
    let fallback: AIAssetSummary

    @Environment(APIClient.self) private var api
    @State private var detail: AIAssetDetail?
    @State private var errorMessage: String?

    var body: some View {
        Form {
            Section("Asset") {
                LabeledContent("Type", value: detail?.asset_type ?? fallback.asset_type)
                LabeledContent("Name", value: detail?.name ?? fallback.name)
                if let repo = (detail?.source_repo ?? fallback.source_repo) {
                    LabeledContent("Repository", value: repo.full_name)
                }
                if let path = (detail?.source_path ?? fallback.source_path) {
                    LabeledContent("Source path", value: path)
                }
                LabeledContent("Detector", value: detail?.detector_id ?? fallback.detector_id)
            }

            if let detail {
                Section("Attributes") {
                    Text(detail.attributes.prettyJSON())
                        .font(.system(.caption2, design: .monospaced))
                        .textSelection(.enabled)
                }

                Section {
                    DisclosureGroup("Evidence packet (raw)") {
                        Text(detail.evidence_packet.prettyJSON())
                            .font(.system(.caption2, design: .monospaced))
                            .textSelection(.enabled)
                            .padding(.top, 4)
                    }
                }
            } else if errorMessage == nil {
                Section { ProgressView() }
            }

            if let errorMessage {
                Section { Text(errorMessage).foregroundStyle(.red) }
            }
        }
        .navigationTitle(detail?.name ?? fallback.name)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func load() async {
        do {
            detail = try await api.getAIAsset(assetId)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
