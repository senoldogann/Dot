import Foundation
import Observation
import WebKit
import os

/// The app's state: the daemon's host, whether the phone is paired, and what the web view should open.
@MainActor
@Observable
final class AppModel {
    /// The `DOT_HOST` this build names, or `nil` when the owner types the host.
    let buildHost: String?
    private(set) var host: DaemonHost?
    private(set) var isPaired: Bool = false
    /// A daemon URL a notification asked to open; the web view loads it and clears it.
    var pendingURL: URL?
    var showsSettings: Bool = false
    /// The web UI, full screen over the native app: the fallback ("Open web UI") and the way to pair in the page.
    var showsWeb: Bool = false
    /// The native app's state of the daemon, once the host is saved and the phone is paired.
    private(set) var native: NativeStore?
    /// A run a notification asked to open: the native list opens its chat.
    var pendingRunId: String?
    /// The scene's phase as the activity lease reads it (item 1440), kept so that a store made later (pairing done while the
    /// app is in front) says it at once.
    @ObservationIgnored private(set) var scene: LeaseScene = .background
    /// Debug builds only: `-DotOpenChat <title prefix>` opens that chat at launch, `-DotShowInspector 1` its inspector,
    /// `-DotShowSettings 1` the settings; for looking at a screen in the simulator, which has no way to tap from here.
    var debugOpenChat: String?
    var debugInspector: Bool = false
    var debugSchedules: Bool = false
    /// A page of Settings to open at launch (`models`, `schedules`, `licences`): debug builds only.
    var debugSettingsPage: String?
    /// Debug builds only: `-DotShowInbox 1` opens the inbox of what waits on the owner at launch (item 1442).
    var debugInbox: Bool = false
    /// Debug builds only: `-DotOpenURL dotagent://run/<id>` opens a link at launch, as a Live Activity's tap does.
    var debugOpenURL: URL?

    @ObservationIgnored private let hostStore: HostStore
    @ObservationIgnored private let keyStore: PhoneKeyStore
    @ObservationIgnored let registrar: PushRegistrar
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "app")
    /// A key given on the command line of a debug build, for the simulator, where the Keychain has no entitlement.
    @ObservationIgnored private var devKey: String?

    init(hostStore: HostStore, keyStore: PhoneKeyStore, registrar: PushRegistrar, buildHost: String?) {
        self.hostStore = hostStore
        self.keyStore = keyStore
        self.registrar = registrar
        self.buildHost = buildHost
        self.host = hostStore.load(buildHost: buildHost)
        #if DEBUG
        // `-DotDevOrigin http://127.0.0.1:8765 -DotDevKey phone.x.y`: a daemon on this Mac for the simulator.
        let args: UserDefaults = UserDefaults.standard
        if let origin: String = args.string(forKey: "DotDevOrigin"), let url: URL = URL(string: origin), let name: String = url.host() {
            self.host = DaemonHost(name: "\(name):\(url.port ?? 80)", origin: url)
            // No key: the pairing screen shows.
            self.devKey = args.string(forKey: "DotDevKey").flatMap { $0.isEmpty ? nil : $0 }
        }
        self.debugSchedules = args.bool(forKey: "DotShowSchedules")
        self.debugSettingsPage = args.string(forKey: "DotSettingsPage")
        self.debugInbox = args.bool(forKey: "DotShowInbox")
        self.debugOpenChat = args.string(forKey: "DotOpenChat")
        self.debugInspector = args.bool(forKey: "DotShowInspector")
        self.showsSettings = args.bool(forKey: "DotShowSettings")
        self.debugOpenURL = args.string(forKey: "DotOpenURL").flatMap(URL.init(string:))
        #endif
        self.isPaired = readIsPaired()
        if let host, isPaired, devKey == nil {
            registrar.start(host: host)
        }
        rebuildNative()
    }

    /// Makes the native store for the saved host and key, or none while the phone is not paired.
    private func rebuildNative() {
        native?.stop()
        native = nil
        guard let host, isPaired, let key: String = currentKey() else { return }
        let store: NativeStore = NativeStore(host: host, key: key)
        native = store
        store.start()
        store.sceneChanged(scene)
    }

    /// The scene became active, inactive or went to the background: the daemon is told at once whether the owner is looking.
    func sceneChanged(_ phase: LeaseScene) {
        scene = phase
        native?.sceneChanged(phase)
    }

    private func currentKey() -> String? {
        if let devKey { return devKey }
        do {
            return try keyStore.read()
        } catch {
            logger.error("reading the phone key failed: error=\(error.description, privacy: .public)")
            return nil
        }
    }

    /// Saves a new daemon host. A key belongs to the daemon that issued it, so changing the host forgets the key.
    func setHost(_ newHost: DaemonHost) {
        if let host, host != newHost {
            forgetKey()
        }
        hostStore.save(newHost)
        host = newHost
        rebuildNative()
    }

    /// Keeps the key the page handed over and starts registering for pushes.
    func didPair(key: String) {
        guard let host else {
            return
        }
        do {
            try keyStore.save(key)
        } catch {
            logger.error("saving the phone key failed: error=\(error.description, privacy: .public)")
            return
        }
        isPaired = true
        logger.info("paired with the daemon: host=\(host.name, privacy: .public)")
        registrar.start(host: host)
        showsWeb = false
        rebuildNative()
    }

    /// Removes the phone's key from the Keychain and stops registering.
    func forgetKey() {
        // The daemon is told first, with the key that is about to go: it forgets the token that starts this phone's Live Activity.
        registrar.forgetStartToken()
        registrar.stop()
        let forgotten: NativeStore? = native
        Task { await forgotten?.clearCache() }
        native?.stop()
        native = nil
        do {
            try keyStore.delete()
        } catch {
            logger.error("removing the phone key failed: error=\(error.description, privacy: .public)")
        }
        isPaired = readIsPaired()
        rebuildNative()
        // The page keeps the key in its own storage and hands it over on every load: it is signed out too, or the
        // key would come back at the next load.
        WKWebsiteDataStore.default().removeData(
            ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast
        ) {}
    }

    /// Opens what a `dotagent://` link asks (the Live Activity's rows and its tap): the chat of a run. A link that asks
    /// for nothing this app does is ignored.
    func open(url: URL) {
        guard native != nil, let link: DotDeepLink = DotDeepLink.parse(url) else {
            logger.warning("link refused: scheme=\(url.scheme ?? "", privacy: .public)")
            return
        }
        switch link {
        case .run(let id): pendingRunId = id
        }
    }

    /// Opens `route` (a path from a notification) in the web view when it is a path on the daemon.
    func open(route: String) {
        guard let host, let url: URL = routeURL(route, host: host) else {
            logger.warning("notification route refused: route=\(route, privacy: .public)")
            return
        }
        // A run's page opens the run's chat in the native list; any other route is a page of the web UI.
        if native != nil, let runId: String = runId(inRoute: route) {
            pendingRunId = runId
            return
        }
        pendingURL = url
        if native != nil { showsWeb = true }
    }

    private func readIsPaired() -> Bool {
        if devKey != nil { return true }
        do {
            return try keyStore.read() != nil
        } catch {
            logger.error("reading the phone key failed: error=\(error.description, privacy: .public)")
            return false
        }
    }
}
