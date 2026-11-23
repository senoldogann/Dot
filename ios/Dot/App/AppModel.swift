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
    /// Debug builds only: `-DotOpenChat <title prefix>` opens that chat at launch, `-DotShowInspector 1` its inspector,
    /// `-DotShowSettings 1` the settings; for looking at a screen in the simulator, which has no way to tap from here.
    var debugOpenChat: String?
    var debugInspector: Bool = false
    var debugSchedules: Bool = false

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
        self.debugOpenChat = args.string(forKey: "DotOpenChat")
        self.debugInspector = args.bool(forKey: "DotShowInspector")
        self.showsSettings = args.bool(forKey: "DotShowSettings")
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

/// The run id of a route like `#/runs/run_abc` or `/runs/run_abc`, or `nil` for any other route.
func runId(inRoute route: String) -> String? {
    let trimmed: String = route.trimmingCharacters(in: CharacterSet(charactersIn: "#/"))
    let parts: [Substring] = trimmed.split(separator: "/")
    guard parts.count == 2, parts[0] == "runs", parts[1].allSatisfy({ $0.isLetter || $0.isNumber || $0 == "_" || $0 == "-" }) else {
        return nil
    }
    return String(parts[1])
}
