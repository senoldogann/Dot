import Foundation
import os

/// The owner's doings that the daemon has not yet heard, sent when the link allows, in the order they were made, each
/// exactly once. Moved from the old app (`ios/Dot/Data/Outbox.swift`, item 1371) with its rules:
///
/// - It lives on disk (`outbox.json` under the engine's `v2/` folder, written atomically at every change, the photos of a
///   message beside it), so a message typed in a lift and an app killed before the link came back lose nothing.
/// - A message goes with the key it was made with, under the kind of its first attempt (plan §11), so a send whose
///   answer was lost is still the daemon's one message (`send_once` keeps a key a day; older ones wait for the owner).
/// - 409 "being sent already" (the first sending is still under way on the Mac) is waited out; any other 409
///   (`RunConflict`) is a refusal, told apart by the body.
/// - Photos are uploaded once: each id the daemon gives is written into the item before the next step.
/// - The other doings say the same twice (no key): a stop, a removal, an order, an answer; "already decided" is an
///   outcome, not a failure.
/// - A refused message stays as "not sent" with the reason, for the app to put its words back in the composer.
public actor Outbox {
    private let directory: URL
    private let file: URL
    private let imageFolder: URL
    private let api: DaemonAPI
    private let link: LinkWatcher
    private let clock: any EngineClock
    private let policy: RetryPolicy
    private let jitter: @Sendable () -> Double
    /// Whether a new chat may be sent now: only while the list is live (R-b §8 #8), so it never lands on a stale list.
    private let mayStartChat: @Sendable () async -> Bool
    private let waker: Waker = Waker()
    private var items: [OutboxItem]
    private var draining: Task<Void, Never>?
    private var suspended: Bool = false
    private var changeWatchers: [UUID: AsyncStream<[OutboxItem]>.Continuation] = [:]
    private var outcomeWatchers: [UUID: AsyncStream<OutboxOutcome>.Continuation] = [:]
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "outbox")

    /// What the disk holds is read here; an item that was being sent when the app ended is waiting again.
    public init(
        directory: URL, api: DaemonAPI, link: LinkWatcher, clock: any EngineClock, policy: RetryPolicy,
        jitter: @escaping @Sendable () -> Double, mayStartChat: @escaping @Sendable () async -> Bool
    ) {
        self.directory = directory
        self.file = directory.appending(path: "outbox.json")
        self.imageFolder = directory.appending(path: "outbox-images", directoryHint: .isDirectory)
        self.api = api
        self.link = link
        self.clock = clock
        self.policy = policy
        self.jitter = jitter
        self.mayStartChat = mayStartChat
        var loaded: [OutboxItem] = Self.read(file)
        for at in loaded.indices where loaded[at].phase == .sending { loaded[at].phase = .waiting }
        self.items = loaded
    }

    /// What the disk holds, for the first frame. A file that cannot be read is kept aside, not dropped.
    public nonisolated static func read(_ file: URL) -> [OutboxItem] {
        guard let data: Data = try? Data(contentsOf: file) else { return [] }
        guard let stored: OutboxFile = try? JSONDecoder().decode(OutboxFile.self, from: data), stored.schema == OutboxFile.schema else {
            let aside: URL = file.deletingLastPathComponent().appending(path: "outbox.unreadable.json")
            try? FileManager.default.removeItem(at: aside)
            try? FileManager.default.moveItem(at: file, to: aside)
            return []
        }
        return stored.items
    }

    // MARK: Watching

    public var current: [OutboxItem] { items }

    public func changes() -> AsyncStream<[OutboxItem]> {
        let (stream, continuation): (AsyncStream<[OutboxItem]>, AsyncStream<[OutboxItem]>.Continuation) = AsyncStream.makeStream(
            of: [OutboxItem].self, bufferingPolicy: .bufferingNewest(1))
        let id: UUID = UUID()
        changeWatchers[id] = continuation
        continuation.yield(items)
        continuation.onTermination = { [weak self] _ in Task { await self?.forgetChanges(id) } }
        return stream
    }

    public func outcomes() -> AsyncStream<OutboxOutcome> {
        let (stream, continuation): (AsyncStream<OutboxOutcome>, AsyncStream<OutboxOutcome>.Continuation) = AsyncStream.makeStream(
            of: OutboxOutcome.self, bufferingPolicy: .unbounded)
        let id: UUID = UUID()
        outcomeWatchers[id] = continuation
        continuation.onTermination = { [weak self] _ in Task { await self?.forgetOutcomes(id) } }
        return stream
    }

    private func forgetChanges(_ id: UUID) { changeWatchers[id] = nil }
    private func forgetOutcomes(_ id: UUID) { outcomeWatchers[id] = nil }

    // MARK: Making and unmaking

    /// Keeps a doing under `id` and starts sending it. `images` are the photos of a message: written beside the file
    /// first, so that the item never names a photo the disk does not hold.
    public func enqueue(_ action: OutboxItem.Action, id: String, images: [Data]) throws {
        var names: [String] = []
        if !images.isEmpty {
            try FileManager.default.createDirectory(at: imageFolder, withIntermediateDirectories: true)
            for (number, data) in images.enumerated() {
                let name: String = "\(id)-\(number).jpg"
                try data.write(to: imageFolder.appending(path: name), options: [.atomic])
                names.append(name)
            }
        }
        let kept: OutboxItem.Action =
            switch action {
            case .message(let thread, let text, _, _): names.isEmpty ? action : .message(thread: thread, text: text, images: names, uploaded: [])
            case .steer(let run, let thread, let text, _, _):
                names.isEmpty ? action : .steer(run: run, thread: thread, text: text, images: names, uploaded: [])
            case .newChat(let text, _, _): names.isEmpty ? action : .newChat(text: text, images: names, uploaded: [])
            default: action
            }
        items.append(OutboxItem(id: id, action: kept, createdAt: clock.now()))
        save()
        start()
    }

    /// Takes an item back (Edit on a message that was not sent): the item and its photos, for the composer.
    public func discard(_ id: String) -> (item: OutboxItem, photos: [Data])? {
        guard let at: Int = items.firstIndex(where: { $0.id == id }) else { return nil }
        let removed: OutboxItem = items.remove(at: at)
        let photos: [Data] = removed.images.compactMap { try? Data(contentsOf: imageFolder.appending(path: $0)) }
        removePhotos(of: removed)
        save()
        return (removed, photos)
    }

    /// Tries now: the wait is cut short, and a doing that failed for good is tried again (a message too old to be sent as
    /// it was is sent as a new one with a new key: the owner chose it). The kind stays the item's own.
    public func retry(_ id: String) {
        guard let at: Int = items.firstIndex(where: { $0.id == id }) else { return }
        if items[at].phase == .failed {
            if items[at].isMessage, clock.now() - items[at].createdAt > OutboxItem.keyKept {
                items[at] = renewed(items[at], action: items[at].action)
            } else {
                items[at].phase = .waiting
                items[at].failure = nil
            }
            save()
        }
        start()
        Task { await waker.wake() }
    }

    /// A steer refused because its run ended, sent as a new message of the chat (a NEW item with a new key of kind
    /// `queue`, made only once the owner saw the refusal, plan §11). `nil` when the item is not such a steer.
    @discardableResult
    public func sendAsMessage(_ id: String) -> String? {
        guard let at: Int = items.firstIndex(where: { $0.id == id }), items[at].phase == .failed,
            case .steer(_, let thread, let text, let images, _) = items[at].action
        else { return nil }
        let old: OutboxItem = items.remove(at: at)
        let fresh: OutboxItem = renewed(old, action: .message(thread: thread, text: text, images: images, uploaded: []))
        items.insert(fresh, at: at)
        save()
        start()
        return fresh.id
    }

    /// The item made again under a new key, its photos renamed to it and to be uploaded again.
    private func renewed(_ old: OutboxItem, action: OutboxItem.Action) -> OutboxItem {
        let key: String = newMessageKey()
        var renamed: [String] = []
        for name in old.images {
            let target: String = name.replacingOccurrences(of: old.id, with: key)
            try? FileManager.default.moveItem(at: imageFolder.appending(path: name), to: imageFolder.appending(path: target))
            renamed.append(target)
        }
        let next: OutboxItem.Action =
            switch action {
            case .message(let thread, let text, _, _): .message(thread: thread, text: text, images: renamed, uploaded: [])
            case .steer(let run, let thread, let text, _, _): .steer(run: run, thread: thread, text: text, images: renamed, uploaded: [])
            case .newChat(let text, _, _): .newChat(text: text, images: renamed, uploaded: [])
            default: action
            }
        return OutboxItem(id: key, action: next, createdAt: clock.now())
    }

    /// Starts sending, or goes on: the app is in front, the link is back.
    public func start() {
        suspended = false
        guard draining == nil else {
            Task { await waker.wake() }
            return
        }
        draining = Task { [weak self] in await self?.drain() }
    }

    /// Wakes a wait for the link or the backoff (the supervisor: the link came back, the list went live).
    public func nudge() { Task { await waker.wake() } }

    /// Stops sending while the app sleeps; what waits stays on disk and goes at the next `start`.
    public func suspend() {
        suspended = true
        Task { await waker.wake() }
    }

    public var isBusy: Bool { draining != nil }

    // MARK: Sending

    private func drain() async {
        defer { draining = nil }
        while !suspended, !Task.isCancelled {
            guard let at: Int = items.firstIndex(where: { $0.phase != .failed }) else { return }
            if items[at].isMessage, clock.now() - items[at].createdAt > OutboxItem.keyKept, items[at].attempts > 0 {
                items[at].phase = .failed
                items[at].failure = .tooOld
                save()
                continue
            }
            let seen: Int = await waker.current
            if !link.state.online {
                items[at].phase = .waiting
                save()
                await waker.wait(seconds: 30, clock: clock, seen: seen)
                continue
            }
            if case .newChat = items[at].action, !(await mayStartChat()) {
                await waker.wait(seconds: 5, clock: clock, seen: seen)
                continue
            }
            guard let now: Int = items.firstIndex(where: { $0.id == items[at].id }) else { continue }
            items[now].phase = .sending
            items[now].attempts += 1
            save()
            let item: OutboxItem = items[now]
            do {
                let outcome: OutboxOutcome = try await perform(item)
                finish(item.id, outcome)
            } catch {
                let failure: DaemonRequestError = error
                if failure.isRetryable || isBeingSent(failure) {
                    if let again: Int = items.firstIndex(where: { $0.id == item.id }) {
                        items[again].phase = .waiting
                        save()
                    }
                    logger.warning("outbox retry id=\(item.id, privacy: .public) attempt=\(item.attempts) error=\(failure.description, privacy: .public)")
                    let pause: Duration = policy.wait(afterFailure: item.attempts, jitter: jitter())
                    let wait: Double = Double(pause.components.seconds) + Double(pause.components.attoseconds) / 1e18
                    await waker.wait(seconds: wait, clock: clock, seen: seen)
                } else {
                    refuse(item, failure: classify(failure, item: item))
                }
            }
        }
    }

    /// 409 "the message is being sent already": the same key's first sending is under way on the Mac; wait, ask again.
    private func isBeingSent(_ failure: DaemonRequestError) -> Bool {
        guard case .status(_, 409, let body) = failure else { return false }
        return body.contains("being sent already")
    }

    private func classify(_ failure: DaemonRequestError, item: OutboxItem) -> OutboxItem.Failure {
        if case .undecodable = failure { return .unreadable }
        let code: Int = failure.statusCode ?? 0
        if case .steer = item.action, code == 409 || code == 404 { return .runEnded(detail: failure.detail) }
        return .refused(code: code, detail: failure.detail)
    }

    private func finish(_ id: String, _ outcome: OutboxOutcome) {
        guard let at: Int = items.firstIndex(where: { $0.id == id }) else { return }
        let done: OutboxItem = items.remove(at: at)
        removePhotos(of: done)
        save()
        for watcher in outcomeWatchers.values { watcher.yield(outcome) }
    }

    private func refuse(_ item: OutboxItem, failure: OutboxItem.Failure) {
        guard let at: Int = items.firstIndex(where: { $0.id == item.id }) else { return }
        logger.error("outbox refused id=\(item.id, privacy: .public) failure=\(String(describing: failure), privacy: .public)")
        if item.isMessage {
            // A message the daemon will not take stays as "not sent", for the owner to edit or send again.
            items[at].phase = .failed
            items[at].failure = failure
        } else {
            items.remove(at: at)
        }
        save()
        for watcher in outcomeWatchers.values { watcher.yield(.refused(item, failure: failure)) }
    }

    /// One doing, once. A failure that may go away is thrown for the loop to wait out; the answers that say a doing is
    /// moot (a card closed, a message already gone, a run already over) are outcomes, not failures.
    private func perform(_ item: OutboxItem) async throws(DaemonRequestError) -> OutboxOutcome {
        switch item.action {
        case .message(let thread, let text, _, _):
            let ids: [String] = try await upload(item)
            return .delivered(item, queue: try await api.queue(QueueMessage(text: text, key: item.id, attachments: ids), thread: thread))
        case .steer(let run, _, let text, _, _):
            let ids: [String] = try await upload(item)
            try await api.steer(SteerMessage(text: text, key: item.id, attachments: ids), run: run)
            return .steered(item)
        case .newChat(let text, _, _):
            let ids: [String] = try await upload(item)
            return .started(item, run: try await api.startChat(NewChatMessage(goal: text, key: item.id, attachments: ids)))
        case .decision(let approval, _, _, let approve, let note, let remember):
            do {
                return .answered(item, status: try await api.decide(approval: approval, approve: approve, note: note, remember: remember).status)
            } catch where error.statusCode == 409 || error.statusCode == 404 || error.statusCode == 422 {
                // The card closed while the phone was away: the card's own record says how it stands.
                return .expired(item, status: try await api.approval(approval).status)
            }
        case .stop(let run, _):
            do {
                try await api.stop(run: run)
            } catch where error.statusCode == 404 || error.statusCode == 409 {
                // Already over: nothing left to stop.
            }
            return .done(item)
        case .stopHelper(let path, _, _):
            do {
                try await api.stop(path: path)
            } catch where error.statusCode == 404 || error.statusCode == 409 {
                // Already over.
            }
            return .done(item)
        case .reorder(let thread, let ids):
            _ = try await api.reorderQueue(thread: thread, ids: ids)
            return .done(item)
        case .removeQueued(_, let message):
            do {
                _ = try await api.removeQueued(message)
            } catch where error.statusCode == 404 {
                // Gone already.
            }
            return .done(item)
        case .steerQueued(_, let message):
            _ = try await api.steerQueued(message)
            return .done(item)
        case .editQueued(_, let message, let text, let attachments):
            _ = try await api.editQueued(message, text: text, attachments: attachments)
            return .done(item)
        case .resumeQueue(let thread):
            _ = try await api.resumeQueue(thread: thread)
            return .done(item)
        case .rename(let thread, let title):
            try await api.rename(thread: thread, title: title)
            return .done(item)
        case .pin(let thread, let pinned):
            try await api.pin(thread: thread, pinned: pinned)
            return .done(item)
        case .delete(let thread):
            try await api.delete(threads: [thread])
            return .done(item)
        case .react(let run, _, let message, let reaction, let on):
            do {
                if on { try await api.react(run: run, message: message, reaction: reaction) } else { try await api.unreact(run: run, message: message, reaction: reaction) }
            } catch where error.statusCode == 404 && !on {
                // Not there: nothing to take back.
            }
            return .done(item)
        case .choices(let thread, let choices):
            try await api.setChoices(thread: thread, choices: choices)
            return .done(item)
        }
    }

    /// The photos of a message, each uploaded once: an id the daemon gave is written into the item before the next.
    private func upload(_ item: OutboxItem) async throws(DaemonRequestError) -> [String] {
        var uploaded: [String] =
            switch item.action {
            case .message(_, _, _, let ids), .steer(_, _, _, _, let ids), .newChat(_, _, let ids): ids
            default: []
            }
        for (number, name) in item.images.enumerated() where number >= uploaded.count {
            guard let data: Data = try? Data(contentsOf: imageFolder.appending(path: name)) else { continue }
            uploaded.append(try await api.uploadAttachment(name: name, jpeg: data))
            if let at: Int = items.firstIndex(where: { $0.id == item.id }) {
                items[at] = items[at].with(uploaded: uploaded)
                save()
            }
        }
        return uploaded
    }

    // MARK: Keeping

    private func save() {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try JSONEncoder().encode(OutboxFile(items: items)).write(to: file, options: [.atomic])
        } catch {
            logger.error("the outbox could not be written error=\(String(describing: error), privacy: .public)")
        }
        for watcher in changeWatchers.values { watcher.yield(items) }
    }

    private func removePhotos(of item: OutboxItem) {
        for name in item.images { try? FileManager.default.removeItem(at: imageFolder.appending(path: name)) }
    }

    /// Forgets everything (the phone's key was forgotten).
    public func clear() {
        items = []
        try? FileManager.default.removeItem(at: file)
        try? FileManager.default.removeItem(at: imageFolder)
        for watcher in changeWatchers.values { watcher.yield([]) }
    }
}
