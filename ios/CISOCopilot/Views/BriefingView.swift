import SwiftUI

struct BriefingView: View {
    let incident: IncidentContext
    @EnvironmentObject var router: IncidentRouter

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "exclamationmark.shield.fill")
                    .font(.system(size: 28))
                    .foregroundColor(.orange)
                VStack(alignment: .leading) {
                    Text(payloadString("kind_label", default: "Incident"))
                        .font(.headline)
                    Text(payloadString("speakable_summary", default: incident.findingId))
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
            }
            .padding()
            .background(Color(.systemGray6))
            .cornerRadius(12)

            // Voice surface lands here in Task 18.
            Text("Voice briefing — coming in Task 18.")
                .foregroundColor(.secondary)
                .padding()

            Spacer()
        }
        .padding()
        .navigationTitle("Briefing")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button("Done") { router.clear() }
            }
        }
    }

    private func payloadString(_ key: String, `default` fallback: String) -> String {
        (incident.payload[key] as? String) ?? fallback
    }
}
