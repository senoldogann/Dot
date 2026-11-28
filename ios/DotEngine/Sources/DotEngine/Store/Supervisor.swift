import Foundation
import os

/// The one owner of every stream of a daemon and of the outbox's pace (plan §5, R-f §4 #3). Views never start a
/// reconnect of their own.
///
/// - Each stream (the list's, each open chat's) is opened with the view read beside it, from the cursors held.
/// - A broken stream is opened again after a jittered wait from 0.5 s up to 15 s; the count starts again only after a
///   connection stayed up 30 s, so a link that passes one keepalive and dies does not hammer the Mac.
/// - A stream silent for 12 s (the new streams write a keepalive every 5 s) is taken for dead and replaced.
/// - In the background nothing streams: every stream is ended at once (a socket left open while iOS suspends the app
///   still wakes the phone's radio for every keepalive and word), and back in front each is opened again from its
///   cursors, which reads each open chat's view once and the list's: the picture catches up in one read, not in a
///   replay of what was missed.
/// - The link: lost, the streams are let go (the picture stays, as not live); back, every wait is cut short; a change
///   of interface (Wi-Fi to cellular) opens every stream again.
public actor Supervisor {
    enum Key: Hashable, Sendable {
        case list
        case chat(String)
    }

    private enum Ending: Sendable {
        /// The connection ended; it had been up this long.
        case ended(upFor: Double)
        /// The chat is gone: its stream is not opened again.
        case gone
        /// Nothing more can be done until the owner acts (key refused, daemon too old).
        case halted
    }

    public static let tail: Int = 20

    private let api: DaemonAPI
    private let store: SyncStore
    private let outbox: Outbox
    private let link: LinkWatcher
    private let clock: any EngineClock
    private let jitter: @Sendable () -> Double
    private let waker: Waker = Waker()
    private var loops: [Key: Task<Void, Never>] = [:]
    private var readers: [Key: Task<Void, Never>] = [:]
    private var beats: [Key: Heartbeat] = [:]
    private var immediate: Set<Key> = []
    private var viewFailures: [String: Int] = [:]
    /// The streams ended when the app went to the background, opened again when it comes back.
    private var paused: Set<Key> = []
    /// When the app left the screen (`nil` on it): an opened chat is marked seen only while the app is on the screen.
    private var backgroundAt: Double?
    private var lastLink: LinkState
    private var halted: Bool = false
    private var watchers: [Task<Void, Never>] = []
    /// The revision of each open chat's row it was last marked seen at (item 1905).
    private var seenSent: [String: Int] = [:]
    /// How many screens hold each chat open (item 1913): a chat shown twice (a switch back to it before the old screen
    /// went, a chat and its helper's) stays open until the last one leaves, whichever order their calls arrive in.
    private var openCounts: [String: Int] = [:]
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "supervisor")

    public init(api: DaemonAPI, store: SyncStore, outbox: Outbox, link: LinkWatcher, clock: any EngineClock, jitter: @escaping @Sendable () -> Double) {
        self.api = api
        self.store = store
        self.outbox = outbox
        self.link = link
        self.clock = clock
        self.jitter = jitter
        self.lastLink = link.state
    }

    // MARK: Life

    /// Starts the list's stream, the outbox (which sends nothing before the daemon was heard) and the watch on the link.
    public func start() async {
        guard loops[.list] == nil else { return }
        let outbox: Outbox = outbox
        let store: SyncStore = store
        watchers.append(Task { for await items in await outbox.changes() { await store.outbox(items) } })
        watchers.append(Task { for await outcome in await outbox.outcomes() { await store.outcome(outcome) } })
        watchers.append(Task { [weak self, link] in
            for await state in link.updates() { await self?.linkChanged(state) }
        })
        await store.set(online: link.state.online)
        run(.list)
        await outbox.start()
    }

    /// Ends every stream and the outbox's sending (the key was forgotten, the app is torn down).
    public func stop() async {
        for task in loops.values { task.cancel() }
        for task in readers.values { task.cancel() }
        for task in watchers { task.cancel() }
        loops = [:]
        readers = [:]
        watchers = []
        await outbox.suspend()
    }

    /// Opens a chat: its picture from the cache, its stream and its view.
    public func openChat(_ thread: String) async {
        openCounts[thread, default: 0] += 1
        await store.chatOpened(thread)
        await seeOpenChats()
        guard loops[.chat(thread)] == nil else { return }
        run(.chat(thread))
    }

    /// Leaves a chat: once no screen holds it, its stream ends and its picture is saved.
    public func closeChat(_ thread: String) async {
        let left: Int = (openCounts[thread] ?? 0) - 1
        openCounts[thread] = left > 0 ? left : nil
        guard left <= 0 else { return }
        end(.chat(thread))
        paused.remove(.chat(thread))
        await store.chatClosed(thread)
    }

    /// The app left the screen: every stream ends (the daemon sees its connection close) and the outbox stops sending.
    public func enterBackground() async {
        backgroundAt = clock.now()
        paused.formUnion(loops.keys)
        for key in paused { end(key) }
        await store.persistAll()
        await outbox.suspend()
    }

    /// The app is back: the streams ended in the background are opened again from their cursors.
    public func enterForeground() async {
        await outbox.start()
        backgroundAt = nil
        await seeOpenChats()
        let resumed: Set<Key> = paused
        paused = []
        // The list's first: its status read tells a reset daemon before a chat's stream reads anything of it.
        for key in resumed.sorted(by: { first, _ in first == .list }) where loops[key] == nil && !halted { run(key) }
        await waker.wake()
    }

    private func replaceAll() {
        for key in loops.keys { replace(key) }
    }

    /// Ends the connection of `key` and opens it again at once, from its cursors.
    private func replace(_ key: Key) {
        immediate.insert(key)
        readers[key]?.cancel()
    }

    private func end(_ key: Key) {
        loops.removeValue(forKey: key)?.cancel()
        readers.removeValue(forKey: key)?.cancel()
        beats[key] = nil
    }

    private func linkChanged(_ state: LinkState) async {
        let was: LinkState = lastLink
        lastLink = state
        await store.set(online: state.online)
        if !state.online {
            await store.set(transport: .offline)
            for reader in readers.values { reader.cancel() }
            return
        }
        if !was.online {
            for key in loops.keys { immediate.insert(key) }
            await waker.wake()
            await outbox.nudge()
        } else if state.interface != was.interface {
            replaceAll()
        }
    }

    // MARK: Connections

    private func run(_ key: Key) {
        loops[key] = Task { [weak self] in await self?.loop(key) }
    }

    private func loop(_ key: Key) async {
        var attempt: Int = 0
        while !Task.isCancelled, !halted {
            if !link.state.online {
                await store.set(transport: .offline)
                let seen: Int = await waker.current
                await waker.wait(seconds: 30, clock: clock, seen: seen)
                continue
            }
            let seen: Int = await waker.current
            let ending: Ending = await connect(key)
            switch ending {
            case .gone, .halted:
                loops[key] = nil
                return
            case .ended(let upFor):
                if upFor >= StreamRules.stableAfter { attempt = 0 }
            }
            if Task.isCancelled { return }
            if immediate.remove(key) != nil {
                attempt = 0
                continue
            }
            attempt += 1
            if key == .list, link.state.online { await store.set(transport: .reconnecting(attempt: attempt)) }
            await waker.wait(seconds: reconnectWait(attempt: attempt, jitter: jitter()), clock: clock, seen: seen)
        }
    }

    /// One connection of a stream, from opening to its end.
    private func connect(_ key: Key) async -> Ending {
        let connection: Int
        let path: String
        var query: [URLQueryItem] = []
        switch key {
        case .list:
            do {
                let status: DaemonStatus = try await api.status()
                await store.reached(at: clock.now())
                if await store.change(to: status.identity) == .reset {
                    // The unsent messages are kept for the owner first, then the outbox lets go of its doings for the old
                    // daemon's chats, then the new daemon is taken as the copies' (and the outbox may send): a kill at any
                    // point finds the reset again, and nothing is told twice.
                    await store.told(resetWith: await outbox.unsentForReset())
                    await outbox.dropForReset()
                }
                await store.daemon(status.identity)
                await outbox.nudge()
            } catch {
                if let ending: Ending = await halt(on: error) { return ending }
                return .ended(upFor: 0)
            }
            let (opened, effects): (Int, [ListEffect]) = await store.listOpening()
            connection = opened
            path = "/api/threads/stream"
            perform(effects)
        case .chat(let thread):
            let (opened, cursors, effects): (Int, String, [SyncEffect]) = await store.chatOpening(thread)
            connection = opened
            path = "/api/threads/\(thread)/stream"
            if !cursors.isEmpty { query = [URLQueryItem(name: "cursors", value: cursors)] }
            perform(effects)
        }
        let bytes: URLSession.AsyncBytes
        do {
            bytes = try await api.openStream(path: path, query: query)
        } catch {
            logger.warning("stream open failed path=\(path, privacy: .public) error=\(error.description, privacy: .public)")
            await broke(key, connection: connection)
            if let ending: Ending = await halt(on: error) { return ending }
            if error.statusCode == 404, case .chat(let thread) = key {
                // A chat deleted, or a cursor of a run it no longer has (a rewind): the view says which.
                return await viewAfterMissingStream(thread)
            }
            return .ended(upFor: 0)
        }
        Traffic.shared.streamOpened()
        let began: Double = clock.now()
        let beat: Heartbeat = Heartbeat(last: began)
        beats[key] = beat
        switch key {
        case .list:
            await store.listOpened(connection)
            await store.set(transport: .up)
            await outbox.nudge()
        case .chat(let thread):
            await store.chatOpened(thread, connection: connection)
        }
        let reader: Task<Void, Never> = Task { [weak self, clock] in
            await Self.read(bytes, beat: beat, clock: clock) { [weak self] frame in await self?.apply(frame, key: key, connection: connection) }
        }
        readers[key] = reader
        let watchdog: Task<Void, Never> = Task { [clock] in
            await Self.watch(beat: beat, clock: clock, reader: reader)
        }
        // The reader is a task of its own, which the loop's cancel does not reach: `end` (the app went to the background)
        // may come during an await above, after the stream opened and before `readers[key]` was set, and the stream would
        // stay open. The loop's cancel, then or later, ends the reader too.
        await withTaskCancellationHandler {
            await reader.value
        } onCancel: {
            reader.cancel()
        }
        watchdog.cancel()
        if readers[key] == reader { readers[key] = nil }
        if beats[key] === beat { beats[key] = nil }
        if key == .list { await store.reached(at: beat.last) }
        if !Task.isCancelled, immediate.contains(key) == false { Traffic.shared.streamBroke() }
        await broke(key, connection: connection)
        if case .chat(let thread) = key, await store.chat(thread)?.freshness == .deleted { return .gone }
        return .ended(upFor: clock.now() - began)
    }

    private func broke(_ key: Key, connection: Int) async {
        switch key {
        case .list: await store.listBroke(connection)
        case .chat(let thread): await store.chatBroke(thread, connection: connection)
        }
    }

    /// A refused key or an old daemon stop everything until the owner acts; `nil` for any other failure.
    private func halt(on error: DaemonRequestError) async -> Ending? {
        guard let code: Int = error.statusCode else { return nil }
        if code == 401 || code == 403 {
            halted = true
            await store.set(transport: .keyRefused)
            return .halted
        }
        if code == 404, case .status(let path, _, _) = error, path == "/api/threads/stream" || path == "/api/status" {
            halted = true
            await store.set(transport: .daemonTooOld)
            return .halted
        }
        return nil
    }

    private func viewAfterMissingStream(_ thread: String) async -> Ending {
        await readView(thread)
        return await store.chat(thread)?.freshness == .deleted ? .gone : .ended(upFor: 0)
    }

    /// Reads a stream's bytes line by line; every line (a keepalive too) is a heartbeat.
    private static func read(
        _ bytes: URLSession.AsyncBytes, beat: Heartbeat, clock: any EngineClock, frame: @Sendable (StreamFrame) async -> Void
    ) async {
        var parser: SSEParser = SSEParser()
        var line: [UInt8] = []
        do {
            for try await byte in bytes {
                line.append(byte)
                guard byte == 0x0A else { continue }
                beat.touch(clock.now())
                Traffic.shared.received(line.count)
                for message in parser.feed(line) { await frame(readFrame(message)) }
                line.removeAll(keepingCapacity: true)
            }
        } catch {
            // The stream broke (or was cancelled): the loop opens it again.
        }
    }

    /// Ends the reader once the stream was silent for the silence limit.
    private static func watch(beat: Heartbeat, clock: any EngineClock, reader: Task<Void, Never>) async {
        while !Task.isCancelled {
            let due: Double = beat.last + StreamRules.silenceLimit - clock.now()
            if due <= 0 {
                reader.cancel()
                return
            }
            do {
                try await clock.sleep(seconds: due)
            } catch {
                return
            }
        }
    }

    // MARK: Frames and effects

    private func apply(_ frame: StreamFrame, key: Key, connection: Int) async {
        switch key {
        case .list:
            perform(await store.listFrame(frame, connection: connection))
            await seeOpenChats()
        case .chat(let thread): perform(await store.chatFrame(thread, frame, connection: connection))
        }
    }

    private func perform(_ effects: [ListEffect]) {
        for effect in effects {
            switch effect {
            case .readList: Task { await self.readList() }
            }
        }
    }

    private func perform(_ effects: [SyncEffect]) {
        for effect in effects {
            switch effect {
            case .readView(let thread): Task { await self.readView(thread) }
            case .closeStream(let thread): end(.chat(thread))
            }
        }
    }

    /// Marks seen on the Mac the chats open on the phone whose row says unread (`PATCH {seen: true}`, as the web's
    /// sidebar does), so they leave "unread" in the list; never while the app is away from the screen. A mark that fails
    /// is tried again at the list's next look.
    private func seeOpenChats() async {
        guard backgroundAt == nil else { return }
        let picture: EngineSnapshot = await store.snapshot
        for row in chatsToMarkSeen(rows: picture.list.rows, open: Set(picture.chats.keys), sent: seenSent) {
            let thread: String = row.summary.id
            seenSent[thread] = row.revision
            Task {
                do throws(DaemonRequestError) {
                    try await api.markSeen(thread: thread)
                } catch {
                    logger.warning("a chat could not be marked seen thread=\(thread, privacy: .public) error=\(error.description, privacy: .public)")
                    self.forgetSeen(thread)
                }
            }
        }
    }

    private func forgetSeen(_ thread: String) { seenSent[thread] = nil }

    private func readList() async {
        let connection: Int = await store.listAsked()
        do {
            perform(await store.listLoaded(try await api.threadsView(), connection: connection))
            await seeOpenChats()
            // A new chat waits for a live list: it goes now rather than at its next look.
            if await store.listIsLive { await outbox.nudge() }
        } catch {
            logger.warning("list read failed error=\(error.description, privacy: .public)")
            await store.listFailed()
            if error.statusCode == 404 {
                halted = true
                await store.set(transport: .daemonTooOld)
                end(.list)
            }
        }
    }

    /// Reads a chat's view; a read that fails while the chat is open is tried again with the streams' backoff.
    private func readView(_ thread: String) async {
        guard let connection: Int = await store.chatViewAsked(thread) else { return }
        do {
            let view: ThreadView = try await api.threadView(thread, tail: Self.tail)
            viewFailures[thread] = nil
            perform(await store.chatViewLoaded(thread, view: view, connection: connection))
        } catch {
            logger.warning("view read failed thread=\(thread, privacy: .public) error=\(error.description, privacy: .public)")
            let missing: Bool = error.statusCode == 404
            perform(await store.chatViewFailed(thread, missing: missing))
            if missing || error.statusCode == 401 || error.statusCode == 403 { return }
            let failures: Int = (viewFailures[thread] ?? 0) + 1
            viewFailures[thread] = failures
            let seen: Int = await waker.current
            await waker.wait(seconds: reconnectWait(attempt: failures, jitter: jitter()), clock: clock, seen: seen)
            guard loops[.chat(thread)] != nil, beats[.chat(thread)] != nil else { return }
            await readView(thread)
        }
    }
}

/// When a stream last said anything, shared by its reader and its watchdog.
final class Heartbeat: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var at: Double

    init(last: Double) {
        at = last
    }

    var last: Double { lock.withLock { at } }

    func touch(_ now: Double) { lock.withLock { at = max(at, now) } }
}
