import Foundation

// The daemon's records as the phone reads them: only the fields the app draws, each key spelled out (a key strategy
// would also rewrite the keys inside an event's `data`). A field the daemon may leave out is optional.

/// A run's status as the daemon writes it (`queued`, `running`, `blocked`, `done`, `failed`, `cancelled`, `ending`).
typealias RunStatus = String

extension String {
    /// Whether a run of this status is still in hand: it may start, work, wait for the owner or be ending.
    var isActiveRunStatus: Bool {
        self == "queued" || self == "running" || self == "blocked" || self == "ending"
    }
}

/// One chat of the list (`GET /api/threads`).
struct ThreadSummary: Codable, Sendable, Hashable, Identifiable {
    let id: String
    var title: String
    var source: String
    var updatedAt: Double
    var pinnedAt: Double?
    var runCount: Int
    var latestRunId: String
    var latestStatus: RunStatus
    var latestMode: String
    var unread: Bool
    var actorId: String?
    var lastMessage: String
    var lastRole: String
    var loopCron: String?
    var loopNextAt: Double?

    enum CodingKeys: String, CodingKey {
        case id, title, source, unread
        case updatedAt = "updated_at"
        case pinnedAt = "pinned_at"
        case runCount = "run_count"
        case latestRunId = "latest_run_id"
        case latestStatus = "latest_status"
        case latestMode = "latest_mode"
        case actorId = "actor_id"
        case lastMessage = "last_message"
        case lastRole = "last_role"
        case loopCron = "loop_cron"
        case loopNextAt = "loop_next_at"
    }

    var isWorking: Bool { latestStatus.isActiveRunStatus && latestStatus != "blocked" }
    var isWaitingOnYou: Bool { latestStatus == "blocked" }
}

/// A run (`GET /api/runs`, and inside a run's detail).
struct RunRecord: Codable, Sendable, Hashable, Identifiable {
    let id: String
    let threadId: String
    var goal: String
    var mode: String
    var model: String
    var status: RunStatus
    var createdAt: Double
    var finishedAt: Double?
    var result: String
    var steps: Int
    var parentRunId: String?
    var source: String
    var agentId: String?
    var followsReaction: String?

    enum CodingKeys: String, CodingKey {
        case id, goal, mode, model, status, result, steps, source
        case threadId = "thread_id"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
        case parentRunId = "parent_run_id"
        case agentId = "agent_id"
        case followsReaction = "follows_reaction"
    }
}

/// A question or a step the run asks the owner about (`approval_requested`, `GET /api/runs/{id}` `questions`).
struct ApprovalRecord: Codable, Sendable, Hashable, Identifiable {
    let id: String
    let runId: String
    var status: String
    var tool: String
    var summary: String
    var severity: String
    var args: JSONValue

    enum CodingKeys: String, CodingKey {
        case id, status, tool, summary, severity, args
        case runId = "run_id"
    }
}

/// A run as its page shows it (`GET /api/runs/{id}`): the record, its helpers and the questions still open.
struct RunDetail: Codable, Sendable, Hashable {
    var run: RunRecord
    var title: String
    var children: [RunRecord]
    var active: Bool
    var questions: [ApprovalRecord]
}

/// One event of a run (`GET /api/runs/{id}/events` and the run's stream). A delta, which is not stored, has id 0.
struct EventRecord: Codable, Sendable, Hashable {
    let id: Int
    let runId: String
    let ts: Double
    let kind: String
    var data: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case id, ts, kind, data
        case runId = "run_id"
    }

    init(id: Int, runId: String, ts: Double, kind: String, data: [String: JSONValue]) {
        self.id = id
        self.runId = runId
        self.ts = ts
        self.kind = kind
        self.data = data
    }

    /// The event as the cache keeps it: what the chat draws, without the long text of a tool's result, the images, the
    /// model's thinking and the token counts.
    func slimmed() -> EventRecord {
        var kept: [String: JSONValue] = data
        switch kind {
        case "tool_result":
            if let text: String = data["text"]?.string, text.count > 240 {
                kept["text"] = .string(String(text.prefix(240)))
            }
            kept["images"] = nil
        case "assistant":
            kept["thinking"] = nil
        default:
            break
        }
        return EventRecord(id: id, runId: runId, ts: ts, kind: kind, data: kept)
    }
}

