import Foundation
import AuthenticationServices
import Observation
import UIKit

/// Cognito OAuth 2.0 authorization-code flow with refresh.
///
/// We keep the refresh token + id_token in Keychain; access_token is also stored
/// but is currently unused (we use id_token as the API JWT since API Gateway's
/// CognitoUserPoolsAuthorizer accepts ID tokens). Refresh happens lazily when
/// the cached id_token is within 60s of expiry.
@Observable
final class AuthManager: NSObject, ASWebAuthenticationPresentationContextProviding {

    // MARK: - Cognito config (matches CDK outputs)

    private enum CognitoConfig {
        static let domain   = "ciso-copilot.auth.us-east-1.amazoncognito.com"
        static let clientId = "4vhj2avv7lgtu4jjbuusi7bjq2"
        static let region   = "us-east-1"
        static let scheme   = "cisocopilot"
        static let redirectURI = "cisocopilot://auth/callback"
        static let scope    = "openid email profile"

        static var authorizeURL: URL {
            var c = URLComponents(string: "https://\(domain)/oauth2/authorize")!
            c.queryItems = [
                .init(name: "client_id",     value: clientId),
                .init(name: "response_type", value: "code"),
                .init(name: "scope",         value: scope),
                .init(name: "redirect_uri",  value: redirectURI),
            ]
            return c.url!
        }

        static var tokenURL: URL {
            URL(string: "https://\(domain)/oauth2/token")!
        }

        static var logoutURL: URL {
            var c = URLComponents(string: "https://\(domain)/logout")!
            c.queryItems = [
                .init(name: "client_id",    value: clientId),
                .init(name: "logout_uri",   value: "cisocopilot://auth/logout"),
            ]
            return c.url!
        }
    }

    // MARK: - State

    enum State: Equatable {
        case signedOut
        case signingIn
        case signedIn
        case error(String)
    }

    var state: State = .signedOut

    override init() {
        super.init()
        // Restore state on launch if a valid id_token (or refreshable refresh_token) exists.
        if Keychain.load(.idToken) != nil || Keychain.load(.refreshToken) != nil {
            state = .signedIn
        }
    }

    // MARK: - Sign in

    func signIn() async {
        state = .signingIn
        do {
            let code = try await runWebAuthFlow()
            try await exchangeCodeForTokens(code: code)
            state = .signedIn
        } catch {
            state = .error(error.localizedDescription)
        }
    }

    func signOut() {
        Keychain.clear()
        state = .signedOut
        // Best-effort revoke the Cognito session in the system browser so the next
        // sign-in shows the picker instead of auto-signing in.
        Task { _ = try? await runLogoutFlow() }
    }

    // MARK: - Token access (for APIClient)

    /// Returns a valid ID token, refreshing if it's expiring within 60s.
    /// Returns nil if the user must re-sign-in.
    func validIdToken() async -> String? {
        if let token = Keychain.load(.idToken),
           let expStr = Keychain.load(.idTokenExpiresAt),
           let exp = Int(expStr),
           exp - Int(Date().timeIntervalSince1970) > 60 {
            return token
        }
        // Try refresh
        do {
            try await refreshTokens()
            return Keychain.load(.idToken)
        } catch {
            state = .signedOut
            return nil
        }
    }

    // MARK: - ASWebAuthenticationSession

    private func runWebAuthFlow() async throws -> String {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<String, Error>) in
            let session = ASWebAuthenticationSession(
                url: CognitoConfig.authorizeURL,
                callbackURLScheme: CognitoConfig.scheme
            ) { callbackURL, error in
                if let error = error {
                    cont.resume(throwing: error); return
                }
                guard let callbackURL = callbackURL,
                      let comps = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
                      let code  = comps.queryItems?.first(where: { $0.name == "code" })?.value else {
                    cont.resume(throwing: AuthError.missingCode); return
                }
                cont.resume(returning: code)
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false  // share cookies for SSO convenience
            DispatchQueue.main.async { _ = session.start() }
        }
    }

    private func runLogoutFlow() async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            let session = ASWebAuthenticationSession(
                url: CognitoConfig.logoutURL,
                callbackURLScheme: CognitoConfig.scheme
            ) { _, _ in
                cont.resume()
            }
            session.presentationContextProvider = self
            DispatchQueue.main.async { _ = session.start() }
        }
    }

    // MARK: - OAuth token exchange

    private func exchangeCodeForTokens(code: String) async throws {
        var req = URLRequest(url: CognitoConfig.tokenURL)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data([
            "grant_type=authorization_code",
            "client_id=\(CognitoConfig.clientId)",
            "code=\(code)",
            "redirect_uri=\(CognitoConfig.redirectURI)",
        ].joined(separator: "&").utf8)

        let (data, response) = try await URLSession.shared.data(for: req)
        try Self.assertOK(response, data: data, context: "token exchange")

        let tokens = try JSONDecoder().decode(TokenResponse.self, from: data)
        persist(tokens)
    }

    private func refreshTokens() async throws {
        guard let refresh = Keychain.load(.refreshToken) else { throw AuthError.noRefreshToken }
        var req = URLRequest(url: CognitoConfig.tokenURL)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data([
            "grant_type=refresh_token",
            "client_id=\(CognitoConfig.clientId)",
            "refresh_token=\(refresh)",
        ].joined(separator: "&").utf8)

        let (data, response) = try await URLSession.shared.data(for: req)
        try Self.assertOK(response, data: data, context: "refresh")

        let tokens = try JSONDecoder().decode(TokenResponse.self, from: data)
        persist(tokens, preserveRefreshIfAbsent: refresh)
    }

    private func persist(_ tokens: TokenResponse, preserveRefreshIfAbsent existing: String? = nil) {
        Keychain.save(.idToken,     tokens.id_token)
        Keychain.save(.accessToken, tokens.access_token)
        if let r = tokens.refresh_token { Keychain.save(.refreshToken, r) }
        else if let r = existing       { Keychain.save(.refreshToken, r) }

        let exp = Int(Date().timeIntervalSince1970) + tokens.expires_in
        Keychain.save(.idTokenExpiresAt, String(exp))
    }

    // MARK: - Helpers

    private struct TokenResponse: Decodable {
        let id_token: String
        let access_token: String
        let refresh_token: String?
        let expires_in: Int
        let token_type: String
    }

    enum AuthError: LocalizedError {
        case missingCode
        case noRefreshToken
        case server(Int, String)

        var errorDescription: String? {
            switch self {
            case .missingCode:     return "Sign-in was cancelled or returned no authorization code."
            case .noRefreshToken:  return "No refresh token available; please sign in again."
            case .server(let s, let body): return "Auth server returned \(s): \(body.prefix(200))"
            }
        }
    }

    private static func assertOK(_ response: URLResponse, data: Data, context: String) throws {
        guard let http = response as? HTTPURLResponse else {
            throw AuthError.server(0, "no http response in \(context)")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw AuthError.server(http.statusCode, body)
        }
    }

    // ASWebAuthenticationPresentationContextProviding
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
            .first { $0.isKeyWindow }
            ?? ASPresentationAnchor()
    }
}
