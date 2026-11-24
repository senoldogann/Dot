import Foundation
import Observation
import os

/// What the app knows of Dot's awareness of the owner, as the screens read it (items 1441 to 1443): the push switches, and
/// later what waits on the owner and what Dot did while they were away. Each is drawn at once from what the phone kept
/// (`awareness.json` in the cache folder) and brought up to date by the daemon; one per store.
@MainActor
@Observable
final class AwarenessModel {
    /// The Notifications switches: what they show, what is on its way, what to say when a flip was not kept.
    private(set) var push: PushSwitches

    @ObservationIgnored private let api: APIClient
    @ObservationIgnored private let cache: ChatCache
    @ObservationIgnored private let link: LinkMonitor?
    @ObservationIgnored private var held: AwarenessSnapshot
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "awareness")

    init(api: APIClient, cache: ChatCache, directory: URL, link: LinkMonitor? = nil) {
        self.api = api
        self.cache = cache
        self.link = link
        let kept: AwarenessSnapshot? = ChatCache.readAwareness(in: directory)
        self.held = kept ?? AwarenessSnapshot(savedAt: 0)
        self.push = PushSwitches(known: kept?.pushSettings)
    }

    /// Writes what is held to the cache folder, off the main actor.
    private func keep() {
        held.savedAt = Date().timeIntervalSince1970
        let snapshot: AwarenessSnapshot = held
        let cache: ChatCache = cache
        Task.detached(priority: .utility) { await cache.saveAwareness(snapshot) }
    }

    // MARK: Push switches (item 1441)

    /// Reads the switches from the daemon (when Settings opens, and when the link is back). A read that does not get through
    /// leaves the last switches the phone knew, dimmed.
    func loadPush() async {
        if let link, !link.state.online {
            push.loadFailed()
            return
        }
        do {
            let settings: PushSettings = try await api.pushSettings()
            push.loaded(settings)
            held.pushSettings = settings
            keep()
        } catch {
            if Task.isCancelled { return }
            push.loadFailed()
            logger.info("the push switches could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    /// The owner flipped a switch: it shows at once and all three are written; a write the daemon does not take puts the
    /// switches back and says so in a plain line.
    func flip(_ kind: PushKind, to on: Bool) {
        if let link, !link.state.online {
            // No link: a flip that cannot be kept is no flip. The switches dim and say why.
            push.loadFailed()
            return
        }
        guard let body: PushSettings = push.flip(kind, to: on) else { return }
        Task { await write(body) }
    }

    private func write(_ first: PushSettings) async {
        var next: PushSettings? = first
        while let body: PushSettings = next {
            do {
                let kept: PushSettings = try await api.setPushSettings(body)
                next = push.saved(kept)
                held.pushSettings = kept
                keep()
            } catch {
                var status: Int?
                if case DaemonRequestError.status(_, let code, _) = error { status = code }
                push.failed(PushSwitches.words(forStatus: status))
                logger.error("the push switches were not kept: \(String(describing: error), privacy: .public)")
                next = nil
            }
        }
    }
}
