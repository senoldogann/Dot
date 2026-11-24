import Foundation
@testable import Dot

/// How the network between the phone and the Mac behaves, for the tests: the same rules whatever the clock, because a
/// request's fate is a function of its path and how often that path was asked, not of the order requests arrived in.
struct NetworkFaults: Sendable {
    /// Seconds a request waits before the daemon (or the network) answers; a request's own point in this range is
    /// picked from its fate.
    var latency: ClosedRange<Double> = 0...0
    /// The share of requests that are lost (the connection drops before an answer).
    var dropRate: Double = 0
    /// No request gets anywhere (`notConnectedToInternet`).
    var offline: Bool = false
    /// Multiplies every wait, so that a profile of seconds can run in a unit test's tenths.
    var scale: Double = 1
    var seed: UInt64 = 7

    static let good: NetworkFaults = NetworkFaults()
    /// The worst network the plan names: 2 to 8 s of latency and three requests in ten lost.
    static func bad(scale: Double = 1) -> NetworkFaults { NetworkFaults(latency: 2...8, dropRate: 0.3, scale: scale) }
    static let down: NetworkFaults = NetworkFaults(offline: true)
}

/// A daemon that lives in the test process: it speaks the routes the app reads and sends, keeps what was sent with
/// its key as the real one does (`send_once`, item 189), and misbehaves as `NetworkFaults` say. A session made by
/// `session()` reaches it through a `URLProtocol`, so no socket is opened.
final class FakeDaemon: @unchecked Sendable {
    struct Seen: Sendable {
        var method: String
        var path: String
        var query: String
        var body: Data?
        var at: Date
        /// The idle timeout the request asked for.
        var timeout: TimeInterval = 0
    }

    let host: DaemonHost
    private let lock: NSLock = NSLock()
    private var faults: NetworkFaults = .good
    private var counts: [String: Int] = [:]
    private var log: [Seen] = []
    private var openStreams: [UUID: FakeStream] = [:]

    // What the daemon holds.
    private var threadList: [ThreadSummary] = []
    private var agentList: [AgentSummary] = []
    private var runsOfThread: [String: [RunRecord]] = [:]
    private var eventsOfRun: [String: [EventRecord]] = [:]
    private var approvals: [String: ApprovalRecord] = [:]
    private var queued: [String: [QueuedMessage]] = [:]
    private var sent: [String: String] = [:]
    private var nextEventId: Int = 1
    private(set) var accepted: Int = 0
    private(set) var attachments: Int = 0
    private var cancelledCount: Int = 0
    private var startedPaths: [String] = []
    /// A daemon that does not know `tail` answers the run's first events and says nothing in a header.
    var supportsTail: Bool = true
    /// The next this many accepted sends have their answer lost: the daemon took the message, the phone never hears.
    private var loseAnswers: Int = 0
    /// Routes (by path prefix) whose next answer is this status, once.
    private var failWith: [(prefix: String, status: Int, body: String)] = []

    init() {
        let name: String = "fake-\(UUID().uuidString.prefix(8).lowercased()).test"
        host = DaemonHost(name: name, origin: URL(string: "https://\(name)")!)
        FakeDaemonProtocol.register(self, host: name)
    }

    deinit { FakeDaemonProtocol.unregister(host: host.name) }

