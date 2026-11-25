import CryptoKit
import Foundation

// The settings routes Settings on the phone reads and writes (wave N3). Each call is made once; the page that made it
// shows the failure and its Retry (a settings write is the owner's own tap, said again by tapping again).

private struct ModelBody: Encodable { let model: String }
private struct RoleBody: Encodable {
    let role: String
    let model: String
}
private struct ReactionSettingBody: Encodable {
    let key: String
    let value: JSONValue
}
private struct ProposalAtBody: Encodable { let at: Double }

extension DaemonAPI {
    public func assistant() async throws(DaemonRequestError) -> AssistantView { try await get("/api/assistant", query: []) }

    public func changeAssistant(_ change: AssistantChange) async throws(DaemonRequestError) -> AssistantView {
        try await send("PUT", "/api/assistant", body: Self.json(change))
    }

    /// `GET /api/health` (public; sent without the key).
    public func health() async throws(DaemonRequestError) -> HealthView {
        var built: URLRequest = request(method: "GET", path: "/api/health", query: [], body: nil)
        built.setValue(nil, forHTTPHeaderField: "Authorization")
        return try decode(try await data(built, path: "/api/health"), path: "/api/health")
    }

    public func fullStatus() async throws(DaemonRequestError) -> StatusView { try await get("/api/status", query: []) }

    public func phoneSetup() async throws(DaemonRequestError) -> PhoneSetupView { try await get("/api/phone", query: []) }

    public func pushSettings() async throws(DaemonRequestError) -> PushSettings { try await get("/api/phone/push-settings", query: []) }

    public func changePushSettings(_ settings: PushSettings) async throws(DaemonRequestError) -> PushSettings {
        try await send("PUT", "/api/phone/push-settings", body: Self.json(settings))
    }

    public func setMainModel(_ model: String) async throws(DaemonRequestError) -> ModelsConfig {
        try await send("POST", "/api/models/main", body: Self.json(ModelBody(model: model)))
    }

    public func setRole(_ role: ModelRole, model: String) async throws(DaemonRequestError) -> ModelsConfig {
        try await send("POST", "/api/models/roles", body: Self.json(RoleBody(role: role.rawValue, model: model)))
    }

    public func codeReview() async throws(DaemonRequestError) -> CodeReviewConfig { try await get("/api/code-review", query: []) }

    /// `PUT /api/code-review` (item 1770): the whole table, written all or none; a model the daemon cannot review with is a 422.
    public func changeCodeReview(_ change: CodeReviewChange) async throws(DaemonRequestError) -> CodeReviewConfig {
        try await send("PUT", "/api/code-review", body: Self.json(change))
    }

    /// `GET /api/budget` (item 1731); a daemon from before it answers 404.
    public func budget() async throws(DaemonRequestError) -> BudgetView { try await get("/api/budget", query: []) }

    /// `PUT /api/budget`: a phone may lower or set a cap; raising one or taking one off is refused (403, `PhoneRefused`).
    public func changeBudget(_ caps: BudgetCaps) async throws(DaemonRequestError) -> BudgetView {
        try await send("PUT", "/api/budget", body: Self.json(caps))
    }

    public func usage(days: Int) async throws(DaemonRequestError) -> UsageView {
        try await get("/api/usage", query: [URLQueryItem(name: "days", value: String(days))])
    }

    public func schedules() async throws(DaemonRequestError) -> [ScheduleView] { try await get("/api/schedules", query: []) }

    public func setSchedule(_ id: String, enabled: Bool) async throws(DaemonRequestError) -> ScheduleView {
        try await send("POST", "/api/schedules/\(id)/\(enabled ? "enable" : "disable")", body: nil)
    }

    public func editSchedule(_ id: String, edit: ScheduleEdit) async throws(DaemonRequestError) -> ScheduleView {
        try await send("PATCH", "/api/schedules/\(id)", body: Self.json(edit))
    }

    public func rules() async throws(DaemonRequestError) -> [RuleView] { try await get("/api/rules", query: []) }

    public func searchMemory(_ query: String, limit: Int) async throws(DaemonRequestError) -> [MemoryHit] {
        try await get("/api/memories/search", query: [URLQueryItem(name: "q", value: query), URLQueryItem(name: "limit", value: String(limit))])
    }

    /// The notes a run proposed to rewrite and that wait for the owner (item 1711), newest first.
    public func proposedNotes() async throws(DaemonRequestError) -> [MemoryNote] { try await get("/api/memories/proposed", query: []) }

    /// Takes a run's proposed words for the note ("Use"); `at` is the proposal shown, so a newer one is not taken blind.
    public func takeProposal(note: String, at: Double) async throws(DaemonRequestError) -> MemoryNote {
        try await send("POST", "/api/memories/\(note)/proposal/take", body: Self.json(ProposalAtBody(at: at)))
    }

