import Foundation

// The daemon's routes the Activity tab reads and writes (item 1826): what is in motion (`GET /api/activity`), what Dot
// did while the owner was away (`/api/away`), the morning brief, the evening check-in, Dot's own initiative, the
// watchers and the drafts that wait. Each type holds the part the phone shows; JSON keys it does not name are ignored,
// so a daemon that adds a field breaks nothing.

/// One thing in motion or ended today (`server/views.py` `ActivityRow`).
public struct ActivityRow: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    /// `waiting`, `working`, `recent`, `learned` or `due`.
    public let section: String
    /// `run`, `delegated`, `campaign`, `approval`, `question`, `takeover`, `schedule`, `reflection`, `lesson`, `journal`.
    public let kind: String
    public let title: String
    public let detail: String
    public let since: Double
    /// Where the web opens it (`#/runs/<id>`, `#/memory`, `#/settings/schedules`).
    public let href: String
    public let runId: String?
    public let status: String
    public let minutes: Double?
    public let tokens: Int?
    public let steps: Int?
    public let parentRunId: String?
    /// The chat of the run at the top of a delegated run's chain (item 1074); `nil` for any other row.
    public let rootThreadId: String?
    /// Whose face the row wears: the agent's id for an agent's run, the campaign's for a session, a delegated run's own;
    /// `nil` for Dot's own.
    public let actorId: String?

    public init(
        id: String, section: String, kind: String, title: String, detail: String, since: Double, href: String, runId: String?, status: String,
        minutes: Double?, tokens: Int?, steps: Int?, parentRunId: String?, rootThreadId: String?, actorId: String?
    ) {
        self.id = id
        self.section = section
        self.kind = kind
        self.title = title
        self.detail = detail
        self.since = since
        self.href = href
        self.runId = runId
        self.status = status
        self.minutes = minutes
        self.tokens = tokens
        self.steps = steps
        self.parentRunId = parentRunId
        self.rootThreadId = rootThreadId
        self.actorId = actorId
    }

    enum CodingKeys: String, CodingKey {
        case id, section, kind, title, detail, since, href, status, minutes, tokens, steps
        case runId = "run_id"
        case parentRunId = "parent_run_id"
        case rootThreadId = "root_thread_id"
        case actorId = "actor_id"
    }
}

/// `GET /api/activity`: what waits for the owner, what works now, what ended today, what was learned today and what is
/// due later today.
public struct ActivityView: Codable, Sendable, Hashable {
    public let waiting: [ActivityRow]
    public let working: [ActivityRow]
    public let endedDelegated: [ActivityRow]
    public let recent: [ActivityRow]
    public let learnedToday: [ActivityRow]
    public let due: [ActivityRow]
    public let generatedAt: Double

    public init(
        waiting: [ActivityRow], working: [ActivityRow], endedDelegated: [ActivityRow], recent: [ActivityRow], learnedToday: [ActivityRow], due: [ActivityRow],
        generatedAt: Double
    ) {
        self.waiting = waiting
        self.working = working
        self.endedDelegated = endedDelegated
        self.recent = recent
        self.learnedToday = learnedToday
        self.due = due
        self.generatedAt = generatedAt
    }

    enum CodingKeys: String, CodingKey {
        case waiting, working, recent, due
        case endedDelegated = "ended_delegated"
        case learnedToday = "learned_today"
        case generatedAt = "generated_at"
    }

    /// Whether nothing is in motion, ended, learned or due today.
    public var isEmpty: Bool { waiting.isEmpty && working.isEmpty && endedDelegated.isEmpty && recent.isEmpty && learnedToday.isEmpty && due.isEmpty }
}

/// A run "while you were away" lists (items 466 and 1424).
public struct AwayRun: Codable, Sendable, Hashable, Identifiable {
    public let runId: String
    public let title: String
    public let source: String
    /// Whether it is a session of a campaign.
    public let campaign: Bool
    /// The agent the run belongs to; `nil` for Dot's own.
    public let agentId: String?
    public let status: String
    public let createdAt: Double
    public let finishedAt: Double?
    /// The first line of its answer, its secrets masked.
    public let answer: String

    public var id: String { runId }

    public init(
        runId: String, title: String, source: String, campaign: Bool, agentId: String?, status: String, createdAt: Double, finishedAt: Double?, answer: String
    ) {
        self.runId = runId
        self.title = title
        self.source = source
        self.campaign = campaign
        self.agentId = agentId
        self.status = status
        self.createdAt = createdAt
        self.finishedAt = finishedAt
        self.answer = answer
    }

    enum CodingKeys: String, CodingKey {
        case title, source, campaign, status, answer
        case runId = "run_id"
        case agentId = "agent_id"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
    }
}

