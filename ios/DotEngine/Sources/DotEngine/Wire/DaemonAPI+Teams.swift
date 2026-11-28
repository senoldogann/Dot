import Foundation

// The owner's teams (item 610, `routes_teams.py`) as group chats, and the bots and teams to start from (items 315 and 504,
// `routes_agents.py`), for the phone's "+" (PLAN §27: New bot, New group chat). Editing and removing a team stay on the
// Mac (PLAN §26).

/// A team as `POST /api/teams` and a team's page give it (`TeamRecord`): its name, what it is for and its bots' ids.
public struct TeamRecordView: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let purpose: String
    public let agents: [String]
}

/// `GET /api/teams/{id}`: the team, its bots as they are now and its chats, the newest activity first.
public struct TeamDetail: Codable, Sendable, Hashable {
    public let record: TeamRecordView
    public let members: [AgentRecord]
    public let chats: [ThreadSummary]
}

/// The body of `POST /api/teams` (`TeamRequest`); `TeamEdit.checked` makes one the daemon takes.
public struct TeamEdit: Codable, Sendable, Hashable {
    public let name: String
    public let purpose: String
    public let agents: [String]

    /// A team the daemon takes, or `nil`: a name of 1 to 60 code points once trimmed (as Python counts them), a purpose of
    /// at most 500, and 1 to 50 bots, each once (`TeamRequest`'s limits), in the order given.
    public static func checked(name: String, purpose: String, agents: [String]) -> TeamEdit? {
        let title: String = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let does: String = purpose.trimmingCharacters(in: .whitespacesAndNewlines)
        let unique: [String] = agents.reduce(into: [String]()) { kept, id in if !kept.contains(id) { kept.append(id) } }
        guard !title.isEmpty, title.unicodeScalars.count <= 60, does.unicodeScalars.count <= 500, (1...50).contains(unique.count) else { return nil }
        return TeamEdit(name: title, purpose: does, agents: unique)
    }
}

/// A bot to start from (`GET /api/agent-templates`, `AgentTemplate`): what its card shows, the questions its Use asks
/// first (three at most), and the schedule it suggests (`schedule` in words, `cron` and `task` as one is made; all
/// three empty for one that runs when asked).
public struct BotTemplate: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let role: String
    public let look: AgentLook
    public let needs: String
    public let schedule: String
    public let cron: String
    public let task: String
    public let questions: [String]
}

/// A team to start from (`GET /api/agent-teams`, `AgentTeam`): `members` names its bots' templates.
public struct TeamTemplate: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let purpose: String
    public let members: [String]
}

/// `POST /api/agent-teams/{id}/use` (`TeamUse`): the team made, or found and completed with the bots it lacked.
public struct TeamUse: Codable, Sendable, Hashable {
    public let team: TeamRecordView
    public let made: [AgentRecord]
}

/// What the owner answered a template's questions, as `POST /api/agent-templates/{id}/use` takes them: the answers
/// trimmed, in order, or `nil` (no body) when every one was left empty.
public func templateAnswers(_ answers: [String]) -> [String]? {
    let trimmed: [String] = answers.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
    return trimmed.contains { !$0.isEmpty } ? trimmed : nil
}

private struct TemplateUseBody: Encodable { let answers: [String] }

/// The schedule a template suggests, added switched off (item 464), so the owner reads its goal before it runs.
private struct SuggestedSchedule: Encodable {
    let name: String
    let goal: String
    let cron: String
    let agentId: String
    let enabled: Bool

    enum CodingKeys: String, CodingKey {
        case name, goal, cron, enabled
        case agentId = "agent_id"
    }
}

extension DaemonAPI {
    /// A team's page (`GET /api/teams/{id}`).
    public func team(_ id: String) async throws(DaemonRequestError) -> TeamDetail {
        try await get("/api/teams/\(id)", query: [])
    }

    /// Makes a team of the owner's bots (`POST /api/teams`).
    public func createTeam(_ edit: TeamEdit) async throws(DaemonRequestError) -> TeamRecordView {
        try await send("POST", "/api/teams", body: Self.json(edit))
    }

    /// The bots to start from, in the gallery's order.
    public func botTemplates() async throws(DaemonRequestError) -> [BotTemplate] {
        try await get("/api/agent-templates", query: [])
    }

    /// A bot made from a template, with the owner's answers to its questions in its instructions.
    public func useBotTemplate(_ id: String, answers: [String]) async throws(DaemonRequestError) -> AgentRecord {
        try await send("POST", "/api/agent-templates/\(id)/use", body: templateAnswers(answers).map { Self.json(TemplateUseBody(answers: $0)) })
    }

    /// The teams to start from, in the gallery's order.
    public func teamTemplates() async throws(DaemonRequestError) -> [TeamTemplate] {
        try await get("/api/agent-teams", query: [])
    }

    /// A ready team's bots, made where the owner lacks them, and the owner's team of them.
    public func useTeamTemplate(_ id: String) async throws(DaemonRequestError) -> TeamUse {
        try await send("POST", "/api/agent-teams/\(id)/use", body: nil)
    }

    /// Adds the schedule `template` suggests for `bot`, switched off.
    public func addSuggestedSchedule(_ template: BotTemplate, bot: AgentRecord) async throws(DaemonRequestError) -> ScheduleView {
        let body: SuggestedSchedule = SuggestedSchedule(name: bot.name, goal: template.task, cron: template.cron, agentId: bot.id, enabled: false)
        return try await send("POST", "/api/schedules", body: Self.json(body))
    }
}
