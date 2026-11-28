import Foundation

// The bots of the phone's home (PLAN §27, item 1881): Dot and each of the owner's agents, as `GET /api/agents` lists
// them (`routes_agents.py`), with the shapes `POST /api/agents` and `PUT /api/agents/{id}` take.

/// The id the daemon gives Dot among the agents (`DOT_AGENT_ID`).
public let dotAgentId: String = "dot"

// `AgentLook`, an agent's face, is shared with Settings › Agents (`SettingsDetailModels.swift`).

/// An agent as the daemon keeps it (`AgentRecord`); Dot's own record has the id `dot`.
public struct AgentRecord: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let role: String
    public let instructions: String
    /// Empty: the configured main model.
    public let model: String
    public let effort: String?
    public let approvals: String?
    public let tools: [String]
    public let createdAt: Double
    public let updatedAt: Double
    public let look: AgentLook?
    public let pausedAt: Double?
    public let refuses: String?

    enum CodingKeys: String, CodingKey {
        case id, name, role, instructions, model, effort, approvals, tools, look, refuses
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case pausedAt = "paused_at"
    }

    public init(
        id: String, name: String, role: String, instructions: String, model: String, effort: String?, approvals: String?, tools: [String],
        createdAt: Double, updatedAt: Double, look: AgentLook?, pausedAt: Double?, refuses: String?
    ) {
        self.id = id
        self.name = name
        self.role = role
        self.instructions = instructions
        self.model = model
        self.effort = effort
        self.approvals = approvals
        self.tools = tools
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.look = look
        self.pausedAt = pausedAt
        self.refuses = refuses
    }
}

/// One reason a bot is overloaded (item 2067, `OverloadReason`): `orders` and `routines` count against the most it
/// should hold; `done_share` is its done share this week against the week before's, in whole percent.
public struct OverloadReason: Codable, Sendable, Hashable {
    public let kind: String
    public let count: Int
    public let limit: Int

    public init(kind: String, count: Int, limit: Int) {
        self.kind = kind
        self.count = count
        self.limit = limit
    }
}

/// What a bot carries (item 2067, `BotLoad`): its open work orders, routines on, objectives, runs of the week, their
/// done share and average steps, and why it is overloaded (none: it is not).
public struct BotLoad: Codable, Sendable, Hashable {
    public let openOrders: Int
    public let activeRoutines: Int
    public let objectives: Int
    public let runs: Int
    public let doneShare: Double?
    public let steps: Double?
    public let reasons: [OverloadReason]

    enum CodingKeys: String, CodingKey {
        case objectives, runs, steps, reasons
        case openOrders = "open_orders"
        case activeRoutines = "active_routines"
        case doneShare = "done_share"
    }

    public init(openOrders: Int, activeRoutines: Int, objectives: Int, runs: Int, doneShare: Double?, steps: Double?, reasons: [OverloadReason]) {
        self.openOrders = openOrders
        self.activeRoutines = activeRoutines
        self.objectives = objectives
        self.runs = runs
        self.doneShare = doneShare
        self.steps = steps
        self.reasons = reasons
    }
}

/// An agent as `GET /api/agents` lists it: its record, its home chat, its conversation with the owner (items 1233,
/// 1245), and what it carries (item 2067; `nil` from a daemon before it); Dot first.
public struct AgentSummary: Codable, Sendable, Hashable {
    public let record: AgentRecord
    public let home: ThreadSummary?
    public let load: BotLoad?

    public init(record: AgentRecord, home: ThreadSummary?, load: BotLoad?) {
        self.record = record
        self.home = home
        self.load = load
    }
}

/// The body of `POST /api/agents` and `PUT /api/agents/{id}` (`AgentRequest`). `model` empty and `effort`/`approvals`
/// `nil` take the configured ones; `tools` `nil` gives a new agent the daemon's own set.
public struct AgentRequest: Codable, Sendable, Hashable {
    public let name: String
    public let role: String
    public let instructions: String
    public let model: String
    public let effort: String?
    public let approvals: String?
    public let tools: [String]?
    public let look: AgentLook?
    public let refuses: String?

