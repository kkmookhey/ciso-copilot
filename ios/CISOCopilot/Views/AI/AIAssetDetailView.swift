import SwiftUI

/// Detail view for a single AI entity. Renders attributes inline and the full
/// evidence packet inside a collapsible DisclosureGroup so the deterministic
/// trust trail is one tap away.
struct AIAssetDetailView: View {
    let assetId: String
    let fallback: EntitySummary

    @Environment(APIClient.self) private var api
    @State private var detail: EntityDetail?
    @State private var errorMessage: String?

    var body: some View {
        Form {
            Section("Entity") {
                LabeledContent("Kind", value: detail?.kind ?? fallback.kind)
                LabeledContent("Name", value: detail?.display_name ?? fallback.display_name)
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
        .navigationTitle(detail?.display_name ?? fallback.display_name)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func load() async {
        do {
            detail = try await api.getEntity(assetId)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
