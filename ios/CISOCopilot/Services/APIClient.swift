import Foundation
import Observation

/// HTTP client for the v2 AWS-native backend. All requests are JWT-authed
/// against API Gateway's CognitoUserPoolsAuthorizer using the ID token from
/// AuthManager.
///
/// The /me endpoint is the only v2 Phase 0 surface. Phase A reintroduces
/// /profile, /brief, /feedback, /history with proper schema.
@Observable
final class APIClient {
    static let baseURL = URL(string: "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1")!

    private let session = URLSession.shared
    private let decoder = JSONDecoder()
    private weak var auth: AuthManager?

    func bind(auth: AuthManager) {
        self.auth = auth
    }

    // MARK: - /me

    func fetchMe() async throws -> MeResponse? {
        let url = Self.baseURL.appending(path: "me")
        var req = URLRequest(url: url)
        try await attachAuthHeader(&req)

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        if http.statusCode == 401 { return nil }  // caller should sign user out
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.badStatus(http.statusCode)
        }
        return try decoder.decode(MeResponse.self, from: data)
    }

    // MARK: - Auth header

    private func attachAuthHeader(_ req: inout URLRequest) async throws {
        guard let auth = auth, let token = await auth.validIdToken() else {
            throw APIError.notAuthenticated
        }
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    }
}

// MARK: - DTOs

struct MeResponse: Decodable {
    let user: MeUser?
    let tenant: MeTenant?
}

struct MeUser: Decodable {
    let email: String?
    let role: String?
}

struct MeTenant: Decodable {
    let tenant_id: String?
    let display_name: String?
    let status: String?  // "pending" | "approved" | "rejected" | "suspended"
}

enum APIError: Error, LocalizedError {
    case badResponse
    case badStatus(Int)
    case notAuthenticated

    var errorDescription: String? {
        switch self {
        case .badResponse:        return "Unexpected response"
        case .badStatus(let s):   return "Server returned \(s)"
        case .notAuthenticated:   return "Not signed in"
        }
    }
}
