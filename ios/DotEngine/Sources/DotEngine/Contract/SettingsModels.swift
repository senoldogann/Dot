import Foundation

// The daemon's settings routes as Settings on the phone reads them (wave N3). Each type holds the part the phone
// shows; JSON keys the type does not name are ignored, so a daemon that adds a field breaks nothing.

/// `GET/PUT /api/assistant`: Dot's shown name and the owner's.
public struct AssistantView: Codable, Sendable, Hashable {
    public let name: String
    public let owner: String
    public let macName: String?

    public init(name: String, owner: String, macName: String?) {
        self.name = name
        self.owner = owner
        self.macName = macName
    }

    enum CodingKeys: String, CodingKey {
        case name, owner
        case macName = "mac_name"
    }
}

/// The body of `PUT /api/assistant`: what changes (the daemon updates only the fields sent).
public struct AssistantChange: Codable, Sendable, Hashable {
    public let name: String
    public let owner: String

    public init(name: String, owner: String) {
        self.name = name
        self.owner = owner
    }
}

/// `GET /api/health` (public): whether Dot answers, its version, and its push and keep-awake state.
public struct HealthView: Codable, Sendable, Hashable {
    public let ok: Bool
    public let version: String
    public let notifications: String
    public let awake: String

    public init(ok: Bool, version: String, notifications: String, awake: String) {
        self.ok = ok
        self.version = version
        self.notifications = notifications
        self.awake = awake
    }
}

/// `GET /api/status`: the part System and Connections show.
public struct StatusView: Codable, Sendable, Hashable {
    public let version: String
    public let uptimeS: Double
    public let activeRuns: [String]
    public let pendingApprovals: Int
    public let schedulerRunning: Bool
    public let failsafeEngaged: Bool
    public let approvals: String
    public let models: ModelsConfig
    public let resume: ResumeConfig

    public init(
        version: String, uptimeS: Double, activeRuns: [String], pendingApprovals: Int, schedulerRunning: Bool, failsafeEngaged: Bool,
        approvals: String, models: ModelsConfig, resume: ResumeConfig
    ) {
        self.version = version
        self.uptimeS = uptimeS
        self.activeRuns = activeRuns
        self.pendingApprovals = pendingApprovals
        self.schedulerRunning = schedulerRunning
        self.failsafeEngaged = failsafeEngaged
        self.approvals = approvals
        self.models = models
        self.resume = resume
    }

    enum CodingKeys: String, CodingKey {
        case version, approvals, models, resume
        case uptimeS = "uptime_s"
        case activeRuns = "active_runs"
        case pendingApprovals = "pending_approvals"
        case schedulerRunning = "scheduler_running"
        case failsafeEngaged = "failsafe_engaged"
    }
}

/// Whether runs a restart cut resume (`[agent] resume`).
public struct ResumeConfig: Codable, Sendable, Hashable {
    public let resume: String
    public let resumeWithinS: Int

    public init(resume: String, resumeWithinS: Int) {
        self.resume = resume
        self.resumeWithinS = resumeWithinS
    }

    enum CodingKeys: String, CodingKey {
        case resume
        case resumeWithinS = "resume_within_s"
    }
}

/// `[models]` as the daemon holds it: the main model and the roles.
public struct ModelsConfig: Codable, Sendable, Hashable {
    public let main: String
    public let vision: String
    public let fast: String
    public let review: String
    public let effort: String
    public let helpers: [String]
    public let standby: [String]
    /// The model a run of a kind of task starts on when none is chosen for it (item 1889; `security`), absent from a
    /// daemon before it and for a kind left to the main model.
    public let kinds: [String: String]?

    public init(main: String, vision: String, fast: String, review: String, effort: String, helpers: [String], standby: [String]) {
        self.main = main
        self.vision = vision
        self.fast = fast
        self.review = review
        self.effort = effort
        self.helpers = helpers
        self.standby = standby
        self.kinds = nil
    }
}

/// A role `POST /api/models/roles` sets.
public enum ModelRole: String, Codable, Sendable, CaseIterable {
    case fast
    case review
    case vision
}

