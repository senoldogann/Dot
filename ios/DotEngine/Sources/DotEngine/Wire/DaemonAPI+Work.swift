import Foundation

// The routes a bot's profile reads for its work and pauses it with (`routes_agents.py`). The web lists work orders,
// objectives and the team's messages without writing them, so the phone only reads; pausing and resuming an agent is the
// owner's own tap, made once, and the screen that made it shows the failure.

extension DaemonAPI {
    /// The work orders the lead handed out: the open ones first, then the newest that were accepted or cancelled.
    public func workOrders() async throws(DaemonRequestError) -> [WorkOrderView] { try await get("/api/work-orders", query: []) }

    /// The owner's objectives: the active ones, then those done or let go.
    public func objectives() async throws(DaemonRequestError) -> [ObjectiveRecord] { try await get("/api/objectives", query: []) }

    /// Today's messages between agents, oldest first.
    public func team() async throws(DaemonRequestError) -> TeamView { try await get("/api/team", query: []) }

    /// Pauses the agent (`POST /api/agents/{id}/pause`, item 465): its schedules start nothing by themselves until it is resumed.
    /// Dot is refused by the daemon (422).
    public func pauseAgent(_ id: String) async throws(DaemonRequestError) -> AgentRecord {
        try await send("POST", "/api/agents/\(id)/pause", body: nil)
    }

    /// Lets a paused agent's schedules fire again (`POST /api/agents/{id}/resume`).
    public func resumeAgent(_ id: String) async throws(DaemonRequestError) -> AgentRecord {
        try await send("POST", "/api/agents/\(id)/resume", body: nil)
    }
}
