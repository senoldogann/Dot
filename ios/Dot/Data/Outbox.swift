import Foundation
import os

/// The owner's doings that the daemon has not yet heard, sent when the link allows, in the order they were made, each
/// exactly once. It lives on disk (`outbox.json` beside the chat cache, written atomically at every change, and the
/// photos of a message beside it), so that a message typed in a lift and an app killed before the link came back lose
/// nothing.
///
/// Exactly once: a message goes with the key it was made with, so a send whose answer was lost and is sent again is the
/// daemon's one message (`send_once` keeps a key a day; a message older than that waits for the owner instead). The
/// other doings are the kind that say the same twice: a stop, a removal, an order, an answer (the second answer finds
/// the card closed and says so).
actor Outbox {
    private let directory: URL
    private let file: URL
    private let imageFolder: URL
    private let api: APIClient
    private let link: LinkMonitor
    private let policy: RetryPolicy
    private let uploadSession: URLSession
    private let clock: @Sendable () -> Double
    private var items: [OutboxItem]
    private var draining: Task<Void, Never>?
    private var pause: Task<Void, Never>?
    private var suspended: Bool = false
    private var changeWatchers: [UUID: AsyncStream<[OutboxItem]>.Continuation] = [:]
    private var outcomeWatchers: [UUID: AsyncStream<OutboxOutcome>.Continuation] = [:]
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "outbox")

    /// What the disk holds is read here (a small file); an item that was being sent when the app ended is waiting again.
    init(
        directory: URL, api: APIClient, link: LinkMonitor, policy: RetryPolicy = .delivery, uploadSession: URLSession? = nil,
        clock: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 }
    ) {
        self.uploadSession = uploadSession ?? api.session
        self.directory = directory
        self.file = directory.appending(path: "outbox.json")
        self.imageFolder = directory.appending(path: "outbox-images", directoryHint: .isDirectory)
        self.api = api
        self.link = link
        self.policy = policy
        self.clock = clock
        var loaded: [OutboxItem] = Self.read(file)
        for at in loaded.indices where loaded[at].phase == .sending { loaded[at].phase = .waiting }
        self.items = loaded
    }

    /// What the disk holds, for the first frame (a message waiting shows before the actor is reached).
    nonisolated static func read(_ file: URL) -> [OutboxItem] {
        guard let data: Data = try? Data(contentsOf: file) else { return [] }
        guard let stored: OutboxFile = try? JSONDecoder().decode(OutboxFile.self, from: data), stored.schema == OutboxFile.schema else {
            // A file that cannot be read is kept aside, not dropped: what was typed in it may still be wanted.
            try? FileManager.default.moveItem(at: file, to: file.deletingLastPathComponent().appending(path: "outbox.unreadable.json"))
            return []
        }
        return stored.items
    }

    // MARK: Watching

    var current: [OutboxItem] { items }

    func changes() -> AsyncStream<[OutboxItem]> {
        let (stream, continuation): (AsyncStream<[OutboxItem]>, AsyncStream<[OutboxItem]>.Continuation) = AsyncStream.makeStream(
            of: [OutboxItem].self, bufferingPolicy: .bufferingNewest(1))
        let id: UUID = UUID()
        changeWatchers[id] = continuation
        continuation.yield(items)
        continuation.onTermination = { [weak self] _ in Task { await self?.forgetChanges(id) } }
        return stream
    }

    func outcomes() -> AsyncStream<OutboxOutcome> {
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

    /// Keeps a doing and starts sending it. `images` are the photos of a message: written beside the file first, so that
    /// the item never names a photo the disk does not hold.
    func enqueue(_ action: OutboxItem.Action, id: String = newMessageKey(), images: [Data] = []) {
        var action: OutboxItem.Action = action
        if case .message(let thread, let text, let typed, let shown, let choices, _, _) = action, !images.isEmpty {
            var names: [String] = []
            try? FileManager.default.createDirectory(at: imageFolder, withIntermediateDirectories: true)
            for (number, data) in images.enumerated() {
                let name: String = "\(id)-\(number).jpg"
                do {
                    try data.write(to: imageFolder.appending(path: name), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
                    names.append(name)
                } catch {
                    logger.error("a photo of a message could not be kept: \(String(describing: error), privacy: .public)")
                }
            }
            action = .message(thread: thread, text: text, typed: typed, shown: shown, choices: choices, images: names, uploaded: [])
        }
        items.append(OutboxItem(id: id, action: action, createdAt: clock()))
        save()
        start()
    }

    /// Takes an item back (Edit on a message that was not sent): the item, so that what it held goes back to the box.
    @discardableResult
    func discard(_ id: String) -> OutboxItem? {
        guard let at: Int = items.firstIndex(where: { $0.id == id }) else { return nil }
        let removed: OutboxItem = items.remove(at: at)
        save()
        return removed
    }

    /// The photos a discarded message held, to put back in the box.
    func photos(of item: OutboxItem) -> [Data] {
        guard case .message(_, _, _, _, _, let names, _) = item.action else { return [] }
        return names.compactMap { try? Data(contentsOf: imageFolder.appending(path: $0)) }
    }

    func forgetPhotos(of item: OutboxItem) { removePhotos(of: item) }

    /// Tries now: the wait is cut short, and a doing that failed for good is tried again (a message too old to be sent
    /// as it was is sent as a new one: the owner chose it).
    func retry(_ id: String) {
        guard let at: Int = items.firstIndex(where: { $0.id == id }) else { return }
        if items[at].phase == .failed {
            items[at].phase = .waiting
            items[at].note = nil
            if items[at].isMessage, clock() - items[at].createdAt > OutboxItem.keyKept {
                let old: OutboxItem = items[at]
                items[at] = OutboxItem(id: newMessageKey(), action: old.action, createdAt: clock())
                if case .message(_, _, _, _, _, let names, _) = old.action { renamePhotos(names, from: old.id, to: items[at].id, at: at) }
            }
            save()
        }
        pause?.cancel()
        start()
    }

    /// Starts sending, or goes on after a stop: the app is in front, the link is back.
    func start() {
        suspended = false
        pause?.cancel()
        guard draining == nil else { return }
        draining = Task { [weak self] in await self?.drain() }
    }

    /// Stops sending while the app sleeps (what waits stays on disk and goes at the next `start`). A send under way is let
    /// to end: it is the one the system gave the app a moment to finish.
    func suspend() {
        suspended = true
        pause?.cancel()
    }

    var isBusy: Bool { draining != nil }

    // MARK: Sending

    private func drain() async {
        // Set in the same turn as the last look at the list, so that a doing made meanwhile starts a drain of its own.
        defer { draining = nil }
        while !suspended, !Task.isCancelled {
            guard let at: Int = items.firstIndex(where: { $0.phase != .failed }) else { return }
            if items[at].isMessage, clock() - items[at].createdAt > OutboxItem.keyKept, items[at].attempts > 0 {
                items[at].phase = .failed
                items[at].note = "This message waited more than a day. Send it again if you still want it."
                save()
                continue
            }
            if !link.state.online {
                // The link is down: nothing is asked of the network until it comes back (or the app is told to try).
                items[at].phase = .waiting
                save()
                await sleepOrWake(link: true)
                continue
            }
            items[at].phase = .sending
            items[at].attempts += 1
            save()
            let item: OutboxItem = items[at]
            do {
                let outcome: OutboxOutcome = try await perform(item)
                finish(item.id, outcome)
            } catch {
                if Task.isCancelled { return }
                if RetryPolicy.isWorthRepeating(error) {
                    if let now: Int = items.firstIndex(where: { $0.id == item.id }) {
                        items[now].phase = .waiting
                        items[now].note = nil
                        save()
                    }
                    logger.info("the outbox will try again: \(String(describing: error), privacy: .public)")
                    await sleepOrWake(link: false, failure: item.attempts)
                } else {
                    refuse(item, error: error)
                }
            }
        }
    }

    /// Waits for the link to come back (when `link`), or for the backoff, or for a call to `retry`/`start`.
    private func sleepOrWake(link waitsForLink: Bool, failure: Int = 1) async {
        let wait: Task<Void, Never> = Task { [link, policy] in
            if waitsForLink {
                _ = await link.waitForOnline(timeout: .seconds(30))
            } else {
                try? await Task.sleep(for: policy.wait(afterFailure: failure, jitter: Double.random(in: 0...1)))
            }
        }
        pause = wait
        await wait.value
        pause = nil
    }

    private func finish(_ id: String, _ outcome: OutboxOutcome) {
        guard let at: Int = items.firstIndex(where: { $0.id == id }) else { return }
        let done: OutboxItem = items.remove(at: at)
        removePhotos(of: done)
        save()
        for watcher in outcomeWatchers.values { watcher.yield(outcome) }
    }

    private func refuse(_ item: OutboxItem, error: any Error) {
        guard let at: Int = items.firstIndex(where: { $0.id == item.id }) else { return }
        let words: String = Self.words(for: error)
        logger.error("the daemon refused a doing for good: \(String(describing: error), privacy: .public)")
        switch item.action {
        case .message:
            // A message the daemon will not take stays in the chat as "not sent", for the owner to edit or send again.
            items[at].phase = .failed
            items[at].note = words
            save()
        default:
            items.remove(at: at)
            save()
        }
        for watcher in outcomeWatchers.values { watcher.yield(.refused(item, words: words)) }
    }

    private static func words(for error: any Error) -> String {
        if case DaemonRequestError.status(_, let code, let body) = error {
            let detail: String = parsedDetail(body)
            return detail.isEmpty ? "Dot did not take it (\(code))." : detail
        }
        return "Dot did not take it."
    }

    private static func parsedDetail(_ body: String) -> String {
        guard let data: Data = body.data(using: .utf8),
            let object: [String: Any] = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail: String = object["detail"] as? String
        else { return "" }
        return String(detail.prefix(160))
    }

    /// One doing, once. A failure that may go away is thrown for the loop to wait out; the answers that say a doing is
    /// moot (a card closed, a message already gone) are outcomes, not failures.
    private func perform(_ item: OutboxItem) async throws -> OutboxOutcome {
        switch item.action {
        case .message(let thread, let text, _, _, let choices, let images, var uploaded):
            // The photos first, each kept once: an id the daemon gave is written into the item before the next step.
            for (number, name) in images.enumerated() where number >= uploaded.count {
                guard let data: Data = try? Data(contentsOf: imageFolder.appending(path: name)) else { continue }
                let uploader: DaemonClient = DaemonClient(session: uploadSession, maxAttempts: 1)
                let upload: AttachmentUpload = AttachmentUpload(name: name, mediaType: "image/jpeg", dataB64: data.base64EncodedString())
                uploaded.append(try await uploader.uploadAttachment(upload, host: api.host, key: api.key))
                remember(uploaded: uploaded, of: item.id)
            }
            do {
                let queue: QueueView = try await api.send(
                    message: Message(text: text, key: item.id, attachments: uploaded, choices: choices), thread: thread)
                return .delivered(item, queue: queue)
            } catch DaemonRequestError.status(_, 409, let body) where body.contains("sent already") {
                // The same key is being sent (the first sending is still under way on the Mac): wait, then ask again.
                throw DaemonRequestError.status(path: "/api/threads/\(thread)/queue", code: 503, body: "the message is being sent already")
            }
        case .decision(let approval, _, _, let approve, let note):
            do {
                let record: ApprovalRecord = try await api.decide(approval: approval, approve: approve, note: note)
                return .answered(item, status: record.status)
            } catch DaemonRequestError.status(_, let code, _) where code == 409 || code == 404 {
                // The card closed while the phone was away (the run ended, it was answered on the Mac): the card's own
                // record says how it stands.
                let record: ApprovalRecord = try await api.approval(approval)
                return .expired(item, status: record.status)
            }
        case .stop(let run, _):
            do {
                try await api.stop(run: run)
            } catch DaemonRequestError.status(_, let code, _) where code == 404 || code == 409 {
                // Already over: nothing left to stop.
            }
            return .done(item)
        case .reorder(let thread, let ids):
            _ = try await api.reorderQueue(thread: thread, ids: ids)
            return .done(item)
        case .removeQueued(_, let message):
            do {
                _ = try await api.removeQueued(message)
            } catch DaemonRequestError.status(_, 404, _) {
                // Gone already.
            }
            return .done(item)
        case .steerQueued(_, let message):
            _ = try await api.steerQueued(message)
            return .done(item)
        }
    }

    private func remember(uploaded: [String], of id: String) {
        guard let at: Int = items.firstIndex(where: { $0.id == id }), case .message(let thread, let text, let typed, let shown, let choices, let images, _) = items[at].action else { return }
        items[at] = OutboxItem(
            id: items[at].id,
            action: .message(thread: thread, text: text, typed: typed, shown: shown, choices: choices, images: images, uploaded: uploaded),
            createdAt: items[at].createdAt, attempts: items[at].attempts, phase: items[at].phase, note: items[at].note)
        save()
    }

    // MARK: Keeping

    private func save() {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data: Data = try JSONEncoder().encode(OutboxFile(items: items))
            try data.write(to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch {
            logger.error("the outbox could not be written: \(String(describing: error), privacy: .public)")
        }
        for watcher in changeWatchers.values { watcher.yield(items) }
    }

    private func removePhotos(of item: OutboxItem) {
        guard case .message(_, _, _, _, _, let names, _) = item.action else { return }
        for name in names { try? FileManager.default.removeItem(at: imageFolder.appending(path: name)) }
    }

    private func renamePhotos(_ names: [String], from old: String, to new: String, at index: Int) {
        guard case .message(let thread, let text, let typed, let shown, let choices, _, _) = items[index].action else { return }
        var renamed: [String] = []
        for name in names {
            let target: String = name.replacingOccurrences(of: old, with: new)
            try? FileManager.default.moveItem(at: imageFolder.appending(path: name), to: imageFolder.appending(path: target))
            renamed.append(target)
        }
        items[index] = OutboxItem(
            id: items[index].id,
            action: .message(thread: thread, text: text, typed: typed, shown: shown, choices: choices, images: renamed, uploaded: []),
            createdAt: items[index].createdAt, attempts: 0, phase: .waiting, note: nil)
    }

    /// Forgets everything (the phone's key was forgotten).
    func clear() {
        items = []
        try? FileManager.default.removeItem(at: file)
        try? FileManager.default.removeItem(at: imageFolder)
        for watcher in changeWatchers.values { watcher.yield([]) }
    }
}
