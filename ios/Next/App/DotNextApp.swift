import DotEngine
import SwiftUI
import UIKit

@main
struct DotNextApp: App {
    @State private var model: AppModel = AppModel()
    @Environment(\.scenePhase) private var scenePhase: ScenePhase

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
        // The iPad's floating tab bar does not read the layout appearances above; it reads the item's own colour (§19).
        UITabBarItem.appearance().badgeColor = .dynamic(\.warn)
        UITabBarItem.appearance().setBadgeTextAttributes([.foregroundColor: UIColor.dynamic(\.ground)], for: .normal)
    }

    var body: some Scene {
        WindowGroup {
            RootView(launch: LaunchOptions.read(UserDefaults.standard))
                .environment(model)
                .onOpenURL { url in model.open(url: url) }
                .onAppear {
                    // A fixture scene draws its own snapshot: no engine starts beneath it to replace it (debug only).
                    if !LaunchOptions.read(UserDefaults.standard).drawsFixture { model.load() }
                }
        }
        .onChange(of: scenePhase) { _, phase in model.scene(phase) }
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
    #if DEBUG
        case gallery(GalleryPage)
        /// The sample chat of the gallery's fixtures.
        case sampleChat(ChatPanel?)
        /// The real chat screen drawn from a fixture (wave N2).
        case chatFixture(ChatFixture)
        /// Settings on a fixture daemon (wave N3).
        case settingsFixture(SettingsFixture)
    #endif
    case tab(AppTab)
    /// The live chat list with its first chat open.
    case liveChat

    /// Every scene name the harness knows.
    static var names: [String] {
        #if DEBUG
            GalleryPage.allCases.map(\.rawValue) + AppTab.allCases.map(\.rawValue) + ["chat", "chat-agents", "chat-background", "live-chat"]
                + ChatFixture.allCases.map(\.rawValue) + SettingsFixture.allCases.map(\.rawValue)
        #else
            AppTab.allCases.map(\.rawValue) + ["live-chat"]
        #endif
    }

    /// The scene of a name; an unknown name is no scene (the app opens normally).
    static func named(_ name: String) -> LaunchScene? {
        #if DEBUG
            if let page = GalleryPage(rawValue: name) { return .gallery(page) }
            if let fixture = ChatFixture(rawValue: name) { return .chatFixture(fixture) }
            if let fixture = SettingsFixture(rawValue: name) { return .settingsFixture(fixture) }
            switch name {
            case "chat": return .sampleChat(nil)
            case "chat-agents": return .sampleChat(.agents)
            case "chat-background": return .sampleChat(.background)
            default: break
            }
        #endif
        if let tab = AppTab(rawValue: name) { return .tab(tab) }
        return name == "live-chat" ? .liveChat : nil
    }
}

/// What the launch asks to show. Release builds read no argument.
struct LaunchOptions: Equatable, Sendable {
    let scene: LaunchScene?
    let sceneName: String?

    /// Whether the scene is a chat fixture (wave N2), which draws without a daemon.
    var drawsFixture: Bool {
        #if DEBUG
            if case .chatFixture = scene { return true }
            if case .settingsFixture = scene { return true }
        #endif
        return false
    }

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
    @Environment(AppModel.self) private var model: AppModel