/// `GET /api/code-review` (item 1663): the models of the pre-push review; a phone changes it too (item 1770).
public struct CodeReviewConfig: Codable, Sendable, Hashable {
    public let model: String
    public let effort: String
    public let fallback: [String]
    public let fallbackEffort: String
    public let smallModel: String
    public let smallMaxLines: Int
    public let maxMinutes: Int

    public init(model: String, effort: String, fallback: [String], fallbackEffort: String, smallModel: String, smallMaxLines: Int, maxMinutes: Int) {
        self.model = model
        self.effort = effort
        self.fallback = fallback
        self.fallbackEffort = fallbackEffort
        self.smallModel = smallModel
        self.smallMaxLines = smallMaxLines
        self.maxMinutes = maxMinutes
    }

    enum CodingKeys: String, CodingKey {
        case model, effort, fallback
        case fallbackEffort = "fallback_effort"
        case smallModel = "small_model"
        case smallMaxLines = "small_max_lines"
        case maxMinutes = "max_minutes"
    }
}

/// The body of `PUT /api/code-review` (item 1770): the whole table as it should stand, both efforts included.
public struct CodeReviewChange: Codable, Sendable, Hashable {
    public var model: String
    public var effort: String
    public var fallback: [String]
    public var fallbackEffort: String
    public var smallModel: String
    public var smallMaxLines: Int
    public var maxMinutes: Int

    /// The table as it stands, to change one part of.
    public init(_ config: CodeReviewConfig) {
        model = config.model
        effort = config.effort
        fallback = config.fallback
        fallbackEffort = config.fallbackEffort
        smallModel = config.smallModel
        smallMaxLines = config.smallMaxLines
        maxMinutes = config.maxMinutes
    }

    enum CodingKeys: String, CodingKey {
        case model, effort, fallback
        case fallbackEffort = "fallback_effort"
        case smallModel = "small_model"
        case smallMaxLines = "small_max_lines"
        case maxMinutes = "max_minutes"
    }
}

/// `[budget]` (item 1731): the caps of what the runs spend, in tokens; 0 is no cap.
public struct BudgetCaps: Codable, Sendable, Hashable {
    public var runTokens: Int
    public var dayTokens: Int
    public var uiDayTokens: Int
    public var scheduleDayTokens: Int
    public var initiativeDayTokens: Int
    public var reviewDayTokens: Int
    public var delegateDayTokens: Int

    public init(
        runTokens: Int, dayTokens: Int, uiDayTokens: Int, scheduleDayTokens: Int, initiativeDayTokens: Int, reviewDayTokens: Int,
        delegateDayTokens: Int
    ) {
        self.runTokens = runTokens
        self.dayTokens = dayTokens
        self.uiDayTokens = uiDayTokens
        self.scheduleDayTokens = scheduleDayTokens
        self.initiativeDayTokens = initiativeDayTokens
        self.reviewDayTokens = reviewDayTokens
        self.delegateDayTokens = delegateDayTokens
    }

    enum CodingKeys: String, CodingKey {
        case runTokens = "run_tokens"
        case dayTokens = "day_tokens"
        case uiDayTokens = "ui_day_tokens"
        case scheduleDayTokens = "schedule_day_tokens"
        case initiativeDayTokens = "initiative_day_tokens"
        case reviewDayTokens = "review_day_tokens"
        case delegateDayTokens = "delegate_day_tokens"
    }
}

/// The tokens the runs of one source that began today used.
public struct SourceSpend: Codable, Sendable, Hashable {
    public let source: String
    public let tokens: Int

    public init(source: String, tokens: Int) {
        self.source = source
        self.tokens = tokens
    }
}

/// `GET/PUT /api/budget` (item 1731).
public struct BudgetView: Codable, Sendable, Hashable {
    public let caps: BudgetCaps
    public let today: Int
    public let bySource: [SourceSpend]

    public init(caps: BudgetCaps, today: Int, bySource: [SourceSpend]) {
        self.caps = caps
        self.today = today
        self.bySource = bySource
    }

    enum CodingKeys: String, CodingKey {
        case caps, today
        case bySource = "by_source"
    }
}

/// `GET /api/usage`: the days' tokens and the plan's windows (item 1403).
public struct UsageView: Codable, Sendable, Hashable {
    public let days: [UsageDay]
    public let windows: [UsageWindow]

    public init(days: [UsageDay], windows: [UsageWindow]) {
        self.days = days
        self.windows = windows
    }
}

