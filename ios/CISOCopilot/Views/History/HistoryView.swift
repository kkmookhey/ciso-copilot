import SwiftUI

struct HistoryView: View {
    @Environment(APIClient.self) private var api
    @State private var history: [HistoryDTO.HistoryEntry] = []
    @State private var loading = false

    var body: some View {
        NavigationStack {
            Group {
                if loading && history.isEmpty {
                    ProgressView()
                } else if history.isEmpty {
                    ContentUnavailableView(
                        "No history yet",
                        systemImage: "clock.arrow.circlepath",
                        description: Text("Briefs from the last 14 days will appear here.")
                    )
                } else {
                    List(history) { entry in
                        VStack(alignment: .leading) {
                            Text(entry.date).font(.headline)
                            Text("\(entry.itemCount) item\(entry.itemCount == 1 ? "" : "s")")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("History")
            .refreshable { await load() }
            .task { await load() }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            history = try await api.getHistory().history
        } catch {
            // Quietly empty.
        }
    }
}
