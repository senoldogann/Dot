import Foundation

// The agent routes the phone's bots use (PLAN §27, `routes_agents.py`). Each is the owner's own tap, made once; the
// screen that made it shows the failure.

private struct WelcomeBody: Encodable { let language: String }

extension DaemonAPI {
    /// Makes an agent (`POST /api/agents`).
    public func createAgent(_ request: AgentRequest) async throws(DaemonRequestError) -> AgentRecord {
        try await send("POST", "/api/agents", body: Self.json(request))
    }

    /// Changes an agent (`PUT /api/agents/{id}`); Dot is refused by the daemon.
    public func updateAgent(_ id: String, _ request: AgentRequest) async throws(DaemonRequestError) -> AgentRecord {
        try await send("PUT", "/api/agents/\(id)", body: Self.json(request))
    }

    /// Starts an agent's first meeting with the owner (item 993): it greets in its role, in `language` (named in English,
    /// "Turkish"), and asks one question; the run of that chat. 409 once it has a chat.
    public func welcome(agent: String, language: String) async throws(DaemonRequestError) -> RunRecord {
        try await send("POST", "/api/agents/\(agent)/welcome", body: Self.json(WelcomeBody(language: language)))
    }

    /// An agent as a file to share (item 315, `GET /api/agents/{id}/export`): the JSON as the daemon wrote it.
    public func exportAgent(_ id: String) async throws(DaemonRequestError) -> Data {
        let path: String = "/api/agents/\(id)/export"
        return try await data(request(method: "GET", path: path, query: [], body: nil), path: path)
    }

    /// Deletes an agent (`DELETE /api/agents/{id}`); its chats stay, worn by Dot from then on.
    public func deleteAgent(_ id: String) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/agents/\(id)", body: nil)
    }

    /// What the chats say that holds the words of `query` (`GET /api/threads/search`), best first.
    public func searchChats(_ query: String, limit: Int) async throws(DaemonRequestError) -> [ChatHit] {
        try await get("/api/threads/search", query: [URLQueryItem(name: "q", value: query), URLQueryItem(name: "limit", value: String(limit))])
    }
}
