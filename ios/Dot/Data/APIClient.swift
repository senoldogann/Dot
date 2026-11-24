import Foundation
import os

/// The daemon's HTTP API as the native screens use it: JSON in and out over HTTPS with the phone's key, on the host
/// and key the app already keeps (`DaemonHost`, `PhoneKeyStore`). Failures are `DaemonRequestError`, as the push
/// registration's. A read is tried again as its `RetryPolicy` says (a few tries with a growing wait, waiting for the
/// link when there is none), and every read is made off the interface: a screen draws what the cache holds and the
/// read only brings it up to date. A write is sent once: what the owner did goes through the outbox, which keeps it and
/// sends it again when the link allows.
struct APIClient: Sendable {
    let host: DaemonHost
    let key: String
    let session: URLSession
    /// The phone's link, when the app watches it: a read waits for it instead of spending tries while it is down.
    let link: LinkMonitor?
    /// How a read is tried again.
    let readPolicy: RetryPolicy

    init(host: DaemonHost, key: String, session: URLSession, link: LinkMonitor? = nil, readPolicy: RetryPolicy = .read) {
        self.host = host
        self.key = key
        self.session = session
        self.link = link
        self.readPolicy = readPolicy
    }

    private static let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "api")

    /// A session for requests: HTTP/2 over the tailnet, no cache of its own (the app keeps its store). A read gives up
    /// after 10 s of silence; a request made while there is no link waits for it (`waitsForConnectivity`) up to 45 s in
    /// all, rather than failing at once and being asked again into the same nothing.
    static func makeSession() -> URLSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 45
        configuration.waitsForConnectivity = true
        configuration.httpMaximumConnectionsPerHost = 6
        configuration.allowsConstrainedNetworkAccess = true
        configuration.allowsExpensiveNetworkAccess = true
        return URLSession(configuration: configuration)
    }

    /// A session for the event streams, made like `session` (the same protocols, proxies and cookies' rules): no timeout on the
    /// whole, 45 s on silence, and no waiting for a link (the feed's own retry knows more).
    static func makeStreamSession(like session: URLSession) -> URLSession {
        let configuration: URLSessionConfiguration = session.configuration
        configuration.timeoutIntervalForRequest = 45
        configuration.timeoutIntervalForResource = 24 * 3600
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }

    /// A session for photos: a base64 photo on a poor link takes minutes, not the 45 s a read is given.
    static func makeUploadSession() -> URLSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 600
        configuration.waitsForConnectivity = true
        return URLSession(configuration: configuration)
    }

    /// Seconds a request of this kind may stay silent: a read is short, a write may carry a photo.
    static func timeout(for method: String) -> TimeInterval { method == "GET" ? 10 : 30 }

    func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil) -> URLRequest {
        var url: URL = host.url(path: path)
        // A query item, not part of the path: `appending(path:)` writes a `?` as `%3F`.
        if !query.isEmpty { url.append(queryItems: query) }
        var request: URLRequest = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = Self.timeout(for: method)
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    func get<T: Decodable & Sendable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        let built: URLRequest = request("GET", path, query: query)
        return try await Retrier.run(readPolicy, link: link) {
            // A phone that knows it has no link does not ask: the retry waits for the link instead.
            if let link, !link.state.online { throw DaemonRequestError.transport(path: path, underlying: URLError(.notConnectedToInternet)) }
            return try decode(try await data(built, path: path), path: path)
        }
    }

    func send<T: Decodable & Sendable>(
        _ method: String, _ path: String, body: (some Encodable & Sendable)? = Optional<String>.none
    ) async throws -> T {
        try decode(try await data(request(method, path, body: try body.map { try JSONEncoder().encode($0) }), path: path), path: path)
    }

    func sendVoid(_ method: String, _ path: String, body: (some Encodable & Sendable)? = Optional<String>.none) async throws {
        _ = try await data(request(method, path, body: try body.map { try JSONEncoder().encode($0) }), path: path)
    }

    fileprivate func decode<T: Decodable>(_ data: Data, path: String) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            Self.logger.error("could not read the answer of \(path, privacy: .public): \(String(describing: error), privacy: .public)")
            throw DaemonRequestError.transport(path: path, underlying: URLError(.cannotParseResponse))
        }
    }

    func data(_ request: URLRequest, path: String) async throws -> Data {
        try await dataAndResponse(request, path: path).0
    }

    func dataAndResponse(_ request: URLRequest, path: String) async throws -> (Data, HTTPURLResponse) {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError {
            throw DaemonRequestError.transport(path: path, underlying: error)
        }
        guard let http: HTTPURLResponse = response as? HTTPURLResponse else {
            throw DaemonRequestError.transport(path: path, underlying: URLError(.badServerResponse))
        }
        guard (200..<300).contains(http.statusCode) else {
            throw DaemonRequestError.status(path: path, code: http.statusCode, body: String(decoding: data, as: UTF8.self))
        }
        return (data, http)
    }
}

