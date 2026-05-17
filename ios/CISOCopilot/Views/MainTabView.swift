import SwiftUI

/// Four-tab root for approved tenants. Replaces the Phase 0 placeholder
/// WelcomeView. Each tab is a NavigationStack so detail pushes don't
/// scribble on the tab bar.
struct MainTabView: View {
    var body: some View {
        TabView {
            NavigationStack { OverviewView() }
                .tabItem { Label("Brief", systemImage: "house.fill") }

            NavigationStack { TopRisksView() }
                .tabItem { Label("Risks", systemImage: "exclamationmark.triangle.fill") }

            NavigationStack { ConnectCloudsView() }
                .tabItem { Label("Connect", systemImage: "plus.circle.fill") }

            NavigationStack { ProfileView() }
                .tabItem { Label("Profile", systemImage: "person.crop.circle.fill") }
        }
    }
}
