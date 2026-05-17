import SwiftUI

@main
struct CISOCopilotApp: App {
    @State private var auth = AuthManager()
    @State private var api  = APIClient()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(auth)
                .environment(api)
                .onAppear { api.bind(auth: auth) }
        }
    }
}
