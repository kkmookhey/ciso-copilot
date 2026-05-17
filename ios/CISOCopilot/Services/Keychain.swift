import Foundation
import Security

/// Minimal Keychain wrapper for storing OAuth tokens.
/// We keep three items: id_token, access_token, refresh_token.
enum Keychain {
    private static let service = "ai.transilience.cisocopilot"

    enum Key: String {
        case idToken      = "id_token"
        case accessToken  = "access_token"
        case refreshToken = "refresh_token"
        case idTokenExpiresAt = "id_token_expires_at"  // unix seconds, as string
    }

    static func save(_ key: Key, _ value: String) {
        let data = Data(value.utf8)
        var query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue,
        ]
        SecItemDelete(query as CFDictionary)
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(query as CFDictionary, nil)
    }

    static func load(_ key: Key) -> String? {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue,
            kSecReturnData as String:  true,
            kSecMatchLimit as String:  kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func clear() {
        for key in [Key.idToken, .accessToken, .refreshToken, .idTokenExpiresAt] {
            let query: [String: Any] = [
                kSecClass as String:       kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrAccount as String: key.rawValue,
            ]
            SecItemDelete(query as CFDictionary)
        }
    }
}
