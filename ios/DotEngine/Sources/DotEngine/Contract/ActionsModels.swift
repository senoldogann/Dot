import Foundation

// The answers of the routes behind a chat's message actions and the run inspector's depth (package W4): Edit from here
// and Retry (`/runs/{id}/rewind`), Continue (`/runs/{id}/resume`), the recap, the cost, the
// lessons, the undo of a file, a saved skill, the chat's loops and its terminal sessions. Each mirrors the daemon's own
// model (named beside it), the part the phone reads.

/// A file of a message, as a rewind hands it back to the composer (`agent.attachments.AttachedFile`).
public struct AttachedFile: Codable, Sendable, Hashable {
    public let id: String
    public let name: String
    public let kind: String
    public let size: Int
}

/// A change to one file a rewind took back or left (`server.views.FileEdit`, the part shown).
public struct RewoundFile: Codable, Sendable, Hashable {
    public let path: String
    public let state: String
    public let reason: String?
}

/// `POST /api/runs/{id}/rewind` (`server.views.RewindView`, item 282): the run the chat ends with now (`nil` when none
/// is left), the message's words and files for the composer, the files taken back and those left.
public struct RewindView: Codable, Sendable, Hashable {
    public let threadId: String
    public let backTo: String?
    public let text: String
    public let files: [AttachedFile]
    public let reverted: [RewoundFile]
    public let left: [RewoundFile]

    enum CodingKeys: String, CodingKey {
        case text, files, reverted, left
        case threadId = "thread_id"
        case backTo = "back_to"
    }
}

/// What a rewind does to the files the runs taken out changed: put them back as they were, or keep them.
public enum RewindFiles: String, Codable, Sendable, Hashable {
    case revert
    case keep
}

/// A run's recap (`agent.recap.Recap`).
public struct Recap: Codable, Sendable, Hashable {
    public let text: String
    public let model: String
    public let steps: Int
    public let status: String
    public let writtenAt: Double

    enum CodingKeys: String, CodingKey {
        case text, model, steps, status
        case writtenAt = "written_at"
    }
}

/// `GET`/`POST /api/runs/{id}/recap` (`agent.recap.RecapView`, item 536).
public struct RecapView: Codable, Sendable, Hashable {
    public let enabled: Bool
    public let recap: Recap?
    public let steps: Int
    public let status: String
    public let due: Bool
}

/// Where a run's time went (`store.stats.TimeStats`, the part shown).
public struct RunTime: Codable, Sendable, Hashable {
    public let modelSeconds: Double
    public let compactionSeconds: Double
    public let cachedTokens: Int
    public let cachedShare: Double?

    enum CodingKeys: String, CodingKey {
        case modelSeconds = "model_seconds"
        case compactionSeconds = "compaction_seconds"
        case cachedTokens = "cached_tokens"
        case cachedShare = "cached_share"
    }
}

/// The numbers of a run's model (`store.stats.ModelStats`, the part shown).
public struct RunModelStats: Codable, Sendable, Hashable {
    public let model: String
    public let steps: Int
    public let inputTokens: Int
    public let outputTokens: Int
    public let toolCalls: Int
    public let toolErrors: Int
    public let time: RunTime

    enum CodingKeys: String, CodingKey {
        case model, steps, time
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
        case toolCalls = "tool_calls"
        case toolErrors = "tool_errors"
    }
}

/// What like runs are expected to spend (`store.stats.RunEstimate`, the part shown).
public struct RunEstimate: Codable, Sendable, Hashable {
    public let median: Int
    public let runs: Int
}

/// A run against the owner's caps (`store.stats.RunBudget`, item 1731).
public struct RunBudget: Codable, Sendable, Hashable {
    public let estimate: RunEstimate?
    public let spent: Int
    public let cap: Int
    public let source: String
    public let day: Int
    public let dayCap: Int

    enum CodingKeys: String, CodingKey {
        case estimate, spent, cap, source, day
        case dayCap = "day_cap"
    }
}

/// `GET /api/runs/{id}/stats` (`store.stats.RunCosts`, item 53): what the run cost in time and tokens. Dot holds no
/// price per model (the usage route says so), so there is no money in it: the phone shows what the web's Cost card shows.
public struct RunCosts: Codable, Sendable, Hashable {
    public let runId: String
    public let status: String
    public let minutes: Double
    public let stats: RunModelStats
    public let toolSeconds: Double
    public let waitedS: Double
    public let budget: RunBudget?

    enum CodingKeys: String, CodingKey {
        case status, minutes, stats, budget
        case runId = "run_id"
        case toolSeconds = "tool_seconds"
        case waitedS = "waited_s"
    }
}

/// A terminal session of a chat (`server.views.TerminalView`, item 229).
public struct TerminalView: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let command: String
    public let background: Bool
    public let startedAt: Double
    public let ended: Bool
    public let exitStatus: Int?

    enum CodingKeys: String, CodingKey {
        case id, command, background, ended
        case startedAt = "started_at"
        case exitStatus = "exit_status"
    }
}

/// A new run of a chat with its words and choices (`POST /api/runs`, kind `run`): Retry's message sent again.
public struct RetryRun: Codable, Sendable, Hashable {
    public let goal: String
    public let key: String
    public let mode: String
    public let model: String?
    public let effort: String?
    public let speed: String
    public let approvals: String?
    public let goalMode: Bool?
    public let threadId: String?
    public let agentId: String?
    public let attachments: [String]

    public init(
        goal: String, key: String, mode: String, model: String?, effort: String?, speed: String, approvals: String?, goalMode: Bool?, threadId: String?,
        agentId: String?, attachments: [String]
    ) {
        self.goal = goal
        self.key = key
        self.mode = mode
        self.model = model
        self.effort = effort
        self.speed = speed
        self.approvals = approvals
        self.goalMode = goalMode
        self.threadId = threadId
        self.agentId = agentId
        self.attachments = attachments
    }

    enum CodingKeys: String, CodingKey {
        case goal, key, mode, model, effort, speed, approvals, attachments
        case goalMode = "goal_mode"
        case threadId = "thread_id"
        case agentId = "agent_id"
    }
}
