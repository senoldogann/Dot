import Foundation

/// A tool step as the chat draws it: one line, collapsed.
struct ToolRow: Sendable, Hashable {
    enum State: Sendable, Hashable { case running, ok, failed }
    let callId: String
    let runId: String
    var tool: String
    var line: String
    var state: State
    /// The id of the step's `tool_call` event (0 when it is not stored): where its detail is read from.
    var eventId: Int = 0
}

/// A step or a question the run asks the owner about. `question` is set for `ask_user`.
struct ApprovalCard: Sendable, Hashable {
    let approvalId: String
    let runId: String
    var tool: String
    var summary: String
    var severity: String
    var question: String?
    /// `pending`, `approved`, `denied`, `expired` or `left`.
    var status: String

    var isPending: Bool { status == "pending" }
}

/// A helper (subagent): a delegated run of the chat's run.
struct Helper: Sendable, Hashable, Identifiable {
    let id: String
    var goal: String
    var status: RunStatus
    /// What it does now, in a few words ("Running swift test").
    var words: String?

    var isLive: Bool { status.isActiveRunStatus }
}

/// A step of the run's plan.
struct PlanStep: Sendable, Hashable {
    var text: String
    var status: String
}

/// One row of the conversation. `id` is stable for the row's life: a streaming message keeps its id as it grows and
/// when its step ends, so a list that keys on it never redraws the rows above.
struct ChatItem: Sendable, Hashable, Identifiable {
    enum Tone: Sendable, Hashable { case plain, warn, bad }
    enum Kind: Sendable, Hashable {
        case user(String)
        case assistant(text: String, streaming: Bool)
        case answer(text: String, status: RunStatus)
        case tool(ToolRow)
        case approval(ApprovalCard)
        case note(String, Tone)
    }

    let id: String
    var kind: Kind
    var ts: Double
}

/// The line over the composer.
enum RunLine: Sendable, Hashable {
    case idle
    case working(since: Double?)
    case waitingOnYou(count: Int)
    case waitingOnSubagents(count: Int, now: String?)
    /// The run went on after a message of the owner's, and its helpers work on without it (item 1351).
    case helpersInBackground(count: Int)
}

/// A chat as the phone draws it, built from the runs of a chat and their events by applying each in order. Applying
/// an event twice leaves the same state (a reconnect may hand over one again), except a streamed delta, which has no
/// id and is healed by the `assistant` event of its step.
struct ChatState: Sendable, Equatable {
    private(set) var items: [ChatItem] = []
    private(set) var helpers: [Helper] = []
    private(set) var plan: [PlanStep] = []
    /// The highest stored event id applied (deltas have none): where a reconnect goes on from.
    private(set) var lastEventId: Int = 0
    private(set) var rootRunIds: [String] = []
    /// The newest root run's status and when it started.
    private(set) var runStatus: RunStatus?
    private(set) var runStartedAt: Double?
    /// The helpers the run stopped waiting for (the `helper_released` event of item 1351): they work on without it.
    private var released: Set<String> = []
    private var index: [String: Int] = [:]

    var newestRunId: String? { rootRunIds.last }
    var isWorking: Bool { runStatus?.isActiveRunStatus ?? false }
    var pendingApprovals: [ApprovalCard] {
        items.compactMap { item in
            if case .approval(let card) = item.kind, card.isPending { return card }
            return nil
        }
    }
    var liveHelpers: [Helper] { helpers.filter(\.isLive) }

    /// What the line over the composer says.
    var line: RunLine {
        guard isWorking else { return .idle }
        let asking: Int = pendingApprovals.count
        if asking > 0 { return .waitingOnYou(count: asking) }
        let live: [Helper] = liveHelpers
        let waitedFor: [Helper] = live.filter { !released.contains($0.id) }
        if !waitedFor.isEmpty {
            let first: Helper = waitedFor[0]
            let now: String? = first.words.map { "\(shortened(first.goal, 40)): \($0)" }
            return .waitingOnSubagents(count: waitedFor.count, now: now)
        }
        if !live.isEmpty { return .helpersInBackground(count: live.count) }
        return .working(since: runStartedAt)
    }

