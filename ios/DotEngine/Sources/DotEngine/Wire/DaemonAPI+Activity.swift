import Foundation

// The routes the Activity tab reads and writes (item 1826). Each call is made once; the screen that made it shows the
// failure and its Retry (a write is the owner's own tap, said again by tapping again).

private struct AwayMarkBody: Encodable { let until: Double }
private struct EnabledBody: Encodable { let enabled: Bool }
private struct TodoBody: Encodable {
    let title: String
    let why: String
}

extension DaemonAPI {
    public func activity() async throws(DaemonRequestError) -> ActivityView { try await get("/api/activity", query: []) }

    /// What Dot did on its own since `since` (the daemon keeps it within the last week).
    public func away(since: Double) async throws(DaemonRequestError) -> AwayView {
        try await get("/api/away", query: [URLQueryItem(name: "since", value: String(since))])
    }

    public func awayRead() async throws(DaemonRequestError) -> AwayRead { try await get("/api/away/read", query: []) }

    /// Marks "while you were away" read up to `until`, for every device (never back, never past now).
    public func markAwayRead(until: Double) async throws(DaemonRequestError) -> AwayRead {
        try await send("POST", "/api/away/read", body: Self.json(AwayMarkBody(until: until)))
    }

    public func brief() async throws(DaemonRequestError) -> BriefView { try await get("/api/brief", query: []) }

    public func changeBrief(_ change: BriefChange) async throws(DaemonRequestError) -> BriefView {
        try await send("PUT", "/api/brief", body: Self.json(change))
    }

    public func checkin() async throws(DaemonRequestError) -> CheckinView { try await get("/api/checkin", query: []) }

    public func changeCheckin(_ change: CheckinChange) async throws(DaemonRequestError) -> CheckinView {
        try await send("PUT", "/api/checkin", body: Self.json(change))
    }

    /// Starts a run of a schedule now, by hand (`POST /api/schedules/{id}/run`; 409 while one of its runs works, while
    /// the emergency stop holds, or in a daemon without a scheduler).
    public func runScheduleNow(_ id: String) async throws(DaemonRequestError) -> RunRecord {
        try await send("POST", "/api/schedules/\(id)/run", body: nil)
    }

    public func initiative() async throws(DaemonRequestError) -> InitiativeView { try await get("/api/initiative", query: []) }

    public func setInitiative(enabled: Bool) async throws(DaemonRequestError) -> InitiativeView {
        try await send("PUT", "/api/initiative", body: Self.json(EnabledBody(enabled: enabled)))
    }

    /// Puts a thing of the owner's on Dot's list (422 when it is on it, or was done or let go in the last 30 days).
    public func addTodo(title: String) async throws(DaemonRequestError) -> InitiativeView {
        try await send("POST", "/api/initiative/todos", body: Self.json(TodoBody(title: title, why: "")))
    }

    public func dropTodo(_ id: String) async throws(DaemonRequestError) -> InitiativeView {
        try await send("POST", "/api/initiative/todos/\(id)/drop", body: nil)
    }

    public func todoAgain(_ id: String) async throws(DaemonRequestError) -> InitiativeView {
        try await send("POST", "/api/initiative/todos/\(id)/again", body: nil)
    }

    public func watchers() async throws(DaemonRequestError) -> [WatcherView] { try await get("/api/watchers", query: []) }

    /// Changes a watcher: the whole of its choices (the phone switches one on or off and sends the rest as they are).
    public func changeWatcher(_ id: String, _ request: WatcherRequest) async throws(DaemonRequestError) -> WatcherView {
        try await send("PATCH", "/api/watchers/\(id)", body: Self.json(request))
    }

    /// The drafts that wait for the owner, newest first (item 1825).
    public func waitingDrafts(limit: Int) async throws(DaemonRequestError) -> [DraftView] {
        try await get("/api/drafts", query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    public func draft(_ id: String) async throws(DaemonRequestError) -> DraftView { try await get("/api/drafts/\(id)", query: []) }

    /// Hands the draft to Mail or Messages with `words`: `sent`, or `failed` with what the application said (409 when it
    /// no longer waits, 422 when the words cannot go out).
    public func sendDraft(_ id: String, words: DraftWords) async throws(DaemonRequestError) -> DraftView {
        try await send("POST", "/api/drafts/\(id)/send", body: Self.json(words))
    }

    public func discardDraft(_ id: String) async throws(DaemonRequestError) -> DraftView {
        try await send("POST", "/api/drafts/\(id)/discard", body: nil)
    }

    /// The harness (Self-improvement): the version in use and the newest `limit` versions, its proposals among them.
    public func harness(limit: Int) async throws(DaemonRequestError) -> HarnessView {
        try await get("/api/harness", query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    /// Puts a version in use: the tasks that start from now on follow it (one the gate for learned texts refuses now is
    /// not adopted: 422).
    public func adoptHarness(_ id: String) async throws(DaemonRequestError) -> HarnessVersion {
        try await send("POST", "/api/harness/\(id)/adopt", body: nil)
    }

    /// Sets a version aside; it stays, to come back to.
    public func retireHarness(_ id: String) async throws(DaemonRequestError) -> HarnessVersion {
        try await send("POST", "/api/harness/\(id)/retire", body: nil)
    }

    /// The run of `id`, for the chat it is in.
    public func runHead(_ id: String) async throws(DaemonRequestError) -> RunHead { try await get("/api/runs/\(id)", query: []) }
}
