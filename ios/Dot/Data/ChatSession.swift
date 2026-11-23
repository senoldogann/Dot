import Foundation
import os

/// What a chat session tells the screen.
enum ChatOutput: Sendable {
    case state(ChatState)
    case connection(FeedState)
    /// The newest run's own record (its model, its status), the daemon's name for the chat and the choices its next
    /// message starts with (its model, reasoning level and mode).
    case run(RunRecord, title: String, choices: ChatChoices?)
    /// Whether the daemon may hold runs older than those the chat shows (a page of them is read on request).
    case older(hasMore: Bool)
    case failed(String)
    /// A moment of the daemon's lifecycle stream (`queue_changed`, `run_finished`, ...), for what the screen keeps
    /// beside the transcript (the queue).
    case lifecycle(String)
    /// What the disk held beside the transcript (the queue, the chat's schedules and reactions), so that a chat opened
    /// with no link shows them as they were.
    case aside(queue: QueueView?, automations: [ThreadAutomation]?, reactions: [ReactionRecord]?)
}

/// What reading a page of older runs did.
enum OlderOutcome: Equatable, Sendable {
    case nothing
    case loaded(added: Int, exhausted: Bool)
    case failed
}

/// One open chat: it reads what the disk holds and tells it at once, asks the daemon for what came after (a delta,
/// by event id), then follows the newest run's stream while it works, resuming where it stopped after a drop. It lives
/// off the main actor: decoding, reducing and writing to disk never wait for a frame, and the screen is handed one
/// new `ChatState` at most every 40 ms while words stream.
actor ChatSession {
    let threadId: String
    private let api: APIClient
    private let cache: ChatCache
    private var state: ChatState = ChatState()
    private var runs: [RunRecord] = []
    private var helperRuns: [RunRecord] = []
    private var events: [EventRecord] = []
    private var cursors: [String: Int] = [:]
    private var feed: EventFeed?
    private var feedRun: String?
    private var feedTask: Task<Void, Never>?
    private var output: AsyncStream<ChatOutput>.Continuation?
    private var publishTask: Task<Void, Never>?
    private var saveTask: Task<Void, Never>?
    private var refreshTask: Task<Void, Never>?
    private var startTask: Task<Void, Never>?
    private var retryTask: Task<Void, Never>?
    /// Bumped whenever a read succeeds or the chat is kicked: a retry that was waiting for its turn finds it changed and
    /// gives way.
    private var retryGeneration: Int = 0
    private var readFailures: Int = 0
    private var lastPublished: ChatState?
    private var asideQueue: QueueView?
    private var asideAutomations: [ThreadAutomation]?
    private var asideReactions: [ReactionRecord]?
    private var listUpdatedAt: Double?
    private let link: LinkMonitor?
    /// The session of the run's stream: made like the requests' (no timeout on the whole, 45 s on silence).
    private let streamSession: URLSession
    private var catching: Bool = false
    private var again: Bool = false
    private var stopped: Bool = false
    /// The daemon refused the phone's key (401, 403): nothing is asked again until the app comes back to the front.
    private var keyRefused: Bool = false
    /// How many events of each run did not read (the daemon's page or stream went past them): a row says so.
    private var unreadable: UnreadableEvents = UnreadableEvents()
    /// Where the next page of older runs starts (the `created_at` of the oldest run read so far); `nil` before any.
    private var olderCursor: Double?
    /// Whether the daemon has no run older than the oldest read; `nil` until a read says (a full first page may have more).
    private var olderExhausted: Bool?
    private var loadingOlder: Bool = false
    /// Callers waiting for a catch-up that was already reading when they asked.
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "chat")

    /// How many runs the newest page holds (`readDelta`) and how many each older page asks for.
    static let firstPage: Int = 12
    static let olderPage: Int = 20

    static let unreadableWords: String = "A message of this run could not be read and is missing here. The web UI shows it."

    /// How soon after the last change the screen is handed the state again while words stream.
    static let publishEvery: Duration = .milliseconds(40)

    /// More events than this after the last one held make the gap too large to replay: the newest `tailSize` are read
    /// instead (item 1371).
    static let gapPage: Int = 500
    static let tailSize: Int = 400

    init(threadId: String, api: APIClient, cache: ChatCache, link: LinkMonitor? = nil, listUpdatedAt: Double? = nil) {
        self.threadId = threadId
        self.api = api
        self.cache = cache
        self.link = link
        self.streamSession = APIClient.makeStreamSession(like: api.session)
        self.listUpdatedAt = listUpdatedAt
    }

    /// Starts the session; the stream ends when it is stopped.
    func start() -> AsyncStream<ChatOutput> {
        let (stream, continuation): (AsyncStream<ChatOutput>, AsyncStream<ChatOutput>.Continuation) = AsyncStream.makeStream(
            of: ChatOutput.self, bufferingPolicy: .unbounded)
        output = continuation
        continuation.onTermination = { [weak self] _ in Task { await self?.stop() } }
        startTask = Task { [weak self] in
            await self?.loadDisk()
            await self?.catchUp()
        }
        return stream
    }

    func stop() {
        stopped = true
        feedTask?.cancel()
        publishTask?.cancel()
        refreshTask?.cancel()
        retryTask?.cancel()
        startTask?.cancel()
        Task { [feed] in await feed?.stop() }
        feed = nil
        output?.finish()
        output = nil
        persistNow()
    }

    /// The chat was rewound (Edit from here): what is held of it is no longer the chat, so it is let go and read again
    /// from the daemon, whose list no longer holds the runs that were taken out.
    func reset() async {
        feedTask?.cancel()
        if let feed { Task { await feed.stop() } }
        feed = nil
        feedRun = nil
        runs = []
        helperRuns = []
        events = []
        cursors = [:]
        unreadable = UnreadableEvents()
        olderCursor = nil
        olderExhausted = nil
        state = ChatState()
        lastPublished = nil
        await cache.removeChat(threadId)
        publishNow()
        await catchUp()
    }

    /// The app is in front again or the network changed: reconnect at once, and read what came after meanwhile.
    func kick() {
        keyRefused = false
        retryGeneration += 1
        Task { [feed] in await feed?.kick() }
        scheduleRefresh(after: .zero)
    }

    /// The app went to the background for good: the stream is closed (a socket held open on a phone that sleeps is a
    /// drain), what is held is written at once, and nothing is asked until `kick`. The session stays, so that the screen
    /// the owner left open comes back with its rows.
    func suspend() {
        retryGeneration += 1
        refreshTask?.cancel()
        retryTask?.cancel()
        feedTask?.cancel()
        if let feed { Task { await feed.stop() } }
        feed = nil
        feedRun = nil
        persistNow()
    }

    /// Keeps what the screen holds beside the transcript with the chat, to draw again with no link.
    func keep(queue: QueueView?, automations: [ThreadAutomation]?, reactions: [ReactionRecord]?) {
        if let queue { asideQueue = queue }
        if let automations { asideAutomations = automations }
        if let reactions { asideReactions = reactions }
        scheduleSave()
    }

    /// Reads the chat once and keeps it, with no screen and no stream: what a prefetch and a background refresh do. The
    /// chat's state when the daemon was reached, else `nil`.
    func warm(listUpdatedAt updated: Double?) async -> ChatState? {
        await loadDisk()
        let reached: Bool = await readDelta()
        guard reached else { return nil }
        if let updated { listUpdatedAt = updated }
        await persist()
        return state
    }

    /// A moment of the daemon's lifecycle stream that may concern this chat (a run queued or ended, a question asked
    /// or answered): looks again shortly, once for a burst.
    func lifecycle(_ kind: String) {
        output?.yield(.lifecycle(kind))
        scheduleRefresh(after: .milliseconds(150))
    }

    /// Reads what the daemon has now; the ids of the approvals that still wait on the owner after it.
    @discardableResult
    func refresh() async -> Set<String> {
        await catchUp()
        return Set(state.pendingApprovals.map(\.approvalId))
    }

    /// The daemon took the owner's answer to a card (its own answer says how the card now stands): it shows so before
    /// the stream's event does.
    func answered(approval id: String, status: String, runId: String) {
        state.apply(EventRecord(
            id: 0, runId: runId, ts: Date().timeIntervalSince1970, kind: "approval_decided",
            data: ["approval_id": .string(id), "status": .string(status)]))
        publishNow()
    }

    /// The newest run was stopped from here: the state says so at once, before the stream's end does.
    func stopped(run id: String) {
        state.setStatus("cancelled", ofRun: id)
        publishNow()
    }

    // MARK: Reading

    private func loadDisk() async {
        guard let stored: StoredChat = await cache.loadChat(threadId) else { return }
        runs = stored.runs
        helperRuns = stored.helpers
        events = stored.events
        for event in stored.events { cursors[event.runId] = max(cursors[event.runId] ?? 0, event.id) }
        state = stored.state()
        unreadable = stored.unreadable ?? UnreadableEvents()
        for (run, ids) in unreadable.byRun { state.setUnreadable(ids.count, ofRun: run) }
        asideQueue = stored.queue
        asideAutomations = stored.automations
        asideReactions = stored.reactions
        listUpdatedAt = listUpdatedAt ?? stored.listUpdatedAt
        publishNow()
        output?.yield(.older(hasMore: hasOlderHint))
        output?.yield(.aside(queue: stored.queue, automations: stored.automations, reactions: stored.reactions))
    }

    private var hasOlderHint: Bool { olderExhausted.map { !$0 } ?? (runs.count >= Self.firstPage) }

    /// Reads one page of runs older than those shown, from the daemon's records, and puts them before the rows held (the
    /// screen keeps the reader's place). It reads on until a page adds a run of its own: a page may hold only helpers'.
    /// A call while one reads, or after the end was reached, does nothing.
    func loadOlder() async -> OlderOutcome {
        guard !loadingOlder, olderExhausted != true, !stopped else { return .nothing }
        loadingOlder = true
        defer { loadingOlder = false }
        var added: Int = 0
        do {
            for _ in 0..<5 {
                guard let cursor: Double = olderCursor ?? runs.map(\.createdAt).min() else { return .nothing }
                let page: [RunRecord] = try await api.runs(thread: threadId, limit: Self.olderPage, before: cursor)
                olderCursor = page.map(\.createdAt).min() ?? cursor
                olderExhausted = page.count < Self.olderPage
                added += state.prependOlder(page)
                if added > 0 || olderExhausted == true { break }
            }
            if added > 0 { publishNow() }
            output?.yield(.older(hasMore: hasOlderHint))
            return .loaded(added: added, exhausted: olderExhausted == true)
        } catch {
            logger.info("older runs could not be read: \(String(describing: error), privacy: .public)")
            if case DaemonRequestError.status(_, let code, _) = error, code == 401 || code == 403 { refuse() }
            return .failed
        }
    }

    private func scheduleRefresh(after delay: Duration) {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            try? await Task.sleep(for: delay)
            if Task.isCancelled { return }
            await self?.catchUp()
        }
    }

    /// Reads what the daemon has after what is held: the chat's newest runs, the newest run's detail and helpers, and
    /// each followed run's events after its cursor. A second call while one reads is made once more when it ends.
    private func catchUp() async {
        if catching {
            // One already reads: this call ends when that read has (and the one it makes again for this call), so that
            // what the caller looks at afterwards is what the daemon said after it asked.
            again = true
            await withCheckedContinuation { waiters.append($0) }
            return
        }
        catching = true
        repeat {
            again = false
            await readDelta()
        } while again && !stopped
        catching = false
        followIfWorking()
        let ending: [CheckedContinuation<Void, Never>] = waiters
        waiters = []
        for waiter in ending { waiter.resume() }
    }

    /// One read of what the daemon has after what is held. True when it got through. Everything that can be asked at once is
    /// asked at once: the chat's list of runs, its helpers, the questions that wait, its newest run's page, and the events
    /// after the cursors of the runs already held (those need no list to be asked), so that a bad link costs one round trip
    /// and not four in a row; the events draw as they arrive. A failure that may pass (the link, the daemon busy) is told
    /// as "reconnecting" and tried again by itself with a growing wait, and any other is the screen's to say.
    @discardableResult
    private func readDelta() async -> Bool {
        do {
            if let link, !link.state.online {
                throw DaemonRequestError.transport(path: "", underlying: URLError(.notConnectedToInternet))
            }
            let held: [String] = Array(state.rootRunIds.suffix(ChatState.followedRuns))
            let starts: [String: Int] = Dictionary(uniqueKeysWithValues: held.map { ($0, cursors[$0] ?? 0) })
            async let listing: [RunRecord] = api.runs(thread: threadId, limit: Self.firstPage)
            async let helpers: [RunRecord] = api.helpers(thread: threadId)
            async let waiting: [ApprovalRecord] = api.pendingApprovals()
            async let heldPage: RunDetail? = detail(of: held.last)
            // What is held is read on from its cursor while the list is on its way; each run's events are put on the
            // screen as they come, and the cursor moves only when they are in the state (a read that fails moves nothing).
            try await withThrowingTaskGroup(of: (String, EventRead).self) { group in
                for run in held {
                    let start: Int = starts[run] ?? 0
                    group.addTask { [self] in (run, try await readEvents(of: run, after: start)) }
                }
                for try await (run, read) in group {
                    commit(read, run: run)
                    publishSoon()
                }
            }
            let listed: [RunRecord] = try await listing
            // The first page says whether there is more behind it; once older pages were read, they say it.
            if olderExhausted == nil, listed.count < Self.firstPage { olderExhausted = true }
            for run in listed {
                if let at: Int = runs.firstIndex(where: { $0.id == run.id }) { runs[at] = run } else { runs.append(run) }
            }
            let roots: [RunRecord] = listed.filter { $0.parentRunId == nil }
            guard let newest: RunRecord = roots.first else {
                readSucceeded()
                return true
            }
            // Oldest first, each run with its own events before the next run's message: the rows come in the order
            // things happened.
            let followed: Set<String> = Set(roots.prefix(ChatState.followedRuns).map(\.id))
            for run in roots.reversed() {
                if followed.contains(run.id) {
                    if state.rootRunIds.contains(run.id) { state.setStatus(run.status, ofRun: run.id) } else { state.addRun(run) }
                    if !held.contains(run.id) {
                        commit(try await readEvents(of: run.id, after: cursors[run.id] ?? 0), run: run.id)
                    }
                } else if state.rootRunIds.contains(run.id) {
                    state.setStatus(run.status, ofRun: run.id)
                } else {
                    state.addSummary(run)
                }
            }
            let page: RunDetail? = try await heldPage
            let shown: RunDetail = try await page?.run.id == newest.id ? page! : api.detail(run: newest.id)
            helperRuns = try await helpers
            state.setHelpers(helperRuns)
            let mine: Set<String> = Set(state.rootRunIds + helperRuns.map(\.id))
            let open: [ApprovalRecord] = try await waiting.filter { mine.contains($0.runId) } + shown.questions
            state.setOpenQuestions(open)
            state.setStatus(shown.run.status, ofRun: shown.run.id)
            output?.yield(.run(shown.run, title: shown.title, choices: shown.choices))
            output?.yield(.older(hasMore: hasOlderHint))
            readSucceeded()
            publishNow()
            scheduleSave()
            return true
        } catch {
            if stopped || Task.isCancelled || (error as? DaemonRequestError).map({ RetryPolicy.isCancellation($0) }) == true { return false }
            logger.info("catch-up failed: \(String(describing: error), privacy: .public)")
            if case DaemonRequestError.status(_, let code, _) = error, code == 401 || code == 403 {
                refuse()
            } else if RetryPolicy.isWorthRepeating(error) {
                // What the phone holds stays on the screen; the line says it is trying, and it tries again by itself.
                readFailures += 1
                output?.yield(.connection(.reconnecting(attempt: readFailures, unauthorized: false)))
                scheduleRetry()
            } else {
                output?.yield(.failed(String(describing: error)))
            }
            // What came before the failure is on the screen and kept.
            publishNow()
            scheduleSave()
            return false
        }
    }

    private func detail(of run: String?) async throws -> RunDetail? {
        guard let run else { return nil }
        return try await api.detail(run: run)
    }

    private func readSucceeded() {
        readFailures = 0
        retryGeneration += 1
        retryTask?.cancel()
        retryTask = nil
        if feed == nil { output?.yield(.connection(.live)) }
    }

    /// Reads again after a failure: with the wait that grows (and with the link, when it is down, which comes first), for as
    /// long as the chat is open and the key is not refused.
    private func scheduleRetry() {
        guard !stopped, !keyRefused else { return }
        retryGeneration += 1
        let generation: Int = retryGeneration
        let wait: Duration = backoffDelay(attempt: readFailures)
        retryTask = Task { [weak self, link] in
            if let link, !link.state.online {
                _ = await link.waitForOnline(timeout: .seconds(30))
            } else {
                try? await Task.sleep(for: wait)
            }
            if Task.isCancelled { return }
            await self?.retry(generation: generation)
        }
    }

    private func retry(generation: Int) async {
        guard generation == retryGeneration, !stopped, !keyRefused else { return }
        await catchUp()
    }

    /// What a read of a run's events brought: its events, the ids that did not read, the highest id of the pages whether
    /// they read or not, and whether the gap was too long to replay (these are the newest events, not the ones after the
    /// cursor).
    struct EventRead: Sendable {
        var events: [EventRecord] = []
        var droppedIds: [Int] = []
        var top: Int?
        var wasTail: Bool = false
    }

    /// Reads a run's events after `start`, and changes nothing here: the caller puts them in the state with `commit`, so
    /// that a read cut short moves no cursor past events that were not drawn. A gap of more than a page (the phone slept
    /// through a long run) is not replayed: the daemon's newest `tailSize` stand in for it.
    private func readEvents(of runId: String, after start: Int) async throws -> EventRead {
        var read: EventRead = EventRead()
        var after: Int = start
        var first: Bool = true
        while true {
            let page: EventsPage = try await api.events(run: runId, after: after, limit: first ? Self.gapPage : 2000)
            if first, page.size >= Self.gapPage, let tail: EventsPage = try await api.eventsTail(run: runId, count: Self.tailSize) {
                logger.info("a gap of \(page.size)+ events of \(runId, privacy: .public) was read as its newest \(tail.events.count)")
                return EventRead(events: tail.events, droppedIds: tail.droppedIds, top: tail.topId, wasTail: true)
            }
            first = false
            read.events.append(contentsOf: page.events)
            read.droppedIds.append(contentsOf: page.droppedIds)
            if let top: Int = page.topId {
                after = top
                read.top = max(read.top ?? 0, top)
            }
            // A page that said no id at all cannot be walked past: it ends the read rather than ask for it again.
            if page.size < 2000 || page.topId == nil { return read }
        }
    }

    /// Puts what was read into the state: the ids that did not read are kept as a set (met again, they are not counted
    /// again), the cursor goes past the whole page (the unread elements included, so that they are not asked for again),
    /// and the rows are drawn. The tail of a long gap replaces what was held of the run, and a line says the steps before
    /// it are not shown here.
    private func commit(_ read: EventRead, run runId: String) {
        if read.wasTail {
            let wanted: Set<Int> = Set(read.events.map(\.id))
            events.removeAll { $0.runId == runId && !wanted.contains($0.id) }
            dropped(runId: runId)
        }
        if unreadable.add(read.droppedIds, run: runId) > 0 { output?.yield(.failed(Self.unreadableWords)) }
        if let top: Int = read.top { cursors[runId] = max(cursors[runId] ?? 0, top) }
        state.apply(read.events)
        state.setUnreadable(unreadable.count(run: runId), ofRun: runId)
        events.append(contentsOf: read.events.filter { $0.id > 0 })
    }

    /// The rows of a run whose middle was not read: rebuilt from the records and the events held, with a line that says
    /// so.
    private func dropped(runId: String) {
        olderCursor = nil
        olderExhausted = nil
        var built: ChatState = ChatState.build(runs: runs, helpers: helperRuns, events: events.filter { $0.runId != runId })
        built.upsertNote(id: "n:gap:\(runId)", words: "Some steps of this run are not shown here. The web UI has them all.", ts: Date().timeIntervalSince1970)
        state = built
    }

    // MARK: Following

    /// The key was refused: the screen says so, and no stream is opened again by this session until a kick.
    private func refuse() {
        keyRefused = true
        feedTask?.cancel()
        if let feed { Task { await feed.stop() } }
        feed = nil
        feedRun = nil
        output?.yield(.connection(.reconnecting(attempt: 0, unauthorized: true)))
    }

    private func followIfWorking() {
        guard !keyRefused else { return }
        guard !stopped, let newest: String = state.newestRunId, state.isWorking else {
            if let feed { Task { await feed.stop() }; self.feed = nil; feedRun = nil }
            // A read that failed said "reconnecting" and tries again: that line is not replaced by "ended".
            if readFailures == 0 { output?.yield(.connection(.ended)) }
            return
        }
        if feedRun == newest, feed != nil { return }
        feedTask?.cancel()
        if let feed { Task { await feed.stop() } }
        let api: APIClient = api
        let after: Int = max(state.lastEventId, cursors[newest] ?? 0)
        let feed: EventFeed = EventFeed(
            session: streamSession, after: after, link: link, isFinal: { $0.kind == "run_finished" && $0.runId == newest },
            open: { after in runStreamRequest(api: api, runId: newest, after: after) })
        self.feed = feed
        feedRun = newest
        feedTask = Task { [weak self] in
            let updates: AsyncStream<FeedUpdate> = await feed.start()
            for await update in updates {
                await self?.take(update, runId: newest)
            }
            await self?.feedEnded(runId: newest)
        }
    }

    private func take(_ update: FeedUpdate, runId: String) {
        switch update {
        case .events(let received):
            for event in received where event.id > 0 { cursors[event.runId] = max(cursors[event.runId] ?? 0, event.id) }
            events.append(contentsOf: received.filter { $0.id > 0 })
            if events.count > 3 * StoredChat.tail { events.removeFirst(events.count - 2 * StoredChat.tail) }
            state.apply(received)
            publishSoon()
            scheduleSave()
        case .state(let feedState):
            if case .reconnecting(_, true) = feedState { keyRefused = true }
            // After a refused key the feed's own end must not replace the line that says so.
            if keyRefused, feedState == .ended { break }
            output?.yield(.connection(feedState))
        case .notice:
            break
        case .unreadable(let eventId):
            // Handed over again by a reconnect, it is the same event: counted once.
            if unreadable.add([eventId ?? UnreadableEvents.unknownId], run: runId) > 0 {
                state.setUnreadable(unreadable.count(run: runId), ofRun: runId)
                publishSoon()
                scheduleSave()
                output?.yield(.failed(Self.unreadableWords))
            }
        }
    }

    private func feedEnded(runId: String) async {
        guard feedRun == runId else { return }
        feed = nil
        feedRun = nil
        // The run's end was told by its stream; the daemon's record says how it ended and what is left.
        if !stopped, !keyRefused { await catchUp() }
    }

    // MARK: Telling and keeping

    private func publishNow() {
        publishTask?.cancel()
        publishTask = nil
        // A state the screen already has is not handed over again: nothing is re-drawn for a read that found nothing new.
        if lastPublished == state { return }
        lastPublished = state
        output?.yield(.state(state))
    }

    private func publishSoon() {
        guard publishTask == nil else { return }
        publishTask = Task { [weak self] in
            try? await Task.sleep(for: Self.publishEvery)
            if Task.isCancelled { return }
            await self?.publishNow()
        }
    }

    private func scheduleSave() {
        guard saveTask == nil else { return }
        saveTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(600))
            if Task.isCancelled { return }
            await self?.persistNow()
        }
    }

    private func persistNow() {
        saveTask?.cancel()
        saveTask = nil
        guard let chat: StoredChat = snapshot() else { return }
        let cache: ChatCache = cache
        Task.detached { await cache.saveChat(chat) }
    }

    /// Writes the chat and returns when it is on disk (a background refresh is let to end only after).
    private func persist() async {
        saveTask?.cancel()
        saveTask = nil
        guard let chat: StoredChat = snapshot() else { return }
        await cache.saveChat(chat)
    }

    private func snapshot() -> StoredChat? {
        guard !runs.isEmpty else { return nil }
        var chat: StoredChat = StoredChat(
            threadId: threadId, runs: Array(runs.sorted { $0.createdAt > $1.createdAt }.prefix(12)), helpers: Array(helperRuns.prefix(30)), events: events,
            savedAt: Date().timeIntervalSince1970, unreadable: unreadable.isEmpty ? nil : unreadable)
        chat.queue = asideQueue
        chat.automations = asideAutomations
        chat.reactions = asideReactions
        chat.listUpdatedAt = listUpdatedAt
        return chat
    }
}