    // MARK: Runs

    /// Registers a root run of the chat: its goal is the owner's message. Runs of a helper, and those that only
    /// follow up a reaction, say nothing of their own here. Applied again, it only refreshes the status.
    mutating func addRun(_ run: RunRecord) {
        guard run.parentRunId == nil else { return }
        if !rootRunIds.contains(run.id) {
            rootRunIds.append(run.id)
            if run.followsReaction == nil, !run.goal.isEmpty {
                upsert(ChatItem(id: "u:\(run.id)", kind: .user(run.goal), ts: run.createdAt))
            }
        }
        if run.id == rootRunIds.last {
            runStatus = run.status
            runStartedAt = run.createdAt
        }
    }

    /// The status the daemon's record of the newest run gives, after a stop or a refresh.
    mutating func setStatus(_ status: RunStatus, ofRun runId: String) {
        guard runId == rootRunIds.last else { return }
        runStatus = status
        if !status.isActiveRunStatus { settleStreaming() }
    }

    /// A root run that is not followed event by event (an older one of the chat): its message and its answer as the
    /// record has them.
    mutating func addSummary(_ run: RunRecord) {
        guard run.parentRunId == nil else { return }
        addRun(run)
        let result: String = run.result.trimmingCharacters(in: .whitespacesAndNewlines)
        if !run.status.isActiveRunStatus, !result.isEmpty {
            upsert(ChatItem(id: "r:\(run.id)", kind: .answer(text: result, status: run.status), ts: run.finishedAt ?? run.createdAt))
        }
    }

    /// Older root runs of the chat as their records have them (a page of `GET /api/runs?before=`, newest first): they go
    /// before every row held, oldest first, so the transcript still reads in order; one already held is skipped. Returns
    /// how many runs were added.
    @discardableResult
    mutating func prependOlder(_ runs: [RunRecord]) -> Int {
        let held: Set<String> = Set(rootRunIds)
        let fresh: [RunRecord] = runs.filter { $0.parentRunId == nil && !held.contains($0.id) }
            .sorted { $0.createdAt < $1.createdAt }
        guard !fresh.isEmpty else { return 0 }
        var rows: [ChatItem] = []
        for run in fresh {
            if run.followsReaction == nil, !run.goal.isEmpty {
                rows.append(ChatItem(id: "u:\(run.id)", kind: .user(run.goal), ts: run.createdAt))
            }
            let result: String = run.result.trimmingCharacters(in: .whitespacesAndNewlines)
            if !run.status.isActiveRunStatus, !result.isEmpty {
                rows.append(ChatItem(id: "r:\(run.id)", kind: .answer(text: result, status: run.status), ts: run.finishedAt ?? run.createdAt))
            }
        }
        let known: Set<String> = Set(items.map(\.id))
        rootRunIds.insert(contentsOf: fresh.map(\.id), at: 0)
        items.insert(contentsOf: rows.filter { !known.contains($0.id) }, at: 0)
        reindex()
        return fresh.count
    }

    /// The helpers of the chat's runs as the daemon lists them: a helper this state has not heard of joins, one it has
    /// takes the daemon's status (the stream may have been cut when it ended).
    mutating func setHelpers(_ runs: [RunRecord]) {
        for run in runs.reversed() where run.parentRunId != nil {
            if let at: Int = helpers.firstIndex(where: { $0.id == run.id }) {
                helpers[at].status = run.status
                if !run.status.isActiveRunStatus { helpers[at].words = nil }
            } else {
                helpers.append(Helper(id: run.id, goal: run.goal, status: run.status, words: nil))
            }
        }
    }

