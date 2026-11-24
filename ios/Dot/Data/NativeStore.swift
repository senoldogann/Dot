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
/// after), the lifecycle stream that says when something changed, the chats open on the screen, what the owner did that
/// the Mac has not yet heard (the outbox), and how the link stands. One per host and key; the app makes a new one when
/// either changes.
@MainActor
@Observable
final class NativeStore {
    let host: DaemonHost
    let api: APIClient
    let cache: ChatCache
    let link: LinkMonitor
    let outbox: OutboxModel
    private(set) var threads: [ThreadSummary]
    private(set) var agents: [AgentSummary]
    private(set) var connection: Connection = .connecting
    private(set) var refreshing: Bool = false
    private(set) var lastError: String?
    private(set) var models: ModelChoices?
    /// Every provider's models, for the pickers; read when a picker opens, and again when asked (`loadProviders`).
    private(set) var providers: [ProviderModels] = []
    /// The schedules, as last read (from disk before the network has answered).
    private(set) var schedules: [ScheduleView] = []
    /// How the phone's link stands, and when the list was last read from the Mac: the banner's words.
    private(set) var linkState: LinkState
    private(set) var lastUpdated: Date?
    /// How many helpers of a chat the daemon last said are live, for the chat's row in the list (item 1466). The daemon's list of
    /// chats says nothing of helpers, so this is what a read of a chat's own helpers found, in this run of the app: a chat read before
    /// is not here, and one whose count was read at an older newest run is not trusted (`ListHelpers`).
    private(set) var helperCounts: [String: HelperCount] = [:]
    /// What was typed and not sent, per chat, kept between launches.
    let drafts: DraftStore
    /// What the phone knows of Dot's awareness of the owner: the push switches (item 1441), later what waits on them and what
    /// Dot did while they were away.
    let awareness: AwarenessModel
    /// Tells the daemon whether the app is in front of the owner and which chat it shows (item 1440), so that its alerts
    /// stay quiet while the owner looks and go the moment they do not.
    @ObservationIgnored let lease: ActivityLease

