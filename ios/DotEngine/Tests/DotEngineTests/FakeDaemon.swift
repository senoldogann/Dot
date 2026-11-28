import Foundation

@testable import DotEngine

/// A daemon in the test process that speaks the sync contract of item 1686 (`docs/phone.md`, "A chat kept in sync"):
/// the list's and a chat's views at a revision, the list's stream (`thread_changed`, lifecycle kinds, `resync`), and the
/// chat's stream (each named run from its cursor, a little behind it as the real one reads, the runs at work from their
/// start, `synchronized`, then live). It keeps a message's key per kind as `send_once` does, refuses a steer for a run
/// that is not at work as `RunConflict`, and misbehaves as the test says. A `URLProtocol` carries the requests: no
/// socket, no delay.
final class FakeDaemon: @unchecked Sendable {
    struct Seen: Sendable {
        let method: String
        let path: String
        let query: [String: String]
        let body: Data?
    }

    struct Chat {
        var record: ThreadRecord
        var revision: Int
        var pinnedAt: Double?
        var queue: [QueuedMessageView]
        var held: Bool
        var asks: [ApprovalRecord]
        /// Its latest run ended after the owner last saw it (item 131); `PATCH {seen: true}` clears it.
        var unread: Bool
    }

    let host: DaemonHost
    let version: String
    /// The id of its database: each fake daemon is a database of its own, as a reset Dot is.
    let store: String = "store_\(UUID().uuidString.prefix(8).lowercased())"
    private let lock: NSLock = NSLock()
    private var chats: [String: Chat] = [:]
    private var runs: [String: RunRecord] = [:]
    private var events: [String: [EventRecord]] = [:]
    private var helpers: [String: [HelperView]] = [:]
    private var keys: [String: String] = [:]
    private var log: [Seen] = []
    private var streams: [UUID: FakeStream] = [:]
    private var watchers: [UUID: AsyncStream<Void>.Continuation] = [:]
    private var nextEvent: Int = 1000
    private var revisionCounter: Int = 1
    private var nextId: Int = 1
    /// The failures a test asked for, each with the requests it answers (by method and path), in order.
    private var failures: [(matches: @Sendable (String, String) -> Bool, status: Int, body: String)] = []
    private var lostAnswers: Int = 0
    private(set) var acceptedTexts: [String] = []
    private var uploads: Int = 0
    private var decided: [String: String] = [:]
    private var voiceBytes: [String: Int] = [:]
    private var voicePieces: [String: Int] = [:]

    var uploadCount: Int { lock.withLock { uploads } }

    /// An approval answered elsewhere (on the Mac).
    func decideElsewhere(_ approval: String, status: String) { lock.withLock { decided[approval] = status } }

    private func approvalRecord(_ id: String, status: String) -> ApprovalRecord {
        ApprovalRecord(id: id, runId: "run_1", createdAt: 1, status: status, tool: "shell", summary: "", severity: "medium", args: nil, note: nil, keptUntil: nil)
    }
    /// No request gets anywhere (`notConnectedToInternet`).
    var offline: Bool {
        get { lock.withLock { isOffline } }
        set { lock.withLock { isOffline = newValue } }
    }
    private var isOffline: Bool = false
    /// Leaves an attachment request pending until its URLSession task is cancelled.
    var holdUploads: Bool {
        get { lock.withLock { holdingUploads } }
        set { lock.withLock { holdingUploads = newValue } }
    }
    private var holdingUploads: Bool = false

    /// The daemon from before item 1686: the sync calls answer 404.
    var old: Bool = false
    /// A chat's stream opens but says nothing (not its replay, not `synchronized`) until `releaseStreamStarts`.
    var holdStreamStarts: Bool {
        get { lock.withLock { holding } }
        set { lock.withLock { holding = newValue } }
    }
    private var holding: Bool = false
    private var held: [(FakeStream, Data)] = []

    func releaseStreamStarts() {
        let waiting: [(FakeStream, Data)] = lock.withLock {
            holding = false
            defer { held = [] }
            return held
        }
        for (stream, data) in waiting { stream.send(data) }
    }

    /// Writes a chat stream's start now, or keeps it while starts are held.
    func start(_ stream: FakeStream, with data: Data) {
        let now: Bool = lock.withLock {
            guard holding else { return true }
            held.append((stream, data))
            return false
        }
        if now { stream.send(data) }
    }

    init(version: String) {
        let name: String = "fake-\(UUID().uuidString.prefix(8).lowercased()).ts.net"
        host = DaemonHost(name: name, origin: URL(string: "https://\(name)")!)
        self.version = version
        FakeProtocol.register(self, host: name)
    }

    deinit { FakeProtocol.unregister(host: host.name) }

