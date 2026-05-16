import Foundation
import SwiftData

@Model
final class StoredProfile {
    var cloud: [String]
    var identity: [String]
    var edr: [String]
    var siem: [String]
    var saas: [String]
    var regulatedData: [String]
    var sector: String?
    var employeeBand: String?
    var createdAt: Date
    var updatedAt: Date

    init(
        cloud: [String] = [],
        identity: [String] = [],
        edr: [String] = [],
        siem: [String] = [],
        saas: [String] = [],
        regulatedData: [String] = [],
        sector: String? = nil,
        employeeBand: String? = nil
    ) {
        self.cloud = cloud
        self.identity = identity
        self.edr = edr
        self.siem = siem
        self.saas = saas
        self.regulatedData = regulatedData
        self.sector = sector
        self.employeeBand = employeeBand
        let now = Date()
        self.createdAt = now
        self.updatedAt = now
    }

    func toDTO() -> StackProfileDTO {
        StackProfileDTO(
            cloud: cloud,
            identity: identity,
            edr: edr,
            siem: siem,
            saas: saas,
            regulatedData: regulatedData,
            sector: sector,
            employeeBand: employeeBand
        )
    }
}

@Model
final class StoredFeedback {
    var itemId: String
    var sentiment: String   // "up" | "down"
    var reason: String?
    var createdAt: Date
    var synced: Bool

    init(itemId: String, sentiment: String, reason: String? = nil) {
        self.itemId = itemId
        self.sentiment = sentiment
        self.reason = reason
        self.createdAt = Date()
        self.synced = false
    }
}
