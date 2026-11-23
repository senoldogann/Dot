import Foundation
import Network
import Observation
import os

/// How the app stands with the daemon, for the one quiet line in the list's head.
enum Connection: Equatable, Sendable {
    case connecting
    case live
    case reconnecting
    case unauthorized
}

/// The native app's state of the daemon: the chat list and the agents (drawn from disk at the first frame, refreshed
/// after), the lifecycle stream that says when something changed, and the chats open on the screen. One per host and
/// key; the app makes a new one when either changes.
@MainActor
@Observable
final class NativeStore {
    let host: DaemonHost
    let api: APIClient
    let cache: ChatCache
    private(set) var threads: [ThreadSummary]
    private(set) var agents: [AgentSummary]
    private(set) var connection: Connection = .connecting
    private(set) var refreshing: Bool = false
    private(set) var lastError: String?
    private(set) var models: ModelChoices?

    /// Chats read before, as their state: a chat that is here opens drawn, in its first frame.
    @ObservationIgnored private var warm: [String: ChatState] = [:]
    /// The sessions of the chats on the screen, by the token of the screen that made each: a chat opened twice in a row
    /// (a navigation that settles) must not stop the later one when the earlier goes.
    @ObservationIgnored private var open: [UUID: ChatSession] = [:]
    @ObservationIgnored private let startedAt: Date = Date()
    @ObservationIgnored private var lifecycleFeed: EventFeed?
    @ObservationIgnored private var lifecycleTask: Task<Void, Never>?
    @ObservationIgnored private var listTask: Task<Void, Never>?
    @ObservationIgnored private var monitor: NWPathMonitor?
    @ObservationIgnored private var wasDown: Bool = false
    @ObservationIgnored private var lastListAt: Date = .distantPast
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "store")

    init(host: DaemonHost, key: String, session: URLSession = APIClient.makeSession(), directory: URL? = nil) {
        self.host = host
        self.api = APIClient(host: host, key: key, session: session)
        let folder: URL = directory ?? ChatCache.directory(for: host)
        self.cache = ChatCache(directory: folder)
        let snapshot: ListSnapshot? = ChatCache.readList(in: folder)
        self.threads = snapshot?.threads ?? []
        self.agents = snapshot?.agents ?? []
    }

    var hasList: Bool { !threads.isEmpty || !agents.isEmpty }

    // MARK: Start

    func start() {
        guard lifecycleTask == nil else { return }
        watchNetwork()
        startLifecycle()
        Task { await refreshList() }
        Task { await loadModels() }
        Task { await preload() }
    }

    func stop() {
        lifecycleTask?.cancel()
        lifecycleTask = nil
        let feed: EventFeed? = lifecycleFeed
        Task { await feed?.stop() }
        lifecycleFeed = nil
        monitor?.cancel()
        monitor = nil
        for session in open.values { Task { await session.stop() } }
        open = [:]
    }

    /// The chats the owner is most likely to open, read from disk off the main actor and kept as states.
    private func preload() async {
        let ids: [String] = threads.prefix(8).map(\.id)
        let cache: ChatCache = cache
        let states: [String: ChatState] = await Task.detached(priority: .utility) {
            var found: [String: ChatState] = [:]
            for id in ids {
                if let stored: StoredChat = await cache.loadChat(id) { found[id] = stored.state() }
            }
            return found
        }.value
        for (id, state) in states where warm[id] == nil { warm[id] = state }
    }

    // MARK: List

    func refreshList() async {
        refreshing = true
        defer { refreshing = false }
        do {
            async let listed: [ThreadSummary] = api.threads()
            async let people: [AgentSummary] = api.agents()
            let (newThreads, newAgents): ([ThreadSummary], [AgentSummary]) = try await (listed, people)
            threads = newThreads
            agents = newAgents
            lastError = nil
            lastListAt = Date()
            let snapshot: ListSnapshot = ListSnapshot(threads: newThreads, agents: newAgents, savedAt: Date().timeIntervalSince1970)
            let cache: ChatCache = cache
            let keep: [String] = newThreads.map(\.id)
            Task.detached(priority: .utility) {
                await cache.saveList(snapshot)
                await cache.prune(keeping: keep)
            }
            if connection != .live, connection != .unauthorized { connection = .live }
        } catch {
            lastError = String(describing: error)
            if case DaemonRequestError.status(_, let code, _) = error, code == 401 || code == 403 {
                connection = .unauthorized
            } else if connection == .live {
                connection = .reconnecting
            }
            logger.info("the list could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    private func refreshListSoon() {
        listTask?.cancel()
        listTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(300))
            if Task.isCancelled { return }
            await self?.refreshList()
        }
    }

    func loadModels() async {
        do {
            models = try await api.status().models
        } catch {
            logger.error("the models could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    /// A thread's row after the chat changed it (its unread mark cleared when opened).
    func markSeen(_ id: String) {
        if let at: Int = threads.firstIndex(where: { $0.id == id }), threads[at].unread {
            threads[at].unread = false
            Task {
                do {
                    try await api.markSeen(thread: id)
                } catch {
                    // The mark comes back at the next read of the list; the log keeps why.
                    logger.error("a chat was not marked seen: \(String(describing: error), privacy: .public)")
                }
            }
        }
    }

    // MARK: Chats

    func warmState(for id: String) -> ChatState? { warm[id] }

    func keep(_ state: ChatState, for id: String) { warm[id] = state }

    func makeSession(for id: String, token: UUID) -> ChatSession {
        let session: ChatSession = ChatSession(threadId: id, api: api, cache: cache)
        open[token] = session
        return session
    }

    func release(token: UUID) {
        if let session: ChatSession = open.removeValue(forKey: token) { Task { await session.stop() } }
    }

    // MARK: Lifecycle stream and reconnecting

    private func startLifecycle() {
        let api: APIClient = api
        let feed: EventFeed = EventFeed(
            session: Self.streamSession, after: 0, wantsEvents: false,
            open: { _ in
                var request: URLRequest = api.request("GET", "/api/stream")
                request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                request.timeoutInterval = 45
                return request
            })
        lifecycleFeed = feed
        lifecycleTask = Task { [weak self] in
            let updates: AsyncStream<FeedUpdate> = await feed.start()
            for await update in updates {
                guard let self else { return }
                self.take(update)
            }
        }
    }

    private func take(_ update: FeedUpdate) {
        switch update {
        case .state(let state):
            switch state {
            case .live:
                // The stream replays nothing: after a break, what it missed is read again.
                if wasDown {
                    wasDown = false
                    refreshListSoon()
                    for session in open.values { Task { await session.kick() } }
                }
                if connection != .unauthorized { connection = .live }
            case .reconnecting(_, let unauthorized):
                wasDown = true
                connection = unauthorized ? .unauthorized : .reconnecting
            case .connecting, .ended:
                break
            }
        case .notice(let kind):
            refreshListSoon()
            for session in open.values { Task { await session.lifecycle(kind) } }
        case .events:
            break
        }
    }

    /// The app is in front again, or the network changed: every stream reconnects at once, and the list is read again
    /// when it is more than a few seconds old.
    func reconnect() {
        // The app's first activation is its start, not a return.
        guard Date().timeIntervalSince(startedAt) > 2 else { return }
        let feed: EventFeed? = lifecycleFeed
        Task { await feed?.kick() }
        for session in open.values { Task { await session.kick() } }
        if Date().timeIntervalSince(lastListAt) > 5 { refreshListSoon() }
    }

    private func watchNetwork() {
        let monitor: NWPathMonitor = NWPathMonitor()
        // The first update is the monitor's own start, not a change.
        let first: OSAllocatedUnfairLock<Bool> = OSAllocatedUnfairLock(initialState: true)
        monitor.pathUpdateHandler = { [weak self] path in
            guard path.status == .satisfied else { return }
            if first.withLock({ (value: inout Bool) -> Bool in
                let wasFirst: Bool = value
                value = false
                return wasFirst
            }) { return }
            Task { @MainActor in self?.reconnect() }
        }
        monitor.start(queue: DispatchQueue(label: "dev.dotagent.phone.network"))
        self.monitor = monitor
    }

    private static let streamSession: URLSession = {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 45
        configuration.timeoutIntervalForResource = 24 * 3600
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

    /// Forgets what the disk holds, as when the key is forgotten.
    func clearCache() async { await cache.clear() }
}