    /// Drops it ("Drop"): the owner's words stay.
    public func dropProposal(note: String, at: Double) async throws(DaemonRequestError) -> MemoryNote {
        try await send("POST", "/api/memories/\(note)/proposal/drop", body: Self.json(ProposalAtBody(at: at)))
    }

    public func reactionSettings() async throws(DaemonRequestError) -> ReactionsConfig { try await get("/api/reactions/settings", query: []) }

    public func setReactionsFollowUps(_ on: Bool) async throws(DaemonRequestError) -> ReactionsConfig {
        try await send("POST", "/api/reactions/settings", body: Self.json(ReactionSettingBody(key: "follow_ups", value: .bool(on))))
    }

    public func releaseFailsafe() async throws(DaemonRequestError) -> FailsafeState { try await send("DELETE", "/api/failsafe", body: nil) }

    // MARK: The connection's probes (no key sent before the proof)

    /// `GET /api/health` without the key: the status, or the loading error, and how long it took.
    public func probeHealth() async -> ProbeAnswer {
        var built: URLRequest = request(method: "GET", path: "/api/health", query: [], body: nil)
        built.setValue(nil, forHTTPHeaderField: "Authorization")
        built.timeoutInterval = 8
        return await probe(built)
    }

    /// `GET /api/status` with the key.
    public func probeStatus() async -> ProbeAnswer {
        var built: URLRequest = request(method: "GET", path: "/api/status", query: [], body: nil)
        built.timeoutInterval = 8
        return await probe(built)
    }

    private func probe(_ built: URLRequest) async -> ProbeAnswer {
        let started: Date = Date()
        func millis() -> Int { Int(Date().timeIntervalSince(started) * 1000) }
        do {
            let (_, response): (Data, URLResponse) = try await session.data(for: built)
            guard let http: HTTPURLResponse = response as? HTTPURLResponse else { return .transport(code: URLError.badServerResponse.rawValue, millis: millis()) }
            return .answered(code: http.statusCode, millis: millis())
        } catch let error as URLError {
            return .transport(code: error.code.rawValue, millis: millis())
        } catch {
            return .transport(code: URLError.unknown.rawValue, millis: millis())
        }
    }

    /// Asks the daemon to prove it holds this phone's key (`GET /api/proof`, item 670) without sending the key.
    public func proveKey(nonce: String) async -> ProofOutcome {
        guard let phone: String = phoneKeyId(key) else { return .notRun }
        var built: URLRequest = request(
            method: "GET", path: "/api/proof", query: [URLQueryItem(name: "nonce", value: nonce), URLQueryItem(name: "phone", value: phone)], body: nil)
        built.setValue(nil, forHTTPHeaderField: "Authorization")
        built.timeoutInterval = 8
        do {
            let answer: ProofAnswer = try decode(try await data(built, path: "/api/proof"), path: "/api/proof")
            return answer.proof == expectedProof(key: key, nonce: nonce) ? .proven : .mismatch
        } catch {
            return error.statusCode == 404 ? .unsupported : .unanswered
        }
    }
}

/// The id of a phone's key (`phone.<12 hex>.<secret>`), or `nil` for another kind of key.
public func phoneKeyId(_ key: String) -> String? {
    let parts: [Substring] = key.split(separator: ".", omittingEmptySubsequences: false)
    guard parts.count == 3, parts[0] == "phone", parts[1].count == 12, parts[1].allSatisfy(\.isHexDigit), parts[2].count == 43 else { return nil }
    return String(parts[1])
}

/// What the daemon answers to `nonce` when it holds `key` (`server/guard.py`): HMAC-SHA256 over "dot daemon proof\n" +
/// nonce, keyed with the key itself, or for a phone's key with the hex SHA-256 of it (the daemon keeps only that hash).
public func expectedProof(key: String, nonce: String) -> String {
    let secret: String = phoneKeyId(key) == nil ? key : hex(SHA256.hash(data: Data(key.utf8)))
    let mac: HMAC<SHA256>.MAC = HMAC<SHA256>.authenticationCode(for: Data("dot daemon proof\n\(nonce)".utf8), using: SymmetricKey(data: Data(secret.utf8)))
    return hex(mac)
}

private func hex(_ bytes: some Sequence<UInt8>) -> String { bytes.map { String(format: "%02x", $0) }.joined() }

/// A fresh nonce for the proof: 32 random bytes as lowercase hex.
public func proofNonce() -> String {
    var generator: SystemRandomNumberGenerator = SystemRandomNumberGenerator()
    return (0..<32).map { _ in String(format: "%02x", UInt8.random(in: 0...255, using: &generator)) }.joined()
}
