import Foundation

// MARK: - Profile

struct StackProfileDTO: Codable {
    var cloud: [String]
    var identity: [String]
    var edr: [String]
    var siem: [String]
    var saas: [String]
    var regulatedData: [String]
    var sector: String?
    var employeeBand: String?
}

struct ProfileRequest: Encodable {
    let deviceId: String
    let stackProfile: StackProfileDTO
    let deviceToken: String?
}

struct TokenRequest: Encodable {
    let deviceId: String
    let deviceToken: String
}

struct FeedbackRequest: Encodable {
    let deviceId: String
    let itemId: String
    let sentiment: String
    let reason: String?
}

// MARK: - Brief / Item

struct BriefDTO: Decodable {
    let date: String
    let items: [ItemDTO]
    let generatedAt: String?
}

struct ItemDTO: Decodable, Identifiable, Hashable {
    let cveId: String
    let description: String
    let cvssScore: Double
    let epssScore: Double
    let inKev: Bool
    let kevDateAdded: String?
    let matchedVendors: [String]
    let matchedProducts: [String]
    let confidence: String        // "high" | "medium" | "low"
    let severity: String          // "act_now" | "check_today" | "watch" | "fyi"
    let relevance: Double
    let whyItMatters: String?
    let boardParagraph: String?
    let teamQuestions: TeamQuestionsDTO?

    var id: String { cveId }
}

struct TeamQuestionsDTO: Decodable, Hashable {
    let infrastructure: [String]
    let soc: [String]
    let vuln_mgmt: [String]
}

// MARK: - History

struct HistoryDTO: Decodable {
    let history: [HistoryEntry]

    struct HistoryEntry: Decodable, Identifiable {
        let date: String
        let generatedAt: String?
        let itemCount: Int

        var id: String { date }
    }
}

// MARK: - Severity helpers

enum Severity: String {
    case actNow = "act_now"
    case checkToday = "check_today"
    case watch
    case fyi

    var label: String {
        switch self {
        case .actNow:     return "Act Now"
        case .checkToday: return "Check Today"
        case .watch:      return "Watch"
        case .fyi:        return "FYI"
        }
    }
}