/// A message waiting in a chat's queue.
struct QueuedMessage: Codable, Sendable, Hashable, Identifiable {
    let id: String
    var position: Int
    var text: String
    var createdAt: Double

    enum CodingKeys: String, CodingKey {
        case id, position, text
        case createdAt = "created_at"
    }
}

/// A chat's queue (`GET /api/threads/{id}/queue`): the next message first.
struct QueueView: Codable, Sendable, Hashable {
    var held: Bool
    var messages: [QueuedMessage]
    var besideRunId: String?
    var besideThreadId: String?

    enum CodingKeys: String, CodingKey {
        case held, messages
        case besideRunId = "beside_run_id"
        case besideThreadId = "beside_thread_id"
    }
}

/// The schedule record a list shows.
struct ScheduleRecord: Codable, Sendable, Hashable, Identifiable {
    let id: String
    var name: String
    var cron: String?
    var runAt: Double?
    var goal: String
    var enabled: Bool
    var nextRunAt: Double?
    var lastRunAt: Double?
    var threadId: String?
    var lastStatus: String

    enum CodingKeys: String, CodingKey {
        case id, name, cron, goal, enabled
        case runAt = "run_at"
        case nextRunAt = "next_run_at"
        case lastRunAt = "last_run_at"
        case threadId = "thread_id"
        case lastStatus = "last_status"
    }
}

struct ScheduleAgentRef: Codable, Sendable, Hashable {
    let id: String
    let name: String
}

/// A schedule as the lists show it (`GET /api/schedules`).
struct ScheduleView: Codable, Sendable, Hashable, Identifiable {
    var record: ScheduleRecord
    var when: String
    var nextLocal: String?
    var agent: ScheduleAgentRef?
    var brief: Bool

    var id: String { record.id }

    enum CodingKeys: String, CodingKey {
        case record, when, agent, brief
        case nextLocal = "next_local"
    }
}

/// A schedule of one chat (`GET /api/threads/{id}/automations`); `loop` is the chat's own loop.
struct ThreadAutomation: Codable, Sendable, Hashable, Identifiable {
    var schedule: ScheduleView
    var loop: Bool
    var id: String { schedule.id }
}

/// An agent (`GET /api/agents`): the record, its newest run and its home chat.
struct AgentSummary: Codable, Sendable, Hashable, Identifiable {
    struct Record: Codable, Sendable, Hashable {
        let id: String
        var name: String
        var role: String
        var pausedAt: Double?

        enum CodingKeys: String, CodingKey {
            case id, name, role
            case pausedAt = "paused_at"
        }
    }

    var record: Record
    var latest: RunRecord?
    var latestTitle: String?
    var home: ThreadSummary?

    var id: String { record.id }

    enum CodingKeys: String, CodingKey {
        case record, latest, home
        case latestTitle = "latest_title"
    }
}

/// A model a picker offers (`GET /api/models`).
struct ModelOption: Codable, Sendable, Hashable, Identifiable {
    let ref: String
    var label: String
    var tools: Bool
    var vision: Bool
    var free: Bool

    var id: String { ref }
}

/// The models of one provider, or why it listed none.
struct ProviderModels: Codable, Sendable, Hashable, Identifiable {
    let provider: String
    var models: [ModelOption]
    var error: String?

    var id: String { provider }
}

/// The configured models, as `GET /api/status` carries them: the default and the review model.
struct ModelChoices: Codable, Sendable, Hashable {
    var main: String
    var review: String
}

struct StatusView: Codable, Sendable, Hashable {
    var version: String
    var models: ModelChoices
}

/// A phone key's answer to the pairing code (`POST /api/phone/pair`).
struct PairedKey: Codable, Sendable {
    let token: String
}
