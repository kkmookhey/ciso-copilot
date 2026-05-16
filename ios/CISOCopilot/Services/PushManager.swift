import Foundation
import UserNotifications
import UIKit
import Observation

@Observable
final class PushManager: NSObject, UNUserNotificationCenterDelegate {
    /// Singleton so AppDelegate can forward APNs callbacks without SwiftUI lifecycle gymnastics.
    static let shared = PushManager()

    var deviceToken: String?
    var authorizationStatus: UNAuthorizationStatus = .notDetermined

    private override init() {
        super.init()
    }

    /// Asks for permission (shows the iOS prompt). On grant, kicks off APNs registration.
    func requestAuthorization() async -> Bool {
        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
            authorizationStatus = granted ? .authorized : .denied
            if granted {
                await MainActor.run {
                    UIApplication.shared.registerForRemoteNotifications()
                }
            }
            return granted
        } catch {
            authorizationStatus = .denied
            return false
        }
    }

    /// Called on every app launch — if already authorized, re-trigger registration so the
    /// AppDelegate callback fires and the token gets posted to the backend.
    func refreshIfAuthorized() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        authorizationStatus = settings.authorizationStatus
        if settings.authorizationStatus == .authorized {
            await MainActor.run {
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
    }

    /// Called by AppDelegate when APNs hands us the device token. Stores it locally and
    /// POSTs to the backend immediately so the next brief-gen Act Now item can push.
    func didRegister(tokenData: Data) {
        let token = tokenData.map { String(format: "%02x", $0) }.joined()
        self.deviceToken = token
        Task { await postToken(token) }
    }

    private func postToken(_ token: String) async {
        let url = APIClient.baseURL.appending(path: "register-token")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = [
            "deviceId":    DeviceID.current,
            "deviceToken": token,
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }
}
