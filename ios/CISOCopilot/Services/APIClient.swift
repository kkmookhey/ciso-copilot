import Foundation
import Observation

@Observable
final class APIClient {
    /// Worker URL. Update after `wrangler deploy` prints the deployed URL.
    /// Form: https://ciso-copilot.<workers-subdomain>.workers.dev
    static var baseURL: URL {
        URL(string: "https://ciso-copilot.kkmookhey.workers.dev")!
    }

    private var deviceId: String { DeviceID.current }
    private let session = URLSession.shared
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    // MARK: - POST /profile

    func postProfile(_ profile: StackProfileDTO, deviceToken: String?) async throws {
        let url = Self.baseURL.appending(path: "profile")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(
            ProfileRequest(deviceId: deviceId, stackProfile: profile, deviceToken: deviceToken)
        )
        try await sendAndVerify(req)
    }

    // MARK: - GET /brief

    func getBrief() async throws -> BriefDTO {
        var components = URLComponents(url: Self.baseURL.appending(path: "brief"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "device", value: deviceId)]
        let (data, response) = try await session.data(from: components.url!)
        try Self.assertOK(response)
        return try decoder.decode(BriefDTO.self, from: data)
    }

    // MARK: - GET /history

    func getHistory() async throws -> HistoryDTO {
        var components = URLComponents(url: Self.baseURL.appending(path: "history"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "device", value: deviceId)]
        let (data, response) = try await session.data(from: components.url!)
        try Self.assertOK(response)
        return try decoder.decode(HistoryDTO.self, from: data)
    }

    // MARK: - POST /feedback

    func postFeedback(itemId: String, sentiment: String, reason: String?) async throws {
        let url = Self.baseURL.appending(path: "feedback")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(
            FeedbackRequest(deviceId: deviceId, itemId: itemId, sentiment: sentiment, reason: reason)
        )
        try await sendAndVerify(req)
    }

    // MARK: - POST /register-token

    func registerToken(_ token: String) async throws {
        let url = Self.baseURL.appending(path: "register-token")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(TokenRequest(deviceId: deviceId, deviceToken: token))
        try await sendAndVerify(req)
    }

    // MARK: - Helpers

    private func sendAndVerify(_ req: URLRequest) async throws {
        let (_, response) = try await session.data(for: req)
        try Self.assertOK(response)
    }

    private static func assertOK(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.badStatus(http.statusCode)
        }
    }
}

enum APIError: Error, LocalizedError {
    case badResponse
    case badStatus(Int)

    var errorDescription: String? {
        switch self {
        case .badResponse:    return "Unexpected response"
        case .badStatus(let s): return "Server returned status \(s)"
        }
    }
}
