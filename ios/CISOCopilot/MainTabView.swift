import SwiftUI

struct MainTabView: View {
    let profile: StoredProfile

    var body: some View {
        TabView {
            BriefView()
                .tabItem { Label("Brief", systemImage: "list.bullet.rectangle") }

            HistoryView()
                .tabItem { Label("History", systemImage: "clock.arrow.circlepath") }

            ProfileView(profile: profile)
                .tabItem { Label("Profile", systemImage: "person.crop.circle") }
        }
    }
}
