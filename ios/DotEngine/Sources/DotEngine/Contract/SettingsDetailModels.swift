import Foundation

// The routes of the Settings pages made native in items 1770-1774, as the web's tabs read and write them, within what
// a phone is for (PLAN §26): Rules' free mode, Memory's notes, an agent's page and its model. Each type holds the part
// the phone shows; keys it does not name are ignored.

// MARK: Rules (item 1772)

/// `GET/PUT /api/policy/free`.
public struct FreeModeView: Codable, Sendable, Hashable {
    public let free: Bool

    public init(free: Bool) { self.free = free }
}

// MARK: Memory (item 1773)

/// The order `GET /api/memories` lists the notes in.
public enum MemoryOrder: String, Codable, Sendable, CaseIterable {
    case changed
    case used
    case mostUsed = "most_used"
    case leastUsed = "least_used"
}

/// `GET /api/memories`: a page of the notes, and which of them rest.
public struct MemoryList: Codable, Sendable, Hashable {
    public let total: Int
    public let items: [MemoryNote]
    public let resting: [String]

    public init(total: Int, items: [MemoryNote], resting: [String]) {
        self.total = total
        self.items = items
        self.resting = resting
    }
}

/// The body of `POST /api/memories` and `PATCH /api/memories/{id}`.
public struct MemoryWrite: Codable, Sendable, Hashable {
    public let content: String
    public let tags: [String]

    public init(content: String, tags: [String]) {
        self.content = content
        self.tags = tags
    }
}

// MARK: Agents (item 1774)

/// An agent's face (item 102): kept as it is when the phone writes the agent back.
public struct AgentLook: Codable, Sendable, Hashable {
    public let shape: String
    public let expression: String
    public let colour: String

    public init(shape: String, expression: String, colour: String) {
        self.shape = shape
        self.expression = expression
        self.colour = colour
    }
}

/// An agent of `/api/agents` (`AgentRecord`): `tools` empty is every action.
public struct AgentRecordView: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let role: String
    public let instructions: String
    public let model: String
    public let effort: String?
    public let approvals: String?
    public let tools: [String]
    public let madeBy: String?
    public let pausedAt: Double?
    public let refuses: String
    public let look: AgentLook?

    public init(
        id: String, name: String, role: String, instructions: String, model: String, effort: String?, approvals: String?, tools: [String], madeBy: String?,
        pausedAt: Double?, refuses: String, look: AgentLook?
    ) {
        self.id = id
        self.name = name
        self.role = role
        self.instructions = instructions
        self.model = model
        self.effort = effort
        self.approvals = approvals
        self.tools = tools
        self.madeBy = madeBy
        self.pausedAt = pausedAt
        self.refuses = refuses
        self.look = look
    }

    enum CodingKeys: String, CodingKey {
        case id, name, role, instructions, model, effort, approvals, tools, refuses, look
        case madeBy = "made_by"
        case pausedAt = "paused_at"
    }
}

/// A chat as an agent's or a team's page names it.
public struct ChatLine: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let title: String
    public let updatedAt: Double
    public let latestStatus: String

    public init(id: String, title: String, updatedAt: Double, latestStatus: String) {
        self.id = id
        self.title = title
        self.updatedAt = updatedAt
        self.latestStatus = latestStatus
    }

    enum CodingKeys: String, CodingKey {
        case id, title
        case updatedAt = "updated_at"
        case latestStatus = "latest_status"
    }
}

/// `GET /api/agents`: an agent and its newest chat.
public struct AgentListItem: Codable, Sendable, Hashable, Identifiable {
    public let record: AgentRecordView
    public let latestTitle: String?
    public let home: ChatLine?

    public var id: String { record.id }

    public init(record: AgentRecordView, latestTitle: String?, home: ChatLine?) {
        self.record = record
        self.latestTitle = latestTitle
        self.home = home
    }

    enum CodingKeys: String, CodingKey {
        case record, home
        case latestTitle = "latest_title"
    }
}

/// `GET /api/agents/{id}`: an agent and its own chat.
public struct AgentDetailView: Codable, Sendable, Hashable {
    public let record: AgentRecordView
    public let home: ChatLine?

    public init(record: AgentRecordView, home: ChatLine?) {
        self.record = record
        self.home = home
    }
}

/// The body of `POST /api/agents/{id}/welcome`: the language the agent's first chat greets the owner in.
public struct AgentWelcome: Codable, Sendable, Hashable {
    public let language: String

    public init(language: String) { self.language = language }
}

/// The body of `POST /api/agents` and `PUT /api/agents/{id}`: `tools` `nil` is every action; `model` `""` the default.
public struct AgentWrite: Encodable, Sendable, Hashable {
    public let name: String
    public let role: String
    public let instructions: String
    public let model: String
    public let effort: String?
    public let approvals: String?
    public let tools: [String]?
    public let refuses: String
    public let look: AgentLook?

    public init(name: String, role: String, instructions: String, model: String, effort: String?, approvals: String?, tools: [String]?, refuses: String, look: AgentLook?) {
        self.name = name
        self.role = role
        self.instructions = instructions
        self.model = model
        self.effort = effort
        self.approvals = approvals
        self.tools = tools
        self.refuses = refuses
        self.look = look
    }

    enum CodingKeys: String, CodingKey { case name, role, instructions, model, effort, approvals, tools, refuses, look }

    /// Writes every key, nulls included: `model`, `effort` and `approvals` are required by the daemon.
    public func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(role, forKey: .role)
        try container.encode(instructions, forKey: .instructions)
        try container.encode(model, forKey: .model)
        try container.encode(effort, forKey: .effort)
        try container.encode(approvals, forKey: .approvals)
        try container.encode(tools, forKey: .tools)
        try container.encode(refuses, forKey: .refuses)
        try container.encode(look, forKey: .look)
    }
}