    /// The approvals and questions of the chat still open as the daemon lists them: one this state lacks is added (the
    /// event that asked it was not read), a card the daemon no longer lists as open is closed.
    mutating func setOpenQuestions(_ questions: [ApprovalRecord]) {
        let open: Set<String> = Set(questions.map(\.id))
        for question in questions {
            // The daemon's list is the truth both ways: a card it lists as open asks (again), one it no longer lists is closed.
            if let at: Int = index["p:\(question.id)"] {
                if case .approval(var card) = items[at].kind, !card.isPending {
                    card.status = "pending"
                    items[at].kind = .approval(card)
                }
                continue
            }
            upsert(ChatItem(
                id: "p:\(question.id)",
                kind: .approval(ApprovalCard(
                    approvalId: question.id, runId: question.runId, tool: question.tool, summary: question.summary,
                    severity: question.severity, question: question.tool == "ask_user" ? question.args["question"]?.string : nil,
                    status: "pending")),
                ts: Date().timeIntervalSince1970))
        }
        for (at, item) in items.enumerated() {
            if case .approval(var card) = item.kind, card.isPending, !open.contains(card.approvalId) {
                card.status = "expired"
                items[at].kind = .approval(card)
            }
        }
        if let waiting: ApprovalCard = pendingApprovals.last, runStatus == "running", waiting.runId == rootRunIds.last {
            runStatus = "blocked"
        } else if runStatus == "blocked", pendingApprovals.isEmpty {
            runStatus = "running"
        }
    }

    /// Events of a run that could not be read: a row says so, for good (the daemon's page and stream went past them).
    mutating func setUnreadable(_ count: Int, ofRun runId: String) {
        guard count > 0 else { return }
        let words: String = count == 1 ? "1 message of this run could not be shown here." : "\(count) messages of this run could not be shown here."
        upsert(ChatItem(id: "n:unreadable:\(runId)", kind: .note(words + " The web UI shows them.", .warn), ts: Date().timeIntervalSince1970))
    }

    /// A line of the chat that is no event's (a note), kept once under `id`.
    mutating func upsertNote(id: String, words: String, ts: Double) {
        upsert(ChatItem(id: id, kind: .note(words, .plain), ts: ts))
    }

    // MARK: Events

    mutating func apply(_ events: [EventRecord]) {
        for event in events { apply(event) }
    }

    mutating func apply(_ event: EventRecord) {
        if event.id > 0 { lastEventId = max(lastEventId, event.id) }
        let isRoot: Bool = rootRunIds.contains(event.runId)
        let data: [String: JSONValue] = event.data
        switch event.kind {
        case "run_queued":
            if data["parent_run_id"]?.string != nil {
                if !helpers.contains(where: { $0.id == event.runId }) {
                    helpers.append(Helper(id: event.runId, goal: data["goal"]?.string ?? "", status: "queued", words: nil))
                }
            } else if !isRoot {
                rootRunIds.append(event.runId)
                runStatus = "queued"
                runStartedAt = event.ts
                if data["follows_reaction"]?.string == nil, let goal: String = data["goal"]?.string, !goal.isEmpty {
                    upsert(ChatItem(id: "u:\(event.runId)", kind: .user(goal), ts: event.ts))
                }
            }
        case "run_started":
            if isRoot {
                if event.runId == rootRunIds.last {
                    runStatus = "running"
                    runStartedAt = runStartedAt ?? event.ts
                }
            } else if let at: Int = helpers.firstIndex(where: { $0.id == event.runId }) {
                helpers[at].status = "running"
            }
        case "run_finished":
            finished(event, isRoot: isRoot)
        case "delta":
            guard isRoot else { return }
            delta(event)
        case "assistant":
            guard isRoot else { return }
            assistant(event)
        case "tool_call":
            toolCall(event, isRoot: isRoot)
        case "tool_result":
            toolResult(event, isRoot: isRoot)
        case "approval_requested":
            approvalRequested(event, isRoot: isRoot)
        case "approval_decided":
            approvalDecided(event)
        case "steer":
            steer(event, isRoot: isRoot)
        case "plan":
            if isRoot, let steps: [JSONValue] = data["steps"]?.array {
                plan = steps.compactMap { step in
                    guard let text: String = step["text"]?.string else { return nil }
                    return PlanStep(text: text, status: step["status"]?.string ?? "pending")
                }
            }
        case "helper_released":
            if let id: String = data["run_id"]?.string { released.insert(id) }
        case "schedule_made":
            if isRoot {
                upsert(ChatItem(
                    id: "n:\(event.id)", kind: .note("Schedule made: \(data["name"]?.string ?? "")", .plain), ts: event.ts))
            }
        case "started_beside":
            if isRoot {
                upsert(ChatItem(id: "n:\(event.id)", kind: .note("Started in a chat of its own", .plain), ts: event.ts))
            }
        case "limit_reached", "stall":
            if isRoot {
                let words: String = data["detail"]?.string ?? data["reason"]?.string ?? "The run hit a limit."
                upsert(ChatItem(id: "n:\(event.id)", kind: .note(shortened(words, 240), .warn), ts: event.ts))
            }
        default:
            break
        }
    }