public struct UsageDay: Codable, Sendable, Hashable {
    public let day: String
    public let runs: Int
    public let inputTokens: Int
    public let outputTokens: Int

    public init(day: String, runs: Int, inputTokens: Int, outputTokens: Int) {
        self.day = day
        self.runs = runs
        self.inputTokens = inputTokens
        self.outputTokens = outputTokens
    }

    enum CodingKeys: String, CodingKey {
        case day, runs
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
    }
}

/// A usage window of a plan (`five_hour`, `seven_day`): how much is used and when it resets.
public struct UsageWindow: Codable, Sendable, Hashable {
    public let kind: String
    public let status: String
    public let utilization: Double?
    public let resetsAt: Double?

    public init(kind: String, status: String, utilization: Double?, resetsAt: Double?) {
        self.kind = kind
        self.status = status
        self.utilization = utilization
        self.resetsAt = resetsAt
    }

    enum CodingKeys: String, CodingKey {
        case kind, status, utilization
        case resetsAt = "resets_at"
    }
}

/// `GET /api/phone`: the Mac's addresses and its tailnet.
public struct PhoneSetupView: Codable, Sendable, Hashable {
    public let addresses: [String]
    public let tailnet: TailnetState
    public let keepAwake: Bool

    public init(addresses: [String], tailnet: TailnetState, keepAwake: Bool) {
        self.addresses = addresses
        self.tailnet = tailnet
        self.keepAwake = keepAwake
    }

    enum CodingKeys: String, CodingKey {
        case addresses, tailnet
        case keepAwake = "keep_awake"
    }
}

public struct TailnetState: Codable, Sendable, Hashable {
    public let installed: Bool
    public let running: Bool
    public let name: String?
    public let served: Bool
    public let reached: Bool
    public let problem: String?

    public init(installed: Bool, running: Bool, name: String?, served: Bool, reached: Bool, problem: String?) {
        self.installed = installed
        self.running = running
        self.name = name
        self.served = served
        self.reached = reached
        self.problem = problem
    }
}

/// `GET/PUT /api/phone/push-settings` (a phone's key only): which kinds of push this phone gets.
public struct PushSettings: Codable, Sendable, Hashable {
    public var needsYou: Bool
    public var ready: Bool
    public var done: Bool

    public init(needsYou: Bool, ready: Bool, done: Bool) {
        self.needsYou = needsYou
        self.ready = ready
        self.done = done
    }

    enum CodingKeys: String, CodingKey {
        case ready, done
        case needsYou = "needs_you"
    }
}

/// `GET /api/schedules`: a schedule and its times in the Mac's zone.
public struct ScheduleView: Codable, Sendable, Hashable, Identifiable {
    public let record: ScheduleRecord
    public let when: String
    public let nextLocal: String?
    public let lastLocal: String?
    public let runAtInput: String?
    public let zone: String
    public let agent: ScheduleAgent?

    public var id: String { record.id }

    public init(record: ScheduleRecord, when: String, nextLocal: String?, lastLocal: String?, runAtInput: String?, zone: String, agent: ScheduleAgent?) {
        self.record = record
        self.when = when
        self.nextLocal = nextLocal
        self.lastLocal = lastLocal
        self.runAtInput = runAtInput
        self.zone = zone
        self.agent = agent
    }

    enum CodingKeys: String, CodingKey {
        case record, when, zone, agent
        case nextLocal = "next_local"
        case lastLocal = "last_local"
        case runAtInput = "run_at_input"
    }
}

public struct ScheduleRecord: Codable, Sendable, Hashable {
    public let id: String
    public let name: String
    public let cron: String?
    public let runAt: Double?
    public let goal: String
    public let mode: String
    public let enabled: Bool
    public let lastRunAt: Double?
    public let nextRunAt: Double?
    public let lastStatus: String
    public let options: ScheduleOptions
    public let runsFailedInRow: Int

    public init(
        id: String, name: String, cron: String?, runAt: Double?, goal: String, mode: String, enabled: Bool, lastRunAt: Double?, nextRunAt: Double?,
        lastStatus: String, options: ScheduleOptions, runsFailedInRow: Int
    ) {
        self.id = id
        self.name = name
        self.cron = cron
        self.runAt = runAt
        self.goal = goal
        self.mode = mode
        self.enabled = enabled
        self.lastRunAt = lastRunAt
        self.nextRunAt = nextRunAt
        self.lastStatus = lastStatus
        self.options = options
        self.runsFailedInRow = runsFailedInRow
    }

