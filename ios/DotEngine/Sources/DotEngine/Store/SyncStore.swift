import Foundation

/// How the link to the daemon stands, told apart from how current the data is (plan §9, R-f §3): a healthy stream
/// never says "Reconnecting", and data that is not live says so on its own line.
public enum Transport: Sendable, Equatable {
    case connecting
    case up
    case reconnecting(attempt: Int)
    /// The device has no link.
    case offline
    /// The daemon refused the phone's key (401/403): pair again.
    case keyRefused
    /// The daemon answers the sync calls 404: it is from before item 1686 ("Update Dot on your Mac").
    case daemonTooOld
}

/// Everything the screens draw, at one moment.
public struct EngineSnapshot: Sendable, Equatable {
    public var list: ListProjection
    public var chats: [String: ChatProjection]
    public var outbox: [OutboxItem]
    /// Messages the daemon took that the stream has not shown yet, by client key.
    public var accepted: [String: AcceptedMessage]
    public var transport: Transport
    public var online: Bool
    /// When the shown copy of a chat (or `list`) was saved, while it is the cache's.
    public var savedAt: [String: Double]
    /// When a chat's copy was saved while a run of it worked.
    public var workingAt: [String: Double]
    /// The chats this phone started, by the outbox key of their first message: the thread the daemon named for each
    /// (item 1849). Kept for the session, unlike `accepted`, which drops a new chat's entry once the list has its row.
    public var startedChats: [String: String]

    public static let empty: EngineSnapshot = EngineSnapshot(
        list: ListProjection(), chats: [:], outbox: [], accepted: [:], transport: .connecting, online: true, savedAt: [:],
        workingAt: [:], startedChats: [:])

    /// The outbox's and the accepted messages of a chat, oldest first, with where each stands: the overlay of the
    /// transcript (R-b §8 #9).
    public func pending(in thread: String) -> [PendingMessage] {
        let chat: ChatProjection = chats[thread] ?? ChatProjection(threadId: thread)
        let waiting: [PendingMessage] = outbox.filter { $0.thread == thread && $0.isMessage }.map { item in
            PendingMessage(key: item.id, text: item.text ?? "", kind: item.kind ?? .queue, at: item.createdAt, delivery: delivery(of: item, online: online))
        }
        let taken: [PendingMessage] = accepted.values.filter { $0.thread == thread }.compactMap { message in
            guard let where_: Delivery = delivery(of: message, in: chat) else { return nil }
            return PendingMessage(key: message.key, text: message.text, kind: message.kind, at: message.at, delivery: where_)
        }
        return (waiting + taken).sorted { $0.at < $1.at }
    }

    /// New chats not yet in the list (the overlay of the list).
    public var pendingChats: [PendingMessage] {
        outbox.filter { if case .newChat = $0.action { return true } else { return false } }.map { item in
            PendingMessage(key: item.id, text: item.text ?? "", kind: .run, at: item.createdAt, delivery: delivery(of: item, online: online))
        }
    }
}

/// One owner's message as the overlay draws it.
public struct PendingMessage: Sendable, Equatable, Identifiable {
    public let key: String
    public let text: String
    public let kind: SendingKind
    public let at: Double
    public let delivery: Delivery

    public var id: String { key }
}

