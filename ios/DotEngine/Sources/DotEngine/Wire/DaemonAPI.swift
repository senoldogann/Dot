import Foundation
import os

/// The daemon's HTTP API as the engine uses it: JSON over HTTPS with the phone's key. A call is made once; who tries it
/// again is the caller's to decide (the supervisor for reads and streams, the outbox for the owner's doings), so that
/// one owner holds every retry (plan §5, R-f §3).
public struct DaemonAPI: Sendable {
    public let host: DaemonHost
    public let key: String
    public let session: URLSession
    public let streamSession: URLSession

    public init(host: DaemonHost, key: String, session: URLSession, streamSession: URLSession) {
        self.host = host
        self.key = key
        self.session = session
        self.streamSession = streamSession
    }

    static let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "api")

    /// A session for requests: no cache of its own (the engine keeps its store); a read gives up after 10 s of silence.
    public static func makeSession() -> URLSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 60
        configuration.waitsForConnectivity = false
        configuration.httpMaximumConnectionsPerHost = 6
        return URLSession(configuration: configuration)
    }

    /// A session for the event streams: no limit on the whole; the supervisor's own 12 s silence rule ends a dead one.
    public static func makeStreamSession() -> URLSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 3600
        configuration.timeoutIntervalForResource = 7 * 24 * 3600
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }

    public func request(method: String, path: String, query: [URLQueryItem], body: Data?) -> URLRequest {
        var url: URL = host.url(path: path)
        // A query item, not part of the path: `appending(path:)` writes a `?` as `%3F`.
        if !query.isEmpty { url.append(queryItems: query) }
        var request: URLRequest = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = method == "GET" ? 10 : 30
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    func data(_ request: URLRequest, path: String) async throws(DaemonRequestError) -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError {
            throw .transport(path: path, underlying: error)
        } catch {
            throw .transport(path: path, underlying: URLError(.unknown))
        }
        guard let http: HTTPURLResponse = response as? HTTPURLResponse else {
            throw .transport(path: path, underlying: URLError(.badServerResponse))
        }
        guard (200..<300).contains(http.statusCode) else {
            throw .status(path: path, code: http.statusCode, body: String(decoding: data, as: UTF8.self))
        }
        return data
    }

    func decode<T: Decodable>(_ data: Data, path: String) throws(DaemonRequestError) -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            Self.logger.error("undecodable answer path=\(path, privacy: .public) error=\(String(describing: error), privacy: .public)")
            throw .undecodable(path: path, detail: String(describing: error))
        }
    }

    func get<T: Decodable>(_ path: String, query: [URLQueryItem]) async throws(DaemonRequestError) -> T {
        try decode(try await data(request(method: "GET", path: path, query: query, body: nil), path: path), path: path)
    }

    func send<T: Decodable>(_ method: String, _ path: String, body: Data?) async throws(DaemonRequestError) -> T {
        try decode(try await data(request(method: method, path: path, query: [], body: body), path: path), path: path)
    }

    func sendVoid(_ method: String, _ path: String, body: Data?) async throws(DaemonRequestError) {
        _ = try await data(request(method: method, path: path, query: [], body: body), path: path)
    }

    static func json(_ value: some Encodable) -> Data {
        do {
            return try JSONEncoder().encode(value)
        } catch {
            preconditionFailure("a request body of the engine's own types does not encode: \(error)")
        }
    }

    // MARK: Streams

    /// Opens an event stream; the bytes as they come. A status other than 2xx is thrown with its body.
    public func openStream(path: String, query: [URLQueryItem]) async throws(DaemonRequestError) -> URLSession.AsyncBytes {
        var built: URLRequest = request(method: "GET", path: path, query: query, body: nil)
        built.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        built.timeoutInterval = 3600
        let bytes: URLSession.AsyncBytes
        let response: URLResponse
        do {
            (bytes, response) = try await streamSession.bytes(for: built)
        } catch let error as URLError {
            throw .transport(path: path, underlying: error)
        } catch {
            throw .transport(path: path, underlying: URLError(.unknown))
        }
        guard let http: HTTPURLResponse = response as? HTTPURLResponse else {
            throw .transport(path: path, underlying: URLError(.badServerResponse))
        }
        guard (200..<300).contains(http.statusCode) else {
            var body: [UInt8] = []
            do {
                for try await byte in bytes where body.count < 2000 { body.append(byte) }
            } catch {
                Self.logger.warning("the refused stream's body broke off path=\(path, privacy: .public)")
            }
            throw .status(path: path, code: http.statusCode, body: String(decoding: body, as: UTF8.self))
        }
        return bytes
    }
}