/// A page of events: those that read, how many elements came, the ids of those that did not read, and the highest id of
/// the page whether it read or not (where the next page starts).
struct EventsPage: Sendable {
    var events: [EventRecord]
    var size: Int
    var droppedIds: [Int]
    var topId: Int?

    var dropped: Int { droppedIds.count }
}

/// An event of a page that is kept if it reads and, if not, still says its id.
struct LossyEvent: Decodable, Sendable {
    let event: EventRecord?
    let id: Int?

    private enum Keys: String, CodingKey { case id }

    init(from decoder: any Decoder) throws {
        event = try? EventRecord(from: decoder)
        id = try? decoder.container(keyedBy: Keys.self).decode(Int.self, forKey: .id)
    }
}

/// An element of a page that is left out when it does not decode, rather than failing the page.
struct Lossy<Value: Decodable & Sendable>: Decodable, Sendable {
    let value: Value?

    init(from decoder: any Decoder) throws {
        value = try? Value(from: decoder)
    }
}

// MARK: Routes

/// A message for a chat (`POST /api/threads/{id}/queue`), written as the web composer writes it: the chat's own choices
/// (model, reasoning level, mode, speed, approvals, goal mode) and the agent whose chat it is ride with it, since the
/// daemon starts a message on what the message names; a part left out takes the daemon's own.
struct Message: Encodable, Sendable {
    let text: String
    let key: String
    let attachments: [String]
    let choices: MessageChoices

    init(text: String, key: String, attachments: [String], choices: MessageChoices = .none) {
        self.text = text
        self.key = key
        self.attachments = attachments
        self.choices = choices
    }

    enum CodingKeys: String, CodingKey {
        case text, key, attachments, mode, model, effort, speed, approvals
        case goalMode = "goal_mode"
        case agentId = "agent_id"
    }

    func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(text, forKey: .text)
        try container.encode(key, forKey: .key)
        try container.encode(attachments, forKey: .attachments)
        try container.encodeIfPresent(choices.mode, forKey: .mode)
        try container.encodeIfPresent(choices.model, forKey: .model)
        try container.encodeIfPresent(choices.effort, forKey: .effort)
        try container.encodeIfPresent(choices.speed, forKey: .speed)
        try container.encodeIfPresent(choices.approvals, forKey: .approvals)
        try container.encodeIfPresent(choices.goalMode, forKey: .goalMode)
        try container.encodeIfPresent(choices.agentId, forKey: .agentId)
    }
}

/// A message that starts a chat of its own (`POST /api/runs`), as the web's home and an agent's page send it: a message
/// from the home starts a chat on the model picked (item 1350), one to an agent that has no chat starts the agent's own.
/// `threadId` stays `nil` for a new chat; the picks are left out when the owner made none (the daemon's own).
struct NewChat: Encodable, Sendable {
    let goal: String
    let key: String
    let attachments: [String]
    let choices: MessageChoices

    init(goal: String, key: String, attachments: [String] = [], choices: MessageChoices = MessageChoices(mode: "auto")) {
        self.goal = goal
        self.key = key
        self.attachments = attachments
        self.choices = choices
    }

    enum CodingKeys: String, CodingKey {
        case goal, key, attachments, mode, model, effort, speed, approvals, standing
        case goalMode = "goal_mode"
        case agentId = "agent_id"
        case threadId = "thread_id"
    }

    func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(goal, forKey: .goal)
        try container.encode(key, forKey: .key)
        try container.encode(attachments, forKey: .attachments)
        try container.encode(false, forKey: .standing)
        try container.encode(choices.mode ?? "auto", forKey: .mode)
        try container.encodeIfPresent(choices.model, forKey: .model)
        try container.encodeIfPresent(choices.effort, forKey: .effort)
        try container.encodeIfPresent(choices.speed, forKey: .speed)
        try container.encodeIfPresent(choices.approvals, forKey: .approvals)
        try container.encodeIfPresent(choices.goalMode, forKey: .goalMode)
        // Null, not left out: a new chat names no chat to go on in, as the web's body does.
        try container.encodeNil(forKey: .threadId)
        try container.encodeIfPresent(choices.agentId, forKey: .agentId)
    }
}

private struct ChoicesChange: Encodable, Sendable { let choices: ChatChoices }
private struct Rewinding: Encodable, Sendable { let files: String }
private struct Reacting: Encodable, Sendable { let reaction: String }