/// The approvals answered for the owner in those runs: by the review model and without asking (yolo).
public struct AwayDecisions: Codable, Sendable, Hashable {
    public let reviewerApproved: Int
    public let reviewerDenied: Int
    public let yoloApproved: Int
    public let yoloDenied: Int

    public init(reviewerApproved: Int, reviewerDenied: Int, yoloApproved: Int, yoloDenied: Int) {
        self.reviewerApproved = reviewerApproved
        self.reviewerDenied = reviewerDenied
        self.yoloApproved = yoloApproved
        self.yoloDenied = yoloDenied
    }

    enum CodingKeys: String, CodingKey {
        case reviewerApproved = "reviewer_approved"
        case reviewerDenied = "reviewer_denied"
        case yoloApproved = "yolo_approved"
        case yoloDenied = "yolo_denied"
    }
}

/// One of the runs with the most approvals answered for the owner.
public struct AwayDecisionRun: Codable, Sendable, Hashable, Identifiable {
    public let runId: String
    public let title: String
    public let reviewer: Int
    public let yolo: Int
    public let denied: Int

    public var id: String { runId }

    public init(runId: String, title: String, reviewer: Int, yolo: Int, denied: Int) {
        self.runId = runId
        self.title = title
        self.reviewer = reviewer
        self.yolo = yolo
        self.denied = denied
    }

    enum CodingKeys: String, CodingKey {
        case title, reviewer, yolo, denied
        case runId = "run_id"
    }
}

/// `GET /api/away?since=`: what Dot did on its own between `since` and `until`.
public struct AwayView: Codable, Sendable, Hashable {
    public let since: Double
    public let until: Double
    public let done: Int
    public let failed: Int
    public let cancelled: Int
    public let working: Int
    public let campaignSessions: Int
    public let runs: [AwayRun]
    public let decisions: AwayDecisions
    public let decisionRuns: [AwayDecisionRun]
    public let drafts: Int

    public init(
        since: Double, until: Double, done: Int, failed: Int, cancelled: Int, working: Int, campaignSessions: Int, runs: [AwayRun], decisions: AwayDecisions,
        decisionRuns: [AwayDecisionRun], drafts: Int
    ) {
        self.since = since
        self.until = until
        self.done = done
        self.failed = failed
        self.cancelled = cancelled
        self.working = working
        self.campaignSessions = campaignSessions
        self.runs = runs
        self.decisions = decisions
        self.decisionRuns = decisionRuns
        self.drafts = drafts
    }

    enum CodingKeys: String, CodingKey {
        case since, until, done, failed, cancelled, working, runs, decisions, drafts
        case campaignSessions = "campaign_sessions"
        case decisionRuns = "decision_runs"
    }
}

/// `GET/POST /api/away/read`: when the owner last marked "while you were away" read, on any device; `nil` before any.
public struct AwayRead: Codable, Sendable, Hashable {
    public let readAt: Double?

    public init(readAt: Double?) { self.readAt = readAt }

    enum CodingKeys: String, CodingKey {
        case readAt = "read_at"
    }
}

/// `GET/PUT /api/brief`: the morning brief as its card shows it. `custom` says its schedule fires at a time the card
/// cannot show (saving replaces it); `accounts` is how many mail and calendar accounts are signed in.
public struct BriefView: Codable, Sendable, Hashable {
    public let enabled: Bool
    /// `HH:MM` in `zone`.
    public let time: String
    /// `every` or `weekdays`.
    public let days: String
    public let custom: Bool
    public let cron: String?
    public let lines: [String]
    public let zone: String
    public let scheduleId: String?
    public let nextLocal: String?
    public let lastLocal: String?
    public let lastStatus: String
    public let lastRunId: String?
    public let accounts: Int

    public init(
        enabled: Bool, time: String, days: String, custom: Bool, cron: String?, lines: [String], zone: String, scheduleId: String?, nextLocal: String?,
        lastLocal: String?, lastStatus: String, lastRunId: String?, accounts: Int
    ) {
        self.enabled = enabled
        self.time = time
        self.days = days
        self.custom = custom
        self.cron = cron
        self.lines = lines
        self.zone = zone
        self.scheduleId = scheduleId
        self.nextLocal = nextLocal
        self.lastLocal = lastLocal
        self.lastStatus = lastStatus
        self.lastRunId = lastRunId
        self.accounts = accounts
    }

    enum CodingKeys: String, CodingKey {
        case enabled, time, days, custom, cron, lines, zone, accounts
        case scheduleId = "schedule_id"
        case nextLocal = "next_local"
        case lastLocal = "last_local"
        case lastStatus = "last_status"
        case lastRunId = "last_run_id"
    }
}

/// The body of `PUT /api/brief`.
public struct BriefChange: Codable, Sendable, Hashable {
    public let enabled: Bool
    public let time: String
    public let days: String
    public let lines: [String]