// MARK: Routes

/// A message for a chat (`POST /api/threads/{id}/queue`): it starts a run when none works there and is queued behind
/// the one that does. `key` makes a second sending the same message (`send_once`, kind `queue`).
public struct QueueMessage: Codable, Sendable, Hashable {
    public let text: String
    public let key: String
    public let attachments: [String]
    /// The skills and accounts picked in the composer (PLAN §22) and "plan first" (`/plan`).
    public let skills: [String]
    public let accounts: [String]
    public let planFirst: Bool

    public init(text: String, key: String, attachments: [String], skills: [String], accounts: [String], planFirst: Bool) {
        self.text = text
        self.key = key
        self.attachments = attachments
        self.skills = skills
        self.accounts = accounts
        self.planFirst = planFirst
    }

    enum CodingKeys: String, CodingKey {
        case text, key, attachments, skills, accounts
        case planFirst = "plan_first"
    }
}

/// A message into the run at work (`POST /api/runs/{id}/steer`, kind `steer`).
public struct SteerMessage: Codable, Sendable, Hashable {
    public let text: String
    public let key: String
    public let attachments: [String]

    public init(text: String, key: String, attachments: [String]) {
        self.text = text
        self.key = key
        self.attachments = attachments
    }
}

/// A message that starts a chat of its own (`POST /api/runs`, kind `run`).
public struct NewChatMessage: Codable, Sendable, Hashable {
    public let goal: String
    public let key: String
    public let attachments: [String]
    public let mode: String
    public let standing: Bool
    /// What the owner chose in the chat's settings before the first message (PLAN §24); left out, the daemon's.
    public let model: String?
    public let effort: String?
    public let speed: String
    public let approvals: String?
    /// Goal mode as the chat's choices hold it; `nil` takes `[agent] goal_mode`.
    public let goalMode: Bool?
    public let planFirst: Bool

    public init(goal: String, key: String, attachments: [String], start: ChatStart) {
        self.goal = goal
        self.key = key
        self.attachments = attachments
        self.mode = start.choices.mode
        self.standing = false
        self.model = start.choices.model
        self.effort = start.choices.effort
        self.speed = start.choices.speed
        self.approvals = start.choices.approvals
        self.goalMode = start.choices.goalMode
        self.planFirst = start.planFirst
    }

    enum CodingKeys: String, CodingKey {
        case goal, key, attachments, mode, standing, model, effort, speed, approvals
        case goalMode = "goal_mode"
        case planFirst = "plan_first"
    }
}

/// What a new chat starts with (PLAN §24): the choices picked before its first message, and "plan first".
public struct ChatStart: Codable, Sendable, Hashable {
    public let choices: ChatChoices
    public let planFirst: Bool

    public init(choices: ChatChoices, planFirst: Bool) {
        self.choices = choices
        self.planFirst = planFirst
    }

    public static let daemons: ChatStart = ChatStart(choices: .daemons, planFirst: false)
}

struct AttachmentUpload: Encodable, Sendable {
    let name: String
    let mediaType: String
    let dataB64: String

    enum CodingKeys: String, CodingKey {
        case name
        case mediaType = "media_type"
        case dataB64 = "data_b64"
    }
}

private struct StoredAttachmentId: Decodable { let id: String }
private struct DecisionBody: Encodable {
    let approve: Bool
    let note: String
    let remember: Bool
}
private struct SecretBody: Encodable { let value: String }
private struct ReactionBody: Encodable { let reaction: String }
private struct AsideBody: Encodable { let text: String }
private struct LoopBody: Encodable {
    let every: String
    let text: String
}
private struct ChoicesBody: Encodable { let choices: ChatChoices }
private struct QueueEditBody: Encodable {
    let text: String
    let attachments: [String]?
}
private struct OrderBody: Encodable { let ids: [String] }
private struct IdsBody: Encodable { let ids: [String] }
private struct TitleBody: Encodable { let title: String }
private struct PinBody: Encodable { let pinned: Bool }
private struct SeenBody: Encodable { let seen: Bool }
private struct CodeBody: Encodable { let code: String }