/// A chat rewound to before one of the owner's messages (`POST /api/runs/{id}/rewind`): the message's words, which go
/// back to the composer.
struct RewindResult: Decodable, Sendable {
    let threadId: String
    let backTo: String?
    let text: String

    enum CodingKeys: String, CodingKey {
        case text
        case threadId = "thread_id"
        case backTo = "back_to"
    }
}

/// A reaction standing on a message of Dot's (`GET /api/threads/{id}/reactions`).
struct ReactionRecord: Codable, Sendable, Hashable {
    let runId: String
    let message: String
    let reaction: String

    enum CodingKeys: String, CodingKey {
        case message, reaction
        case runId = "run_id"
    }
}

struct ReactionOutcome: Decodable, Sendable {
    let reactions: [ReactionRecord]
    let effect: String
    let said: String
}

private struct Decision: Encodable, Sendable {
    let approve: Bool
    let note: String
    let remember: Bool
}

private struct Order: Encodable, Sendable { let ids: [String] }
private struct Seen: Encodable, Sendable { let seen: Bool }
private struct ModelPick: Encodable, Sendable { let model: String }
private struct RolePick: Encodable, Sendable {
    let role: String
    let model: String
}
private struct Code: Encodable, Sendable { let code: String }
private struct Nothing: Encodable, Sendable {}

/// A key for a message the app sends (`^[A-Za-z0-9-]{16,64}$`): sent again after a lost answer, the daemon starts the
/// message once.
func newMessageKey() -> String { UUID().uuidString }

