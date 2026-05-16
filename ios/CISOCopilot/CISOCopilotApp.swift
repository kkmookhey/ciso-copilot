import SwiftUI
import SwiftData

@main
struct CISOCopilotApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var api = APIClient()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(api)
                .environment(PushManager.shared)
        }
        .modelContainer(for: [StoredProfile.self, StoredFeedback.self])
    }
}
