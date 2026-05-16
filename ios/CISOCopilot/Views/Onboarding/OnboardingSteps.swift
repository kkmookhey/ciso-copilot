import SwiftUI

struct ChipPicker: View {
    let title: String
    let subtitle: String
    let options: [String]
    @Binding var selection: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.title2).bold()
            Text(subtitle).foregroundStyle(.secondary)

            FlowLayout(spacing: 8) {
                ForEach(options, id: \.self) { opt in
                    Chip(label: opt, isSelected: selection.contains(opt)) {
                        if let idx = selection.firstIndex(of: opt) {
                            selection.remove(at: idx)
                        } else {
                            selection.append(opt)
                        }
                    }
                }
            }
            Spacer()
        }
        .padding()
    }
}

struct Chip: View {
    let label: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(isSelected ? Color.accentColor : Color(.secondarySystemBackground))
                .foregroundStyle(isSelected ? Color.white : Color.primary)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var lineHeight: CGFloat = 0
        for sv in subviews {
            let size = sv.sizeThatFits(.unspecified)
            if x + size.width > maxWidth {
                x = 0
                y += lineHeight + spacing
                lineHeight = 0
            }
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
        return CGSize(width: maxWidth, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let maxWidth = bounds.width
        var x: CGFloat = bounds.minX
        var y: CGFloat = bounds.minY
        var lineHeight: CGFloat = 0
        for sv in subviews {
            let size = sv.sizeThatFits(.unspecified)
            if x + size.width > bounds.minX + maxWidth {
                x = bounds.minX
                y += lineHeight + spacing
                lineHeight = 0
            }
            sv.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }
}

// MARK: - Steps

struct CloudStep: View {
    @Binding var draft: OnboardingDraft
    var body: some View {
        ChipPicker(
            title: "Cloud platforms",
            subtitle: "Where do you run production workloads?",
            options: ["AWS", "Azure", "GCP", "Oracle Cloud"],
            selection: Binding(get: { draft.cloud }, set: { draft.cloud = $0 })
        )
    }
}

struct IdentityStep: View {
    @Binding var draft: OnboardingDraft
    var body: some View {
        ChipPicker(
            title: "Identity provider",
            subtitle: "Who does SSO / MFA / directory?",
            options: ["Okta", "Microsoft Entra", "Azure AD", "Ping Identity", "Duo", "OneLogin"],
            selection: Binding(get: { draft.identity }, set: { draft.identity = $0 })
        )
    }
}

struct EDRStep: View {
    @Binding var draft: OnboardingDraft
    var body: some View {
        ChipPicker(
            title: "Endpoint protection",
            subtitle: "What's on the endpoints?",
            options: ["CrowdStrike", "SentinelOne", "Microsoft Defender", "Carbon Black"],
            selection: Binding(get: { draft.edr }, set: { draft.edr = $0 })
        )
    }
}

struct SIEMStep: View {
    @Binding var draft: OnboardingDraft
    var body: some View {
        ChipPicker(
            title: "SIEM / logging",
            subtitle: "Where do logs land?",
            options: ["Splunk", "Datadog", "Elastic", "Sumo Logic", "Microsoft Sentinel"],
            selection: Binding(get: { draft.siem }, set: { draft.siem = $0 })
        )
    }
}

struct SaaSStep: View {
    @Binding var draft: OnboardingDraft
    var body: some View {
        ChipPicker(
            title: "Key SaaS",
            subtitle: "Top SaaS that hold sensitive data or run critical workflows.",
            options: ["Microsoft 365", "Google Workspace", "Salesforce", "Slack", "Atlassian", "GitHub", "GitLab", "Zoom", "Workday"],
            selection: Binding(get: { draft.saas }, set: { draft.saas = $0 })
        )
    }
}

struct FinalStep: View {
    @Binding var draft: OnboardingDraft
    @Binding var saving: Bool
    @Binding var error: String?
    let onSubmit: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "checkmark.shield.fill")
                .font(.system(size: 60))
                .foregroundStyle(.tint)
            Text("Ready to generate your first brief.")
                .font(.title2).bold()
                .multilineTextAlignment(.center)
            Text("We'll cross-reference your stack against public threat data and surface what matters.")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            if let error = error {
                Text(error).font(.caption).foregroundStyle(.red).padding()
            }

            Button(action: onSubmit) {
                if saving {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 8)
                } else {
                    Text("Generate my first brief")
                        .padding(.horizontal, 20)
                        .padding(.vertical, 8)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(saving)

            Spacer()
        }
        .padding()
    }
}
