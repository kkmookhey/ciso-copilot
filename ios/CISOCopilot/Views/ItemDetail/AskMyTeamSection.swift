import SwiftUI

struct AskMyTeamSection: View {
    let questions: TeamQuestionsDTO
    @State private var expanded: Team?

    enum Team: String, CaseIterable, Identifiable {
        case infrastructure, soc, vulnMgmt
        var id: String { rawValue }

        var label: String {
            switch self {
            case .infrastructure: return "Infrastructure"
            case .soc:            return "SOC"
            case .vulnMgmt:       return "Vuln Mgmt"
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ask my team").font(.headline)
            HStack {
                ForEach(Team.allCases) { team in
                    Button {
                        expanded = (expanded == team) ? nil : team
                    } label: {
                        Text(team.label)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background((expanded == team) ? Color.accentColor : Color(.secondarySystemBackground))
                            .foregroundStyle((expanded == team) ? .white : .primary)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            if let team = expanded {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(questionsFor(team).enumerated()), id: \.offset) { (idx, q) in
                        HStack(alignment: .top) {
                            Text("\(idx + 1).").foregroundStyle(.secondary)
                            Text(q)
                            Spacer()
                            ShareButton(text: q)
                        }
                    }
                }
                .padding(.top, 4)
            }
        }
    }

    private func questionsFor(_ team: Team) -> [String] {
        switch team {
        case .infrastructure: return questions.infrastructure
        case .soc:            return questions.soc
        case .vulnMgmt:       return questions.vuln_mgmt
        }
    }
}
