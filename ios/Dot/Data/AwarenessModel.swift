import Foundation
import Observation
import UserNotifications
import os

/// What the app knows of Dot's awareness of the owner, as the screens read it (items 1441 to 1443): the push switches, and
/// later what waits on the owner and what Dot did while they were away. Each is drawn at once from what the phone kept
/// (`awareness.json` in the cache folder) and brought up to date by the daemon; one per store.
@MainActor
@Observable
final class AwarenessModel {
    /// The Notifications switches: what they show, what is on its way, what to say when a flip was not kept.
    private(set) var push: PushSwitches
    /// Everything that waits on the owner, as last read (from disk before the network has answered), and whether the last read
    /// reached Dot: until one does, what is shown is what the phone last held (item 1442).
    private(set) var needsYou: NeedsYouList
    private(set) var needsYouIsLive: Bool = false

    @ObservationIgnored private let api: APIClient
    @ObservationIgnored private let cache: ChatCache
    @ObservationIgnored private let link: LinkMonitor?
    @ObservationIgnored private var held: AwarenessSnapshot
    @ObservationIgnored private var needsYouRead: Task<Void, Never>?
    /// Puts a number on the app's icon: after each read of what waits on the owner it is that count.
    @ObservationIgnored private let setBadge: @Sendable (Int) async -> Void
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "awareness")

    init(
        api: APIClient, cache: ChatCache, directory: URL, link: LinkMonitor? = nil,
        badge: @escaping @Sendable (Int) async -> Void = AwarenessModel.systemBadge
    ) {
        self.api = api
        self.cache = cache
        self.link = link
        self.setBadge = badge
        let kept: AwarenessSnapshot? = ChatCache.readAwareness(in: directory)
        self.held = kept ?? AwarenessSnapshot(savedAt: 0)
        self.push = PushSwitches(known: kept?.pushSettings)
        self.needsYou = kept?.needsYou ?? .empty
    }

    /// The count on the app's icon. The daemon sets it on every alert it sends (`aps.badge`); an answer given on the Mac sends
    /// no alert that would lower it, so the app sets it again from every read of what waits.
    static let systemBadge: @Sendable (Int) async -> Void = { count in
        do {
            try await UNUserNotificationCenter.current().setBadgeCount(max(0, count))
        } catch {
            // Badges not allowed in the iPhone's settings: nothing to show, nothing to say.
            Logger(subsystem: "dev.dotagent.phone", category: "awareness").info(
                "the badge was not set: \(String(describing: error), privacy: .public)")
        }
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

    // MARK: What waits on the owner (item 1442)

    /// Reads what waits on the owner (on foreground, on a pull to refresh, when a push arrives): a second call while one reads
    /// waits for that one. A read that does not get through leaves what the phone held, marked as not read now.
    func refreshNeedsYou() async {
        if let running: Task<Void, Never> = needsYouRead {
            await running.value
            return
        }
        let task: Task<Void, Never> = Task { await readNeedsYou() }
        needsYouRead = task
        await task.value
        needsYouRead = nil
    }

    private func readNeedsYou() async {
        if let link, !link.state.online {
            needsYouIsLive = false
            return
        }
        do {
            let list: NeedsYouList = try await api.needsYou()
            if list != needsYou { needsYou = list }
            needsYouIsLive = true
            held.needsYou = list
            keep()
            await setBadge(list.count)
        } catch {
            if Task.isCancelled { return }
            needsYouIsLive = false
            logger.info("what waits on the owner could not be read: \(String(describing: error), privacy: .public)")
        }
    }
}
