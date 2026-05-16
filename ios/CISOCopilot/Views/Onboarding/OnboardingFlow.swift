import SwiftUI
import SwiftData

struct OnboardingFlow: View {
    @Environment(\.modelContext) private var modelContext
    @Environment(APIClient.self) private var api
    @Environment(PushManager.self) private var push

    @State private var step = 0
    @State private var draft = OnboardingDraft()
    @State private var saving = false
    @State private var error: String?

    private let totalSteps = 8

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ProgressView(value: Double(step + 1), total: Double(totalSteps))
                    .padding(.horizontal)
                    .padding(.top, 8)

                TabView(selection: $step) {
                    CloudStep(draft: $draft).tag(0)
                    IdentityStep(draft: $draft).tag(1)
                    EDRStep(draft: $draft).tag(2)
                    SIEMStep(draft: $draft).tag(3)
                    SaaSStep(draft: $draft).tag(4)
                    RegulatedDataStep(draft: $draft).tag(5)
                    OrgStep(draft: $draft).tag(6)
                    FinalStep(draft: $draft, saving: $saving, error: $error, onSubmit: submit).tag(7)
                }
                .tabViewStyle(.page(indexDisplayMode: .never))

                HStack {
                    if step > 0 {
                        Button("Back") { withAnimation { step -= 1 } }
                    }
                    Spacer()
                    if step < totalSteps - 1 {
                        Button("Next") { withAnimation { step += 1 } }
                            .buttonStyle(.borderedProminent)
                    }
                }
                .padding()
            }
            .navigationTitle("Set up your stack")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func submit() {
        Task {
            saving = true
            defer { saving = false }
            do {
                _ = await push.requestAuthorization()
                let profile = StoredProfile(
                    cloud: draft.cloud,
                    identity: draft.identity,
                    edr: draft.edr,
                    siem: draft.siem,
                    saas: draft.saas,
                    regulatedData: draft.regulatedData,
                    sector: draft.sector,
                    employeeBand: draft.employeeBand
                )
                modelContext.insert(profile)
                try modelContext.save()

                try await api.postProfile(profile.toDTO(), deviceToken: push.deviceToken)
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

@Observable
final class OnboardingDraft {
    var cloud: [String] = []
    var identity: [String] = []
    var edr: [String] = []
    var siem: [String] = []
    var saas: [String] = []
    var regulatedData: [String] = []
    var sector: String?
    var employeeBand: String?
}