    // MARK: Handlers

    private mutating func finished(_ event: EventRecord, isRoot: Bool) {
        let status: RunStatus = event.data["status"]?.string ?? "done"
        if !isRoot {
            if let at: Int = helpers.firstIndex(where: { $0.id == event.runId }) {
                helpers[at].status = status
                helpers[at].words = nil
            }
            return
        }
        settleStreaming()
        if event.runId == rootRunIds.last { runStatus = status }
        let result: String = (event.data["result"]?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let restated: Set<String> = Set(
            (event.data["restated"]?.array ?? []).compactMap { $0.string?.trimmingCharacters(in: .whitespacesAndNewlines) })
        // The reply that said the same words as the answer is the answer: it is shown once.
        items.removeAll { item in
            guard item.id.hasPrefix("a:\(event.runId):"), case .assistant(let text, _) = item.kind else { return false }
            let trimmed: String = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed == result || restated.contains(trimmed)
        }
        reindex()
        if !result.isEmpty {
            upsert(ChatItem(id: "r:\(event.runId)", kind: .answer(text: result, status: status), ts: event.ts))
        }
        if let needs: String = event.data["needs"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines), !needs.isEmpty {
            upsert(ChatItem(id: "n:needs:\(event.runId)", kind: .note("Needs from you: \(needs)", .warn), ts: event.ts))
        }
        // Whatever still waits for the owner in a run that ended is closed.
        for (at, item) in items.enumerated() {
            if case .approval(var card) = item.kind, card.runId == event.runId, card.isPending {
                card.status = "expired"
                items[at].kind = .approval(card)
            }
        }
    }

    private mutating func delta(_ event: EventRecord) {
        guard let step: Int = event.data["step"]?.int else { return }
        let id: String = "a:\(event.runId):\(step)"
        switch event.data["kind"]?.string {
        case "text":
            let piece: String = event.data["text"]?.string ?? ""
            if let at: Int = index[id], case .assistant(let text, _) = items[at].kind {
                items[at].kind = .assistant(text: text + piece, streaming: true)
            } else {
                upsert(ChatItem(id: id, kind: .assistant(text: piece, streaming: true), ts: event.ts))
            }
        case "reset":
            removeItem(id)
        default:
            break
        }
    }

    private mutating func assistant(_ event: EventRecord) {
        guard let step: Int = event.data["step"]?.int else { return }
        let id: String = "a:\(event.runId):\(step)"
        let text: String = event.data["text"]?.string ?? ""
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            removeItem(id)
        } else {
            upsert(ChatItem(id: id, kind: .assistant(text: text, streaming: false), ts: event.ts))
        }
    }

    private mutating func toolCall(_ event: EventRecord, isRoot: Bool) {
        guard let callId: String = event.data["call_id"]?.string, let tool: String = event.data["tool"]?.string else { return }
        let line: String = toolPhrase(tool: tool, args: event.data["args"])
        if !isRoot {
            if let at: Int = helpers.firstIndex(where: { $0.id == event.runId }) { helpers[at].words = line }
            return
        }
        if quietTools.contains(tool) { return }
        upsert(ChatItem(
            id: "t:\(callId)",
            kind: .tool(ToolRow(callId: callId, runId: event.runId, tool: tool, line: line, state: .running, eventId: event.id)),
            ts: event.ts))
    }

