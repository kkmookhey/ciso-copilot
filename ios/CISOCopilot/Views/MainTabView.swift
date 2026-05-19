import SwiftUI

/// Shared app state across the tab view — primarily so any descendant view
/// can switch tabs (e.g., tapping a stat card on Overview jumps to Connect).
@Observable
final class AppState {
    enum Tab: Hashable {
        case brief, risks, register, ai, connect, profile
    }
    var selectedTab: Tab = .brief
}

/// Five-tab root for approved tenants. Each tab is a NavigationStack so
/// detail pushes don't scribble on the tab bar.
struct MainTabView: View {
    @State private var appState = AppState()

    var body: some View {
        TabView(selection: $appState.selectedTab) {
            NavigationStack { OverviewView() }
                .tabItem { Label("Brief", systemImage: "house.fill") }
                .tag(AppState.Tab.brief)

            NavigationStack { TopRisksView() }
                .tabItem { Label("Risks", systemImage: "exclamationmark.triangle.fill") }
                .tag(AppState.Tab.risks)

            NavigationStack { RisksRegisterView() }
                .tabItem { Label("Register", systemImage: "list.bullet.clipboard.fill") }
                .tag(AppState.Tab.register)

            NavigationStack { AIInventoryView() }
                .tabItem { Label("AI", systemImage: "brain.head.profile") }
                .tag(AppState.Tab.ai)

            NavigationStack { ConnectCloudsView() }
                .tabItem { Label("Connect", systemImage: "plus.circle.fill") }
                .tag(AppState.Tab.connect)

            NavigationStack { ProfileView() }
                .tabItem { Label("Profile", systemImage: "person.crop.circle.fill") }
                .tag(AppState.Tab.profile)
        }
        .environment(appState)
    }
}