    func session(timeout: TimeInterval = 15) -> URLSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FakeDaemonProtocol.self]
        configuration.timeoutIntervalForRequest = timeout
        return URLSession(configuration: configuration)
    }

    func api(
        key: String = "phone.test.key", timeout: TimeInterval = 15, link: LinkMonitor? = nil, readPolicy: RetryPolicy = .read
    ) -> APIClient {
        APIClient(host: host, key: key, session: session(timeout: timeout), link: link, readPolicy: readPolicy)
    }

    // MARK: Scripting

    func set(_ next: NetworkFaults) {
        lock.lock()
        faults = next
        lock.unlock()
    }

    var requests: [Seen] {
        lock.lock()
        defer { lock.unlock() }
        return log
    }

    func count(method: String? = nil, pathContaining part: String) -> Int {
        requests.filter { (method == nil || $0.method == method) && $0.path.contains(part) }.count
    }

    var openStreamCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return openStreams.count
    }

    var cancelled: Int {
        lock.lock()
        defer { lock.unlock() }
        return cancelledCount
    }

    func noteStarted(_ path: String) {
        lock.lock()
        startedPaths.append(path)
        lock.unlock()
    }

    /// How many requests for a path were begun (answered or not).
    func started(pathContaining part: String) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return startedPaths.filter { $0.contains(part) }.count
    }

    func noteCancelled() {
        lock.lock()
        cancelledCount += 1
        lock.unlock()
    }

    /// `n` more events on the newest run of a chat, stored (no stream is told: the phone was away).
    func sayMany(_ count: Int, thread: String = "thr_1") {
        lock.lock()
        let run: String = runsOfThread[thread]?.last?.id ?? ""
        for number in 0..<count {
            appendUnlocked(event(
                "tool_call", run, ["call_id": .string("many\(nextEventId)_\(number)"), "tool": .string("shell"), "args": .object([:])]))
        }
        lock.unlock()
    }

    func loseNextAnswers(_ count: Int) {
        lock.lock()
        loseAnswers = count
        lock.unlock()
    }

    func failNext(_ prefix: String, status: Int, body: String = #"{"detail":"refused"}"#) {
        lock.lock()
        failWith.append((prefix, status, body))
        lock.unlock()
    }

    /// A chat of `runCount` runs, each with a message, `steps` tool steps and an answer; the newest may be at work.
    @discardableResult
    func addChat(
        _ id: String = "thr_1", title: String = "A chat", runCount: Int = 3, steps: Int = 6, working: Bool = false,
        updatedAt: Double = 2_000
    ) -> ThreadSummary {
        lock.lock()
        defer { lock.unlock() }
        var runs: [RunRecord] = []
        for number in 0..<runCount {
            let isNewest: Bool = number == runCount - 1
            let run: RunRecord = makeRun(
                "run_\(id)_\(number)", thread: id, goal: "Message \(number) of \(title)",
                status: isNewest && working ? "running" : "done", created: 1_000 + Double(number) * 10,
                result: isNewest && working ? "" : "Answer \(number)")
            runs.append(run)
            appendUnlocked(event("run_queued", run.id, ["goal": .string(run.goal)]))
            appendUnlocked(event("run_started", run.id, [:]))
            for step in 0..<steps {
                appendUnlocked(event(
                    "tool_call", run.id,
                    ["call_id": .string("c\(number)_\(step)"), "tool": .string("shell"), "args": .object(["command": .string("echo \(step)")])]))
                appendUnlocked(event(
                    "tool_result", run.id, ["call_id": .string("c\(number)_\(step)"), "tool": .string("shell"), "text": .string("\(step)")]))
            }
            appendUnlocked(event("assistant", run.id, ["step": .int(steps), "text": .string("Working on \(number)")]))
            if !(isNewest && working) {
                appendUnlocked(event("run_finished", run.id, ["status": .string("done"), "result": .string("Answer \(number)")]))
            }
        }
        runsOfThread[id] = runs
        let summary: ThreadSummary = ThreadSummary(
            id: id, title: title, source: "ui", updatedAt: updatedAt, pinnedAt: nil, runCount: runCount,
            latestRunId: runs.last?.id ?? "", latestStatus: runs.last?.status ?? "done", latestMode: "task", unread: false,
            actorId: nil, lastMessage: "Answer", lastRole: "assistant", loopCron: nil, loopNextAt: nil)
        threadList.removeAll { $0.id == id }
        threadList.append(summary)
        threadList.sort { $0.updatedAt > $1.updatedAt }
        return summary
    }

    func addAgent(_ id: String = "agt_1", name: String = "Aino") {
        lock.lock()
        agentList.append(AgentSummary(record: .init(id: id, name: name, role: "helper", pausedAt: nil), latest: nil, latestTitle: nil, home: nil))
        lock.unlock()
    }

    /// An event of the newest run of `thread`, stored and pushed to the streams that follow it.
    @discardableResult
    func say(_ kind: String, thread: String = "thr_1", _ data: [String: JSONValue] = [:]) -> EventRecord {
        lock.lock()
        let run: String = runsOfThread[thread]?.last?.id ?? ""
        let made: EventRecord = appendUnlocked(event(kind, run, data))
        if kind == "run_finished", let at: Int = runsOfThread[thread]?.indices.last {
            runsOfThread[thread]?[at].status = (data["status"]?.string) ?? "done"
            if let result: String = data["result"]?.string { runsOfThread[thread]?[at].result = result }
        }
        let followers: [FakeStream] = Array(openStreams.values)
        lock.unlock()
        for stream in followers { stream.push(made) }
        return made
    }

    func ask(_ approvalId: String, thread: String = "thr_1", status: String = "pending") {
        lock.lock()
        let run: String = runsOfThread[thread]?.last?.id ?? ""
        approvals[approvalId] = ApprovalRecord(
            id: approvalId, runId: run, status: status, tool: "shell", summary: "Run a command", severity: "ask", args: .object([:]))
        lock.unlock()
    }

    func expire(_ approvalId: String) {
        lock.lock()
        approvals[approvalId]?.status = "expired"
        lock.unlock()
    }

    func approvalStatus(_ id: String) -> String? {
        lock.lock()
        defer { lock.unlock() }
        return approvals[id]?.status
    }

    var queuedTexts: [String] {
        lock.lock()
        defer { lock.unlock() }
        return queued.values.flatMap { $0 }.map(\.text)
    }

    /// The highest event id stored for the runs of a chat.
    func newestEventId(thread: String = "thr_1") -> Int {
        lock.lock()
        defer { lock.unlock() }
        return (runsOfThread[thread] ?? []).flatMap { eventsOfRun[$0.id] ?? [] }.map(\.id).max() ?? 0
    }

    var storedEventCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return eventsOfRun.values.reduce(0) { $0 + $1.count }
    }

    /// Ends every open stream as a network drop does.
    func dropStreams() {
        lock.lock()
        let all: [FakeStream] = Array(openStreams.values)
        lock.unlock()
        for stream in all { stream.fail() }
    }

    private func event(_ kind: String, _ run: String, _ data: [String: JSONValue]) -> EventRecord {
        EventRecord(id: 0, runId: run, ts: 1_000, kind: kind, data: data)
    }

    @discardableResult
    private func appendUnlocked(_ draft: EventRecord) -> EventRecord {
        let stored: EventRecord = EventRecord(id: nextEventId, runId: draft.runId, ts: draft.ts + Double(nextEventId), kind: draft.kind, data: draft.data)
        nextEventId += 1
        eventsOfRun[draft.runId, default: []].append(stored)
        return stored
    }

    // MARK: What the protocol asks

    /// What happens to a request: the wait before it and whether it is lost or refused, decided by its path and how often
    /// that path was asked.
    func fate(of path: String, method: String) -> (wait: TimeInterval, lost: Bool, offline: Bool, status: (code: Int, body: String)?) {
        lock.lock()
        defer { lock.unlock() }
        let key: String = "\(method) \(path)"
        let nth: Int = counts[key, default: 0]
        counts[key] = nth + 1
        let roll: Double = Self.unit(seed: faults.seed, key: "\(key)#\(nth)")
        let spread: Double = Self.unit(seed: faults.seed &+ 1, key: "\(key)#\(nth)")
        let wait: TimeInterval = (faults.latency.lowerBound + spread * (faults.latency.upperBound - faults.latency.lowerBound)) * faults.scale
        var status: (code: Int, body: String)?
        if let at: Int = failWith.firstIndex(where: { path.hasPrefix($0.prefix) }) {
            let refused = failWith.remove(at: at)
            status = (refused.status, refused.body)
        }
        return (wait, roll < faults.dropRate, faults.offline, status)
    }

    private static func unit(seed: UInt64, key: String) -> Double {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325 ^ seed
        for byte in key.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 0x100_0000_01b3
        }
        hash ^= hash >> 33
        hash = hash &* 0xff51_afd7_ed55_8ccd
        hash ^= hash >> 33
        return Double(hash % 1_000_000) / 1_000_000
    }

    struct Reply: Sendable {
        var status: Int = 200
        var body: Data = Data("{}".utf8)
        /// The daemon took what it was sent but the answer is lost on the way.
        var loseAnswer: Bool = false
        var stream: (after: Int, run: String?)?
        var headers: [String: String] = [:]
    }

    func reply(method: String, path: String, query: [String: String], body: Data?, timeout: TimeInterval = 0) -> Reply {
        lock.lock()
        log.append(Seen(method: method, path: path, query: query.map { "\($0)=\($1)" }.sorted().joined(separator: "&"), body: body, at: Date(), timeout: timeout))
        lock.unlock()
        let encoder: JSONEncoder = JSONEncoder()
        func json(_ value: some Encodable) -> Reply { Reply(body: (try? encoder.encode(value)) ?? Data("null".utf8)) }
        lock.lock()
        defer { lock.unlock() }
        let parts: [String] = path.split(separator: "/").map(String.init)
        guard parts.first == "api" else { return Reply(status: 404) }
        let route: [String] = Array(parts.dropFirst())
        switch (method, route) {
        case ("GET", ["threads"]): return json(threadList)
        case ("GET", ["agents"]): return json(agentList)
        case ("GET", ["status"]): return json(StatusView(version: "test", models: ModelChoices(main: "ollama:scripted", review: "ollama:scripted")))
        case ("GET", ["models"]): return json([ProviderModels]())
        case ("GET", ["schedules"]): return json([ScheduleView]())
        case ("GET", ["stream"]): return Reply(stream: (0, nil))
        case ("POST", ["attachments"]):
            attachments += 1
            return Reply(body: Data(#"{"id":"att_\#(attachments)"}"#.utf8))
        case ("GET", ["runs"]):
            let thread: String = query["thread_id"] ?? ""
            var runs: [RunRecord] = (runsOfThread[thread] ?? []).sorted { $0.createdAt > $1.createdAt }
            if let before: Double = query["before"].flatMap(Double.init) { runs = runs.filter { $0.createdAt < before } }
            return json(Array(runs.prefix(Int(query["limit"] ?? "") ?? 50)))
        case ("GET", let shape) where Self.fits(shape, ["approvals", "*"]):
            guard let record: ApprovalRecord = approvals[route[1]] else { return Reply(status: 404) }
            return json(record)
        case ("GET", ["approvals"]):
            return json(approvals.values.filter { $0.status == "pending" }.sorted { $0.id < $1.id })
        case ("POST", let shape) where Self.fits(shape, ["approvals", "*", "decision"]):
            let id: String = route[1]
            guard var record: ApprovalRecord = approvals[id] else { return Reply(status: 404) }
            guard record.status == "pending" else { return Reply(status: 409, body: Data(#"{"detail":"the approval is no longer open"}"#.utf8)) }
            let approve: Bool = ((try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any])?["approve"] as? Bool) ?? false
            record.status = approve ? "approved" : "denied"
            approvals[id] = record
            return json(record)
        case ("GET", let shape) where Self.fits(shape, ["threads", "*", "helpers"]): return json([RunRecord]())
        case ("GET", let shape) where Self.fits(shape, ["threads", "*", "automations"]): return json([ThreadAutomation]())
        case ("GET", let shape) where Self.fits(shape, ["threads", "*", "reactions"]): return json([ReactionRecord]())
        case ("GET", let shape) where Self.fits(shape, ["threads", "*", "queue"]): return json(QueueView(held: false, messages: queued[route[1]] ?? [], besideRunId: nil, besideThreadId: nil))
        case ("PATCH", let shape) where Self.fits(shape, ["threads", "*"]): return Reply()
        case ("POST", let shape) where Self.fits(shape, ["threads", "*", "queue"]):
            let thread: String = route[1]
            let object: [String: Any] = (try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]) ?? [:]
            let key: String = (object["key"] as? String) ?? ""
            let text: String = (object["text"] as? String) ?? ""
            if sent[key] == nil {
                // Accepted once: a key sent again gets what the first sending made (`send_once`).
                accepted += 1
                let message: QueuedMessage = QueuedMessage(id: "q_\(accepted)", position: queued[thread]?.count ?? 0, text: text, createdAt: 1)
                queued[thread, default: []].append(message)
                sent[key] = message.id
            }
            var made: Reply = json(QueueView(held: false, messages: queued[thread] ?? [], besideRunId: nil, besideThreadId: nil))
            made.status = 201
            if loseAnswers > 0 {
                loseAnswers -= 1
                made.loseAnswer = true
            }
            return made
        case ("PUT", let shape) where Self.fits(shape, ["threads", "*", "queue"]): return json(QueueView(held: false, messages: queued[route[1]] ?? [], besideRunId: nil, besideThreadId: nil))
        case ("DELETE", let shape) where Self.fits(shape, ["queue", "*"]):
            for (thread, messages) in queued { queued[thread] = messages.filter { $0.id != route[1] } }
            return json(QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil))
        case ("POST", let shape) where Self.fits(shape, ["runs", "*", "cancel"]):
            for (thread, runs) in runsOfThread {
                if let at: Int = runs.firstIndex(where: { $0.id == route[1] }) { runsOfThread[thread]?[at].status = "cancelled" }
            }
            return Reply()
        case ("GET", let shape) where Self.fits(shape, ["runs", "*", "events"]):
            let after: Int = Int(query["after"] ?? "") ?? 0
            let limit: Int = Int(query["limit"] ?? "") ?? 2000
            let all: [EventRecord] = eventsOfRun[route[1]] ?? []
            if supportsTail, let tail: Int = query["tail"].flatMap(Int.init) {
                var answer: Reply = json(Array(all.suffix(tail)))
                answer.headers = ["X-Dot-Events-Tail": String(tail)]
                return answer
            }
            return json(Array(all.filter { $0.id > after }.prefix(limit)))
        case ("GET", let shape) where Self.fits(shape, ["runs", "*", "stream"]):
            return Reply(stream: (max(Int(query["after"] ?? "") ?? 0, Int(query["last-event-id"] ?? "") ?? 0), route[1]))
        case ("GET", let shape) where Self.fits(shape, ["runs", "*"]):
            for runs in runsOfThread.values {
                if let run: RunRecord = runs.first(where: { $0.id == route[1] }) {
                    let questions: [ApprovalRecord] = approvals.values.filter { $0.runId == run.id && $0.status == "pending" }
                    return json(RunDetail(run: run, title: "Title of \(run.threadId)", children: [], active: run.status.isActiveRunStatus, questions: questions, choices: nil))
                }
            }
            return Reply(status: 404)
        default:
            return Reply(status: 404, body: Data(#"{"detail":"no such route"}"#.utf8))
        }
    }

    private static func fits(_ shape: [String], _ pattern: [String]) -> Bool {
        shape.count == pattern.count && zip(shape, pattern).allSatisfy { $1 == "*" || $0 == $1 }
    }

    func events(of run: String, after: Int) -> [EventRecord] {
        lock.lock()
        defer { lock.unlock() }
        return (eventsOfRun[run] ?? []).filter { $0.id > after }
    }

    func follow(_ stream: FakeStream) {
        lock.lock()
        openStreams[stream.id] = stream
        lock.unlock()
    }

    func unfollow(_ stream: FakeStream) {
        lock.lock()
        openStreams[stream.id] = nil
        lock.unlock()
    }
}

/// One open event stream of a `FakeDaemon`.
final class FakeStream: @unchecked Sendable {
    let id: UUID = UUID()
    let run: String?
    private let send: @Sendable (Data) -> Void
    private let end: @Sendable (Error?) -> Void

    init(run: String?, send: @escaping @Sendable (Data) -> Void, end: @escaping @Sendable (Error?) -> Void) {
        self.run = run
        self.send = send
        self.end = end
    }

    func push(_ event: EventRecord) {
        guard run == nil || run == event.runId else { return }
        send(Self.frame(event))
        if event.kind == "run_finished" { end(nil) }
    }

    func fail() { end(URLError(.networkConnectionLost)) }

    static func frame(_ event: EventRecord) -> Data {
        let body: String = String(decoding: (try? JSONEncoder().encode(event)) ?? Data(), as: UTF8.self)
        return Data("id: \(event.id)\nevent: \(event.kind)\ndata: \(body)\n\n".utf8)
    }
}

/// The `URLProtocol` that stands in for the network: it finds the `FakeDaemon` by host.
final class FakeDaemonProtocol: URLProtocol, @unchecked Sendable {
    private static let registry: NSLock = NSLock()
    nonisolated(unsafe) private static var daemons: [String: FakeDaemon] = [:]

    static func register(_ daemon: FakeDaemon, host: String) {
        registry.lock()
        daemons[host] = daemon
        registry.unlock()
    }

    static func unregister(host: String) {
        registry.lock()
        daemons[host] = nil
        registry.unlock()
    }

    private static func daemon(for host: String) -> FakeDaemon? {
        registry.lock()
        defer { registry.unlock() }
        return daemons[host]
    }

    private let state: NSLock = NSLock()
    private var cancelled: Bool = false
    private var finished: Bool = false
    private var follower: FakeStream?
    private var daemon: FakeDaemon?

    override class func canInit(with request: URLRequest) -> Bool { daemon(for: request.url?.host() ?? "") != nil }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url: URL = request.url, let found: FakeDaemon = Self.daemon(for: url.host() ?? "") else {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotFindHost))
            return
        }
        daemon = found
        found.noteStarted(url.path)
        let path: String = url.path
        let method: String = request.httpMethod ?? "GET"
        var query: [String: String] = [:]
        for item in URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? [] { query[item.name] = item.value ?? "" }
        if let last: String = request.value(forHTTPHeaderField: "Last-Event-ID") { query["last-event-id"] = last }
        let body: Data? = readBody()
        let fate = found.fate(of: path, method: method)
        let wait: TimeInterval = fate.offline ? 0.01 : fate.wait
        DispatchQueue.global().asyncAfter(deadline: .now() + max(0.001, wait)) { [self] in
            if isCancelled { return }
            markFinished()
            if fate.offline {
                client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
                return
            }
            if fate.lost {
                client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
                return
            }
            if let refused = fate.status {
                respond(Reply2(status: refused.code, body: Data(refused.body.utf8)), url: url)
                return
            }
            let answer: FakeDaemon.Reply = found.reply(method: method, path: path, query: query, body: body, timeout: request.timeoutInterval)
            if answer.loseAnswer {
                client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
                return
            }
            if let wanted = answer.stream {
                openStream(after: wanted.after, run: wanted.run, url: url, on: found)
                return
            }
            respond(Reply2(status: answer.status, body: answer.body, headers: answer.headers), url: url)
        }
    }

    private struct Reply2 {
        var status: Int
        var body: Data
        var headers: [String: String] = [:]
    }

    private func respond(_ answer: Reply2, url: URL) {
        var fields: [String: String] = ["Content-Type": "application/json"]
        for (name, value) in answer.headers { fields[name] = value }
        let response: HTTPURLResponse = HTTPURLResponse(url: url, statusCode: answer.status, httpVersion: "HTTP/1.1", headerFields: fields)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: answer.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    private func openStream(after: Int, run: String?, url: URL, on daemon: FakeDaemon) {
        let response: HTTPURLResponse = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "text/event-stream"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        let stream: FakeStream = FakeStream(
            run: run,
            send: { [weak self] data in
                guard let self, !self.isCancelled else { return }
                self.client?.urlProtocol(self, didLoad: data)
            },
            end: { [weak self] error in
                guard let self, !self.isCancelled else { return }
                if let error { self.client?.urlProtocol(self, didFailWithError: error) } else { self.client?.urlProtocolDidFinishLoading(self) }
            })
        state.lock()
        follower = stream
        state.unlock()
        daemon.follow(stream)
        if let run {
            var finished: Bool = false
            for stored in daemon.events(of: run, after: after) {
                client?.urlProtocol(self, didLoad: FakeStream.frame(stored))
                if stored.kind == "run_finished" { finished = true }
            }
            if finished {
                daemon.unfollow(stream)
                client?.urlProtocolDidFinishLoading(self)
            }
        } else {
            client?.urlProtocol(self, didLoad: Data(": ready\n\n".utf8))
        }
    }

    private func markFinished() {
        state.lock()
        finished = true
        state.unlock()
    }

    private var isCancelled: Bool {
        state.lock()
        defer { state.unlock() }
        return cancelled
    }

    private func readBody() -> Data? {
        if let body: Data = request.httpBody { return body }
        guard let stream: InputStream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var collected: Data = Data()
        var buffer: [UInt8] = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count: Int = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            collected.append(buffer, count: count)
        }
        return collected
    }

    override func stopLoading() {
        state.lock()
        let wasFirst: Bool = !cancelled
        cancelled = true
        let stream: FakeStream? = follower
        state.unlock()
        if let stream { daemon?.unfollow(stream) }
        if wasFirst, !finished { daemon?.noteCancelled() }
    }
}
