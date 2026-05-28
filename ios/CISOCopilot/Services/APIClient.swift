import Foundation
import Observation

/// HTTP client for the v2 AWS-native backend. Auto-attaches the Cognito
/// ID token (refreshed lazily by AuthManager when within 60s of expiry).
@Observable
final class APIClient {
    static let baseURL: URL = {
        guard let s = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
              !s.isEmpty,
              let u = URL(string: s) else {
            fatalError("API_BASE_URL missing or invalid in Info.plist. " +
                       "Copy ios/Local.xcconfig.example to ios/Local.xcconfig and rebuild.")
        }
        return u
    }()

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

    /// Register the APNs device token from didRegisterForRemoteNotifications.
    /// Called by RootView when the AppDelegate posts the device-token-ready
    /// notification (AppDelegate has no @Environment access to APIClient).
    func registerDeviceToken(_ hexToken: String) async throws {
        var req = try await authedRequest(method: "POST", path: "me/device-token")
        req.httpBody = try encoder.encode(["token": hexToken])
        let (_, response) = try await session.data(for: req)
        try Self.assertOK(response)
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

    // MARK: - /onboarding/entra/initiate

    func initiateEntraOnboarding(displayName: String) async throws -> InitiateEntraResponse {
        var req = try await authedRequest(method: "POST", path: "onboarding/entra/initiate")
        req.httpBody = try encoder.encode(["display_name": displayName])
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(InitiateEntraResponse.self, from: data)
    }

    // MARK: - /onboarding/gcp/initiate

    func initiateGcpOnboarding(displayName: String) async throws -> InitiateGcpResponse {
        var req = try await authedRequest(method: "POST", path: "onboarding/gcp/initiate")
        req.httpBody = try encoder.encode(["display_name": displayName])
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(InitiateGcpResponse.self, from: data)
    }

    // MARK: - /auth/discover-tenant (UNAUTHED — pre-login)

    /// Email-based IdP routing. Returns the Cognito authorize URL with the
    /// correct per-tenant identity_provider hint so the sign-in flow bypasses
    /// the Cognito Hosted UI picker and lands directly on the user's Microsoft
    /// tenant (or Google).
    func discoverTenant(email: String) async throws -> DiscoverTenantResponse {
        var req = URLRequest(url: Self.baseURL.appending(path: "auth/discover-tenant"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode([
            "email":    email,
            "platform": "ios",
        ])

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw APIError.badResponse }
        if !(200..<300).contains(http.statusCode) {
            // Surface server-supplied message if any.
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.discoveryFailed(http.statusCode, body)
        }
        return try decoder.decode(DiscoverTenantResponse.self, from: data)
    }

    // MARK: - /voice/session

    /// Mints an OpenAI Realtime ephemeral session for the iOS client.
    /// Returns the client_secret which is used as Bearer to open the WebSocket
    /// directly to OpenAI — our backend is not on the audio path.
    func voiceSession() async throws -> VoiceSessionResponse {
        let req = try await authedRequest(method: "POST", path: "voice/session")
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(VoiceSessionResponse.self, from: data)
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

    /// Total open findings matching the filters, without fetching the rows.
    /// Backed by the response's `total` field.
    func findingsTotal(severity: String? = nil, cloud: String? = nil) async throws -> Int {
        var components = URLComponents(
            url: Self.baseURL.appending(path: "findings"),
            resolvingAgainstBaseURL: false
        )!
        var qs: [URLQueryItem] = [URLQueryItem(name: "limit", value: "1")]
        if let s = severity { qs.append(URLQueryItem(name: "severity", value: s)) }
        if let c = cloud    { qs.append(URLQueryItem(name: "cloud",    value: c)) }
        components.queryItems = qs

        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)

        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(FindingsResponse.self, from: data).total
    }

    // MARK: - /events

    func listEvents(kind: String? = nil, severity: String? = nil, limit: Int = 50) async throws -> [AlertEvent] {
        var components = URLComponents(
            url: Self.baseURL.appending(path: "events"),
            resolvingAgainstBaseURL: false
        )!
        var qs: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(limit))]
        if let k = kind     { qs.append(URLQueryItem(name: "kind",     value: k)) }
        if let s = severity { qs.append(URLQueryItem(name: "severity", value: s)) }
        components.queryItems = qs

        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)

        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(EventsResponse.self, from: data).events
    }

    func eventsTotal(kind: String? = nil, severity: String? = nil) async throws -> Int {
        var components = URLComponents(
            url: Self.baseURL.appending(path: "events"),
            resolvingAgainstBaseURL: false
        )!
        var qs: [URLQueryItem] = [URLQueryItem(name: "limit", value: "1")]
        if let k = kind     { qs.append(URLQueryItem(name: "kind",     value: k)) }
        if let s = severity { qs.append(URLQueryItem(name: "severity", value: s)) }
        components.queryItems = qs

        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)

        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(EventsResponse.self, from: data).total
    }

    // MARK: - /policies

    func listPolicies(status: String? = nil) async throws -> [PolicySummary] {
        var components = URLComponents(url: Self.baseURL.appending(path: "policies"), resolvingAgainstBaseURL: false)!
        if let s = status { components.queryItems = [URLQueryItem(name: "status", value: s)] }
        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(PoliciesListResponse.self, from: data).policies
    }

    func getPolicy(_ id: String) async throws -> Policy {
        var req = URLRequest(url: Self.baseURL.appending(path: "policies/\(id)"))
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(Policy.self, from: data)
    }

    func updatePolicy(_ id: String, contentMd: String? = nil, status: String? = nil) async throws {
        var req = try await authedRequest(method: "PATCH", path: "policies/\(id)")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: String] = [:]
        if let c = contentMd { body["content_md"] = c }
        if let s = status    { body["status"]     = s }
        req.httpBody = try encoder.encode(body)
        let (_, response) = try await session.data(for: req)
        try Self.assertOK(response)
    }

    func enrichPolicy(_ id: String) async throws -> String {
        var req = try await authedRequest(method: "POST", path: "policies/\(id)/enrich")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        struct R: Decodable { let content_md: String }
        return try decoder.decode(R.self, from: data).content_md
    }

    func generateAllPolicies(companyName: String, effectiveDate: String, approver: String?) async throws -> Int {
        var req = try await authedRequest(method: "POST", path: "policies/generate-all")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var vars: [String: String] = ["company_name": companyName, "effective_date": effectiveDate]
        if let a = approver, !a.isEmpty { vars["approver"] = a }
        req.httpBody = try encoder.encode(["vars": vars])
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        struct R: Decodable { let count: Int }
        return try decoder.decode(R.self, from: data).count
    }

    // MARK: - /questionnaires

    func listQuestionnaires() async throws -> [QuestionnaireSummary] {
        var req = URLRequest(url: Self.baseURL.appending(path: "questionnaires"))
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(QuestionnairesListResponse.self, from: data).questionnaires
    }

    func getQuestionnaire(_ id: String) async throws -> QuestionnaireDetail {
        var req = URLRequest(url: Self.baseURL.appending(path: "questionnaires/\(id)"))
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(QuestionnaireDetail.self, from: data)
    }

    func patchQuestionnaireItem(_ qid: String, _ iid: String, answer: String?) async throws {
        var req = try await authedRequest(method: "PATCH", path: "questionnaires/\(qid)/items/\(iid)")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        struct Body: Encodable { let answer: String? }
        req.httpBody = try encoder.encode(Body(answer: answer))
        let (_, response) = try await session.data(for: req)
        try Self.assertOK(response)
    }

    func suggestQuestionnaireItem(_ qid: String, _ iid: String) async throws {
        var req = try await authedRequest(method: "POST", path: "questionnaires/\(qid)/items/\(iid)")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        let (_, response) = try await session.data(for: req)
        try Self.assertOK(response)
    }

    // MARK: - /trust (admin)

    func getTrustPage() async throws -> TrustPageSettings? {
        var req = URLRequest(url: Self.baseURL.appending(path: "trust"))
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        struct R: Decodable { let page: TrustPageSettings? }
        return try decoder.decode(R.self, from: data).page
    }

    func putTrustPage(_ settings: TrustPageSettings) async throws {
        var req = try await authedRequest(method: "PUT", path: "trust")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(settings)
        let (_, response) = try await session.data(for: req)
        try Self.assertOK(response)
    }

    // MARK: - /risks

    func listRisks(status: String? = nil, severity: String? = nil) async throws -> [Risk] {
        var components = URLComponents(
            url: Self.baseURL.appending(path: "risks"),
            resolvingAgainstBaseURL: false
        )!
        var qs: [URLQueryItem] = []
        if let s = status   { qs.append(URLQueryItem(name: "status",   value: s)) }
        if let s = severity { qs.append(URLQueryItem(name: "severity", value: s)) }
        if !qs.isEmpty { components.queryItems = qs }

        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(RisksResponse.self, from: data).risks
    }

    func createRisk(title: String, severity: String,
                    description: String? = nil, owner: String? = nil,
                    dueDate: String? = nil, findingId: String? = nil) async throws -> String {
        var req = try await authedRequest(method: "POST", path: "risks")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: String] = ["title": title, "severity": severity]
        if let d = description { body["description"] = d }
        if let o = owner       { body["owner"]       = o }
        if let dd = dueDate    { body["due_date"]    = dd }
        if let fid = findingId { body["finding_id"]  = fid }
        req.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        struct R: Decodable { let risk_id: String }
        return try decoder.decode(R.self, from: data).risk_id
    }

    func updateRiskStatus(_ riskId: String, status: String) async throws {
        var req = try await authedRequest(method: "PATCH", path: "risks/\(riskId)")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(["status": status])
        let (_, response) = try await session.data(for: req)
        try Self.assertOK(response)
    }

    // MARK: - /findings/rollup

    func findingsRollup(severity: String? = nil, cloud: String? = nil, q: String? = nil) async throws -> FindingsRollupResponse {
        var components = URLComponents(
            url: Self.baseURL.appending(path: "findings/rollup"),
            resolvingAgainstBaseURL: false
        )!
        var qs: [URLQueryItem] = []
        if let s = severity { qs.append(URLQueryItem(name: "severity", value: s)) }
        if let c = cloud    { qs.append(URLQueryItem(name: "cloud",    value: c)) }
        if let t = q, !t.isEmpty { qs.append(URLQueryItem(name: "q",  value: t)) }
        if !qs.isEmpty { components.queryItems = qs }

        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(FindingsRollupResponse.self, from: data)
    }

    func listFindingsByCheck(_ checkId: String, limit: Int = 200) async throws -> [Finding] {
        var components = URLComponents(
            url: Self.baseURL.appending(path: "findings"),
            resolvingAgainstBaseURL: false
        )!
        components.queryItems = [
            URLQueryItem(name: "check_id", value: checkId),
            URLQueryItem(name: "limit",    value: String(limit)),
        ]
        var req = URLRequest(url: components.url!)
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(FindingsResponse.self, from: data).findings
    }

    // MARK: - /findings/summary

    func findingsSummary() async throws -> FindingsSummaryResponse {
        var req = URLRequest(url: Self.baseURL.appending(path: "findings/summary"))
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(FindingsSummaryResponse.self, from: data)
    }

    // MARK: - /compliance/summary

    func complianceSummary() async throws -> ComplianceSummaryResponse {
        var req = URLRequest(url: Self.baseURL.appending(path: "compliance/summary"))
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(ComplianceSummaryResponse.self, from: data)
    }

    // MARK: - /entities

    func listEntities(domain: String? = nil, kind: String? = nil) async throws -> [EntitySummary] {
        var comps = URLComponents(url: Self.baseURL.appending(path: "entities"), resolvingAgainstBaseURL: false)!
        var qs: [URLQueryItem] = []
        if let domain { qs.append(.init(name: "domain", value: domain)) }
        if let kind   { qs.append(.init(name: "kind",   value: kind)) }
        if !qs.isEmpty { comps.queryItems = qs }
        var req = URLRequest(url: comps.url!)
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        struct Resp: Decodable { let entities: [EntitySummary]; let next_page: Int? }
        return try decoder.decode(Resp.self, from: data).entities
    }

    func getEntity(_ id: String) async throws -> EntityDetail {
        var req = URLRequest(url: Self.baseURL.appending(path: "entities/\(id)"))
        try await attachAuthHeader(&req)
        let (data, response) = try await session.data(for: req)
        try Self.assertOK(response)
        return try decoder.decode(EntityDetail.self, from: data)
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

struct EventsResponse: Decodable {
    let events: [AlertEvent]
    let total: Int
    let limit: Int
    let offset: Int
}

struct AlertEvent: Decodable, Identifiable, Hashable {
    let event_id: String
    let kind: String          // "alert" | "drift"
    let source: String        // e.g. "aws.guardduty"
    let severity: String
    let title: String
    let description: String?
    let resource_arn: String?
    let actor: String?
    let fired_at: String
    let ingested_at: String

    var id: String { event_id }
}

struct RisksResponse: Decodable {
    let risks: [Risk]
    let count: Int
}

struct Risk: Decodable, Identifiable, Hashable {
    let risk_id:     String
    let title:       String
    let description: String?
    let severity:    String   // critical | high | medium | low | info
    let status:      String   // open | mitigated | accepted | transferred | closed
    let owner:       String?
    let due_date:    String?  // YYYY-MM-DD
    let finding_id:  String?
    let notes:       String?
    let created_at:  String
    let updated_at:  String

    var id: String { risk_id }
}

struct FindingsRollupResponse: Decodable {
    let groups:         [FindingGroup]
    let total_findings: Int
    let total_groups:   Int
}

struct FindingGroup: Decodable, Identifiable, Hashable {
    let domain:           String
    let check_id:         String
    let title:            String
    let check_title:      String?
    let severity:         String
    let count:            Int
    let frameworks:       [String: [String]]
    let sample_resources: [SampleResource]

    var id: String { "\(domain)/\(check_id)" }

    struct SampleResource: Decodable, Hashable {
        let resource_arn: String
        let region:       String?
    }
}

struct PoliciesListResponse: Decodable { let policies: [PolicySummary] }

struct PolicySummary: Decodable, Identifiable, Hashable {
    let policy_id:     String
    let template_key:  String
    let title:         String
    let status:        String
    let version:       Int
    let soc2_controls: [String]
    let created_at:    String
    let updated_at:    String

    var id: String { policy_id }
}

struct Policy: Decodable {
    let policy_id:     String
    let template_key:  String
    let title:         String
    let status:        String
    let version:       Int
    let content_md:    String
    let soc2_controls: [String]
    let created_at:    String
    let updated_at:    String
}

struct QuestionnairesListResponse: Decodable { let questionnaires: [QuestionnaireSummary] }

struct QuestionnaireSummary: Decodable, Identifiable, Hashable {
    let questionnaire_id: String
    let name:             String
    let template_key:     String
    let status:           String
    let created_at:       String
    let updated_at:       String
    let total:            Int
    let answered:         Int

    var id: String { questionnaire_id }
}

struct QuestionnaireDetail: Decodable {
    let questionnaire_id: String
    let name:             String
    let template_key:     String
    let status:           String
    let created_at:       String
    let source_filename:  String?
    let items:            [QuestionnaireItem]
}

struct QuestionnaireItem: Decodable, Identifiable, Hashable {
    let item_id:        String
    let question_id:    String
    let question:       String
    let category:       String?
    let answer:         String?
    let confidence:     String?
    let notes:          String?
    let sort_order:     Int

    var id: String { item_id }
}

struct TrustPageSettings: Codable {
    var page_id:             String?
    var slug:                String
    var public_name:         String
    var notes:               String?
    var is_published:        Bool
    var show_compliance:     Bool
    var show_finding_counts: Bool
    var show_clouds:         Bool
    var show_last_scan:      Bool
    var created_at:          String?
    var updated_at:          String?
}

struct FindingsSummaryResponse: Decodable {
    let by_severity: BySeverity
    let by_cloud:    ByCloud
    let total:       Int

    struct BySeverity: Decodable {
        let critical: Int
        let high:     Int
        let medium:   Int
        let low:      Int
        let info:     Int
    }
    struct ByCloud: Decodable {
        let aws:   Int
        let azure: Int
        let gcp:   Int
        let entra: Int
    }

    var severitySlices: [(name: String, value: Int)] {
        [
            ("critical", by_severity.critical),
            ("high",     by_severity.high),
            ("medium",   by_severity.medium),
            ("low",      by_severity.low),
            ("info",     by_severity.info),
        ].filter { $0.value > 0 }
    }

    var cloudBars: [(name: String, value: Int)] {
        [
            ("AWS",   by_cloud.aws),
            ("Azure", by_cloud.azure),
            ("GCP",   by_cloud.gcp),
            ("Entra", by_cloud.entra),
        ].filter { $0.value > 0 }
    }
}

struct ComplianceSummaryResponse: Decodable {
    let summary: [String: FrameworkScore]
    let by_framework_control: [FrameworkControlRow]
}

struct FrameworkScore: Decodable {
    let total: Int
    let passing: Int
    let failing: Int
    let score_pct: Double
}

struct FrameworkControlRow: Decodable {
    let framework: String
    let control_id: String
    let fail_count: Int
    let pass_count: Int
    let total: Int
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

struct InitiateEntraResponse: Decodable {
    let connection_id: String
    let state:         String
    let consent_url:   String
}

struct InitiateGcpResponse: Decodable {
    let connection_id: String
    let external_id:   String
    let script_url:    String
    let run_command:   String
}

struct VoiceSessionResponse: Decodable {
    let session_id:    String?
    let client_secret: String?
    let expires_at:    Int?
    let model:         String?
}

struct DiscoverTenantResponse: Decodable {
    let idp_name:      String
    let idp_provider:  String   // "microsoft" | "google"
    let tenant_id:     String?
    let authorize_url: String
}

struct FindingsResponse: Decodable {
    let findings: [Finding]
    let count: Int   // this page
    let total: Int   // all matches under the filters
    let limit: Int
    let offset: Int
}

struct Finding: Decodable, Identifiable, Hashable {
    let finding_id: String
    let check_id: String
    let title: String
    let check_title: String?
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

// MARK: - Entity DTOs (unified inventory — SP1)

struct EntitySummary: Decodable, Identifiable, Hashable {
    let id: String
    let kind: String
    let natural_key: String
    let display_name: String
    let domain: String
    let source_path: String?
    let detector_id: String
    let first_seen_at: String
    let last_seen_at: String
    let attributes: AnyJSON

    // Manual Hashable/Equatable — entity id is unique, AnyJSON isn't Hashable.
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
    static func == (lhs: EntitySummary, rhs: EntitySummary) -> Bool { lhs.id == rhs.id }
}

struct EntityDetail: Decodable {
    let id: String
    let kind: String
    let natural_key: String
    let display_name: String
    let domain: String
    let source_path: String?
    let detector_id: String
    let first_seen_at: String
    let last_seen_at: String
    let attributes: AnyJSON
    let evidence_packet: AnyJSON
    let connection_id: String?
}

/// Loosely-typed JSON for fields where the iOS detail view just renders the
/// raw structure (attributes, evidence_packet).
enum AnyJSON: Decodable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([AnyJSON])
    case object([String: AnyJSON])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let b = try? c.decode(Bool.self)            { self = .bool(b); return }
        if let n = try? c.decode(Double.self)          { self = .number(n); return }
        if let s = try? c.decode(String.self)          { self = .string(s); return }
        if let a = try? c.decode([AnyJSON].self)       { self = .array(a); return }
        if let o = try? c.decode([String: AnyJSON].self) { self = .object(o); return }
        self = .null
    }

    func prettyJSON() -> String {
        let obj = jsonObject()
        let wrapped: Any = (obj is NSNull) ? ["_": NSNull()] : obj
        guard JSONSerialization.isValidJSONObject(wrapped),
              let data = try? JSONSerialization.data(
                  withJSONObject: wrapped, options: [.prettyPrinted, .sortedKeys]),
              let s = String(data: data, encoding: .utf8) else { return "" }
        return s
    }

    private func jsonObject() -> Any {
        switch self {
        case .null:           return NSNull()
        case .bool(let b):    return b
        case .number(let n):  return n
        case .string(let s):  return s
        case .array(let a):   return a.map { $0.jsonObject() }
        case .object(let o):
            var out: [String: Any] = [:]
            for (k, v) in o { out[k] = v.jsonObject() }
            return out
        }
    }
}

enum APIError: Error, LocalizedError {
    case badResponse
    case badStatus(Int)
    case notAuthenticated
    case discoveryFailed(Int, String)

    var errorDescription: String? {
        switch self {
        case .badResponse:      return "Unexpected response"
        case .badStatus(let s): return "Server returned \(s)"
        case .notAuthenticated: return "Not signed in"
        case .discoveryFailed(let s, let body):
            if let data = body.data(using: .utf8),
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let msg  = json["message"] as? String ?? json["error"] as? String {
                return "Could not route sign-in (\(s)): \(msg)"
            }
            return "Could not route sign-in (\(s))"
        }
    }
}
