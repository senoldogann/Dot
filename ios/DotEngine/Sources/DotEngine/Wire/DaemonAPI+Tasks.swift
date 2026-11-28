import Foundation

// The routes of a chat's tasks (item 2013, `routes_threads.py` and `routes_queue.py`): the list of them, the Stop of
// every task, and **Move** of a message or a run the router sent to the wrong task.

/// The body of a Move: another task's thread, or `nil` for a new task, sent as `null` (the daemon requires the key).
struct MoveBody: Encodable {
    let toThreadId: String?

    enum CodingKeys: String, CodingKey {
        case toThreadId = "to_thread_id"
    }

    func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(toThreadId, forKey: .toThreadId)
    }
}

extension DaemonAPI {
    /// The tasks of the chat of `thread` (`GET /api/threads/{id}/tasks`): a lane's names its chat's.
    public func tasks(of thread: String) async throws(DaemonRequestError) -> TasksView {
        try await get("/api/threads/\(thread)/tasks", query: [])
    }

    /// Stops every task of the chat (`POST /api/threads/{id}/stop`, item 1984): its own run and its lanes'.
    public func stopChat(thread: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/threads/\(thread)/stop", body: nil)
    }

    /// Moves a queued message the router put in a task's queue to `to` (a task's thread), or to a new task (`nil`).
    public func moveQueued(_ message: String, to thread: String?) async throws(DaemonRequestError) -> RoutedMessage {
        try await send("POST", "/api/queue/\(message)/move", body: Self.json(MoveBody(toThreadId: thread)))
    }

    /// Stops a run the router started for the wrong task and sends its words to `to`, or to a new task (`nil`).
    public func moveRun(_ run: String, to thread: String?) async throws(DaemonRequestError) -> RoutedMessage {
        try await send("POST", "/api/runs/\(run)/move", body: Self.json(MoveBody(toThreadId: thread)))
    }
}
