import SwiftUI
import SwiftData

struct RootView: View {
    @Query private var profiles: [StoredProfile]

    var body: some View {
        Group {
            if let profile = profiles.first {
                MainTabView(profile: profile)
            } else {
                OnboardingFlow()
            }
        }
        .task {
            // Re-fire APNs registration on every launch if previously authorized.
            // Catches the case where token wasn't successfully posted to backend on first launch.
            await PushManager.shared.refreshIfAuthorized()
        }
    }
}
