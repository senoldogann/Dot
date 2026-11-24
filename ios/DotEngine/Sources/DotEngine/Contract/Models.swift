import Foundation

// The daemon's sync contract (item 1686, `docs/schemas/thread-sync.schema.json`) as the engine reads it. Each type decodes
// the fields the app uses; a field the app does not read is not named, so a daemon that adds fields breaks nothing.
// `ContractSchemaTests` decodes the smallest and the fullest instance the schema allows into every type here.

/// One event of a run, as `/api/runs/{id}/stream` and the chat's stream send it. `id` 0 is a live-only event (the
/// words being written, `thread_changed`).
public struct EventRecord: Codable, Sendable, Hashable {
    public let id: Int
    public let runId: String
    public let ts: Double
    public let kind: String
    public let data: [String: JSONValue]

    public init(id: Int, runId: String, ts: Double, kind: String, data: [String: JSONValue]) {
        self.id = id
        self.runId = runId
        self.ts = ts
        self.kind = kind
        self.data = data
    }

    enum CodingKeys: String, CodingKey {
        case id, ts, kind, data
        case runId = "run_id"
    }
}

public struct ThreadRecord: Codable, Sendable, Hashable {
    public let id: String
    public let title: String
    public let source: String
    public let createdAt: Double
    public let updatedAt: Double
    public let agentId: String?

    enum CodingKeys: String, CodingKey {
        case id, title, source
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case agentId = "agent_id"
    }
}

/// A chat as the list shows it.
public struct ThreadSummary: Codable, Sendable, Hashable {
    public let id: String
    public let title: String
    public let source: String
    public let createdAt: Double
    public let updatedAt: Double
    public let pinnedAt: Double?
    public let runCount: Int
    public let latestRunId: String
    public let latestStatus: String
    public let latestCreatedAt: Double
    public let unread: Bool
    public let actorId: String?
    public let lastMessage: String
    public let lastRole: String

    enum CodingKeys: String, CodingKey {
        case id, title, source, unread
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case pinnedAt = "pinned_at"
        case runCount = "run_count"
        case latestRunId = "latest_run_id"
        case latestStatus = "latest_status"
        case latestCreatedAt = "latest_created_at"
        case actorId = "actor_id"
        case lastMessage = "last_message"
        case lastRole = "last_role"
    }
}

public struct ThreadRow: Codable, Sendable, Hashable {
    public let summary: ThreadSummary
    public let revision: Int
    public let asks: Int
}

/// `GET /api/threads/view`.
public struct ThreadsView: Codable, Sendable, Hashable {
    public let revision: Int
    public let threads: [ThreadRow]
}

public struct RunRecord: Codable, Sendable, Hashable {
    public let id: String
    public let threadId: String
    public let goal: String
    public let mode: String
    public let model: String
    public let status: String
    public let createdAt: Double
    public let finishedAt: Double?
    public let result: String
    public let parentRunId: String?
    public let source: String
    public let effort: String?

    enum CodingKeys: String, CodingKey {
        case id, goal, mode, model, status, result, source, effort
        case threadId = "thread_id"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
        case parentRunId = "parent_run_id"
    }

    /// Whether the run still goes (it can be stopped, a steer reaches it).
    public var isActive: Bool { status == "queued" || status == "running" || status == "blocked" || status == "ending" }
}

public struct ChatChoices: Codable, Sendable, Hashable {
    public let mode: String
    public let model: String?
    public let effort: String?
    public let speed: String
    public let approvals: String?
    public let goalMode: Bool?

    enum CodingKeys: String, CodingKey {
        case mode, model, effort, speed, approvals
        case goalMode = "goal_mode"
    }
}

/// The newest run's page (`GET /api/runs/{id}`), the part the engine reads.
public struct RunDetail: Codable, Sendable, Hashable {
    public let run: RunRecord
    public let title: String
    public let active: Bool
    public let held: Bool
    public let choices: ChatChoices?
}

public struct QueuedMessageView: Codable, Sendable, Hashable {
    public let id: String
    public let threadId: String
    public let position: Int
    public let text: String
    public let createdAt: Double
    public let source: String

    enum CodingKeys: String, CodingKey {
        case id, position, text, source
        case threadId = "thread_id"
        case createdAt = "created_at"
    }
}

/// A chat's queue: what starts after the run at work, in order; `held` while the owner held it.
public struct QueueView: Codable, Sendable, Hashable {
    public let held: Bool
    public let messages: [QueuedMessageView]
    public let besideRunId: String?
    public let besideThreadId: String?

    public init(held: Bool, messages: [QueuedMessageView], besideRunId: String?, besideThreadId: String?) {
        self.held = held
        self.messages = messages
        self.besideRunId = besideRunId
        self.besideThreadId = besideThreadId
    }

    enum CodingKeys: String, CodingKey {
        case held, messages
        case besideRunId = "beside_run_id"
        case besideThreadId = "beside_thread_id"
    }
}

/// An approval or a question waiting for the owner.
public struct ApprovalRecord: Codable, Sendable, Hashable {
    public let id: String
    public let runId: String
    public let createdAt: Double
    public let status: String
    public let tool: String
    public let summary: String
    public let severity: String

    enum CodingKeys: String, CodingKey {
        case id, status, tool, summary, severity
        case runId = "run_id"
        case createdAt = "created_at"
    }
}

/// What a helper does now (`step` of a helper): the tool, a short target of its arguments, the plain line.
public struct HelperStep: Codable, Sendable, Hashable {
    public let tool: String
    public let target: String
    public let line: String
    public let at: Double

