import Foundation
import Observation

/// HTTP client for the v2 AWS-native backend. Auto-attaches the Cognito
/// ID token (refreshed lazily by AuthManager when within 60s of expiry).
@Observable
final class APIClient {
    static let baseURL = URL(string: "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1")!

    private let session = URLSession.shared
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()
    private weak var auth: AuthManager?

    func bind(auth: AuthManager) {
        self.auth = auth
    }

    // MARK: - /me

    func fetchMe() async throws -> MeResponse? {
        let req = try await authedRequest(method: "GET", path: "me")
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        if http.statusCode == 401 { return nil }
        try Self.assertOK(http)
        return try decoder.decode(MeResponse.self, from: data)
    }

    // MARK: - /connections

    func listConnections() async throws -> [Connection] {
        let req = try await authedRequest(method: "GET", path: "connections")
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(ConnectionsResponse.self, from: data).connections
    }

    // MARK: - /onboarding/aws/initiate

    func initiateAwsOnboarding(displayName: String) async throws -> InitiateAwsResponse {
        var req = try await authedRequest(method: "POST", path: "onboarding/aws/initiate")
        req.httpBody = try encoder.encode(["display_name": displayName])
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(InitiateAwsResponse.self, from: data)
    }

    // MARK: - /onboarding/azure/initiate

    func initiateAzureOnboarding(displayName: String) async throws -> InitiateAzureResponse {
        var req = try await authedRequest(method: "POST", path: "onboarding/azure/initiate")
        req.httpBody = try encoder.encode(["display_name": displayName])
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(InitiateAzureResponse.self, from: data)
    }

    // MARK: - /findings

    func listFindings(severity: String? = nil, cloud: String? = nil, limit: Int = 50) async throws -> [Finding] {
        var components = URLComponents(
            url: Self.baseURL.appending(path: "findings"),
            resolvingAgainstBaseURL: false
        )!
        var qs: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(limit))]
        if let s = severity { qs.append(URLQueryItem(name: "severity", value: s)) }
        if let c = cloud    { qs.append(URLQueryItem(name: "cloud",    value: c)) }
        components.queryItems = qs

        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)

        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(FindingsResponse.self, from: data).findings
    }

    // MARK: - Helpers

    private func authedRequest(method: String, path: String) async throws -> URLRequest {
        var req = URLRequest(url: Self.baseURL.appending(path: path))
        req.httpMethod = method
        try await attachAuthHeader(&req)
        return req
    }

    private func attachAuthHeader(_ req: inout URLRequest) async throws {
        guard let auth = auth, let token = await auth.validIdToken() else {
            throw APIError.notAuthenticated
        }
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    }

    private static func assertOK(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        try assertOK(http)
    }

    private static func assertOK(_ http: HTTPURLResponse) throws {
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.badStatus(http.statusCode)
        }
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

struct ConnectionsResponse: Decodable {
    let connections: [Connection]
}

struct Connection: Decodable, Identifiable {
    let conn_id: String
    let cloud_type: String
    let display_name: String
    let status: String   // "pending" | "active" | "error" | "revoked"
    let account_identifier: String?
    let signals: ConnectionSignals
    let last_scan_at: String?
    let created_at: String

    var id: String { conn_id }
}

struct ConnectionSignals: Decodable {
    let pull_scan: Bool?
    let alerts: Bool?
    let drift: Bool?
}

struct InitiateAwsResponse: Decodable {
    let connection_id: String
    let external_id: String
    let cfn_url: String
    let template_url: String
}

struct InitiateAzureResponse: Decodable {
    let connection_id: String
    let external_id: String
    let script_url: String
    let run_command: String
}

struct FindingsResponse: Decodable {
    let findings: [Finding]
    let count: Int
    let limit: Int
    let offset: Int
}

struct Finding: Decodable, Identifiable, Hashable {
    let finding_id: String
    let check_id: String
    let title: String
    let description: String?
    let severity: String     // "critical" | "high" | "medium" | "low" | "info"
    let status: String
    let resource_arn: String?
    let resource_type: String?
    let region: String?
    let domain: String
    let frameworks: [String: [String]]
    let remediation: String?
    let first_seen: String
    let last_seen: String

    var id: String { finding_id }
}

enum APIError: Error, LocalizedError {
    case badResponse
    case badStatus(Int)
    case notAuthenticated

    var errorDescription: String? {
        switch self {
        case .badResponse:      return "Unexpected response"
        case .badStatus(let s): return "Server returned \(s)"
        case .notAuthenticated: return "Not signed in"
        }
    }
}