    public init(enabled: Bool, time: String, days: String, lines: [String]) {
        self.enabled = enabled
        self.time = time
        self.days = days
        self.lines = lines
    }
}

/// `GET/PUT /api/checkin`: the evening check-in as its card shows it (item 930).
public struct CheckinView: Codable, Sendable, Hashable {
    public let enabled: Bool
    public let time: String
    public let days: String
    public let zone: String
    public let scheduleId: String?
    public let nextLocal: String?
    public let lastLocal: String?
    public let lastRunId: String?

    public init(enabled: Bool, time: String, days: String, zone: String, scheduleId: String?, nextLocal: String?, lastLocal: String?, lastRunId: String?) {
        self.enabled = enabled
        self.time = time
        self.days = days
        self.zone = zone
        self.scheduleId = scheduleId
        self.nextLocal = nextLocal
        self.lastLocal = lastLocal
        self.lastRunId = lastRunId
    }

    enum CodingKeys: String, CodingKey {
        case enabled, time, days, zone
        case scheduleId = "schedule_id"
        case nextLocal = "next_local"
        case lastLocal = "last_local"
        case lastRunId = "last_run_id"
    }
}

/// The body of `PUT /api/checkin`.
public struct CheckinChange: Codable, Sendable, Hashable {
    public let enabled: Bool
    public let time: String
    public let days: String

    public init(enabled: Bool, time: String, days: String) {
        self.enabled = enabled
        self.time = time
        self.days = days
    }
}

/// A thing on Dot's own list (item 715): `status` is `doing`, `open`, `done` or `dropped`; `source` `owner` or `run`.
public struct InitiativeTodo: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let title: String
    public let why: String
    public let source: String
    public let status: String
    public let attempts: Int
    public let runId: String?
    public let note: String
    public let updatedAt: Double

    public init(id: String, title: String, why: String, source: String, status: String, attempts: Int, runId: String?, note: String, updatedAt: Double) {
        self.id = id
        self.title = title
        self.why = why
        self.source = source
        self.status = status
        self.attempts = attempts
        self.runId = runId
        self.note = note
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, title, why, source, status, attempts, note
        case runId = "run_id"
        case updatedAt = "updated_at"
    }
}

/// `GET/PUT /api/initiative`: whether Dot works on its own list while the Mac is idle, and the list. `idleReadable`
/// false: macOS does not say how long the Mac has been idle, so nothing starts; `approvals` is the mode its runs start
/// in; `peakHoursUtc` the weekday hours (UTC) none starts in; `lookModel` the model that looks for work ("" default).
public struct InitiativeView: Codable, Sendable, Hashable {
    public let enabled: Bool
    public let lookedAt: Double?
    public let todos: [InitiativeTodo]
    public let idleReadable: Bool
    public let approvals: String
    public let peakHoursUtc: [Int]
    public let lookModel: String

    public init(enabled: Bool, lookedAt: Double?, todos: [InitiativeTodo], idleReadable: Bool, approvals: String, peakHoursUtc: [Int], lookModel: String) {
        self.enabled = enabled
        self.lookedAt = lookedAt
        self.todos = todos
        self.idleReadable = idleReadable
        self.approvals = approvals
        self.peakHoursUtc = peakHoursUtc
        self.lookModel = lookModel
    }

    enum CodingKeys: String, CodingKey {
        case enabled, todos, approvals
        case lookedAt = "looked_at"
        case idleReadable = "idle_readable"
        case peakHoursUtc = "peak_hours_utc"
        case lookModel = "look_model"
    }

    /// The things being done or to do.
    public var waiting: Int { todos.filter { $0.status == "open" || $0.status == "doing" }.count }
}

/// A watcher (item 571) as the page shows it: `kind` is `mail`, `calendar` or `github`; `action` `tell` or `run`;
/// `minutes` how often it looks (how long before an event, for the calendar); `told` its tells in the last hour.
public struct WatcherView: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let kind: String
    public let enabled: Bool
    public let account: String
    public let words: String
    public let minutes: Int
    public let action: String
    public let goal: String
    public let lookedAt: Double?
    public let nextLookAt: Double?
    public let error: String?
    public let last: String
    public let told: Int

    public init(
        id: String, name: String, kind: String, enabled: Bool, account: String, words: String, minutes: Int, action: String, goal: String, lookedAt: Double?,
        nextLookAt: Double?, error: String?, last: String, told: Int
    ) {
        self.id = id
        self.name = name
        self.kind = kind
        self.enabled = enabled
        self.account = account
        self.words = words
        self.minutes = minutes
        self.action = action
        self.goal = goal
        self.lookedAt = lookedAt
        self.nextLookAt = nextLookAt
        self.error = error
        self.last = last
        self.told = told
    }

    enum CodingKeys: String, CodingKey {
        case id, name, kind, enabled, account, words, minutes, action, goal, error, last, told
        case lookedAt = "looked_at"
        case nextLookAt = "next_look_at"
    }

    /// The whole of its choices, as `PATCH /api/watchers/{id}` takes them, with `enabled` as given.
    public func request(enabled: Bool) -> WatcherRequest {
        WatcherRequest(name: name, kind: kind, enabled: enabled, account: account, words: words, minutes: minutes, action: action, goal: goal)
    }
}

