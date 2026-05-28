import SwiftUI
import UIKit

@main
struct CISOCopilotApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate

    @State private var auth = AuthManager()
    @State private var api  = APIClient()
    @StateObject private var incidentRouter = IncidentRouter()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(auth)
                .environment(api)
                .onAppear { api.bind(auth: auth) }
                .environmentObject(incidentRouter)
                .sheet(item: Binding(
                    get: { incidentRouter.activeIncident.map { IncidentSheetID(context: $0) } },
                    set: { if $0 == nil { incidentRouter.clear() } }
                )) { sheetID in
                    NavigationStack {
                        BriefingView(incident: sheetID.context)
                            .environmentObject(incidentRouter)
                    }
                }
        }
    }

    private struct IncidentSheetID: Identifiable {
        let context: IncidentContext
        var id: String { context.findingId }
    }
}

class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    // User tapped the notification (foreground OR cold start).
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                 didReceive response: UNNotificationResponse,
                                 withCompletionHandler completionHandler: @escaping () -> Void) {
        let userInfo = response.notification.request.content.userInfo
        if let findingId = userInfo["finding_id"] as? String {
            // Stringify the payload for IncidentRouter.handleNavigate.
            let context = userInfo.reduce(into: [String: Any]()) { acc, kv in
                if let k = kv.key as? String { acc[k] = kv.value }
            }
            NotificationCenter.default.post(
                name: .navigateToBriefing,
                object: findingId,
                userInfo: context
            )
        }
        completionHandler()
    }

    // Foreground push — show banner so the user can tap and navigate.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                 willPresent notification: UNNotification,
                                 withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
