import Foundation
import SwiftUI

extension Notification.Name {
    static let navigateToBriefing = Notification.Name("navigateToBriefing")
}

/// Holds the currently-active incident so any view can react to push-tap navigation.
class IncidentRouter: ObservableObject {
    @Published var activeIncident: IncidentContext?

    init() {
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleNavigate(_:)),
            name: .navigateToBriefing,
            object: nil
        )
    }

    @objc private func handleNavigate(_ note: Notification) {
        guard let findingId = note.object as? String else { return }
        let context = note.userInfo as? [String: Any] ?? [:]
        DispatchQueue.main.async {
            self.activeIncident = IncidentContext(findingId: findingId, payload: context)
        }
    }

    func clear() { activeIncident = nil }
}

struct IncidentContext: Equatable {
    let findingId: String
    let payload: [String: Any]

    static func == (lhs: IncidentContext, rhs: IncidentContext) -> Bool {
        lhs.findingId == rhs.findingId
    }
}