    enum CodingKeys: String, CodingKey {
        case id, name, cron, goal, mode, enabled, options
        case runAt = "run_at"
        case lastRunAt = "last_run_at"
        case nextRunAt = "next_run_at"
        case lastStatus = "last_status"
        case runsFailedInRow = "runs_failed_in_row"
    }
}

public struct ScheduleOptions: Codable, Sendable, Hashable {
    public let model: String?
    public let effort: String?
    public let approvals: String?
    public let goalMode: Bool?
    public let timezone: String?

    public init(model: String?, effort: String?, approvals: String?, goalMode: Bool?, timezone: String?) {
        self.model = model
        self.effort = effort
        self.approvals = approvals
        self.goalMode = goalMode
        self.timezone = timezone
    }

    enum CodingKeys: String, CodingKey {
        case model, effort, approvals, timezone
        case goalMode = "goal_mode"
    }
}

public struct ScheduleAgent: Codable, Sendable, Hashable {
    public let id: String
    public let name: String
    public let pausedAt: Double?

    public init(id: String, name: String, pausedAt: Double?) {
        self.id = id
        self.name = name
        self.pausedAt = pausedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, name
        case pausedAt = "paused_at"
    }
}

/// The body of `PATCH /api/schedules/{id}`: the whole schedule as it should stand (the daemon takes no partial edit).
/// `agent_id` is left out, so the schedule keeps its agent; `enabled` is left out, so it keeps its switch.
public struct ScheduleEdit: Codable, Sendable, Hashable {
    public let name: String
    public let goal: String
    public let cron: String?
    public let at: String?
    public let mode: String
    public let model: String?
    public let effort: String?
    public let approvals: String?
    public let goalMode: Bool?
    public let timezone: String?

    public init(name: String, goal: String, cron: String?, at: String?, mode: String, model: String?, effort: String?, approvals: String?, goalMode: Bool?, timezone: String?) {
        self.name = name
        self.goal = goal
        self.cron = cron
        self.at = at
        self.mode = mode
        self.model = model
        self.effort = effort
        self.approvals = approvals
        self.goalMode = goalMode
        self.timezone = timezone
    }

    enum CodingKeys: String, CodingKey {
        case name, goal, cron, at, mode, model, effort, approvals, timezone
        case goalMode = "goal_mode"
    }

    /// Writes every key, nulls included: the daemon requires each of them (an absent key is a 422).
    public func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(goal, forKey: .goal)
        try container.encode(cron, forKey: .cron)
        try container.encode(at, forKey: .at)
        try container.encode(mode, forKey: .mode)
        try container.encode(model, forKey: .model)
        try container.encode(effort, forKey: .effort)
        try container.encode(approvals, forKey: .approvals)
        try container.encode(goalMode, forKey: .goalMode)
        try container.encode(timezone, forKey: .timezone)
    }
}

/// `GET /api/rules`: a rule of what Dot may do.
public struct RuleView: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let position: Int
    public let enabled: Bool
    public let behavior: String
    public let toolGlob: String
    public let matchRegex: String
    public let description: String

    public init(id: String, position: Int, enabled: Bool, behavior: String, toolGlob: String, matchRegex: String, description: String) {
        self.id = id
        self.position = position
        self.enabled = enabled
        self.behavior = behavior
        self.toolGlob = toolGlob
        self.matchRegex = matchRegex
        self.description = description
    }

    enum CodingKeys: String, CodingKey {
        case id, position, enabled, behavior, description
        case toolGlob = "tool_glob"
        case matchRegex = "match_regex"
    }
}