extension DaemonAPI {
    /// `GET /api/threads/view`: the list. A daemon from before item 1686 answers 404 (`DaemonTooOld`).
    public func threadsView() async throws(DaemonRequestError) -> ThreadsView { try await get("/api/threads/view", query: []) }

    /// `GET /api/threads/{id}/view`, the newest `tail` runs.
    public func threadView(_ thread: String, tail: Int) async throws(DaemonRequestError) -> ThreadView {
        try await get("/api/threads/\(thread)/view", query: [URLQueryItem(name: "tail", value: String(tail))])
    }

    public func status() async throws(DaemonRequestError) -> DaemonStatus { try await get("/api/status", query: []) }

    public func uploadAttachment(name: String, jpeg: Data) async throws(DaemonRequestError) -> String {
        let body: Data = Self.json(AttachmentUpload(name: name, mediaType: "image/jpeg", dataB64: jpeg.base64EncodedString()))
        let stored: StoredAttachmentId = try await send("POST", "/api/attachments", body: body)
        return stored.id
    }

    public func queue(_ message: QueueMessage, thread: String) async throws(DaemonRequestError) -> QueueView {
        try await send("POST", "/api/threads/\(thread)/queue", body: Self.json(message))
    }

    public func steer(_ message: SteerMessage, run: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/runs/\(run)/steer", body: Self.json(message))
    }

    public func startChat(_ message: NewChatMessage) async throws(DaemonRequestError) -> RunRecord {
        try await send("POST", "/api/runs", body: Self.json(message))
    }

    public func decide(approval: String, approve: Bool, note: String, remember: Bool) async throws(DaemonRequestError) -> ApprovalRecord {
        try await send(
            "POST", "/api/approvals/\(approval)/decision", body: Self.json(DecisionBody(approve: approve, note: note, remember: remember)))
    }

    /// Puts a reaction on a message of Dot's (`POST /runs/{id}/messages/{message}/reactions`, item 586).
    public func react(run: String, message: String, reaction: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/runs/\(run)/messages/\(message)/reactions", body: Self.json(ReactionBody(reaction: reaction)))
    }

