import Foundation
import os

/// The daemon's HTTP API as the native screens use it: JSON in and out over HTTPS with the phone's key, on the host
/// and key the app already keeps (`DaemonHost`, `PhoneKeyStore`). Failures are `DaemonRequestError`, as the push
/// registration's. Nothing here retries: a screen shows what it has (the cache) and tries again at the next
/// reconnect, so that a request never holds the interface.
struct APIClient: Sendable {
    let host: DaemonHost
    let key: String
    let session: URLSession

    private static let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "api")

    /// A session for requests: HTTP/2 over the tailnet, no cache of its own (the app keeps its store), failing in 15 s.
    static func makeSession() -> URLSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.waitsForConnectivity = false
        configuration.httpMaximumConnectionsPerHost = 6
        return URLSession(configuration: configuration)
    }

    func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil) -> URLRequest {
        var url: URL = host.url(path: path)
        // A query item, not part of the path: `appending(path:)` writes a `?` as `%3F`.
        if !query.isEmpty { url.append(queryItems: query) }
        var request: URLRequest = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    func get<T: Decodable & Sendable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try decode(try await data(request("GET", path, query: query), path: path), path: path)
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
        return data
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

struct Message: Encodable, Sendable {
    let text: String
    let key: String
    let model: String?
    let attachments: [String]

    enum CodingKeys: String, CodingKey { case text, key, model, attachments }

    func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(text, forKey: .text)
        try container.encode(key, forKey: .key)
        try container.encodeIfPresent(model, forKey: .model)
        try container.encode(attachments, forKey: .attachments)
    }
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

    func runs(thread: String, limit: Int = 12) async throws -> [RunRecord] {
        try await get(
            "/api/runs", query: [URLQueryItem(name: "thread_id", value: thread), URLQueryItem(name: "limit", value: String(limit))])
    }

    func detail(run: String) async throws -> RunDetail { try await get("/api/runs/\(run)") }

    /// A run's stored events after `after`. An element that does not read is left out, not the whole page, and its id (when
    /// it has one) is kept so that the cursor goes past it once and the caller says it to the owner (a row of the chat).
    func events(run: String, after: Int, limit: Int = 2000) async throws -> EventsPage {
        let page: [LossyEvent] = try await get(
            "/api/runs/\(run)/events",
            query: [URLQueryItem(name: "after", value: String(after)), URLQueryItem(name: "limit", value: String(limit))])
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
