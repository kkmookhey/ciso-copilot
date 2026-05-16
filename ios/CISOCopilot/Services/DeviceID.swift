import Foundation

enum DeviceID {
    private static let key = "ai.transilience.cisocopilot.deviceID"

    static var current: String {
        if let existing = UserDefaults.standard.string(forKey: key) {
            return existing
        }
        let new = UUID().uuidString
        UserDefaults.standard.set(new, forKey: key)
        return new
    }
}