    func session() -> URLSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FakeProtocol.self]
        return URLSession(configuration: configuration)
    }

    func api() -> DaemonAPI {
        let session: URLSession = session()
        return DaemonAPI(host: host, key: "phone-key", session: session, streamSession: session)
    }

    // MARK: Watching

    func changes() -> AsyncStream<Void> {
        let (stream, continuation): (AsyncStream<Void>, AsyncStream<Void>.Continuation) = AsyncStream.makeStream(
            of: Void.self, bufferingPolicy: .bufferingNewest(1))
        let id: UUID = UUID()
        lock.withLock { watchers[id] = continuation }
        continuation.onTermination = { [weak self] _ in self?.lock.withLock { self?.watchers[id] = nil } }
        return stream
    }

    private func told() {
        let all: [AsyncStream<Void>.Continuation] = lock.withLock { Array(watchers.values) }
        for one in all { one.yield() }
    }

    var requests: [Seen] { lock.withLock { log } }

    func count(_ method: String, _ part: String) -> Int {
        requests.filter { $0.method == method && $0.path.contains(part) }.count
    }

    var openStreams: Int { lock.withLock { streams.count } }

    func openStreams(of path: String) -> Int { lock.withLock { streams.values.filter { $0.path == path }.count } }

    // MARK: Scripting

    @discardableResult
    func addChat(_ id: String, title: String) -> ThreadRecord {
        let record: ThreadRecord = ThreadRecord(id: id, title: title, source: "ui", createdAt: 1, updatedAt: 1, agentId: nil)
        lock.withLock {
            revisionCounter += 1
            chats[id] = Chat(record: record, revision: revisionCounter, pinnedAt: nil, queue: [], held: false, asks: [], unread: false)
        }
        return record
    }

    /// A run of a chat; `status` `running` for one at work. Stores its `run_queued` (and `run_finished` for an ended one).
    @discardableResult
    func addRun(_ id: String, thread: String, goal: String, status: String, parent: String?) -> RunRecord {
        let at: Double = lock.withLock { Double(nextEvent) }
        let record: RunRecord = RunRecord(
            id: id, threadId: thread, goal: goal, mode: "task", model: "scripted", status: status, createdAt: at,
            finishedAt: nil, result: status == "done" ? "Done: \(goal)" : "", parentRunId: parent, source: "ui", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        lock.withLock { runs[id] = record }
        var queued: [String: JSONValue] = ["goal": .string(goal)]
        if let parent { queued["parent_run_id"] = .string(parent) }
        say(id, "run_queued", queued)
        if status == "done" { say(id, "run_finished", ["status": .string("done"), "result": .string(record.result)]) }
        bump(thread, tell: true)
        return record
    }

    /// A helper a run of `parentThread` started in a chat of its own.
    func addHelper(_ id: String, parentRun: String, parentThread: String, title: String) {
        addChat("thr_\(id)", title: title)
        addRun(id, thread: "thr_\(id)", goal: title, status: "running", parent: parentRun)
        let view: HelperView = HelperView(
            runId: id, parentRunId: parentRun, threadId: "thr_\(id)", agentId: nil, title: title, status: "running", startedAt: 100,
            finishedAt: nil, provider: "claude_code", model: "sonnet", effort: "medium", step: nil, blocking: true, stop: "/api/runs/\(id)/cancel")
        lock.withLock { helpers[parentThread, default: []].append(view) }
        bump(parentThread, tell: true)
    }

    /// A stored event of a run, told to every stream that follows it.
    @discardableResult
    func say(_ run: String, _ kind: String, _ data: [String: JSONValue]) -> EventRecord {
        let event: EventRecord = lock.withLock {
            nextEvent += 1
            let made: EventRecord = EventRecord(id: nextEvent, runId: run, ts: Double(nextEvent), kind: kind, data: data)
            events[run, default: []].append(made)
            if kind == "run_finished", let record: RunRecord = runs[run] {
                runs[run] = RunRecord(
                    id: record.id, threadId: record.threadId, goal: record.goal, mode: record.mode, model: record.model,
                    status: data["status"]?.string ?? "done", createdAt: record.createdAt, finishedAt: Double(nextEvent),
                    result: data["result"]?.string ?? "", parentRunId: record.parentRunId, source: record.source, effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
            }
            return made
        }
        push(event)
        return event
    }

    /// A stored event with an id the test picks (one told late, lower than one sent already).
    func sayWithId(_ id: Int, run: String, _ kind: String, _ data: [String: JSONValue]) {
        let event: EventRecord = EventRecord(id: id, runId: run, ts: Double(id), kind: kind, data: data)
        lock.withLock {
            events[run, default: []].append(event)
            events[run]?.sort { $0.id < $1.id }
        }
        push(event)
    }

    /// Words being written (id 0).
    func delta(_ run: String, _ text: String, step: Int) {
        push(EventRecord(id: 0, runId: run, ts: 0, kind: "delta", data: ["kind": .string("text"), "text": .string(text), "step": .int(step)]))
    }

    /// Thinking being written (id 0).
    func thinking(_ run: String, _ text: String, step: Int) {
        push(EventRecord(id: 0, runId: run, ts: 0, kind: "delta", data: ["kind": .string("thinking"), "text": .string(text), "step": .int(step)]))
    }

    /// Moves a chat's revision; `tell` false is a change whose `thread_changed` was lost.
    /// The chat's latest run ended unseen: its row says unread, told to the list.
    func markUnread(_ thread: String) {
        lock.withLock { chats[thread]?.unread = true }
        bump(thread, tell: true)
    }

    func isUnread(_ thread: String) -> Bool { lock.withLock { chats[thread]?.unread ?? false } }

    func bump(_ thread: String, tell: Bool) {
        let revision: Int? = lock.withLock {
            guard chats[thread] != nil else { return nil }
            revisionCounter += 1
            chats[thread]?.revision = revisionCounter
            return revisionCounter
        }
        guard let revision, tell else { return }
        pushChanged(ThreadChanged(threadId: thread, revision: revision, deleted: false))
    }

    func rename(_ thread: String, to title: String, tell: Bool) {
        lock.withLock {
            guard let record: ThreadRecord = chats[thread]?.record else { return }
            chats[thread]?.record = ThreadRecord(
                id: record.id, title: title, source: record.source, createdAt: record.createdAt, updatedAt: 2, agentId: nil)
        }
        bump(thread, tell: tell)
    }

    /// A queue changed with no word on any stream (as when the bus dropped it, or the phone was away).
    func setQueueSilently(_ thread: String, texts: [String]) {
        lock.withLock {
            chats[thread]?.queue = texts.enumerated().map { index, text in
                QueuedMessageView(id: "que_\(thread)_\(index)", threadId: thread, position: index, text: text, createdAt: 1, source: "ui")
            }
        }
        bump(thread, tell: false)
    }

    func deleteChat(_ thread: String) {
        let revision: Int = lock.withLock {
            chats[thread] = nil
            revisionCounter += 1
            return revisionCounter
        }
        pushChanged(ThreadChanged(threadId: thread, revision: revision, deleted: true))
    }

    /// A frame as raw bytes, to the streams of `thread` (`nil`: the list's).
    func pushRaw(_ text: String, thread: String?) {
        let path: String = thread.map { "/api/threads/\($0)/stream" } ?? "/api/threads/stream"
        let targets: [FakeStream] = lock.withLock { streams.values.filter { $0.path == path } }
        for stream in targets { stream.send(Data(text.utf8)) }
    }

    /// Every open stream breaks.
    func dropStreams() {
        let all: [FakeStream] = lock.withLock { Array(streams.values) }
        for stream in all { stream.fail() }
    }

    /// The next request whose path starts with `prefix`, of any method, fails with `status` and `body`.
    func failNext(_ prefix: String, status: Int, body: String) {
        lock.withLock { failures.append(({ _, path in path.hasPrefix(prefix) }, status, body)) }
    }

    /// The next `method` request of exactly `path` fails with `status` and `body`: a stream's reconnect or a view's read
    /// under the same path, which can come first, is not taken for it.
    func failNext(_ method: String, path: String, status: Int, body: String) {
        lock.withLock { failures.append(({ seen, at in seen == method && at == path }, status, body)) }
    }

    func loseNextAnswers(_ count: Int) { lock.withLock { lostAnswers = count } }

    func runStatus(_ id: String) -> String? { lock.withLock { runs[id]?.status } }

    func queueTexts(_ thread: String) -> [String] { lock.withLock { chats[thread]?.queue.map(\.text) ?? [] } }

    func steers(_ run: String) -> [String] {
        lock.withLock { (events[run] ?? []).filter { $0.kind == "steer" }.compactMap { $0.data["text"]?.string } }
    }

    // MARK: Telling

    private func push(_ event: EventRecord) {
        let targets: [FakeStream] = lock.withLock {
            let thread: String? = runs[event.runId]?.threadId
            let parentThread: String? = runs[event.runId]?.parentRunId.flatMap { runs[$0]?.threadId }
            return streams.values.filter { stream in
                if stream.path == "/api/threads/stream" {
                    return ["run_queued", "run_started", "run_finished", "approval_requested", "approval_decided"].contains(event.kind)
                }
                return stream.path == "/api/threads/\(thread ?? "-")/stream" || stream.path == "/api/threads/\(parentThread ?? "-")/stream"
            }
        }
        for stream in targets { stream.send(FakeStream.frame(event)) }
        told()
    }

    private func pushChanged(_ change: ThreadChanged) {
        let event: EventRecord = EventRecord(
            id: 0, runId: "system", ts: 0, kind: "thread_changed",
            data: ["thread_id": .string(change.threadId), "revision": .int(change.revision), "deleted": .bool(change.deleted)])
        let targets: [FakeStream] = lock.withLock {
            streams.values.filter { $0.path == "/api/threads/stream" || $0.path == "/api/threads/\(change.threadId)/stream" }
        }
        for stream in targets {
            stream.send(FakeStream.frame(event))
            if change.deleted, stream.path != "/api/threads/stream" { stream.finish() }
        }
        told()
    }

    // MARK: Answering

    struct Reply {
        var status: Int
        var body: Data
        var stream: Bool
        var lost: Bool
    }

    /// The captures of `pattern` (`"*"` takes any part) in `parts`; `nil` when they differ.
    static func match(_ parts: [String], _ pattern: [String]) -> [String]? {
        guard parts.count == pattern.count else { return nil }
        var found: [String] = []
        for (part, wanted) in zip(parts, pattern) {
            if wanted == "*" { found.append(part) } else if part != wanted { return nil }
        }
        return found
    }

    private static func json(_ value: some Encodable) -> Data { (try? JSONEncoder().encode(value)) ?? Data("null".utf8) }

    private static func refused(_ status: Int, _ detail: String) -> Reply {
        Reply(status: status, body: Data(#"{"detail":"\#(detail)"}"#.utf8), stream: false, lost: false)
    }

    /// The fate of a request before it reaches the routes: offline, a failure the test asked for.
    func fate(method: String, path: String, query: [String: String], body: Data?) -> Reply? {
        lock.withLock {
            log.append(Seen(method: method, path: path, query: query, body: body))
            if isOffline { return Reply(status: 0, body: Data(), stream: false, lost: true) }
            if let at: Int = failures.firstIndex(where: { $0.matches(method, path) }) {
                let failure: (matches: @Sendable (String, String) -> Bool, status: Int, body: String) = failures.remove(at: at)
                return Reply(status: failure.status, body: Data(failure.body.utf8), stream: false, lost: false)
            }
            return nil
        }
    }

    func reply(method: String, path: String, query: [String: String], body: Data?) -> Reply {
        defer { told() }
        let parts: [String] = path.split(separator: "/").map(String.init)
        if old, path == "/api/threads/view" || path.hasSuffix("/stream") || path.hasSuffix("/view") { return Self.refused(404, "Not Found") }
        if method == "GET", let found: [String] = Self.match(parts, ["api", "status"]) {
            _ = found
            return Reply(status: 200, body: Self.json(DaemonStatus(version: version, storeId: store)), stream: false, lost: false)
        }
        if method == "GET", let found: [String] = Self.match(parts, ["api", "threads", "view"]) {
            _ = found
            return Reply(status: 200, body: Self.json(threadsView()), stream: false, lost: false)
        }
        if method == "GET", let found: [String] = Self.match(parts, ["api", "threads", "stream"]) {
            _ = found
            return Reply(status: 200, body: Data(), stream: true, lost: false)
        }
        if method == "GET", let found: [String] = Self.match(parts, ["api", "threads", "*", "view"]) {
            let thread: String = found[0]
            guard let view: ThreadView = threadView(thread) else { return Self.refused(404, "no such chat") }
            return Reply(status: 200, body: Self.json(view), stream: false, lost: false)
        }
        if method == "GET", let found: [String] = Self.match(parts, ["api", "threads", "*", "stream"]) {
            let thread: String = found[0]
            let known: Bool = lock.withLock { chats[thread] != nil }
            return known ? Reply(status: 200, body: Data(), stream: true, lost: false) : Self.refused(404, "no such chat")
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "threads", "*", "queue"]) {
            let thread: String = found[0]
            return queue(thread: thread, body: body)
        }
        if method == "POST", Self.match(parts, ["api", "runs"]) != nil {
            return startChat(body: body)
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "runs", "*", "steer"]) {
            let run: String = found[0]
            return steer(run: run, body: body)
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "runs", "*", "cancel"]) {
            let run: String = found[0]
            guard runStatus(run) != nil else { return Self.refused(404, "no such run") }
            say(run, "run_finished", ["status": .string("cancelled")])
            return Reply(status: 202, body: Data("{}".utf8), stream: false, lost: false)
        }
        if method == "PATCH", let found: [String] = Self.match(parts, ["api", "threads", "*"]) {
            let thread: String = found[0]
            let fields: [String: JSONValue] = (try? JSONDecoder().decode([String: JSONValue].self, from: body ?? Data())) ?? [:]
            if let title: String = fields["title"]?.string {
                rename(thread, to: title, tell: true)
            }
            if let seen: Bool = fields["seen"]?.bool {
                lock.withLock { chats[thread]?.unread = !seen }
                bump(thread, tell: true)
            }
            return Reply(status: 204, body: Data(), stream: false, lost: false)
        }
        if (method == "POST" && Self.match(parts, ["api", "attachments"]) != nil) || (method == "PUT" && Self.match(parts, ["api", "attachments", "stream"]) != nil) {
            let id: Int = lock.withLock {
                uploads += 1
                return uploads
            }
            return Reply(status: 201, body: Data(#"{"id":"att_\#(id)"}"#.utf8), stream: false, lost: false)
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "approvals", "*", "decision"]) {
            let approval: String = found[0]
            let status: String = lock.withLock { decided[approval] ?? "pending" }
            if status != "pending" { return Self.refused(409, "already decided") }
            lock.withLock { decided[approval] = "approved" }
            return Reply(status: 200, body: Self.json(approvalRecord(approval, status: "approved")), stream: false, lost: false)
        }
        if method == "GET", let found: [String] = Self.match(parts, ["api", "approvals", "*"]) {
            let approval: String = found[0]
            let status: String = lock.withLock { decided[approval] ?? "pending" }
            return Reply(status: 200, body: Self.json(approvalRecord(approval, status: status)), stream: false, lost: false)
        }
        if let asks: Reply = phoneAsksReply(method: method, parts: parts, body: body) { return asks }
        if let voice: Reply = voiceReply(method: method, parts: parts, body: body) { return voice }
        if let models: Reply = modelsReply(method: method, parts: parts, body: body) { return models }
        if method == "DELETE", let found: [String] = Self.match(parts, ["api", "threads"]) {
            _ = found
            if let fields: [String: [String]] = try? JSONDecoder().decode([String: [String]].self, from: body ?? Data()) {
                for id in fields["ids"] ?? [] { deleteChat(id) }
            }
            return Reply(status: 200, body: Data(#"{"deleted":[],"missing":[]}"#.utf8), stream: false, lost: false)
        }
        return Self.refused(404, "Not Found")
    }

    /// The Mac's dictation (`routes_voice.py`): a stream takes its pieces in order; its end says how many bytes came.
    private func voiceReply(method: String, parts: [String], body: Data?) -> Reply? {
        if method == "GET", Self.match(parts, ["api", "voice"]) != nil {
            return Reply(status: 200, body: Data(#"{"transcribe":true,"why":null}"#.utf8), stream: false, lost: false)
        }
        if method == "POST", Self.match(parts, ["api", "voice", "streams"]) != nil {
            let id: String = lock.withLock {
                nextId += 1
                voiceBytes["vs_\(nextId)"] = 0
                return "vs_\(nextId)"
            }
            return Reply(status: 200, body: Data(#"{"id":"\#(id)"}"#.utf8), stream: false, lost: false)
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "voice", "streams", "*", "audio"]) {
            guard let fields = try? JSONDecoder().decode([String: JSONValue].self, from: body ?? Data()),
                let piece: Int = fields["piece"]?.int, let b64: String = fields["data_b64"]?.string, let data = Data(base64Encoded: b64)
            else { return Self.refused(422, "bad piece") }
            let inTurn: Bool = lock.withLock {
                guard voiceBytes[found[0]] != nil, voicePieces[found[0], default: 0] == piece else { return false }
                voicePieces[found[0], default: 0] += 1
                voiceBytes[found[0], default: 0] += data.count
                return true
            }
            return inTurn ? Reply(status: 204, body: Data(), stream: false, lost: false) : Self.refused(422, "piece out of turn")
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "voice", "streams", "*", "end"]) {
            guard let bytes: Int = lock.withLock({ voiceBytes.removeValue(forKey: found[0]) }) else { return Self.refused(404, "no such stream") }
            return Reply(status: 200, body: Data(#"{"text":"heard \#(bytes) bytes"}"#.utf8), stream: false, lost: false)
        }
        if method == "DELETE", let found: [String] = Self.match(parts, ["api", "voice", "streams", "*"]) {
            lock.withLock { voiceBytes[found[0]] = nil }
            return Reply(status: 204, body: Data(), stream: false, lost: false)
        }
        return nil
    }

    // MARK: Dot's asks to the phone (item 2051)

    private var heldAsks: [String] = []
    private var takenAnswers: [String: Data] = [:]

    /// An ask the next `GET /api/phone/asks/next` gives, as the daemon writes it.
    func queueAsk(_ json: String) { lock.withLock { heldAsks.append(json) } }

    /// The answers the phone sent, by ask id.
    var phoneAnswers: [String: Data] { lock.withLock { takenAnswers } }

    private func phoneAsksReply(method: String, parts: [String], body: Data?) -> Reply? {
        if method == "GET", Self.match(parts, ["api", "phone", "asks", "next"]) != nil {
            if old { return Self.refused(404, "Not Found") }
            let next: String? = lock.withLock { heldAsks.isEmpty ? nil : heldAsks.removeFirst() }
            guard let next else { return Reply(status: 204, body: Data(), stream: false, lost: false) }
            return Reply(status: 200, body: Data(next.utf8), stream: false, lost: false)
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "phone", "asks", "*"]) {
            lock.withLock { takenAnswers[found[0]] = body ?? Data() }
            return Reply(status: 204, body: Data(), stream: false, lost: false)
        }
        return nil
    }

    /// Settings › Models' routes (item 1907): each write answers with what it kept, as the daemon's do.
    private func modelsReply(method: String, parts: [String], body: Data?) -> Reply? {
        let fields: [String: JSONValue] = (try? JSONDecoder().decode([String: JSONValue].self, from: body ?? Data())) ?? [:]
        func ok(_ json: String) -> Reply { Reply(status: 200, body: Data(json.utf8), stream: false, lost: false) }
        let config: (String, [String]) -> String = { kind, standby in
            let list: String = standby.map { "\"\($0)\"" }.joined(separator: ",")
            let kinds: String = kind.isEmpty ? "{}" : #"{"security":"\#(kind)"}"#
            return #"{"main":"m:main","vision":"m:see","fast":"m:fast","review":"m:review","effort":"medium","helpers":[],"standby":[\#(list)],"kinds":\#(kinds)}"#
        }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "agents", "*", "pause"]) { return ok(Self.agentJSON(found[0], pausedAt: "1795750115.4")) }
        if method == "POST", let found: [String] = Self.match(parts, ["api", "agents", "*", "resume"]) { return ok(Self.agentJSON(found[0], pausedAt: "null")) }
        switch (method, parts) {
        case ("PUT", ["api", "models", "kinds"]):
            return ok(config(fields["model"]?.string ?? "", []))
        case ("PUT", ["api", "models", "standby"]):
            return ok(config("", (fields["models"]?.array ?? []).compactMap(\.string)))
        case ("GET", ["api", "models", "learning"]):
            return ok(#"{"proposer":"","composer":"m:compose","selector":"","experience":"","review":"m:review"}"#)
        case ("PUT", ["api", "models", "learning"]):
            let role: String = fields["role"]?.string ?? ""
            let model: String = fields["model"]?.string ?? ""
            let roles: String = ["proposer", "composer", "selector", "experience"].map { "\"\($0)\":\"\($0 == role ? model : "")\"" }.joined(separator: ",")
            return ok("{\(roles),\"review\":\"m:review\"}")
        case ("GET", ["api", "initiative"]):
            return ok(#"{"enabled":true,"look_model":"","items":[]}"#)
        case ("PUT", ["api", "initiative", "look-model"]):
            return ok(#"{"enabled":true,"look_model":"\#(fields["model"]?.string ?? "")","items":[]}"#)
        case ("GET", ["api", "repair"]):
            return ok(#"{"worker":{"model":"default","effort":"default","fast":false},"worker_lost":false,"nights":[]}"#)
        case ("PUT", ["api", "repair", "worker"]):
            let model: String = fields["model"]?.string ?? ""
            let effort: String = fields["effort"]?.string ?? ""
            let fast: Bool = fields["fast"]?.bool ?? false
            return ok(#"{"worker":{"model":"\#(model)","effort":"\#(effort)","fast":\#(fast)},"worker_lost":false}"#)
        case ("GET", ["api", "work-orders"]):
            return ok("[]")
        case ("GET", ["api", "objectives"]):
            return ok("[]")
        case ("GET", ["api", "team"]):
            return ok(#"{"messages":[]}"#)
        default:
            return nil
        }
    }

    /// An agent's record as the daemon answers a pause or a resume.
    private static func agentJSON(_ id: String, pausedAt: String) -> String {
        #"{"id":"\#(id)","name":"Scribe","role":"Writes the weekly note","instructions":"","model":"","effort":null,"approvals":null,"tools":[],"created_at":0,"updated_at":0,"made_by":null,"look":null,"paused_at":\#(pausedAt),"refuses":""}"#
    }

    /// The bodies the phone sent to a path that holds `part`, in order.
    func bodies(_ method: String, _ part: String) -> [Data] {
        requests.filter { $0.method == method && $0.path.contains(part) }.compactMap(\.body)
    }

    var openVoiceStreams: Int { lock.withLock { voiceBytes.count } }

    private func queue(thread: String, body: Data?) -> Reply {
        guard let message: QueueMessage = try? JSONDecoder().decode(QueueMessage.self, from: body ?? Data()) else {
            return Self.refused(422, "bad body")
        }
        let first: Bool = lock.withLock { keys["queue:\(message.key)"] == nil }
        if first {
            lock.withLock { keys["queue:\(message.key)"] = "done" }
            acceptedTexts.append(message.text)
            let working: Bool = lock.withLock { runs.values.contains { $0.threadId == thread && $0.parentRunId == nil && $0.isActive } }
            if working {
                lock.withLock {
                    let position: Int = chats[thread]?.queue.count ?? 0
                    nextId += 1
                    chats[thread]?.queue.append(
                        QueuedMessageView(id: "que_\(nextId)", threadId: thread, position: position, text: message.text, createdAt: 1, source: "ui"))
                }
                bump(thread, tell: true)
            } else {
                let id: String = lock.withLock {
                    nextId += 1
                    return "run_q\(nextId)"
                }
                addRun(id, thread: thread, goal: message.text, status: "running", parent: nil)
                say(id, "run_started", [:])
            }
        }
        let lose: Bool = lock.withLock {
            guard lostAnswers > 0 else { return false }
            lostAnswers -= 1
            return true
        }
        if lose { return Reply(status: 0, body: Data(), stream: false, lost: true) }
        let view: QueueView = lock.withLock {
            QueueView(held: chats[thread]?.held ?? false, messages: chats[thread]?.queue ?? [], besideRunId: nil, besideThreadId: nil)
        }
        return Reply(status: 201, body: Self.json(view), stream: false, lost: false)
    }

    /// A new chat (`POST /api/runs`): a chat of its own and its first run, started once per key.
    private func startChat(body: Data?) -> Reply {
        guard let message: NewChatMessage = try? JSONDecoder().decode(NewChatMessage.self, from: body ?? Data()) else {
            return Self.refused(422, "bad body")
        }
        let (first, number): (Bool, Int) = lock.withLock {
            if let known: String = keys["run:\(message.key)"], let made: Int = Int(known) { return (false, made) }
            nextId += 1
            keys["run:\(message.key)"] = String(nextId)
            return (true, nextId)
        }
        if first {
            acceptedTexts.append(message.goal)
            addChat("thr_c\(number)", title: message.goal)
            addRun("run_c\(number)", thread: "thr_c\(number)", goal: message.goal, status: "running", parent: nil)
        }
        let record: RunRecord? = lock.withLock { runs["run_c\(number)"] }
        guard let record else { return Self.refused(500, "the run was not made") }
        return Reply(status: 201, body: Self.json(record), stream: false, lost: false)
    }

    private func steer(run: String, body: Data?) -> Reply {
        guard let message: SteerMessage = try? JSONDecoder().decode(SteerMessage.self, from: body ?? Data()) else {
            return Self.refused(422, "bad body")
        }
        let seen: Bool = lock.withLock { keys["steer:\(message.key)"] != nil }
        if !seen {
            guard let record: RunRecord = lock.withLock({ runs[run] }), record.isActive else {
                return Self.refused(409, "the run is not active")
            }
            lock.withLock { keys["steer:\(message.key)"] = "done" }
            say(record.id, "steer", ["text": .string(message.text)])
        }
        return Reply(status: 202, body: Data("{}".utf8), stream: false, lost: false)
    }

    // MARK: Views

    private func summary(_ chat: Chat) -> ThreadSummary {
        let newest: RunRecord? = runs.values.filter { $0.threadId == chat.record.id }.max { $0.createdAt < $1.createdAt }
        return ThreadSummary(
            id: chat.record.id, title: chat.record.title, source: "ui", createdAt: 1, updatedAt: 1, pinnedAt: chat.pinnedAt,
            runCount: 0, latestRunId: newest?.id ?? "", latestStatus: newest?.status ?? "done", latestCreatedAt: newest?.createdAt ?? 0,
            unread: chat.unread, actorId: nil, lastMessage: "", lastRole: "user")
    }

    func threadsView() -> ThreadsView {
        lock.withLock {
            ThreadsView(
                revision: revisionCounter,
                threads: chats.values.map {
                    ThreadRow(summary: summary($0), revision: $0.revision, asks: $0.asks.count, helpers: 0, background: [])
                })
        }
    }

    func threadView(_ thread: String) -> ThreadView? {
        lock.withLock {
            guard let chat: Chat = chats[thread] else { return nil }
            let own: [RunRecord] = runs.values.filter { $0.threadId == thread }.sorted { $0.createdAt < $1.createdAt }
            let newest: RunRecord? = own.last
            let detail: RunDetail? = newest.map { RunDetail(run: $0, title: chat.record.title, active: $0.isActive, held: false, choices: nil) }
            let tail: [EventRecord] = newest.map { Array((events[$0.id] ?? []).suffix(200)) } ?? []
            let liveHelpers: [HelperView] = (helpers[thread] ?? []).map { helper in
                let status: String = runs[helper.runId]?.status ?? helper.status
                return HelperView(
                    runId: helper.runId, parentRunId: helper.parentRunId, threadId: helper.threadId, agentId: nil, title: helper.title,
                    status: status, startedAt: helper.startedAt, finishedAt: nil, provider: helper.provider, model: helper.model,
                    effort: helper.effort, step: helper.step, blocking: helper.blocking, stop: helper.stop)
            }
            return ThreadView(
                revision: chat.revision, thread: chat.record, summary: summary(chat), runs: Array(own.suffix(20)), older: own.count > 20,
                newest: detail, newestEvents: tail, newestEventsCut: false,
                queue: QueueView(held: chat.held, messages: chat.queue, besideRunId: nil, besideThreadId: nil), asks: chat.asks,
                helpers: liveHelpers, background: [], reactions: [], drafts: [])
        }
    }

    /// What a chat's stream sends at its start: each named run after its cursor (from 64 ids behind it, as the real one
    /// reads after a fall-behind), the runs at work from their start, then `synchronized`.
    func streamStart(thread: String, cursors: [String: Int]) -> Data {
        lock.withLock {
            var family: Set<String> = Set(cursors.keys)
            for run in runs.values where run.isActive && (run.threadId == thread || runs[run.parentRunId ?? ""]?.threadId == thread) {
                family.insert(run.id)
            }
            var out: [EventRecord] = []
            var newest: [String: Int] = [:]
            for run in family {
                let from: Int = max(0, (cursors[run] ?? 0) - 64)
                let sent: [EventRecord] = (events[run] ?? []).filter { $0.id > from }
                out.append(contentsOf: sent)
                newest[run] = max(cursors[run] ?? 0, sent.last?.id ?? 0)
            }
            var data: Data = Data()
            for event in out.sorted(by: { $0.id < $1.id }) { data.append(FakeStream.frame(event)) }
            let synced: EventRecord = EventRecord(
                id: 0, runId: "system", ts: 0, kind: "synchronized",
                data: ["revision": .int(chats[thread]?.revision ?? 0), "runs": .object(newest.mapValues { .int($0) })])
            data.append(FakeStream.frame(synced))
            return data
        }
    }

    func follow(_ stream: FakeStream) {
        lock.withLock { streams[stream.id] = stream }
        told()
    }

    func unfollow(_ stream: FakeStream) {
        lock.withLock { streams[stream.id] = nil }
        told()
    }
}

/// One open stream of the fake daemon.
final class FakeStream: @unchecked Sendable {
    let id: UUID = UUID()
    let path: String
    private let write: @Sendable (Data) -> Void
    private let end: @Sendable ((any Error)?) -> Void

    init(path: String, write: @escaping @Sendable (Data) -> Void, end: @escaping @Sendable ((any Error)?) -> Void) {
        self.path = path
        self.write = write
        self.end = end
    }

    func send(_ data: Data) { write(data) }

    func fail() { end(URLError(.networkConnectionLost)) }

    func finish() { end(nil) }

    static func frame(_ event: EventRecord) -> Data {
        let body: String = String(decoding: (try? JSONEncoder().encode(event)) ?? Data(), as: UTF8.self)
        let id: String = event.id > 0 ? "id: \(event.id)\n" : ""
        return Data("\(id)event: \(event.kind)\ndata: \(body)\n\n".utf8)
    }
}

/// The network of the tests: finds the fake daemon by host.
final class FakeProtocol: URLProtocol, @unchecked Sendable {
    private static let registry: NSLock = NSLock()
    nonisolated(unsafe) private static var daemons: [String: FakeDaemon] = [:]

    static func register(_ daemon: FakeDaemon, host: String) { registry.withLock { daemons[host] = daemon } }
    static func unregister(host: String) { registry.withLock { daemons[host] = nil } }
    private static func daemon(for host: String) -> FakeDaemon? { registry.withLock { daemons[host] } }

    private let state: NSLock = NSLock()
    private var stopped: Bool = false
    private var stream: FakeStream?
    private var daemon: FakeDaemon?

    override class func canInit(with request: URLRequest) -> Bool { daemon(for: request.url?.host() ?? "") != nil }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url: URL = request.url, let found: FakeDaemon = Self.daemon(for: url.host() ?? "") else {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotFindHost))
            return
        }
        daemon = found
        let method: String = request.httpMethod ?? "GET"
        var asked: [String: String] = [:]
        for item in URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? [] { asked[item.name] = item.value ?? "" }
        let query: [String: String] = asked
        let body: Data? = readBody()
        if (method == "POST" && url.path == "/api/attachments") || (method == "PUT" && url.path == "/api/attachments/stream"), found.holdUploads { return }
        DispatchQueue.global().async { [self] in
            let reply: FakeDaemon.Reply =
                found.fate(method: method, path: url.path, query: query, body: body)
                ?? found.reply(method: method, path: url.path, query: query, body: body)
            if isStopped { return }
            if reply.lost {
                client?.urlProtocol(self, didFailWithError: URLError(found.offline ? .notConnectedToInternet : .networkConnectionLost))
                return
            }
            if reply.stream {
                open(url: url, query: query, on: found)
                return
            }
            let response: HTTPURLResponse = HTTPURLResponse(
                url: url, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: reply.body)
            client?.urlProtocolDidFinishLoading(self)
        }
    }

    private var isStopped: Bool { state.withLock { stopped } }

    private func open(url: URL, query: [String: String], on daemon: FakeDaemon) {
        let response: HTTPURLResponse = HTTPURLResponse(
            url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "text/event-stream"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        let made: FakeStream = FakeStream(
            path: url.path,
            write: { [weak self] data in
                guard let self, !self.isStopped else { return }
                self.client?.urlProtocol(self, didLoad: data)
            },
            end: { [weak self] error in
                guard let self, !self.isStopped else { return }
                self.state.withLock { self.stopped = true }
                if let stream = self.stream { self.daemon?.unfollow(stream) }
                if let error { self.client?.urlProtocol(self, didFailWithError: error) } else { self.client?.urlProtocolDidFinishLoading(self) }
            })
        state.withLock { stream = made }
        // The list's stream starts silent (a keepalive read late would move the heartbeat behind the test's clock).
        if url.path != "/api/threads/stream" {
            let thread: String = url.pathComponents.dropLast().last ?? ""
            var cursors: [String: Int] = [:]
            for part in (query["cursors"] ?? "").split(separator: ",") {
                let pieces: [Substring] = part.split(separator: ":")
                if pieces.count == 2, let at: Int = Int(pieces[1]) { cursors[String(pieces[0])] = at }
            }
            daemon.follow(made)
            daemon.start(made, with: daemon.streamStart(thread: thread, cursors: cursors))
            return
        }
        daemon.follow(made)
    }

    private func readBody() -> Data? {
        if let body: Data = request.httpBody { return body }
        guard let input: InputStream = request.httpBodyStream else { return nil }
        input.open()
        defer { input.close() }
        var collected: Data = Data()
        var buffer: [UInt8] = [UInt8](repeating: 0, count: 4096)
        while input.hasBytesAvailable {
            let count: Int = input.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            collected.append(buffer, count: count)
        }
        return collected
    }

    override func stopLoading() {
        let open: FakeStream? = state.withLock {
            stopped = true
            return stream
        }
        if let open { daemon?.unfollow(open) }
    }
}
