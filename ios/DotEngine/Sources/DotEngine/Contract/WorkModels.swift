import Foundation

// What a bot is given to do and what it passes on, as the Mac's Agents page reads it (items 87, 907, 908; `routes_agents.py`):
// work orders (`GET /api/work-orders`), the owner's objectives (`GET /api/objectives`) and today's messages between
// agents (`GET /api/team`). The web lists them and writes none: the lead (Dot) hands work out and judges it in its
// chats, so the phone reads them too. Each type holds the part the phone shows; JSON keys it does not name are ignored.

/// Where a work order stands (`WORK_ORDER_MOVES` in `store/models.py` is the daemon's own table of the moves between).
public enum WorkOrderStatus: String, Sendable, Hashable, CaseIterable {
    case assigned
    case working
    case inReview = "in_review"
    case returned
    case accepted
    case escalated
    case cancelled

    /// Somebody still has something to do or to decide about it (`OPEN_WORK_ORDERS`).
    public var isOpen: Bool { self != .accepted && self != .cancelled }
}

/// A piece of work the lead handed to one agent (`WorkOrderRecord`).
public struct WorkOrderRecord: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let agentId: String
    public let title: String
    public let brief: String
    public let acceptance: String
    public let objectiveId: String?
    public let status: String
    public let attempts: Int
    public let dueAt: Double?
    public let tainted: Bool
    public let note: String
    public let createdAt: Double
    public let updatedAt: Double

    enum CodingKeys: String, CodingKey {
        case id, title, brief, acceptance, status, attempts, tainted, note
        case agentId = "agent_id"
        case objectiveId = "objective_id"
        case dueAt = "due_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(
        id: String, agentId: String, title: String, brief: String, acceptance: String, objectiveId: String?, status: String, attempts: Int, dueAt: Double?,
        tainted: Bool, note: String, createdAt: Double, updatedAt: Double
    ) {
        self.id = id
        self.agentId = agentId
        self.title = title
        self.brief = brief
        self.acceptance = acceptance
        self.objectiveId = objectiveId
        self.status = status
        self.attempts = attempts
        self.dueAt = dueAt
        self.tainted = tainted
        self.note = note
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    /// The status as the daemon's table names it; `nil` for one this app does not know (it is shown as the daemon wrote it).
    public var state: WorkOrderStatus? { WorkOrderStatus(rawValue: status) }

    /// Open unless the status says it is over; a status this app does not know counts as open, so that nothing is hidden.
    public var isOpen: Bool { state?.isOpen ?? true }
}

/// A work order as the list reads it: the record, the name of its agent ("" when that agent is gone) and the title of
/// the objective it serves.
public struct WorkOrderView: Codable, Sendable, Hashable, Identifiable {
    public let order: WorkOrderRecord
    public let agentName: String
    public let objectiveTitle: String?

    public var id: String { order.id }

    enum CodingKeys: String, CodingKey {
        case order
        case agentName = "agent_name"
        case objectiveTitle = "objective_title"
    }

    public init(order: WorkOrderRecord, agentName: String, objectiveTitle: String?) {
        self.order = order
        self.agentName = agentName
        self.objectiveTitle = objectiveTitle
    }
}

/// One dated line of progress on an objective.
public struct ObjectiveNote: Codable, Sendable, Hashable {
    public let at: Double
    public let text: String
    public let tainted: Bool

    public init(at: Double, text: String, tainted: Bool) {
        self.at = at
        self.text = text
        self.tainted = tainted
    }
}

/// How an objective's work is done; the phone shows the step that comes next.
public struct ObjectiveWorkflow: Codable, Sendable, Hashable {
    public let nextStep: String
    /// The step was written by a run that had read content third parties can author: not the owner's words.
    public let nextStepTainted: Bool

    enum CodingKeys: String, CodingKey {
        case nextStep = "next_step"
        case nextStepTainted = "next_step_tainted"
    }

    public init(nextStep: String, nextStepTainted: Bool) {
        self.nextStep = nextStep
        self.nextStepTainted = nextStepTainted
    }
}

/// Something the owner wants Dot to move forward over time (`ObjectiveRecord`): what, why it matters and how progress
/// is judged; `status` is `active`, `done` or `dropped`.
public struct ObjectiveRecord: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let title: String
    public let why: String
    public let measure: String
    public let status: String
    public let notes: [ObjectiveNote]
    public let tainted: Bool
    public let createdAt: Double
    public let updatedAt: Double
    public let workflow: ObjectiveWorkflow

    enum CodingKeys: String, CodingKey {
        case id, title, why, measure, status, notes, tainted, workflow
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(
        id: String, title: String, why: String, measure: String, status: String, notes: [ObjectiveNote], tainted: Bool, createdAt: Double, updatedAt: Double,
        workflow: ObjectiveWorkflow
    ) {
        self.id = id
        self.title = title
        self.why = why
        self.measure = measure
        self.status = status
        self.notes = notes
        self.tainted = tainted
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.workflow = workflow
    }

    public var isActive: Bool { status == "active" }

    /// Whether anything on it (its words, a line of progress or its next step) was written after Dot read content third parties can
    /// author: not the owner's words.
    public var hasOutsideWords: Bool { tainted || workflow.nextStepTainted || notes.contains { $0.tainted } }
}

