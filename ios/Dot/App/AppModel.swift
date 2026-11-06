import Foundation
import Observation
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

    @ObservationIgnored private let hostStore: HostStore
    @ObservationIgnored private let keyStore: PhoneKeyStore
    @ObservationIgnored let registrar: PushRegistrar
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "app")

    init(hostStore: HostStore, keyStore: PhoneKeyStore, registrar: PushRegistrar, buildHost: String?) {
        self.hostStore = hostStore
        self.keyStore = keyStore
        self.registrar = registrar
        self.buildHost = buildHost
        self.host = hostStore.load(buildHost: buildHost)
        self.isPaired = readIsPaired()
        if let host, isPaired {
            registrar.start(host: host)
        }
    }

    /// Saves a new daemon host. A key belongs to the daemon that issued it, so changing the host forgets the key.
    func setHost(_ newHost: DaemonHost) {
        if let host, host != newHost {
            forgetKey()
        }
        hostStore.save(newHost)
        host = newHost
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
    }

    /// Removes the phone's key from the Keychain and stops registering.
    func forgetKey() {
        registrar.stop()
        do {
            try keyStore.delete()
        } catch {
            logger.error("removing the phone key failed: error=\(error.description, privacy: .public)")
        }
        isPaired = readIsPaired()
    }

    /// Opens `route` (a path from a notification) in the web view when it is a path on the daemon.
    func open(route: String) {
        guard let host, let url: URL = routeURL(route, host: host) else {
            logger.warning("notification route refused: route=\(route, privacy: .public)")
            return
        }
        pendingURL = url
    }

    private func readIsPaired() -> Bool {
        do {
            return try keyStore.read() != nil
        } catch {
            logger.error("reading the phone key failed: error=\(error.description, privacy: .public)")
            return false
        }
    }
}