    public init(
        name: String, role: String, instructions: String, model: String, effort: String?, approvals: String?, tools: [String]?, look: AgentLook?,
        refuses: String?
    ) {
        self.name = name
        self.role = role
        self.instructions = instructions
        self.model = model
        self.effort = effort
        self.approvals = approvals
        self.tools = tools
        self.look = look
        self.refuses = refuses
    }

    enum CodingKeys: String, CodingKey {
        case name, role, instructions, model, effort, approvals, tools, look, refuses
    }

    /// `effort` and `approvals` are required by the daemon, `null` for the configured ones, so they are written even
    /// when `nil`; `tools`, `look` and `refuses` are left out when `nil` (the daemon's own defaults).
    public func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(role, forKey: .role)
        try container.encode(instructions, forKey: .instructions)
        try container.encode(model, forKey: .model)
        try container.encode(effort, forKey: .effort)
        try container.encode(approvals, forKey: .approvals)
        try container.encodeIfPresent(tools, forKey: .tools)
        try container.encodeIfPresent(look, forKey: .look)
        try container.encodeIfPresent(refuses, forKey: .refuses)
    }

    /// The longest name and role the daemon takes (`AgentRequest`: 60 and 120 characters).
    public static let nameLimit: Int = 60
    public static let roleLimit: Int = 120

    /// A new bot from the two things the phone asks: its name and what it should do (PLAN §27). The first line of what
    /// it does, cut to the role's limit, is its role; all of it is its instructions. `nil` when the name is empty.
    public static func newBot(name: String, does: String) -> AgentRequest? {
        let trimmedName: String = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let instructions: String = does.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { return nil }
        let firstLine: String = instructions.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        return AgentRequest(
            name: String(trimmedName.prefix(nameLimit)), role: String(firstLine.prefix(roleLimit)), instructions: instructions, model: "", effort: nil,
            approvals: nil, tools: nil, look: nil, refuses: nil)
    }

    /// The same agent under another name (Rename in a bot's menu): every other field as saved.
    public static func renamed(_ record: AgentRecord, to name: String) -> AgentRequest {
        AgentRequest(
            name: String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(nameLimit)), role: record.role, instructions: record.instructions,
            model: record.model, effort: record.effort, approvals: record.approvals, tools: record.tools, look: record.look, refuses: record.refuses)
    }
}

/// One find of the search's Files (`GET /api/threads/search/files`): a file the owner attached (`kind` "attachment") or
/// one a run of the chat wrote (`kind` "made"), in the chat the owner sees; `path` is where it is (`~/…`), an
/// attachment's own name for one attached.
public struct FileHit: Codable, Sendable, Hashable {
    public let threadId: String
    public let runId: String
    public let kind: String
    public let name: String
    public let path: String
    public let attachmentId: String?
    public let at: Double

    enum CodingKeys: String, CodingKey {
        case kind, name, path, at
        case threadId = "thread_id"
        case runId = "run_id"
        case attachmentId = "attachment_id"
    }

    public init(threadId: String, runId: String, kind: String, name: String, path: String, attachmentId: String?, at: Double) {
        self.threadId = threadId
        self.runId = runId
        self.kind = kind
        self.name = name
        self.path = path
        self.attachmentId = attachmentId
        self.at = at
    }
}

/// One find of the home search in what the chats say (`GET /api/threads/search`, item 1882): the chat it is in, and
/// the words around the match with the matched words in `[...]`.
public struct ChatHit: Codable, Sendable, Hashable {
    public let threadId: String
    public let seq: Int
    public let kind: String
    public let ref: String
    public let snippet: String

    enum CodingKeys: String, CodingKey {
        case seq, kind, ref, snippet
        case threadId = "thread_id"
    }

    public init(threadId: String, seq: Int, kind: String, ref: String, snippet: String) {
        self.threadId = threadId
        self.seq = seq
        self.kind = kind
        self.ref = ref
        self.snippet = snippet
    }
}