    public func unreact(run: String, message: String, reaction: String) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/runs/\(run)/messages/\(message)/reactions/\(reaction)", body: nil)
    }

    /// Keeps the choices the chat's next follow-up starts with (item 199).
    public func setChoices(thread: String, choices: ChatChoices) async throws(DaemonRequestError) {
        try await sendVoid("PATCH", "/api/threads/\(thread)", body: Self.json(ChoicesBody(choices: choices)))
    }

    /// The models of every provider the daemon knows (`GET /api/models`).
    public func models() async throws(DaemonRequestError) -> [ProviderModels] { try await get("/api/models", query: []) }

    /// The emergency stop (`POST /api/failsafe`): every run stops, desktop control is blocked and the scheduler held
    /// until it is released on the Mac.
    public func emergencyStop() async throws(DaemonRequestError) -> FailsafeState { try await send("POST", "/api/failsafe", body: nil) }

    public func failsafe() async throws(DaemonRequestError) -> FailsafeState { try await get("/api/failsafe", query: []) }

    /// A screenshot a tool returned (`GET /api/images/{name}`).
    public func image(named name: String) async throws(DaemonRequestError) -> Data {
        let path: String = "/api/images/\(name)"
        return try await data(request(method: "GET", path: path, query: [], body: nil), path: path)
    }

    /// A side question the run answers without seeing it (`/btw`, item 650's aside): the answer's words.
    public func aside(run: String, question: String) async throws(DaemonRequestError) -> AsideAnswer {
        try await send("POST", "/api/runs/\(run)/btw", body: Self.json(AsideBody(text: question)))
    }

    /// A loop of the chat (`/loop 30m <message>`, item 281): the message asked again on that rhythm.
    public func loop(run: String, every: String, text: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/runs/\(run)/loop", body: Self.json(LoopBody(every: every, text: text)))
    }

    /// What the composer's menus offer: skills and accounts (`GET /api/palette`).
    public func palette() async throws(DaemonRequestError) -> PaletteView { try await get("/api/palette", query: []) }

    public func agents() async throws(DaemonRequestError) -> [AgentSummary] { try await get("/api/agents", query: []) }

    public func teams() async throws(DaemonRequestError) -> [TeamSummary] { try await get("/api/teams", query: []) }

    /// What a run did that undo can or cannot take back (`GET /api/runs/{id}/actions`).
    public func runActions(run: String) async throws(DaemonRequestError) -> RunActions { try await get("/api/runs/\(run)/actions", query: []) }

    /// What a run changed (`GET /api/runs/{id}/changes`).
    public func changes(run: String) async throws(DaemonRequestError) -> RunChanges { try await get("/api/runs/\(run)/changes", query: []) }

    /// One approval as it stands now (`pending`, `approved`, `denied`, `expired`, `left`).
    public func approval(_ id: String) async throws(DaemonRequestError) -> ApprovalRecord {
        try await get("/api/approvals/\(id)", query: [])
    }

    /// Gives the secret a card asks for (item 857); the value is sent once and kept nowhere on the phone.
    public func giveSecret(approval: String, value: String) async throws(DaemonRequestError) -> ApprovalRecord {
        try await send("POST", "/api/approvals/\(approval)/secret", body: Self.json(SecretBody(value: value)))
    }

    public func stop(run: String) async throws(DaemonRequestError) { try await sendVoid("POST", "/api/runs/\(run)/cancel", body: nil) }

    /// Stops a helper by the path its view names (`stop`, `POST`).
    public func stop(path: String) async throws(DaemonRequestError) { try await sendVoid("POST", path, body: nil) }

    public func editQueued(_ message: String, text: String, attachments: [String]?) async throws(DaemonRequestError) -> QueueView {
        try await send("PATCH", "/api/queue/\(message)", body: Self.json(QueueEditBody(text: text, attachments: attachments)))
    }

    public func reorderQueue(thread: String, ids: [String]) async throws(DaemonRequestError) -> QueueView {
        try await send("PUT", "/api/threads/\(thread)/queue", body: Self.json(OrderBody(ids: ids)))
    }

    public func removeQueued(_ message: String) async throws(DaemonRequestError) -> QueueView {
        try await send("DELETE", "/api/queue/\(message)", body: nil)
    }

    public func steerQueued(_ message: String) async throws(DaemonRequestError) -> QueueView {
        try await send("POST", "/api/queue/\(message)/steer", body: nil)
    }

    public func resumeQueue(thread: String) async throws(DaemonRequestError) -> QueueView {
        try await send("POST", "/api/threads/\(thread)/queue/resume", body: nil)
    }

    public func rename(thread: String, title: String) async throws(DaemonRequestError) {
        try await sendVoid("PATCH", "/api/threads/\(thread)", body: Self.json(TitleBody(title: title)))
    }

    public func pin(thread: String, pinned: Bool) async throws(DaemonRequestError) {
        try await sendVoid("PATCH", "/api/threads/\(thread)", body: Self.json(PinBody(pinned: pinned)))
    }

    public func markSeen(thread: String) async throws(DaemonRequestError) {
        try await sendVoid("PATCH", "/api/threads/\(thread)", body: Self.json(SeenBody(seen: true)))
    }

    public func delete(threads: [String]) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/threads", body: Self.json(IdsBody(ids: threads)))
    }

    /// Trades the 8-character code Settings › Phone on the Mac shows for a key of this phone's own (no key needed).
    public static func pair(code: String, host: DaemonHost, session: URLSession) async throws(DaemonRequestError) -> String {
        let api: DaemonAPI = DaemonAPI(host: host, key: "", session: session, streamSession: session)
        var built: URLRequest = api.request(method: "POST", path: "/api/phone/pair", query: [], body: json(CodeBody(code: code)))
        built.setValue(nil, forHTTPHeaderField: "Authorization")
        let paired: PairedKey = try api.decode(try await api.data(built, path: "/api/phone/pair"), path: "/api/phone/pair")
        return paired.token
    }
}