    /// Chats read before, as their state: a chat that is here opens drawn, in its first frame.
    @ObservationIgnored private var warm: [String: ChatState] = [:]
    /// The sessions of the chats on the screen, by the token of the screen that made each: a chat opened twice in a row
    /// (a navigation that settles) must not stop the later one when the earlier goes.
    @ObservationIgnored private var open: [UUID: ChatSession] = [:]
    /// The tokens of the same screens, oldest first: the chat on the screen is the newest that is still open (a navigation
    /// that settles holds two for a moment).
    @ObservationIgnored private var openOrder: [UUID] = []
    @ObservationIgnored private let startedAt: Date = Date()
    @ObservationIgnored private let folder: URL
    /// The session of the streams: the requests' own configuration (so that the same protocols and proxies apply) with no
    /// timeout on the whole and 45 s on silence (the daemon writes a keepalive every 15 s).
    @ObservationIgnored private let streamSession: URLSession
    @ObservationIgnored private var lifecycleFeed: EventFeed?
    @ObservationIgnored private var lifecycleTask: Task<Void, Never>?
    @ObservationIgnored private var listTask: Task<Void, Never>?
    @ObservationIgnored private var listRefresh: Task<Void, Never>?
    @ObservationIgnored private var needsYouTask: Task<Void, Never>?
    @ObservationIgnored private var linkTask: Task<Void, Never>?
    @ObservationIgnored private var outcomeTask: Task<Void, Never>?
    @ObservationIgnored private var preloadTask: Task<Void, Never>?
    @ObservationIgnored private var prefetchTask: Task<Void, Never>?
    @ObservationIgnored private var graceTask: Task<Void, Never>?
    @ObservationIgnored private let grace: BackgroundGrace = BackgroundGrace()
    @ObservationIgnored private var inBackground: Bool = false
    @ObservationIgnored private var feedsClosed: Bool = false
    @ObservationIgnored private var wasDown: Bool = false
    @ObservationIgnored private var lastListAt: Date = .distantPast
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "store")

    /// How long the app keeps its streams after it leaves the screen: a call that ends, a message that is answered in
    /// another app, comes back to a chat that is still live.
    @ObservationIgnored var graceSeconds: Double = 20

    init(
        host: DaemonHost, key: String, session: URLSession = APIClient.makeSession(), directory: URL? = nil,
        link: LinkMonitor = LinkMonitor(watching: true), uploadSession: URLSession = APIClient.makeUploadSession()
    ) {
        self.host = host
        self.link = link
        self.streamSession = APIClient.makeStreamSession(like: session)
        self.linkState = link.state
        let client: APIClient = APIClient(host: host, key: key, session: session, link: link)
        self.api = client
        // The lease's reports have a client of their own: one try, no waiting for a link (see `makeLeaseSession`).
        let leaseClient: APIClient = APIClient(host: host, key: key, session: APIClient.makeLeaseSession(like: session))
        let leaseLog: Logger = Logger(subsystem: "dev.dotagent.phone", category: "lease")
        self.lease = ActivityLease(
            paired: true,
            send: { report in
                do {
                    try await leaseClient.report(activity: report)
                } catch {
                    // Never shown to the owner: a lease that is not said ends by itself, and the alerts go.
                    leaseLog.info("the activity lease was not said: \(String(describing: error), privacy: .public)")
                }
            })
        let folder: URL = directory ?? ChatCache.directory(for: host)
        self.folder = folder
        let chatCache: ChatCache = ChatCache(directory: folder)
        self.cache = chatCache
        self.drafts = DraftStore(directory: folder)
        self.awareness = AwarenessModel(api: client, cache: chatCache, directory: folder, link: link)
        let box: Outbox = Outbox(directory: folder, api: client, link: link, uploadSession: uploadSession)
        self.outbox = OutboxModel(box: box, initial: Outbox.read(folder.appending(path: "outbox.json")))
        #if DEBUG
        // `-DotClearDrafts 1`: the simulator tests start from no drafts.
        if UserDefaults.standard.bool(forKey: "DotClearDrafts") { self.drafts.clear() }
        #endif
        let snapshot: ListSnapshot? = ChatCache.readList(in: folder)
        self.threads = snapshot?.threads ?? []
        self.agents = snapshot?.agents ?? []
        self.lastUpdated = snapshot.map { Date(timeIntervalSince1970: $0.savedAt) }
        if let extras: ExtrasSnapshot = ChatCache.readExtras(in: folder) {
            self.models = extras.models
            self.providers = extras.providers ?? []
            self.schedules = extras.schedules ?? []
        }
        // The first chats are decoded off the main actor while the screen is being made.
        preloadTask = Task { [weak self] in await self?.preload() }
    }

    var hasList: Bool { !threads.isEmpty || !agents.isEmpty }

    /// The line over the screens, or none: it says the phone is offline (what it shows was saved) or trying again, and never
    /// stands in the way of reading.
    func banner(now: Date = Date()) -> ConnectionBanner? {
        ConnectionBanner.make(link: linkState, connection: connection, lastUpdated: lastUpdated, waiting: outbox.items.count, now: now)
    }

    // MARK: Start

    func start() {
        guard lifecycleTask == nil else { return }
        feedsClosed = false
        watchLink()
        startLifecycle()
        outbox.watch()
        watchOutcomes()
        outbox.start()
        Task { await refreshList() }
        Task { await loadModels() }
        Task { await awareness.refreshNeedsYou() }
        Task { await awareness.refreshAway() }
    }

    func stop() {
        lease.stop()
        lifecycleTask?.cancel()
        lifecycleTask = nil
        linkTask?.cancel()
        linkTask = nil
        outcomeTask?.cancel()
        outcomeTask = nil
        graceTask?.cancel()
        prefetchTask?.cancel()
        listTask?.cancel()
        listRefresh?.cancel()
        needsYouTask?.cancel()
        preloadTask?.cancel()
        let feed: EventFeed? = lifecycleFeed
        Task { await feed?.stop() }
        lifecycleFeed = nil
        link.stop()
        outbox.suspend()
        outbox.stop()
        grace.end()
        for session in open.values { Task { await session.stop() } }
        open = [:]
        openOrder = []
    }

    /// The chats the owner is most likely to open, decoded off the main actor, several at once, and kept as states.
    private func preload() async {
        let ids: [String] = threads.prefix(8).map(\.id)
        let folder: URL = folder
        let states: [String: ChatState] = await Task.detached(priority: .userInitiated) {
            await withTaskGroup(of: (String, ChatState)?.self) { group in
                for id in ids {
                    group.addTask {
                        guard let stored: StoredChat = ChatCache.readChat(id, in: folder) else { return nil }
                        return (id, stored.state())
                    }
                }
                var found: [String: ChatState] = [:]
                for await pair in group { if let pair { found[pair.0] = pair.1 } }
                return found
            }
        }.value
        for (id, state) in states where warm[id] == nil { warm[id] = state }
    }

    // MARK: List

    /// Reads the list and the agents. A second call while one reads waits for that one (a pull to refresh, a change of
    /// network and a message of the lifecycle stream arriving together make one request).
    func refreshList() async {
        if let running: Task<Void, Never> = listRefresh {
            await running.value
            return
        }
        let task: Task<Void, Never> = Task { await readList() }
        listRefresh = task
        await task.value
        listRefresh = nil
    }

    /// A pull to refresh: the list and what waits on the owner are read together.
    func refreshEverything() async {
        async let listed: Void = refreshList()
        async let waiting: Void = awareness.refreshNeedsYou()
        async let away: Void = awareness.refreshAway()
        _ = await (listed, waiting, away)
    }

    private func readList() async {
        // No link: nothing is asked (the banner says so, and the list the phone holds stays); the link coming back reads it.
        guard link.state.online else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            async let listed: [ThreadSummary] = api.threads()
            async let people: [AgentSummary] = api.agents()
            let (newThreads, newAgents): ([ThreadSummary], [AgentSummary]) = try await (listed, people)
            if newThreads != threads { threads = newThreads }
            if newAgents != agents { agents = newAgents }
            lastError = nil
            lastListAt = Date()
            lastUpdated = lastListAt
            let snapshot: ListSnapshot = ListSnapshot(threads: newThreads, agents: newAgents, savedAt: Date().timeIntervalSince1970)
            let cache: ChatCache = cache
            let keep: [String] = newThreads.map(\.id)
            Task.detached(priority: .utility) {
                await cache.saveList(snapshot)
                await cache.prune(keeping: keep)
            }
            if connection != .live, connection != .unauthorized { connection = .live }
            prefetch()
        } catch {
            if Task.isCancelled { return }
            lastError = String(describing: error)
            if case DaemonRequestError.status(_, let code, _) = error, code == 401 || code == 403 {
                connection = .unauthorized
            } else if connection == .live, !RetryPolicy.isNoLink(error) {
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

    /// What waits on the owner is read again soon (a moment of waiting joins the notices that come together).
    private func refreshNeedsYouSoon() {
        needsYouTask?.cancel()
        needsYouTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(300))
            if Task.isCancelled { return }
            await self?.awareness.refreshNeedsYou()
        }
    }

    func loadModels() async {
        do {
            models = try await api.status().models
            keepExtras()
        } catch {
            logger.error("the models could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    /// The models of every provider, once: a picker draws what is held and asks again only when it holds nothing or `force`.
    func loadProviders(force: Bool = false) async {
        if !providers.isEmpty, !force { return }
        do {
            providers = try await api.models()
            keepExtras()
        } catch {
            logger.error("the providers' models could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    /// The schedules: what the disk held is on the screen (`schedules`) while this reads, and a read that fails leaves it.
    func loadSchedules() async {
        do {
            schedules = try await api.schedules()
            keepExtras()
        } catch {
            logger.error("the schedules could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    private func keepExtras() {
        let extras: ExtrasSnapshot = ExtrasSnapshot(
            models: models, providers: providers.isEmpty ? nil : providers, schedules: schedules.isEmpty ? nil : schedules,
            savedAt: Date().timeIntervalSince1970)
        let cache: ChatCache = cache
        Task.detached(priority: .utility) { await cache.saveExtras(extras) }
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

    /// What a chat's row in the list says of its helpers: how many are live as last read (`ListHelpers`).
    func helpersLive(in thread: ThreadSummary) -> Int { ListHelpers.live(helperCounts[thread.id], thread: thread) }

    /// A session read the helpers of a chat (item 1466): the row of the chat says how many are live.
    private func helpersReported(_ thread: String, live: Int, newestRun: String?) {
        let next: HelperCount? = live > 0 ? HelperCount(live: live, newestRun: newestRun) : nil
        if helperCounts[thread] != next { helperCounts[thread] = next }
    }

    private func helpersReport() -> HelpersReported {
        { [weak self] thread, live, newestRun in
            Task { @MainActor in self?.helpersReported(thread, live: live, newestRun: newestRun) }
        }
    }

    func makeSession(for id: String, token: UUID) -> ChatSession {
        let session: ChatSession = ChatSession(
            threadId: id, api: api, cache: cache, link: link, listUpdatedAt: threads.first(where: { $0.id == id })?.updatedAt,
            helpersReported: helpersReport())
        open[token] = session
        openOrder.removeAll { $0 == token }
        openOrder.append(token)
        lease.showing(chat: chatOnScreen)
        return session
    }

    func release(token: UUID) {
        if let session: ChatSession = open.removeValue(forKey: token) { Task { await session.stop() } }
        openOrder.removeAll { $0 == token }
        lease.showing(chat: chatOnScreen)
    }

    /// The chat on the screen (its thread id), `nil` when the list or Settings is.
    var chatOnScreen: String? { openOrder.last.flatMap { open[$0]?.threadId } }

    /// The runs of the chat on the screen, its own and its helpers' (and the newest one the list names): an alert about one of
    /// them is about the chat the owner is reading (item 1446). `nil` when no chat is on the screen.
    func runIdsOnScreen() -> Set<String>? {
        guard let id: String = chatOnScreen else { return nil }
        var ids: Set<String> = []
        if let state: ChatState = warm[id] {
            ids.formUnion(state.rootRunIds)
            ids.formUnion(state.helpers.map(\.id))
        }
        if let latest: String = threads.first(where: { $0.id == id })?.latestRunId, !latest.isEmpty { ids.insert(latest) }
        return ids
    }

    /// The scene's phase changed: the lease says so to the daemon at once (item 1440).
    func sceneChanged(_ phase: LeaseScene) { lease.scene(phase) }

    // MARK: Reading ahead

    /// After a read of the list: the chats that moved on past what the phone holds are read once, one after another, so that
    /// opening one draws what the Mac has. Few on a link that costs, none on one the system asks to spare (Low Data
    /// Mode), none while the app is out of sight, and never the chat that is open (it reads itself).
    /// `force` is for a read the system asked for while the app is out of sight (a background refresh, a silent push): the
    /// app's own reading ahead stops when it leaves the screen, that one is what the system gave its time for.
    private func prefetch(budget given: Int? = nil, force: Bool = false) {
        prefetchTask?.cancel()
        let budget: Int = given ?? link.state.prefetchBudget
        guard budget > 0, force || !inBackground else { return }
        let candidates: [ThreadSummary] = threads
        let openIds: Set<String> = Set(open.values.map(\.threadId))
        let folder: URL = folder
        let api: APIClient = api
        let cache: ChatCache = cache
        let link: LinkMonitor = link
        let report: HelpersReported = helpersReport()
        prefetchTask = Task.detached(priority: .utility) { [weak self] in
            let chosen: [ThreadSummary] = PrefetchRules.chosen(
                from: candidates, open: openIds, budget: budget, held: { ChatCache.readChat($0, in: folder) })
            for thread in chosen {
                if Task.isCancelled || !link.state.online { return }
                let session: ChatSession = ChatSession(threadId: thread.id, api: api, cache: cache, link: link, helpersReported: report)
                if let state: ChatState = await session.warm(listUpdatedAt: thread.updatedAt) {
                    await self?.warmed(thread.id, state)
                }
            }
        }
    }

    private func warmed(_ id: String, _ state: ChatState) {
        if !open.values.contains(where: { $0.threadId == id }) { warm[id] = state }
    }

    // MARK: Lifecycle stream and reconnecting

    private func startLifecycle() {
        let api: APIClient = api
        let feed: EventFeed = EventFeed(
            session: streamSession, after: 0, wantsEvents: false, link: link,
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
            if NeedsYouRules.refreshes(onNotice: kind) { refreshNeedsYouSoon() }
            for session in open.values { Task { await session.lifecycle(kind) } }
        case .events, .unreadable:
            break
        }
    }

    /// The app is in front again, or the network changed: every stream reconnects at once, and the list is read again
    /// when it is more than a few seconds old.
    func reconnect() {
        // The app's first activation is its start, not a return.
        guard Date().timeIntervalSince(startedAt) > 2 else { return }
        kickAll()
    }

    private func kickAll() {
        let feed: EventFeed? = lifecycleFeed
        Task { await feed?.kick() }
        for session in open.values { Task { await session.kick() } }
        if Date().timeIntervalSince(lastListAt) > 5 { refreshListSoon() }
        // What waits on the owner may have changed on the Mac while the phone was away, and Dot may have done things on its own.
        Task { await awareness.refreshNeedsYou() }
        Task { await awareness.refreshAway() }
        outbox.start()
    }

    /// The link changed: the banner follows it at once; a link that came back wakes every stream, the outbox and the list.
    private func watchLink() {
        let link: LinkMonitor = link
        linkTask = Task { [weak self] in
            var first: Bool = true
            for await state in link.updates() {
                guard let self else { return }
                let before: LinkState = self.linkState
                self.linkState = state
                // The first report is the monitor's own start, not a change.
                if first {
                    first = false
                    continue
                }
                if state.online, !before.online {
                    self.logger.info("the link is back")
                    self.kickAll()
                }
            }
        }
    }

    /// What the daemon answered to the owner's doings that were waiting: the screens read their own chats' (a card
    /// closed, a message delivered); the list and the queue are read again here.
    private func watchOutcomes() {
        outcomeTask = Task { [weak self] in
            guard let box: Outbox = self?.outbox.box else { return }
            for await _ in await box.outcomes() {
                guard let self else { return }
                self.refreshListSoon()
                // An answer given from the phone closes a card (or finds it closed): the inbox and the badge follow.
                self.refreshNeedsYouSoon()
            }
        }
    }

    // MARK: Background and foreground

    /// The app left the screen: what is on its way is let to end and what is held is written; after a few seconds out of
    /// sight the streams are closed (a socket kept open on a sleeping phone is a drain), and no timer is left running.
    func enterBackground() {
        guard !inBackground else { return }
        inBackground = true
        prefetchTask?.cancel()
        for session in open.values { Task { await session.suspend() } }
        // Time to finish a send and the writes; given back when the streams are closed.
        grace.begin { [weak self] in self?.closeFeeds() }
        graceTask?.cancel()
        graceTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(self?.graceSeconds ?? 0))
            if Task.isCancelled { return }
            self?.closeFeeds()
        }
    }

    private func closeFeeds() {
        guard inBackground, !feedsClosed else { return }
        feedsClosed = true
        lifecycleTask?.cancel()
        lifecycleTask = nil
        let feed: EventFeed? = lifecycleFeed
        Task { await feed?.stop() }
        lifecycleFeed = nil
        outbox.suspend()
        grace.end()
        logger.info("the streams are closed while the app is out of sight")
    }

    /// The app is in front again: what was closed opens, every chat on the screen reads what came after, the list is read,
    /// and what waited in the outbox goes. The streams are kicked at once; nothing waits for the network before the screen
    /// shows what it holds.
    func enterForeground() {
        guard inBackground else { return }
        inBackground = false
        graceTask?.cancel()
        graceTask = nil
        grace.end()
        if feedsClosed {
            feedsClosed = false
            wasDown = false
            startLifecycle()
            refreshListSoon()
            Task { await awareness.refreshNeedsYou() }
            Task { await awareness.refreshAway() }
            for session in open.values { Task { await session.kick() } }
            outbox.start()
        } else {
            kickAll()
        }
    }

    /// A refresh the system asked for while the app was out of sight (a background refresh, a silent push): the list and the
    /// chats that moved on are read and written, within `seconds`.
    func refreshInBackground(seconds: Double = 20) async -> Bool {
        // Reached only when the list was read now: a refresh that asked nothing (no link) or failed says so to the system.
        let began: Date = Date()
        // What waits on the owner is read beside the list: the badge is set from it while iOS gives the app its seconds.
        async let waiting: Void = awareness.refreshNeedsYou()
        await refreshList()
        await waiting
        guard link.state.online, lastListAt >= began else { return false }
        prefetch(budget: min(3, link.state.prefetchBudget), force: true)
        let task: Task<Void, Never>? = prefetchTask
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await task?.value }
            group.addTask { try? await Task.sleep(for: .seconds(seconds)) }
            await group.next()
            group.cancelAll()
        }
        prefetchTask?.cancel()
        return true
    }

    /// Forgets what the disk holds, as when the key is forgotten.
    func clearCache() async {
        drafts.clear()
        await outbox.clear()
        await cache.clear()
    }
}