/// A message between agents (`TeamMessage`): who sent it and to whom (Dot's id is `dot`), the words, the reply (`nil`
/// until the run ends) and the run's status.
public struct TeamMessage: Codable, Sendable, Hashable, Identifiable {
    public let runId: String
    public let fromId: String
    public let fromName: String
    public let toId: String
    public let toName: String
    public let message: String
    public let reply: String?
    public let status: String
    public let ts: Double
    public let finishedAt: Double?

    public var id: String { runId }

    enum CodingKeys: String, CodingKey {
        case message, reply, status, ts
        case runId = "run_id"
        case fromId = "from_id"
        case fromName = "from_name"
        case toId = "to_id"
        case toName = "to_name"
        case finishedAt = "finished_at"
    }

    public init(
        runId: String, fromId: String, fromName: String, toId: String, toName: String, message: String, reply: String?, status: String, ts: Double,
        finishedAt: Double?
    ) {
        self.runId = runId
        self.fromId = fromId
        self.fromName = fromName
        self.toId = toId
        self.toName = toName
        self.message = message
        self.reply = reply
        self.status = status
        self.ts = ts
        self.finishedAt = finishedAt
    }

    /// The run is still at work on it (no reply yet).
    public var isAtWork: Bool { ["queued", "running", "blocked", "ending"].contains(status) }
}

/// `GET /api/team`: today's messages between agents, oldest first.
public struct TeamView: Codable, Sendable, Hashable {
    public let messages: [TeamMessage]

    public init(messages: [TeamMessage]) { self.messages = messages }
}

// MARK: What a bot's profile shows

/// How far off a work order is due: the web's `span` ("5 min", "3 h", "2 days") as a number and a unit, which the app says
/// in the owner's language.
public enum SpanWords: Sendable, Hashable {
    case minutes(Int)
    case hours(Int)
    case days(Int)

    /// The biggest unit that holds `seconds` (under an hour in minutes, under a day in hours); negative counts as none.
    public init(seconds: Double) {
        let wanted: Double = max(seconds, 0)
        if wanted < 3600 {
            self = .minutes(Int(wanted / 60))
        } else if wanted < 86_400 {
            self = .hours(Int(wanted / 3600))
        } else {
            self = .days(Int(wanted / 86_400))
        }
    }
}

/// When an open order is due: in a while, or late by a while.
public enum DueWords: Sendable, Hashable {
    case dueIn(SpanWords)
    case late(SpanWords)
}

extension WorkOrderRecord {
    /// The web's `dueWords`: nothing for an order with no time or one that is over.
    public func due(now: Double) -> DueWords? {
        guard let dueAt, isOpen else { return nil }
        return dueAt > now ? .dueIn(SpanWords(seconds: dueAt - now)) : .late(SpanWords(seconds: now - dueAt))
    }
}

/// What a bot's profile lists. The web's Agents page lists every order, every active objective and every message of the day
/// unscoped; scoping them to a bot is the phone's own. The daemon sends the newest 60 orders and 60 objectives
/// (`_WORK_ORDERS_LISTED`), so an old item of a bot can be missing from its page while other bots' items fill them.
///
/// The orders of a bot: every order for Dot (the lead hands them all out), the orders given to it for an agent.
public func botOrders(botId: String, orders: [WorkOrderView]) -> [WorkOrderView] {
    botId == dotAgentId ? orders : orders.filter { $0.order.agentId == botId }
}

/// The objectives of a bot: every one for Dot (it keeps them), the objectives its orders serve for an agent.
public func botObjectives(botId: String, orders: [WorkOrderView], objectives: [ObjectiveRecord]) -> [ObjectiveRecord] {
    if botId == dotAgentId { return objectives }
    let served: Set<String> = Set(botOrders(botId: botId, orders: orders).compactMap { $0.order.objectiveId })
    return objectives.filter { served.contains($0.id) }
}

/// The messages to a bot and from it, each newest first.
public struct BotHandoffs: Sendable, Hashable {
    public let received: [TeamMessage]
    public let sent: [TeamMessage]

    public var count: Int { received.count + sent.count }
}

/// The messages of the day that went to the bot or came from it (Dot's id is `dot`: the messages between two other agents are not
/// Dot's).
public func botHandoffs(botId: String, team: TeamView) -> BotHandoffs {
    let newestFirst: [TeamMessage] = team.messages.sorted { $0.ts > $1.ts }
    return BotHandoffs(received: newestFirst.filter { $0.toId == botId }, sent: newestFirst.filter { $0.fromId == botId })
}