    private mutating func toolResult(_ event: EventRecord, isRoot: Bool) {
        guard isRoot, let callId: String = event.data["call_id"]?.string, let at: Int = index["t:\(callId)"],
            case .tool(var row) = items[at].kind
        else { return }
        let ok: Bool = event.data["ok"]?.bool ?? true
        row.state = ok ? .ok : .failed
        if let summary: String = event.data["summary"]?.string, !summary.isEmpty { row.line = shortened(summary, 120) }
        items[at].kind = .tool(row)
    }

    private mutating func approvalRequested(_ event: EventRecord, isRoot: Bool) {
        guard let id: String = event.data["approval_id"]?.string else { return }
        let tool: String = event.data["tool"]?.string ?? ""
        let args: JSONValue? = event.data["args"]
        let card: ApprovalCard = ApprovalCard(
            approvalId: id, runId: event.runId, tool: tool, summary: event.data["summary"]?.string ?? "",
            severity: event.data["severity"]?.string ?? "",
            question: tool == "ask_user" ? args?["question"]?.string : nil, status: "pending")
        upsert(ChatItem(id: "p:\(id)", kind: .approval(card), ts: event.ts))
        if isRoot, event.runId == rootRunIds.last { runStatus = "blocked" }
    }

    private mutating func approvalDecided(_ event: EventRecord) {
        guard let id: String = event.data["approval_id"]?.string, let at: Int = index["p:\(id)"],
            case .approval(var card) = items[at].kind
        else { return }
        card.status = event.data["status"]?.string ?? "approved"
        items[at].kind = .approval(card)
        if runStatus == "blocked", pendingApprovals.isEmpty { runStatus = "running" }
    }

    private mutating func steer(_ event: EventRecord, isRoot: Bool) {
        guard isRoot, event.data["reaction"] == nil else { return }
        let text: String = event.data["owner_text"]?.string ?? event.data["text"]?.string ?? ""
        guard !text.isEmpty else { return }
        if event.data["vouches"]?.bool == false, event.data["owner_text"] == nil {
            upsert(ChatItem(id: "n:\(event.id)", kind: .note(shortened(text, 240), .plain), ts: event.ts))
        } else {
            upsert(ChatItem(id: "s:\(event.id)", kind: .user(text), ts: event.ts))
        }
    }

    // MARK: Rows

    private mutating func settleStreaming() {
        for (at, item) in items.enumerated() {
            if case .assistant(let text, true) = item.kind { items[at].kind = .assistant(text: text, streaming: false) }
        }
    }

    private mutating func upsert(_ item: ChatItem) {
        if let at: Int = index[item.id] {
            items[at].kind = item.kind
        } else {
            index[item.id] = items.count
            items.append(item)
        }
    }

    private mutating func removeItem(_ id: String) {
        guard index[id] != nil else { return }
        items.removeAll { $0.id == id }
        reindex()
    }

    private mutating func reindex() {
        index = Dictionary(uniqueKeysWithValues: items.enumerated().map { ($1.id, $0) })
    }
}

/// Tools whose calls are not rows: the answer is its own message.
private let quietTools: Set<String> = ["finish", "update_plan", "plan"]

func shortened(_ text: String, _ limit: Int) -> String {
    let flat: String = text.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespaces)
    return flat.count <= limit ? flat : String(flat.prefix(limit - 1)) + "…"
}

/// A tool call as one line: the tool's name in words and what it is about ("Shell: swift test").
func toolPhrase(tool: String, args: JSONValue?) -> String {
    let name: String = tool.replacingOccurrences(of: "_", with: " ").capitalizedFirst
    for key in ["command", "path", "url", "query", "goal", "text", "name"] {
        if let value: String = args?[key]?.string, !value.isEmpty { return "\(name): \(shortened(value, 80))" }
    }
    return name
}

extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
