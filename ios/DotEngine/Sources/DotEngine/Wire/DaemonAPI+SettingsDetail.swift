import Foundation

// The routes of the Settings pages made native in items 1771-1774, the same the web's tabs call, within what a phone
// is for (PLAN §26). A phone's key is refused only where the daemon says so (`PhoneRefused`, 403): the page says it
// with the Mac's place.

extension DaemonAPI {
    // MARK: Scheduled tasks (item 1771)

    /// `DELETE /api/schedules/{id}`: the schedule as it was.
    public func deleteSchedule(_ id: String) async throws(DaemonRequestError) -> ScheduleView {
        try await send("DELETE", "/api/schedules/\(id)", body: nil)
    }

    /// `POST /api/schedules/{id}/run`: a run of it now, besides its rhythm.
    public func runSchedule(_ id: String) async throws(DaemonRequestError) -> RunRecord {
        try await send("POST", "/api/schedules/\(id)/run", body: nil)
    }

    /// `POST /api/schedules`: a new schedule, switched on.
    public func createSchedule(_ schedule: ScheduleCreate) async throws(DaemonRequestError) -> ScheduleView {
        try await send("POST", "/api/schedules", body: Self.json(schedule))
    }

    /// `GET /api/schedules/{id}/firings`: its last runs, newest first (the daemon keeps at most 50 in a page).
    public func scheduleFirings(_ id: String, limit: Int) async throws(DaemonRequestError) -> [ScheduleFiring] {
        try await get("/api/schedules/\(id)/firings", query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    // MARK: Rules (item 1772)

    public func freeMode() async throws(DaemonRequestError) -> FreeModeView { try await get("/api/policy/free", query: []) }

    /// `PUT /api/policy/free`: a phone's key may switch it off, not on.
    public func setFreeMode(_ free: Bool) async throws(DaemonRequestError) -> FreeModeView {
        try await send("PUT", "/api/policy/free", body: Self.json(FreeModeView(free: free)))
    }

    // MARK: Memory (item 1773)

    public func memories(limit: Int, offset: Int, order: MemoryOrder) async throws(DaemonRequestError) -> MemoryList {
        try await get(
            "/api/memories",
            query: [URLQueryItem(name: "limit", value: String(limit)), URLQueryItem(name: "offset", value: String(offset)), URLQueryItem(name: "order", value: order.rawValue)])
    }

    public func editMemory(_ id: String, note: MemoryWrite) async throws(DaemonRequestError) -> MemoryNote {
        try await send("PATCH", "/api/memories/\(id)", body: Self.json(note))
    }

    /// `DELETE /api/memories/{id}`: the note goes to the trash, which the Mac's Memory page empties or restores.
    public func forgetMemory(_ id: String) async throws(DaemonRequestError) -> MemoryNote { try await send("DELETE", "/api/memories/\(id)", body: nil) }

    // MARK: Agents (item 1774)

    public func agentList() async throws(DaemonRequestError) -> [AgentListItem] { try await get("/api/agents", query: []) }

    public func agent(_ id: String) async throws(DaemonRequestError) -> AgentDetailView { try await get("/api/agents/\(id)", query: []) }

    /// `PUT /api/agents/{id}`: the whole agent as it should stand (the phone changes its model and keeps the rest).
    public func editAgent(_ id: String, agent: AgentWrite) async throws(DaemonRequestError) -> AgentRecordView {
        try await send("PUT", "/api/agents/\(id)", body: Self.json(agent))
    }

    /// `POST /api/agents/{id}/welcome`: the agent's first chat, in which it greets the owner (409: it has one already).
    public func welcomeAgent(_ id: String, welcome: AgentWelcome) async throws(DaemonRequestError) -> RunRecord {
        try await send("POST", "/api/agents/\(id)/welcome", body: Self.json(welcome))
    }
}