extension APIClient {
    func threads(limit: Int = 100) async throws -> [ThreadSummary] {
        try await get("/api/threads", query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    func agents() async throws -> [AgentSummary] { try await get("/api/agents") }

    /// One approval as the daemon stores it: how it stands now (`pending`, `approved`, `denied`, `expired`, `left`).
    func approval(_ id: String) async throws -> ApprovalRecord { try await get("/api/approvals/\(id)") }

    /// The newest runs of a chat; with `before`, those that began before that time (item 1360), the page after the one
    /// whose oldest run began then.
    func runs(thread: String, limit: Int = 12, before: Double? = nil) async throws -> [RunRecord] {
        var query: [URLQueryItem] = [URLQueryItem(name: "thread_id", value: thread), URLQueryItem(name: "limit", value: String(limit))]
        // Written in full: a time rounded up to the microsecond would let the run it was read from through again.
        if let before { query.append(URLQueryItem(name: "before", value: String(before))) }
        return try await get("/api/runs", query: query)
    }

    /// Starts a chat of its own with a message.
    func start(chat: NewChat) async throws -> RunRecord { try await send("POST", "/api/runs", body: chat) }

    /// Keeps the choices the chat's next message starts with (`PATCH /api/threads/{id}`).
    func keep(choices: ChatChoices, thread: String) async throws {
        try await sendVoid("PATCH", "/api/threads/\(thread)", body: ChoicesChange(choices: choices))
    }

    /// Rewinds the chat to before the message of `run` (Edit from here); the files the runs changed stay as they are.
    func rewind(run: String) async throws -> RewindResult {
        try await send("POST", "/api/runs/\(run)/rewind", body: Rewinding(files: "keep"))
    }

    func reactions(thread: String) async throws -> [ReactionRecord] { try await get("/api/threads/\(thread)/reactions") }

    func react(run: String, message: String, reaction: String) async throws -> ReactionOutcome {
        try await send("POST", "/api/runs/\(run)/messages/\(message)/reactions", body: Reacting(reaction: reaction))
    }

    func unreact(run: String, message: String, reaction: String) async throws -> ReactionOutcome {
        try await send("DELETE", "/api/runs/\(run)/messages/\(message)/reactions/\(reaction)", body: Optional<String>.none)
    }

    func detail(run: String) async throws -> RunDetail { try await get("/api/runs/\(run)") }

    /// A run's stored events after `after`. An element that does not read is left out, not the whole page, and its id (when
    /// it has one) is kept so that the cursor goes past it once and the caller says it to the owner (a row of the chat).
    func events(run: String, after: Int, limit: Int = 2000) async throws -> EventsPage {
        let page: [LossyEvent] = try await get(
            "/api/runs/\(run)/events",
            query: [URLQueryItem(name: "after", value: String(after)), URLQueryItem(name: "limit", value: String(limit))])
        return Self.page(of: page, run: run)
    }

    /// The newest `count` stored events of a run, oldest first (item 1370); `nil` from a daemon that does not know `tail` (it
    /// would answer with the run's first events, which are no tail), so that the caller reads the pages as before.
    func eventsTail(run: String, count: Int) async throws -> EventsPage? {
        let path: String = "/api/runs/\(run)/events"
        let built: URLRequest = request("GET", path, query: [URLQueryItem(name: "tail", value: String(count))])
        let (data, response): (Data, HTTPURLResponse) = try await Retrier.run(readPolicy, link: link) {
            try await dataAndResponse(built, path: path)
        }
        guard response.value(forHTTPHeaderField: "X-Dot-Events-Tail") != nil else { return nil }
        let page: [LossyEvent] = try decode(data, path: path)
        return Self.page(of: page, run: run)
    }

    private static func page(of page: [LossyEvent], run: String) -> EventsPage {
        let read: [EventRecord] = page.compactMap(\.event)
        let failed: [LossyEvent] = page.filter { $0.event == nil }
        if !failed.isEmpty {
            Logger(subsystem: "dev.dotagent.phone", category: "api").error(
                "\(failed.count) events of \(run, privacy: .public) could not be read and are left out")
        }
        return EventsPage(
            events: read, size: page.count, droppedIds: failed.map { $0.id ?? UnreadableEvents.unknownId },
            topId: page.compactMap(\.id).max())
    }

    /// The approvals and questions waiting for the owner, in every chat (`GET /api/approvals?status=pending`).
    func pendingApprovals() async throws -> [ApprovalRecord] {
        try await get("/api/approvals", query: [URLQueryItem(name: "status", value: "pending"), URLQueryItem(name: "limit", value: "100")])
    }

    func helpers(thread: String) async throws -> [RunRecord] { try await get("/api/threads/\(thread)/helpers") }

    /// Sends a message to a chat: it starts a run when none works there, and is queued behind the one that does.
    @discardableResult
    func send(message: Message, thread: String) async throws -> QueueView {
        try await send("POST", "/api/threads/\(thread)/queue", body: message)
    }

    func queue(thread: String) async throws -> QueueView { try await get("/api/threads/\(thread)/queue") }

    func reorderQueue(thread: String, ids: [String]) async throws -> QueueView {
        try await send("PUT", "/api/threads/\(thread)/queue", body: Order(ids: ids))
    }

    func removeQueued(_ id: String) async throws -> QueueView {
        try await send("DELETE", "/api/queue/\(id)", body: Optional<String>.none)
    }

    func steerQueued(_ id: String) async throws -> QueueView {
        try await send("POST", "/api/queue/\(id)/steer", body: Optional<String>.none)
    }

    /// Lets a held queue go on (`POST /api/threads/{id}/queue/resume`): its next message starts now when no run works.
    func resumeQueue(thread: String) async throws -> QueueView {
        try await send("POST", "/api/threads/\(thread)/queue/resume", body: Optional<String>.none)
    }

    func stop(run: String) async throws {
        try await sendVoid("POST", "/api/runs/\(run)/cancel")
    }

    /// Answers an approval or a question; `note` is a question's answer. A deny skips it.
    func decide(approval: String, approve: Bool, note: String = "", remember: Bool = false) async throws -> ApprovalRecord {
        try await send("POST", "/api/approvals/\(approval)/decision", body: Decision(approve: approve, note: note, remember: remember))
    }

    func markSeen(thread: String) async throws {
        try await sendVoid("PATCH", "/api/threads/\(thread)", body: Seen(seen: true))
    }

    func schedules() async throws -> [ScheduleView] { try await get("/api/schedules") }

    func automations(thread: String) async throws -> [ThreadAutomation] { try await get("/api/threads/\(thread)/automations") }

    func setSchedule(_ id: String, on: Bool) async throws -> ScheduleView {
        try await send("POST", "/api/schedules/\(id)/\(on ? "enable" : "disable")", body: Optional<String>.none)
    }

    func status() async throws -> StatusView { try await get("/api/status") }

    func models() async throws -> [ProviderModels] { try await get("/api/models") }

    func chooseMain(model: String) async throws {
        try await sendVoid("POST", "/api/models/main", body: ModelPick(model: model))
    }

    func chooseRole(_ role: String, model: String) async throws {
        try await sendVoid("POST", "/api/models/roles", body: RolePick(role: role, model: model))
    }

    /// Trades the pairing code Settings › Phone on the Mac shows for a key of this phone's own (no key needed).
    func pair(code: String) async throws -> String {
        var request: URLRequest = request("POST", "/api/phone/pair", body: try JSONEncoder().encode(Code(code: code)))
        request.setValue(nil, forHTTPHeaderField: "Authorization")
        let paired: PairedKey = try decode(try await data(request, path: "/api/phone/pair"), path: "/api/phone/pair")
        return paired.token
    }
}
