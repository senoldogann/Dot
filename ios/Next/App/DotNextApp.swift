import SwiftUI
import UIKit

@main
struct DotNextApp: App {
    init() {
        // The Chats badge counts what waits on the owner: amber, never the system's red (red means failure only).
        let appearance: UITabBarAppearance = UITabBarAppearance()
        appearance.configureWithDefaultBackground()
        for item in [appearance.stackedLayoutAppearance, appearance.inlineLayoutAppearance, appearance.compactInlineLayoutAppearance] {
            item.normal.badgeBackgroundColor = .dynamic(\.warn)
            item.normal.badgeTextAttributes = [.foregroundColor: UIColor.dynamic(\.ground)]
        }
        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some Scene {
        WindowGroup {
            RootView(launch: LaunchOptions.read(UserDefaults.standard))
        }
    }
}

/// The four tabs (plan §2, review S1): Chats = answerable and active, Agents = agents and delegation, Activity = what
/// happened while you were away, Settings = configuration.
enum AppTab: String, CaseIterable, Hashable, Sendable {
    case chats
    case agents
    case activity
    case settings
}

/// A scene the screenshot harness can open at launch with `-DotNextScene <name>` (debug builds only; see
/// `scripts/shots.sh`): a gallery page, a tab, or the sample chat.
enum LaunchScene: Equatable, Sendable {
    case gallery(GalleryPage)
    case tab(AppTab)
    case chat(ChatPanel?)

    /// Every scene name the harness knows.
    static let names: [String] =
        GalleryPage.allCases.map(\.rawValue) + AppTab.allCases.map(\.rawValue) + ["chat", "chat-agents", "chat-background"]

    /// The scene of a name; an unknown name is no scene (the app opens normally).
    static func named(_ name: String) -> LaunchScene? {
        if let page = GalleryPage(rawValue: name) { return .gallery(page) }
        if let tab = AppTab(rawValue: name) { return .tab(tab) }
        switch name {
        case "chat": return .chat(nil)
        case "chat-agents": return .chat(.agents)
        case "chat-background": return .chat(.background)
        default: return nil
        }
    }
}

/// What the launch asks to show. Release builds read no argument.
struct LaunchOptions: Equatable, Sendable {
    let scene: LaunchScene?
    let sceneName: String?

    static func read(_ defaults: UserDefaults) -> LaunchOptions {
        #if DEBUG
            let name: String? = defaults.string(forKey: "DotNextScene")
            return LaunchOptions(scene: name.flatMap(LaunchScene.named), sceneName: name)
        #else
            return LaunchOptions(scene: nil, sceneName: nil)
        #endif
    }
}

struct RootView: View {
    let launch: LaunchOptions

    var body: some View {
        Group {
            switch launch.scene {
            case .gallery(let page): NavigationStack { GalleryPageView(page: page) }
            case .tab(let tab): AppShell(initialTab: tab, opensSampleChat: false, initialPanel: nil)
            case .chat(let panel): AppShell(initialTab: .chats, opensSampleChat: true, initialPanel: panel)
            case nil: AppShell(initialTab: .chats, opensSampleChat: false, initialPanel: nil)
            }
        }
        .tint(Tone.text)
        .task {
            #if DEBUG
                if let name = launch.sceneName { await SceneReady.signal(name) }
            #endif
        }
    }
}

#if DEBUG
    /// Tells the screenshot harness the scene is drawn: after the first frame and a settle for glass and fonts, the
    /// scene's name is written to `Library/Caches/dotnext-ready.txt` in the app's container, which the script polls.
    enum SceneReady {
        static let settle: Duration = .milliseconds(700)

        static func signal(_ name: String) async {
            do {
                try await Task.sleep(for: settle)
            } catch is CancellationError {
                return
            } catch {
                preconditionFailure("Task.sleep throws only CancellationError: \(error)")
            }
            let caches: URL = URL.cachesDirectory.appendingPathComponent("dotnext-ready.txt")
            do {
                try Data(name.utf8).write(to: caches, options: .atomic)
            } catch {
                preconditionFailure("could not write the ready mark at \(caches.path): \(error)")
            }
        }
    }
#endif

/// The shell: the system's floating tab bar (never minimised), tinted with the ink.
struct AppShell: View {
    @State private var tab: AppTab
    let opensSampleChat: Bool
    let initialPanel: ChatPanel?

    init(initialTab: AppTab, opensSampleChat: Bool, initialPanel: ChatPanel?) {
        _tab = State(initialValue: initialTab)
        self.opensSampleChat = opensSampleChat
        self.initialPanel = initialPanel
    }

    var body: some View {
        TabView(selection: $tab) {
            Tab(String(localized: "Chats", comment: "Tab bar: the chats tab"), systemImage: "bubble.left.and.bubble.right", value: AppTab.chats) {
                ChatsTab(opensSampleChat: opensSampleChat, initialPanel: initialPanel)
            }
            .badge(Samples.chats.filter { $0.status == .waitingOnYou || $0.status == .question }.count)
            Tab(String(localized: "Agents", comment: "Tab bar: the agents and teams tab"), systemImage: "person.2", value: AppTab.agents) { AgentsTab() }
            Tab(String(localized: "Activity", comment: "Tab bar: what Dot did while the owner was away"), systemImage: "clock.arrow.circlepath", value: AppTab.activity) { ActivityTab() }
            Tab(String(localized: "Settings", comment: "Tab bar: the settings tab"), systemImage: "gearshape", value: AppTab.settings) { SettingsTab() }
        }
        .tabBarMinimizeBehavior(.never)
    }
}

/// A class in the app's bundle, so code (and the tests) can find the bundle's resources.
final class AppBundleAnchor {}