/// The body of `PATCH /api/watchers/{id}`: the whole of a watcher's choices.
public struct WatcherRequest: Codable, Sendable, Hashable {
    public let name: String
    public let kind: String
    public let enabled: Bool
    public let account: String
    public let words: String
    public let minutes: Int
    public let action: String
    public let goal: String

    public init(name: String, kind: String, enabled: Bool, account: String, words: String, minutes: Int, action: String, goal: String) {
        self.name = name
        self.kind = kind
        self.enabled = enabled
        self.account = account
        self.words = words
        self.minutes = minutes
        self.action = action
        self.goal = goal
    }
}

/// A draft a run wrote for the owner (item 314): `channel` is `mail` or `message`; `status` `waiting`, `sending`,
/// `sent`, `discarded` or `failed` (`error` says why the last send failed).
public struct DraftView: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let runId: String
    public let channel: String
    public let to: [String]
    public let cc: [String]
    public let subject: String
    public let body: String
    public let status: String
    public let createdAt: Double
    public let error: String?

    public init(id: String, runId: String, channel: String, to: [String], cc: [String], subject: String, body: String, status: String, createdAt: Double, error: String?) {
        self.id = id
        self.runId = runId
        self.channel = channel
        self.to = to
        self.cc = cc
        self.subject = subject
        self.body = body
        self.status = status
        self.createdAt = createdAt
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case id, channel, to, cc, subject, body, status, error
        case runId = "run_id"
        case createdAt = "created_at"
    }

    /// Whether it still waits for the owner: none sent or discarded it yet, or its last send failed (it can be sent
    /// again).
    public var isOpen: Bool { status == "waiting" || status == "failed" }
}

/// The body of `POST /api/drafts/{id}/send`: the words that go (a message has no cc and no subject).
public struct DraftWords: Codable, Sendable, Hashable {
    public let to: [String]
    public let cc: [String]
    public let subject: String
    public let body: String

    public init(to: [String], cc: [String], subject: String, body: String) {
        self.to = to
        self.cc = cc
        self.subject = subject
        self.body = body
    }
}

/// The head of `GET /api/runs/{id}`: the run, for the chat it is in.
public struct RunHead: Codable, Sendable, Hashable {
    public let run: RunRecord

    public init(run: RunRecord) { self.run = run }
}

/// A version of Dot's habits and tips (the harness, docs/harness.md): `state` is `candidate` (new, nobody tried it),
/// `probed` (tried by the nightly loop), `adopted` (every task follows it) or `retired` (set aside); `madeBy` is
/// `owner`, `proposer` or `composer`.
public struct HarnessVersion: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let madeBy: String
    public let state: String
    public let summary: String
    public let habits: String
    public let toolGuidance: String
    public let createdAt: Double

    public init(id: String, madeBy: String, state: String, summary: String, habits: String, toolGuidance: String, createdAt: Double) {
        self.id = id
        self.madeBy = madeBy
        self.state = state
        self.summary = summary
        self.habits = habits
        self.toolGuidance = toolGuidance
        self.createdAt = createdAt
    }

    enum CodingKeys: String, CodingKey {
        case id, state, summary, habits
        case madeBy = "made_by"
        case toolGuidance = "tool_guidance"
        case createdAt = "created_at"
    }

    /// Whether it is a proposal that waits for the owner: new or tried, neither in use nor set aside.
    public var isProposal: Bool { state == "candidate" || state == "probed" }
}

/// `GET /api/harness`: the version in use (`nil`: Dot's own instructions alone), a page of the versions, newest first,
/// which come from content others can write and why (`fromOutside`, by id), and which the gate for learned texts
/// refuses now (`refused`, by id, in words: such a version is never put in use).
public struct HarnessView: Codable, Sendable, Hashable {
    public let adopted: HarnessVersion?
    public let versions: [HarnessVersion]
    public let fromOutside: [String: [String]]
    public let refused: [String: String]

    public init(adopted: HarnessVersion?, versions: [HarnessVersion], fromOutside: [String: [String]], refused: [String: String]) {
        self.adopted = adopted
        self.versions = versions
        self.fromOutside = fromOutside
        self.refused = refused
    }

    enum CodingKeys: String, CodingKey {
        case adopted, versions, refused
        case fromOutside = "from_outside"
    }

    /// The proposals that wait for the owner, newest first.
    public var proposals: [HarnessVersion] { versions.filter(\.isProposal) }
}