    var body: some View {
        Group {
            switch launch.scene {
            #if DEBUG
                case .gallery(let page): NavigationStack { GalleryPageView(page: page) }
                case .sampleChat(let panel): NavigationStack { SampleChatScreen(chat: Samples.chats[0], initialPanel: panel) }
                case .chatFixture(let fixture):
                    NavigationStack {
                        ChatScreen(thread: ChatFixture.thread, initialPanel: fixture.panel, editsFirstQueued: fixture == .editing)
                    }
                    .onAppear { model.showFixture(fixture.snapshot(now: Date().timeIntervalSince1970), mac: "MacBook Air") }
                case .settingsFixture(let fixture):
                    paired(AppShell(initialTab: .settings, opensFirstChat: false, settings: fixture.start))
                        .onAppear { model.showSettingsFixture(fixture) }
            #endif
            case .tab(let tab): paired(AppShell(initialTab: tab, opensFirstChat: false, settings: SettingsStart(path: [], query: "", editsFirstSchedule: false, triesRaise: false, connectApps: .closed)))
            case .liveChat: paired(AppShell(initialTab: .chats, opensFirstChat: true, settings: SettingsStart(path: [], query: "", editsFirstSchedule: false, triesRaise: false, connectApps: .closed)))
            case nil: paired(AppShell(initialTab: .chats, opensFirstChat: false, settings: SettingsStart(path: [], query: "", editsFirstSchedule: false, triesRaise: false, connectApps: .closed)))
            }
        }
        .tint(Tone.text)
        .appearancePreferences()
        .alert(
            String(localized: "Pair this phone with another Mac?", comment: "Alert: a pair link opened while the phone is paired"),
            isPresented: Binding(get: { model.phase == .ready && model.pairingStartWaiting != nil }, set: { if !$0 { model.pairingStartWaiting = nil } })
        ) {
            Button(String(localized: "Pair again", comment: "Settings: forgets the refused key and opens pairing")) { Task { await model.forget() } }
            Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), role: .cancel) { model.pairingStartWaiting = nil }
        } message: {
            Text(String(localized: "This phone forgets its key for the Mac it is paired with; then one tap pairs it with the Mac of the link.", comment: "Alert: what pairing again does"))
        }
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

extension RootView {
    /// The shell once a key is held; pairing before; a plain word while the key is read.
    @ViewBuilder func paired(_ shell: AppShell) -> some View {
        switch model.phase {
        case .loading: LoadingState(message: String(localized: "Reaching your Mac…", comment: "Chat list while the first list read is under way")).background(Tone.ground)
        case .unpaired: PairingView()
        case .ready: shell
        case .noStorage:
            EmptyState(
                title: String(localized: "This build cannot keep anything", comment: "Shown when the app group of the app is missing"), systemImage: "exclamationmark.triangle",
                detail: String(localized: "It was signed without its app group. Install a build signed for Dot Next.", comment: "Detail when the app group of the app is missing"),
                actionTitle: nil, action: {}
            ).background(Tone.ground)
        }
    }
}

/// The shell: the system's floating tab bar (never minimised), tinted with the ink.
struct AppShell: View {
    @State private var tab: AppTab
    let opensFirstChat: Bool
    let settings: SettingsStart
    @Environment(AppModel.self) private var model: AppModel

    init(initialTab: AppTab, opensFirstChat: Bool, settings: SettingsStart) {
        _tab = State(initialValue: initialTab)
        self.opensFirstChat = opensFirstChat
        self.settings = settings
    }

    var body: some View {
        TabView(selection: $tab) {
            Tab(String(localized: "Chats", comment: "Tab bar: the chats tab"), systemImage: "bubble.left.and.bubble.right", value: AppTab.chats) {
                ChatsTab(opensFirstChat: opensFirstChat)
            }
            .badge(model.list.needsYou)
            Tab(String(localized: "Agents", comment: "Tab bar: the agents and teams tab"), systemImage: "person.2", value: AppTab.agents) { AgentsTab() }
            Tab(String(localized: "Activity", comment: "Tab bar: what Dot did while the owner was away"), systemImage: "clock.arrow.circlepath", value: AppTab.activity) { ActivityTab() }
            Tab(String(localized: "Settings", comment: "Tab bar: the settings tab"), systemImage: "gearshape", value: AppTab.settings) { SettingsTab(start: settings) }
        }
        .tabBarMinimizeBehavior(.never)
    }
}

/// A class in the app's bundle, so code (and the tests) can find the bundle's resources.
final class AppBundleAnchor {}