    public init(tool: String, target: String, line: String, at: Double) {
        self.tool = tool
        self.target = target
        self.line = line
        self.at = at
    }
}

/// A run a run of the chat started in a chat of its own (plan §18).
public struct HelperView: Codable, Sendable, Hashable {
    public let runId: String
    public let parentRunId: String?
    public let threadId: String
    public let agentId: String?
    public let title: String
    public let status: String
    public let startedAt: Double
    public let finishedAt: Double?
    public let provider: String
    public let model: String
    public let effort: String?
    public let step: HelperStep?
    public let blocking: Bool
    public let stop: String

    public init(
        runId: String, parentRunId: String?, threadId: String, agentId: String?, title: String, status: String,
        startedAt: Double, finishedAt: Double?, provider: String, model: String, effort: String?, step: HelperStep?,
        blocking: Bool, stop: String
    ) {
        self.runId = runId
        self.parentRunId = parentRunId
        self.threadId = threadId
        self.agentId = agentId
        self.title = title
        self.status = status
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.provider = provider
        self.model = model
        self.effort = effort
        self.step = step
        self.blocking = blocking
        self.stop = stop
    }

    enum CodingKeys: String, CodingKey {
        case title, status, provider, model, effort, step, blocking, stop
        case runId = "run_id"
        case parentRunId = "parent_run_id"
        case threadId = "thread_id"
        case agentId = "agent_id"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }

    /// The same helper doing `next`.
    public func doing(_ next: HelperStep) -> HelperView {
        HelperView(
            runId: runId, parentRunId: parentRunId, threadId: threadId, agentId: agentId, title: title, status: status,
            startedAt: startedAt, finishedAt: finishedAt, provider: provider, model: model, effort: effort, step: next,
            blocking: blocking, stop: stop)
    }
}

/// What works for the chat with no run of it waiting: `helper`, `command` or `loop`.
public struct BackgroundWork: Codable, Sendable, Hashable {
    public let kind: String
    public let id: String
    public let title: String
    public let startedAt: Double

    enum CodingKeys: String, CodingKey {
        case kind, id, title
        case startedAt = "started_at"
    }
}

public struct ReactionRecord: Codable, Sendable, Hashable {
    public let runId: String
    public let message: String
    public let reaction: String

    enum CodingKeys: String, CodingKey {
        case message, reaction
        case runId = "run_id"
    }
}

public struct DraftRecord: Codable, Sendable, Hashable {
    public let id: String
    public let runId: String
    public let channel: String
    public let subject: String
    public let status: String

    enum CodingKeys: String, CodingKey {
        case id, channel, subject, status
        case runId = "run_id"
    }
}

/// `GET /api/threads/{id}/view`: the chat's small state at `revision`, and the newest run's tail.
public struct ThreadView: Codable, Sendable, Hashable {
    public let revision: Int
    public let thread: ThreadRecord
    public let summary: ThreadSummary?
    public let runs: [RunRecord]
    public let older: Bool
    public let newest: RunDetail?
    public let newestEvents: [EventRecord]
    public let newestEventsCut: Bool
    public let queue: QueueView
    public let asks: [ApprovalRecord]
    public let helpers: [HelperView]
    public let background: [BackgroundWork]
    public let reactions: [ReactionRecord]
    public let drafts: [DraftRecord]

    enum CodingKeys: String, CodingKey {
        case revision, thread, summary, runs, older, newest, queue, asks, helpers, background, reactions, drafts
        case newestEvents = "newest_events"
        case newestEventsCut = "newest_events_cut"
    }

    /// The same view with its helpers replaced (a helper's step came on the stream).
    public func with(helpers next: [HelperView]) -> ThreadView {
        ThreadView(
            revision: revision, thread: thread, summary: summary, runs: runs, older: older, newest: newest,
            newestEvents: newestEvents, newestEventsCut: newestEventsCut, queue: queue, asks: asks, helpers: next,
            background: background, reactions: reactions, drafts: drafts)
    }

    public init(
        revision: Int, thread: ThreadRecord, summary: ThreadSummary?, runs: [RunRecord], older: Bool, newest: RunDetail?,
        newestEvents: [EventRecord], newestEventsCut: Bool, queue: QueueView, asks: [ApprovalRecord],
        helpers: [HelperView], background: [BackgroundWork], reactions: [ReactionRecord], drafts: [DraftRecord]
    ) {
        self.revision = revision
        self.thread = thread
        self.summary = summary
        self.runs = runs
        self.older = older
        self.newest = newest
        self.newestEvents = newestEvents
        self.newestEventsCut = newestEventsCut
        self.queue = queue
        self.asks = asks
        self.helpers = helpers
        self.background = background
        self.reactions = reactions
        self.drafts = drafts
    }
}

/// `thread_changed`: the chat's revision moved (read the view again), or it was deleted.
public struct ThreadChanged: Codable, Sendable, Hashable {
    public let threadId: String
    public let revision: Int
    public let deleted: Bool

    enum CodingKeys: String, CodingKey {
        case revision, deleted
        case threadId = "thread_id"
    }
}

/// `synchronized`: every followed run is past its stored tail; `runs` names the newest id sent of each.
public struct Synchronized: Codable, Sendable, Hashable {
    public let revision: Int
    public let runs: [String: Int]
}

/// `POST /api/phone/pair`.
public struct PairedKey: Codable, Sendable {
    public let token: String
}

/// `GET /api/status`, the part the cache keys on.
public struct DaemonStatus: Codable, Sendable, Hashable {
    public let version: String
}
