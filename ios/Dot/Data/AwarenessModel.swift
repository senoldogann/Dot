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
    /// What Dot did on its own since the owner last looked, for the card at the top of the chat list: `nil` when it did
    /// nothing, when the owner put the card away, or at the first visit (item 1443).
    private(set) var away: AwayView?

    @ObservationIgnored private let api: APIClient
    @ObservationIgnored private let cache: ChatCache
    @ObservationIgnored private let link: LinkMonitor?
    @ObservationIgnored private var held: AwarenessSnapshot
    @ObservationIgnored private var needsYouRead: Task<Void, Never>?
    @ObservationIgnored private var awayReading: Task<Void, Never>?
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
        self.away = kept?.away
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

    // MARK: What Dot did while the owner was away (item 1443)

    /// The card of what Dot did on its own since the owner last looked (on start and on foreground, on a pull to refresh): the
    /// mark of the last look is Dot's, shared by the Mac and every phone, so the card is read against it. The first visit from
    /// any device marks the moment and shows nothing. A read that does not get through leaves the card the phone held.
    func refreshAway() async {
        if let running: Task<Void, Never> = awayReading {
            await running.value
            return
        }
        let task: Task<Void, Never> = Task { await readAway() }
        awayReading = task
        await task.value
        awayReading = nil
    }

    private func readAway() async {
        if let link, !link.state.online { return }
        do {
            // A card put away with no link is told to Dot first, so that this read is against the mark the owner set.
            if let pending: Double = held.awayMarkPending {
                try await api.markAway(until: pending)
                held.awayMarkPending = nil
                keep()
            }
            let mark: AwayMark = try await api.awayMark()
            guard let readAt: Double = mark.readAt else {
                // The first visit from any device: mark the moment, show nothing.
                try await api.markAway(until: Date().timeIntervalSince1970)
                setAway(nil)
                return
            }
            let view: AwayView = try await api.away(since: readAt)
            setAway(AwayRules.card(from: view) == nil ? nil : view)
        } catch {
            if Task.isCancelled { return }
            logger.info("what Dot did while the owner was away could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    private func setAway(_ view: AwayView?) {
        if view != away { away = view }
        held.away = view
        keep()
    }

    /// The owner put the card away: it goes at once, and the moment it covered is marked read on Dot, so that the Mac and every
    /// phone agree. A mark that does not get through is kept and sent by the next read.
    func dismissAway() {
        guard let view: AwayView = away else { return }
        away = nil
        held.away = nil
        held.awayMarkPending = max(view.until, held.awayMarkPending ?? 0)
        keep()
        Task { await sendPendingMark() }
    }

    private func sendPendingMark() async {
        guard let pending: Double = held.awayMarkPending else { return }
        do {
            try await api.markAway(until: pending)
            // A dismissal made while this one was on its way stays.
            if held.awayMarkPending == pending { held.awayMarkPending = nil }
            keep()
        } catch {
            logger.info("the dismissal of the away card was not told to Dot yet: \(String(describing: error), privacy: .public)")
        }
    }
}