/// A memory note, the part Settings shows.
public struct MemoryNote: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let content: String
    public let tags: [String]
    public let updatedAt: Double
    public let pinnedAt: Double?
    public let tainted: Bool
    public let proposed: ProposedChange?

    public init(id: String, content: String, tags: [String], updatedAt: Double, pinnedAt: Double?, tainted: Bool, proposed: ProposedChange?) {
        self.id = id
        self.content = content
        self.tags = tags
        self.updatedAt = updatedAt
        self.pinnedAt = pinnedAt
        self.tainted = tainted
        self.proposed = proposed
    }

    enum CodingKeys: String, CodingKey {
        case id, content, tags, tainted, proposed
        case updatedAt = "updated_at"
        case pinnedAt = "pinned_at"
    }
}

/// A run's rewrite of a note the owner wrote or approved, waiting for the owner (item 1711).
public struct ProposedChange: Codable, Sendable, Hashable {
    public let content: String
    public let runId: String
    public let tainted: Bool
    public let at: Double

    public init(content: String, runId: String, tainted: Bool, at: Double) {
        self.content = content
        self.runId = runId
        self.tainted = tainted
        self.at = at
    }

    enum CodingKeys: String, CodingKey {
        case content, tainted, at
        case runId = "run_id"
    }
}

/// One hit of `GET /api/memories/search`.
public struct MemoryHit: Codable, Sendable, Hashable {
    public let record: MemoryNote
    public let similarity: Double?

    public init(record: MemoryNote, similarity: Double?) {
        self.record = record
        self.similarity = similarity
    }
}

/// `GET/POST /api/reactions/settings`: what each reaction does.
public struct ReactionsConfig: Codable, Sendable, Hashable {
    public let followUps: Bool
    public let good: String
    public let love: String
    public let wrong: String
    public let again: String
    public let explain: String
    public let hold: String

    public init(followUps: Bool, good: String, love: String, wrong: String, again: String, explain: String, hold: String) {
        self.followUps = followUps
        self.good = good
        self.love = love
        self.wrong = wrong
        self.again = again
        self.explain = explain
        self.hold = hold
    }

    enum CodingKeys: String, CodingKey {
        case good, love, wrong, again, explain, hold
        case followUps = "follow_ups"
    }
}

/// `GET /api/proof`: the daemon's proof that it holds this phone's key.
public struct ProofAnswer: Codable, Sendable, Hashable {
    public let proof: String

    public init(proof: String) { self.proof = proof }
}

/// A role of the night's learning (item 1033), `PUT /api/models/learning` sets.
public enum LearningRole: String, Codable, Sendable, CaseIterable {
    case proposer
    case composer
    case selector
    case experience
}

/// `GET`/`PUT /api/models/learning`: the model of each role of the night's learning, "" where it plays on the review
/// model, and that review model.
public struct LearningModels: Codable, Sendable, Hashable {
    public let proposer: String
    public let composer: String
    public let selector: String
    public let experience: String
    public let review: String

    public init(proposer: String, composer: String, selector: String, experience: String, review: String) {
        self.proposer = proposer
        self.composer = composer
        self.selector = selector
        self.experience = experience
        self.review = review
    }

    public func model(of role: LearningRole) -> String {
        switch role {
        case .proposer: proposer
        case .composer: composer
        case .selector: selector
        case .experience: experience
        }
    }
}

/// The part of `GET /api/initiative` the Models page reads: the model that looks for work on Dot's own initiative
/// (item 916), "" for the main model.
public struct InitiativeLook: Codable, Sendable, Hashable {
    public let lookModel: String

    public init(lookModel: String) { self.lookModel = lookModel }

    enum CodingKeys: String, CodingKey {
        case lookModel = "look_model"
    }
}

/// How Claude Code works for self-repair (item 933): its model of the Claude plan (`default`, `opus`, …), its effort
/// (`default`, `low` … `max`) and fast mode.
public struct RepairWorker: Codable, Sendable, Hashable {
    public let model: String
    public let effort: String
    public let fast: Bool

    public init(model: String, effort: String, fast: Bool) {
        self.model = model
        self.effort = effort
        self.fast = fast
    }
}

/// The part of `GET /api/repair` the Models page reads: the worker, and whether its kept choice could not be read.
public struct RepairWorkerView: Codable, Sendable, Hashable {
    public let worker: RepairWorker
    public let workerLost: Bool

    public init(worker: RepairWorker, workerLost: Bool) {
        self.worker = worker
        self.workerLost = workerLost
    }

    enum CodingKeys: String, CodingKey {
        case worker
        case workerLost = "worker_lost"
    }
}