/// The store of one daemon (plan §5): the list's and the open chats' projections, the outbox's items and what the
/// daemon took, the link's health. It applies the reducers and returns their effects to the supervisor, which owns
/// the network; every change is published as one snapshot.
public actor SyncStore {
    private var state: EngineSnapshot
    private let cache: SyncCache
    private let clock: any EngineClock
    private var version: String?
    private var watchers: [UUID: AsyncStream<EngineSnapshot>.Continuation] = [:]
    private var runsAtSend: [String: Set<String>] = [:]

    public init(cache: SyncCache, clock: any EngineClock) {
        self.cache = cache
        self.clock = clock
        var first: EngineSnapshot = .empty
        if let list: CacheEnvelope<ListProjection> = cache.loadList(now: clock.now()) {
            first.list = ListReducer.restored(list.content)
            first.savedAt["list"] = list.savedAt
        }
        self.state = first
    }

    public var snapshot: EngineSnapshot { state }

    /// Every change, the present picture first.
    public func updates() -> AsyncStream<EngineSnapshot> {
        let (stream, continuation): (AsyncStream<EngineSnapshot>, AsyncStream<EngineSnapshot>.Continuation) = AsyncStream.makeStream(
            of: EngineSnapshot.self, bufferingPolicy: .bufferingNewest(1))
        let id: UUID = UUID()
        watchers[id] = continuation
        continuation.yield(state)
        continuation.onTermination = { [weak self] _ in Task { await self?.forget(id) } }
        return stream
    }

    private func forget(_ id: UUID) { watchers[id] = nil }

    private func publish() {
        for watcher in watchers.values { watcher.yield(state) }
    }

    // MARK: The daemon

    /// The daemon's version, read at each connection; a version other than the cache's drops the cache's cursors.
    public func daemon(version next: String) {
        defer { version = next }
        guard let was: String = version ?? cachedVersion(), was != next else { return }
        cache.clear()
        state.savedAt = [:]
        state.workingAt = [:]
        for id in state.chats.keys { state.chats[id] = ChatProjection(threadId: id) }
        state.list = ListProjection()
        publish()
    }

    private func cachedVersion() -> String? {
        cache.loadList(now: clock.now())?.daemonVersion
    }

    public func set(transport: Transport) {
        guard state.transport != transport else { return }
        state.transport = transport
        publish()
    }

    public func set(online: Bool) {
        guard state.online != online else { return }
        state.online = online
        publish()
    }

    // MARK: The list

    public func listOpening() -> (Int, [ListEffect]) {
        let (next, effects): (ListProjection, [ListEffect]) = ListReducer.opening(state.list)
        state.list = next
        publish()
        return (next.marks.connection, effects)
    }

    public func listOpened(_ connection: Int) {
        state.list = ListReducer.opened(state.list, connection: connection)
        publish()
    }

    public func listBroke(_ connection: Int) {
        state.list = ListReducer.broke(state.list, connection: connection)
        publish()
    }

    public func listFrame(_ frame: StreamFrame, connection: Int) -> [ListEffect] {
        let (next, effects): (ListProjection, [ListEffect]) = ListReducer.frame(state.list, frame, connection: connection)
        if next != state.list {
            state.list = next
            publish()
        }
        if case .threadChanged(let change) = frame, change.deleted {
            cache.removeChat(change.threadId)
        }
        return effects
    }

    /// A list read goes out: the connection it belongs to.
    public func listAsked() -> Int {
        state.list = ListReducer.asked(state.list)
        return state.list.marks.connection
    }

    public func listLoaded(_ view: ThreadsView, connection: Int) -> [ListEffect] {
        let (next, effects): (ListProjection, [ListEffect]) = ListReducer.loaded(state.list, view: view, connection: connection)
        state.list = next
        state.savedAt["list"] = nil
        if let version { cache.saveList(next, version: version, now: clock.now()) }
        publish()
        return effects
    }

    public func listFailed() {
        state.list = ListReducer.failed(state.list)
        publish()
    }

    public var listIsLive: Bool { state.list.freshness == .live }

    // MARK: Chats

    /// Opens a chat's picture: the cache's copy when it may be shown, else an empty one.
    public func chatOpened(_ thread: String) {
        guard state.chats[thread] == nil else { return }
        if let copy: CacheEnvelope<ChatProjection> = cache.loadChat(thread, now: clock.now()),
            version == nil || copy.daemonVersion == version
        {
            state.chats[thread] = ChatReducer.restored(copy.content)
            state.savedAt[thread] = copy.savedAt
            state.workingAt[thread] = copy.workingAt
        } else {
            state.chats[thread] = ChatProjection(threadId: thread)
        }
        publish()
    }

    /// A chat left: its picture is saved and let go.
    public func chatClosed(_ thread: String) {
        guard let chat: ChatProjection = state.chats.removeValue(forKey: thread) else { return }
        if let version { cache.saveChat(chat, version: version, now: clock.now()) }
        state.savedAt[thread] = nil
        state.workingAt[thread] = nil
        publish()
    }

    public func chat(_ thread: String) -> ChatProjection? { state.chats[thread] }

    public func chatOpening(_ thread: String) -> (Int, String, [SyncEffect]) {
        guard let chat: ChatProjection = state.chats[thread] else { return (0, "", []) }
        let (next, effects): (ChatProjection, [SyncEffect]) = ChatReducer.opening(chat)
        state.chats[thread] = next
        publish()
        return (next.marks.connection, next.cursorQuery, effects)
    }

    public func chatOpened(_ thread: String, connection: Int) {
        update(thread) { ChatReducer.opened($0, connection: connection) }
    }

    public func chatBroke(_ thread: String, connection: Int) {
        update(thread) { ChatReducer.broke($0, connection: connection) }
        persist(thread)
    }

    public func chatFrame(_ thread: String, _ frame: StreamFrame, connection: Int) -> [SyncEffect] {
        guard let chat: ChatProjection = state.chats[thread] else { return [] }
        let (next, effects): (ChatProjection, [SyncEffect]) = ChatReducer.frame(chat, frame, connection: connection)
        if next != chat {
            state.chats[thread] = next
            pruneAccepted()
            publish()
        }
        if next.freshness == .deleted { cache.removeChat(thread) }
        return effects
    }

    /// A view read goes out: the connection it belongs to; `nil` when the chat is no longer open.
    public func chatViewAsked(_ thread: String) -> Int? {
        guard let chat: ChatProjection = state.chats[thread] else { return nil }
        state.chats[thread] = ChatReducer.viewAsked(chat, connection: chat.marks.connection)
        return chat.marks.connection
    }

    public func chatViewLoaded(_ thread: String, view: ThreadView, connection: Int) -> [SyncEffect] {
        guard let chat: ChatProjection = state.chats[thread] else { return [] }
        let (next, effects): (ChatProjection, [SyncEffect]) = ChatReducer.viewLoaded(chat, view: view, connection: connection)
        state.chats[thread] = next
        state.savedAt[thread] = nil
        state.workingAt[thread] = nil
        pruneAccepted()
        persist(thread)
        publish()
        return effects
    }

    public func chatViewFailed(_ thread: String, missing: Bool) -> [SyncEffect] {
        guard let chat: ChatProjection = state.chats[thread] else { return [] }
        let (next, effects): (ChatProjection, [SyncEffect]) = ChatReducer.viewFailed(chat, missing: missing)
        state.chats[thread] = next
        if missing { cache.removeChat(thread) }
        publish()
        return effects
    }

    public var openChats: [String] { Array(state.chats.keys) }

    private func update(_ thread: String, _ change: (ChatProjection) -> ChatProjection) {
        guard let chat: ChatProjection = state.chats[thread] else { return }
        let next: ChatProjection = change(chat)
        guard next != chat else { return }
        state.chats[thread] = next
        publish()
    }

    /// Saves a chat's picture (a view came, the stream broke, the app went to the background).
    public func persist(_ thread: String) {
        guard let version, let chat: ChatProjection = state.chats[thread] else { return }
        cache.saveChat(chat, version: version, now: clock.now())
    }

    public func persistAll() {
        for thread in state.chats.keys { persist(thread) }
        if let version { cache.saveList(state.list, version: version, now: clock.now()) }
    }

    // MARK: The outbox

    public func outbox(_ items: [OutboxItem]) {
        guard items != state.outbox else { return }
        // The chat's runs when a message is first seen in the outbox: a run with its words not among them is the one it
        // started (no clock of the phone is compared with the Mac's).
        for item in items where item.isMessage && runsAtSend[item.id] == nil {
            runsAtSend[item.id] = item.thread.flatMap { state.chats[$0]?.knownRunIds } ?? []
        }
        state.outbox = items
        publish()
    }

    public func outcome(_ outcome: OutboxOutcome) {
        let key: String? =
            switch outcome {
            case .delivered(let item, _), .steered(let item), .started(let item, _): item.id
            default: nil
            }
        let before: Set<String> = key.flatMap { runsAtSend.removeValue(forKey: $0) } ?? []
        var changed: Bool = false
        if case .started(let item, let run) = outcome {
            state.startedChats[item.id] = run.threadId
            changed = true
        }
        if let message: AcceptedMessage = accepted(from: outcome, at: clock.now(), runsBefore: before) {
            state.accepted[message.key] = message
            pruneAccepted()
            changed = true
        }
        if changed { publish() }
    }

    /// Drops the overlay of a message the chat now shows in its own place.
    private func pruneAccepted() {
        for (key, message) in state.accepted {
            guard let thread: String = message.thread, let chat: ChatProjection = state.chats[thread] else {
                // A new chat: dropped once the list has its row.
                if message.kind == .run, let thread: String = message.thread, state.list.rows.contains(where: { $0.summary.id == thread }),
                    state.chats[thread] == nil
                {
                    state.accepted[key] = nil
                }
                continue
            }
            let current: AcceptedMessage = following(message, in: chat)
            state.accepted[key] = delivery(of: current, in: chat) == nil ? nil : current
        }
    }

    /// Forgets everything (the phone's key was forgotten).
    public func clear() {
        cache.clear()
        state = .empty
        version = nil
        publish()
    }
}
