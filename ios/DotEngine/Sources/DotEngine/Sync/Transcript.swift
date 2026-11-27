import Foundation

/// How much of the work log a chat shows (D9, §20): a pure view filter, default Hide all (item 1769, the web's
/// "What it is doing"). The table below is the
/// contract (PLAN.md §13 S6); `shows(_:mode:)` is it in code and `WorkLogTests` checks every cell.
///
/// | What                                   | Show all | Hide successful | Hide all | Zen    |
/// |----------------------------------------|----------|-----------------|----------|--------|
/// | command, read/edit, wait, search, web, screen step (succeeded) | row | hidden | hidden | hidden |
/// | the same, still running                | row      | row             | hidden   | hidden |
/// | a failed tool                          | row      | row             | row      | hidden |
/// | helper group (each helper's step)      | card     | card            | card     | hidden |
/// | thinking (live as written, then the step's stored thinking) | row | row       | hidden   | hidden |
/// | plan                                   | card     | card            | card     | hidden |
/// | interim prose (not the run's last answer) | shown | shown           | shown    | hidden |
/// | the run's answer                       | shown    | shown           | shown    | shown  |
/// | run error, usage limit                 | shown    | shown           | shown    | shown  |
/// | asks and their decisions               | shown    | shown           | shown    | shown  |
/// | an ask nobody had to answer (`approval_auto` the policy allowed) | not drawn: the run's inspector keeps it ||||
/// | the owner's words, Dot's notes         | shown    | shown           | shown    | shown  |
///
/// The helper group and the plan are cards of the transcript this engine builds; the app's stream draws neither (item
/// 1880: the working capsule's people and plan segments keep them), and reads a Zen run's plan from every row.
///
/// The app draws two or more consecutive commands that ended well as one "Ran N commands" line that opens to them (item
/// 1769); a command at work or one that failed stands alone. This filter keeps each of them a row.
/// "Response streaming" off: the words and the thinking being written are not drawn; an answer appears once it is whole.
/// Hide all and Zen draw one live line under the last message instead of the steps (`workNow`, item 1769): what the run
/// does now ("Thinking", "Running swift test"); a tap on it shows the steps of the chat. Once a run ended, Hide all folds
/// all of its steps into its "Worked for" line; Zen shows none.
/// The answer's footer (copy, reaction, time) is on the run's final answer alone, once the run ended (`endedAt`).
public enum ToolCallMode: String, CaseIterable, Codable, Sendable {
    case showAll
    case hideSuccessful
    case hideAll
    case zen
}

/// How a tool call stands.
public enum ToolState: Sendable, Equatable {
    case running
    case done
    case failed
}

/// What kind of step a tool call is, for the fold's tally and the filter (read from the tool's name: Dot's own tools
/// and the Claude plan's).
public enum ToolKind: String, Sendable, Equatable, CaseIterable {
    case command
    case read
    case edit
    case search
    case wait
    case web
    case screen
    case helper
    case other

    public static func of(_ tool: String) -> ToolKind {
        let name: String = tool.lowercased()
        if name == "delegate" || name == "task" || name.hasPrefix("agent") { return .helper }
        if name.hasPrefix("browser") || name.contains("fetch") || name.hasPrefix("web") || name.contains("url") { return .web }
        if name.hasPrefix("screen") || name.hasPrefix("computer") || name.contains("mouse") || name.contains("keyboard")
            || name.hasPrefix("desktop")
        {
            return .screen
        }
        if name.contains("wait") || name == "sleep" { return .wait }
        if name.contains("search") || name.contains("grep") || name.contains("glob") || name.hasPrefix("find") { return .search }
        if name.contains("write") || name.contains("edit") || name.contains("patch") || name.contains("replace") { return .edit }
        if name.contains("read") || name == "cat" || name.hasPrefix("list_") || name == "ls" || name == "view" { return .read }
        if name.contains("shell") || name.contains("bash") || name.contains("terminal") || name.contains("command") || name == "run" {
            return .command
        }
        return .other
    }
}

/// One tool call of the work log: what it was asked (its arguments as lines), what it said back, how it stands, the
/// screenshots it returned (names under `/api/images/`).
public struct ToolEntry: Sendable, Equatable, Hashable {
    public let id: String
    public let tool: String
    public let kind: ToolKind
    public let target: String
    public let args: String
    public let output: String
    public let state: ToolState
    public let images: [String]
    public let startedAt: Double
    public let endedAt: Double?

    public init(
        id: String, tool: String, kind: ToolKind, target: String, args: String, output: String, state: ToolState, images: [String],
        startedAt: Double, endedAt: Double?
    ) {
        self.id = id
        self.tool = tool
        self.kind = kind
        self.target = target
        self.args = args
        self.output = output
        self.state = state
        self.images = images
        self.startedAt = startedAt
        self.endedAt = endedAt
    }
}

/// How many steps of each kind a fold holds: the words "read 3 files, ran 2 commands" are the app's.
public struct WorkTally: Sendable, Equatable, Hashable {
    public let counts: [ToolKind: Int]
    public let failed: Int

    public init(counts: [ToolKind: Int], failed: Int) {
        self.counts = counts
        self.failed = failed
    }

    /// The kinds with a count, in a fixed order (commands, reads, edits, searches, web, screen, waits, helpers, other).
    public var ordered: [(ToolKind, Int)] {
        let order: [ToolKind] = [.read, .command, .edit, .search, .web, .screen, .wait, .helper, .other]
        return order.compactMap { kind in counts[kind].flatMap { $0 > 0 ? (kind, $0) : nil } }
    }

    public static func of(_ tools: [ToolEntry]) -> WorkTally {
        var counts: [ToolKind: Int] = [:]
        for tool in tools { counts[tool.kind, default: 0] += 1 }
        return WorkTally(counts: counts, failed: tools.filter { $0.state == .failed }.count)
    }
}

/// A finished stretch of work folded to one line (plan §4.1): "Worked for 2m 14s · read 3 files, ran 2 commands". A
/// fold with a failed step is drawn open.
public struct WorkFold: Sendable, Equatable {
    public let id: String
    public let seconds: Int
    public let tally: WorkTally
    public let items: [TranscriptItem]

    public init(id: String, seconds: Int, tally: WorkTally, items: [TranscriptItem]) {
        self.id = id
        self.seconds = seconds
        self.tally = tally
        self.items = items
    }

    public var hasFailure: Bool { tally.failed > 0 }
}

/// A step of a run's plan.
public struct PlanStep: Sendable, Equatable, Hashable {
    public let text: String
    /// `pending`, `in_progress`, `done`, `failed`, `skipped`.
    public let status: String

    public init(text: String, status: String) {
        self.text = text
        self.status = status
    }
}

/// Who settled an ask, as far as the phone can tell (the daemon's `approval_decided` does not say who).
public enum AskDecider: Sendable, Equatable, Hashable {
    /// The owner on this phone.
    case thisPhone
    /// The owner on the Mac, the web or another device.
    case elsewhere
    /// Answered without a person: the review model or a rule (`approval_auto`), by its name.
    case automatic(String)
    /// Nobody in time.
    case nobody
}

/// An ask left in the transcript where it was asked (plan §13 B1): pending while the dock holds it, then what was decided
/// and by whom.
public struct AskAnchor: Sendable, Equatable, Hashable {
    public let id: String
    public let approvalId: String
    public let severity: String
    public let summary: String
    /// `pending`, `approved`, `denied`, `expired`, `left`.
    public let status: String
    public let decider: AskDecider?
    public let note: String
}

/// One row of a chat's transcript, built from its projection (plan §4.1).
public indirect enum TranscriptItem: Sendable, Equatable, Identifiable {
    /// The owner's words: a run's goal or a message sent into a run. `delivery` is set while it is not yet plain.
    case owner(id: String, text: String, delivery: Delivery?)
    /// A note of Dot's from another run (a helper's report among them), drawn as Dot's row.
    case note(id: String, text: String)
    /// Dot's words; `writing` while they are still being written (the stream's live words). `endedAt` (epoch seconds)
    /// only on the run's final answer once the run ended: the row then has its footer (copy, reaction, the time).
    case answer(id: String, text: String, writing: Bool, endedAt: Double?)
    /// The model's thinking of a step: `live` while it is being written.
    case thought(id: String, text: String, live: Bool)
    case tool(ToolEntry)
    /// A call the run told as one of Dot's short messages, which its step's own words did not tell (a `narration` event,
    /// item 1976): drawn in the conversation of "Show steps" off alone, in the words of the live line for that call.
    case narration(id: String, step: HelperStep)
    /// The helpers a run started, each with its current step (plan §18).
    case helpers(id: String, runId: String, helpers: [HelperView])
    /// The run's plan as a checklist, as it stands now.
    case plan(id: String, steps: [PlanStep])
    case decision(AskAnchor)
    case work(WorkFold)
    /// A run that ended other than done (`failed`, `cancelled`): the cause in one line.
    case ended(id: String, status: String, detail: String)
    /// A run the Claude plan's usage limit stopped; `resetsAt` when the plan said (epoch seconds).
    case limit(id: String, resetsAt: Double?)

    public var id: String {
        switch self {
        case .owner(let id, _, _), .note(let id, _), .answer(let id, _, _, _), .thought(let id, _, _), .helpers(let id, _, _), .plan(let id, _), .ended(let id, _, _),
            .limit(let id, _), .narration(let id, _):
            id
        case .tool(let entry): entry.id
        case .decision(let anchor): anchor.id
        case .work(let fold): fold.id
        }
    }
}

/// What the transcript knows beside the chat's picture: the asks this phone answered (for "who answered").
public struct TranscriptContext: Sendable, Equatable {
    public let answeredHere: Set<String>

    public init(answeredHere: Set<String>) { self.answeredHere = answeredHere }
    public static let none: TranscriptContext = TranscriptContext(answeredHere: [])
}

/// The transcript of a chat: its runs oldest first, each its goal, its steps as the stream told them (or, for an older
/// run the window holds without events, its goal and result), then the owner's messages not yet shown in their place.
/// A finished run's steps fold (`WorkFold`). Pure: `mode` and `streaming` (§20) only filter.
public func transcript(
    of chat: ChatProjection, pending: [PendingMessage], mode: ToolCallMode, streaming: Bool, context: TranscriptContext
) -> [TranscriptItem] {
    builtRuns(of: chat, streaming: streaming, context: context).flatMap { shaped($0.rows, finished: $0.finished, mode: mode) }
        + pending.map { .owner(id: "p:\($0.key)", text: $0.text, delivery: $0.delivery) }
}

/// A run's rows and whether it is over, for the transcript's filter and fold.
struct BuiltRun {
    let rows: [Built]
    let finished: Bool
}

/// The chat's runs, oldest first, each as its rows (`runItems`). Pure.
private func builtRuns(of chat: ChatProjection, streaming: Bool, context: TranscriptContext) -> [BuiltRun] {
    let listed: [RunRecord] = chat.view.map(ownRuns(of:)) ?? []
    // A run the stream told of (its `run_queued`) before the view was read again shows at once (the send's first event).
    let known: Set<String> = Set(listed.map(\.id)).union((chat.view?.helpers ?? []).map(\.runId))
    let told: [RunRecord] = chat.runs.compactMap { id, timeline in
        guard !known.contains(id), let first: EventRecord = timeline.events.first, first.kind == "run_queued",
            first.data["parent_run_id"] == nil || first.data["parent_run_id"] == .null
        else { return nil }
        return RunRecord(
            id: id, threadId: chat.threadId, goal: first.data["goal"]?.string ?? "", mode: "", model: "", status: "running",
            createdAt: first.ts, finishedAt: nil, result: "", parentRunId: nil, source: "", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
    }
    let runs: [RunRecord] = (listed + told).sorted { $0.createdAt < $1.createdAt }
    let helpers: [HelperView] = chat.view?.helpers ?? []
    return runs.map { run in
        BuiltRun(
            rows: runItems(
                run, timeline: chat.runs[run.id], writing: streaming ? chat.writing[run.id] : nil, thinking: streaming ? chat.thinking[run.id] : nil,
                helpers: helpers.filter { $0.parentRunId == run.id }, context: context),
            finished: !run.isActive || hasEnded(chat.runs[run.id]))
    }
}

/// The transcript of "Show steps" off (items 1915 and 1976): the chat told as a conversation, as Grok Bot tells its work.
/// The owner's words, Dot's words (those on the way to its calls too, which stay where they were said), the lines of the
/// calls its words did not tell (`narration`), the asks, the helpers and how runs ended; a run at work's steps and
/// thoughts are left out but a failed call, its live line saying what it does. A finished run's steps, thoughts and plan
/// are one fold, "Worked for 2m", before its answer: a tap there shows them, so nothing is lost. Pure.
public func conversation(of chat: ChatProjection, pending: [PendingMessage], streaming: Bool, context: TranscriptContext) -> [TranscriptItem] {
    builtRuns(of: chat, streaming: streaming, context: context).flatMap { conversed($0.rows, finished: $0.finished) }
        + pending.map { .owner(id: "p:\($0.key)", text: $0.text, delivery: $0.delivery) }
}

/// The rows of one run in the conversation (`conversation`). Pure.
func conversed(_ built: [Built], finished: Bool) -> [TranscriptItem] {
    guard finished else {
        return built.filter { row in
            switch row.category {
            case .thought: false
            case .tool(let state): state == .failed
            default: true
            }
        }.map(\.item)
    }
    let isStep: (Built) -> Bool = { row in
        switch row.category {
        case .tool, .thought, .plan: true
        default: false
        }
    }
    let steps: [Built] = built.filter(isStep)
    var out: [Built] = built.filter { !isStep($0) }
    guard let first: Built = steps.first else { return out.map(\.item) }
    let tools: [ToolEntry] = steps.compactMap { if case .tool(let entry) = $0.item { entry } else { nil } }
    let start: Double = steps.map(\.start).min() ?? first.start
    let end: Double = steps.map(\.end).max() ?? start
    let fold: Built = Built(
        item: .work(WorkFold(id: "f:\(first.item.id)", seconds: max(0, Int((end - start).rounded())), tally: .of(tools), items: steps.map(\.item))),
        category: .tool(.done), start: start, end: end)
    out.insert(fold, at: out.firstIndex { $0.category == .answer(final: true) } ?? out.count)
    return out.map(\.item)
}

/// The reaction target of an answer: its run and the message key the daemon names it by (`answer`, `step-N`).
public func reactionTarget(of answerId: String, in chat: ChatProjection) -> (run: String, message: String)? {
    if answerId.hasPrefix("r:") { return (String(answerId.dropFirst(2)), "answer") }
    guard answerId.hasPrefix("a:"), let eventId: Int = Int(answerId.dropFirst(2)) else { return nil }
    for (run, timeline) in chat.runs {
        if let event: EventRecord = timeline.events.first(where: { $0.id == eventId }), let step: Int = event.data["step"]?.int, step > 0 {
            return (run, "step-\(step)")
        }
    }
    return nil
}

// MARK: Building one run

/// What a row is, for the filter (the table above).
enum Category: Equatable {
    case owner
    case note
    case tool(ToolState)
    case helpers
    case plan
    case answer(final: Bool)
    case writing
    case thought
    case decision
    case runEnd
    case narration
}

/// A row with what the filter and the fold need: its category and when it began and ended.
struct Built: Equatable {
    var item: TranscriptItem
    var category: Category
    var start: Double
    var end: Double
}

private func hasEnded(_ timeline: RunTimeline?) -> Bool {
    timeline?.events.contains { $0.kind == "run_finished" } ?? false
}

private func runItems(
    _ run: RunRecord, timeline: RunTimeline?, writing: String?, thinking: LiveThought?, helpers: [HelperView], context: TranscriptContext) -> [Built] {
    // A run a helper's report started (item 1351) begins with Dot's note, not the owner's words.
    let opening: TranscriptItem =
        run.source == "helper_report" ? .note(id: "u:\(run.id)", text: run.goal) : .owner(id: "u:\(run.id)", text: run.goal, delivery: nil)
    // A run that follows the owner's reaction (item 844) draws no message of the owner's: the reaction stands on its message.
    // Nor does an agent's first meeting (item 993, source `welcome`): its goal is Dot's words to the agent, and the agent
    // speaks first (item 1881).
    var items: [Built] =
        run.followsReaction != nil || run.source == "welcome"
        ? [] : [Built(item: opening, category: run.source == "helper_report" ? .note : .owner, start: run.createdAt, end: run.createdAt)]
    var calls: [String: Int] = [:]
    var asks: [String: Int] = [:]
    var planAt: Int?
    // Whether the newest step wrote words, which tell its calls: no line of a call stands beside them (item 1976).
    var said: Bool = false
    let events: [EventRecord] = timeline?.events ?? []
    for event in events {
        switch event.kind {
        case "narration":
            guard !said, let tool: String = event.data["tool"]?.string else { continue }
            let about: String = event.data["args"]?.object?.values.compactMap(\.string).first ?? ""
            let step: HelperStep = HelperStep(tool: tool, target: about, line: "", at: event.ts)
            items.append(Built(item: .narration(id: "n:\(event.id)", step: step), category: .narration, start: event.ts, end: event.ts))
        case "assistant":
            let thought: String = event.data["thinking"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !thought.isEmpty {
                let step: Int = event.data["step"]?.int ?? event.id
                items.append(Built(item: .thought(id: "th:\(run.id):\(step)", text: thought, live: false), category: .thought, start: event.ts, end: event.ts))
            }
            let text: String = event.data["text"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            said = !text.isEmpty
            guard !text.isEmpty else { continue }
            items.append(Built(item: .answer(id: "a:\(event.id)", text: text, writing: false, endedAt: nil), category: .answer(final: false), start: event.ts, end: event.ts))
        case "tool_call":
            guard let step: HelperStep = helperStep(of: event) else { continue }
            // An ask and a plan stand as their own rows (the anchor, the checklist): their tool calls are not drawn twice.
            if silentTools.contains(step.tool) { continue }
            let call: String = event.data["call_id"]?.string ?? String(event.id)
            calls[call] = items.count
            let entry: ToolEntry = ToolEntry(
                id: "t:\(call)", tool: step.tool, kind: ToolKind.of(step.tool), target: step.target, args: argumentLines(event.data["args"]),
                output: "", state: .running, images: [], startedAt: event.ts, endedAt: nil)
            items.append(Built(item: .tool(entry), category: .tool(.running), start: event.ts, end: event.ts))
        case "tool_result":
            guard let call: String = event.data["call_id"]?.string, let at: Int = calls[call], case .tool(let entry) = items[at].item else { continue }
            let state: ToolState = event.data["ok"]?.bool == false ? .failed : .done
            let output: String = event.data["text"]?.string ?? event.data["error"]?.string ?? ""
            let images: [String] = (event.data["images"]?.array ?? []).compactMap(\.string)
            let done: ToolEntry = ToolEntry(
                id: entry.id, tool: entry.tool, kind: entry.kind, target: entry.target.isEmpty ? (event.data["target"]?.string ?? "") : entry.target,
                args: entry.args, output: output, state: state, images: images, startedAt: entry.startedAt, endedAt: event.ts)
            items[at] = Built(item: .tool(done), category: .tool(state), start: entry.startedAt, end: event.ts)
        case "plan":
            let steps: [PlanStep] = (event.data["steps"]?.array ?? []).compactMap { step in
                guard let text: String = step["text"]?.string, !text.isEmpty else { return nil }
                return PlanStep(text: text, status: step["status"]?.string ?? "pending")
            }
            guard !steps.isEmpty else { continue }
            if let at: Int = planAt {
                items[at] = Built(item: .plan(id: "plan:\(run.id)", steps: steps), category: .plan, start: items[at].start, end: event.ts)
            } else {
                planAt = items.count
                items.append(Built(item: .plan(id: "plan:\(run.id)", steps: steps), category: .plan, start: event.ts, end: event.ts))
            }
        case "approval_requested", "question_left", "approval_auto":
            guard let approval: String = event.data["approval_id"]?.string else { continue }
            let auto: Bool = event.kind == "approval_auto"
            // What the policy allowed with nobody asked (yolo, free, a rule, the auto review) is the run's record, not the
            // chat's: the inspector keeps it. A refusal changed what the run did, and stays.
            if auto, (event.data["status"]?.string ?? "approved") == "approved" { continue }
            let status: String = auto ? (event.data["status"]?.string ?? "approved") : (event.kind == "question_left" ? "left" : "pending")
            let decider: AskDecider? = auto ? .automatic(event.data["decided_by"]?.string ?? "") : nil
            let anchor: AskAnchor = AskAnchor(
                id: "ask:\(approval)", approvalId: approval, severity: event.data["severity"]?.string ?? "",
                summary: askLine(event.data), status: status, decider: decider, note: event.data["note"]?.string ?? "")
            if let at: Int = asks[approval] {
                items[at].item = .decision(anchor)
            } else {
                asks[approval] = items.count
                items.append(Built(item: .decision(anchor), category: .decision, start: event.ts, end: event.ts))
            }
        case "approval_decided":
            guard let approval: String = event.data["approval_id"]?.string, let at: Int = asks[approval],
                case .decision(let anchor) = items[at].item
            else { continue }
            let status: String = event.data["status"]?.string ?? "approved"
            let decider: AskDecider =
                status == "expired" ? .nobody : context.answeredHere.contains(approval) ? .thisPhone : .elsewhere
            items[at].item = .decision(
                AskAnchor(
                    id: anchor.id, approvalId: approval, severity: anchor.severity, summary: anchor.summary, status: status, decider: decider,
                    note: event.data["note"]?.string ?? ""))
        case "steer":
            guard let text: String = event.data["text"]?.string, !text.isEmpty else { continue }
            if event.data["of_dot"]?.bool == true || event.data["vouches"]?.bool == false {
                items.append(Built(item: .note(id: "n:\(event.id)", text: text), category: .note, start: event.ts, end: event.ts))
            } else if let timeline {
                let delivery: Delivery? = readsAfterStep(steer: event, in: timeline) ? .readsAfterStep : nil
                items.append(Built(item: .owner(id: "s:\(event.id)", text: text, delivery: delivery), category: .owner, start: event.ts, end: event.ts))
            }
        case "run_finished":
            let result: String = (event.data["result"]?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let status: String = event.data["status"]?.string ?? "done"
            // The replies that said the answer's words are the answer: it is shown once, at the end (as the old app does).
            let restated: Set<String> = Set(
                (event.data["restated"]?.array ?? []).compactMap { $0.string?.trimmingCharacters(in: .whitespacesAndNewlines) }
            ).union(result.isEmpty ? [] : [result])
            items = items.filter { built in
                if case .answer(_, let text, _, _) = built.item { return !restated.contains(text) }
                return true
            }
            appendHelpers(&items, run: run, helpers: helpers, at: event.ts)
            let error: [String: JSONValue]? = event.data["error"]?.object
            if status != "done", error?["kind"]?.string == "plan_limit" {
                let resets: Double? = error?["context"]?["resets_at"]?.double ?? error?["resets_at"]?.double
                items.append(Built(item: .limit(id: "e:\(run.id)", resetsAt: resets), category: .runEnd, start: event.ts, end: event.ts))
            } else {
                let message: String = error?["message"]?.string ?? ""
                // A failed run's result is its error's words: they stand once, in the card that says it failed.
                let resultIsTheError: Bool = status != "done" && (message.isEmpty || result == message.trimmingCharacters(in: .whitespacesAndNewlines))
                if !result.isEmpty, !resultIsTheError {
                    items.append(Built(item: .answer(id: "r:\(run.id)", text: result, writing: false, endedAt: nil), category: .answer(final: true), start: event.ts, end: event.ts))
                }
                if status != "done" {
                    let cause: String = firstLine(message.isEmpty ? (status == "failed" ? result : "") : message)
                    items.append(Built(item: .ended(id: "e:\(run.id)", status: status, detail: cause), category: .runEnd, start: event.ts, end: event.ts))
                }
            }
        default:
            continue
        }
    }
    if events.isEmpty, !run.isActive {
        appendHelpers(&items, run: run, helpers: helpers, at: run.finishedAt ?? run.createdAt)
        let result: String = run.result.trimmingCharacters(in: .whitespacesAndNewlines)
        if !result.isEmpty {
            items.append(Built(item: .answer(id: "r:\(run.id)", text: result, writing: false, endedAt: nil), category: .answer(final: true), start: run.createdAt, end: run.createdAt))
        }
        if run.status != "done" {
            items.append(Built(item: .ended(id: "e:\(run.id)", status: run.status, detail: ""), category: .runEnd, start: run.createdAt, end: run.createdAt))
        }
    }
    if !hasEnded(timeline), run.isActive || !events.isEmpty {
        appendHelpers(&items, run: run, helpers: helpers, at: events.last?.ts ?? run.createdAt)
    }
    if let thinking, !thinking.text.isEmpty, run.isActive, !hasEnded(timeline) {
        let at: Double = events.last?.ts ?? run.createdAt
        items.append(Built(item: .thought(id: "th:\(run.id):\(thinking.step)", text: thinking.text, live: true), category: .thought, start: at, end: at))
    }
    if let writing, !writing.isEmpty, run.isActive {
        let at: Double = events.last?.ts ?? run.createdAt
        items.append(Built(item: .answer(id: "w:\(run.id)", text: writing, writing: true, endedAt: nil), category: .writing, start: at, end: at))
    }
    // While the run works, its newest reply is not yet known to be its last; once it ended, the last reply is the answer.
    if hasEnded(timeline) || !run.isActive, !items.contains(where: { $0.category == .answer(final: true) }),
        let last: Int = items.lastIndex(where: { $0.category == .answer(final: false) })
    {
        items[last].category = .answer(final: true)
    }
    // The final answer's footer comes once the run ended, with the time it ended.
    if hasEnded(timeline) || !run.isActive {
        let ended: Double = events.last(where: { $0.kind == "run_finished" })?.ts ?? run.finishedAt ?? run.createdAt
        for at in items.indices where items[at].category == .answer(final: true) {
            if case .answer(let id, let text, _, _) = items[at].item {
                items[at].item = .answer(id: id, text: text, writing: false, endedAt: ended)
            }
        }
    }
    return items
}

private func appendHelpers(_ items: inout [Built], run: RunRecord, helpers: [HelperView], at: Double) {
    guard !helpers.isEmpty, !items.contains(where: { $0.category == .helpers }) else { return }
    items.append(Built(item: .helpers(id: "h:\(run.id)", runId: run.id, helpers: helpers), category: .helpers, start: at, end: at))
}

/// An ask's line: a question's own words, else its summary.
private func askLine(_ data: [String: JSONValue]) -> String {
    if data["severity"]?.string == "question", let question: String = data["args"]?["question"]?.string, !question.isEmpty { return question }
    if data["severity"]?.string == "secret", let label: String = data["args"]?["label"]?.string, !label.isEmpty { return label }
    return data["summary"]?.string ?? ""
}

/// A tool's arguments as `key: value` lines, the long values kept whole (the detail scrolls).
func argumentLines(_ args: JSONValue?) -> String {
    guard let fields: [String: JSONValue] = args?.object else { return "" }
    return fields.keys.sorted().map { key in "\(key): \(plainValue(fields[key] ?? .null))" }.joined(separator: "\n")
}

private func plainValue(_ value: JSONValue) -> String {
    switch value {
    case .null: "null"
    case .bool(let flag): flag ? "true" : "false"
    case .int(let number): String(number)
    case .double(let number): String(number)
    case .string(let text): text
    case .array(let values): "[" + values.map(plainValue).joined(separator: ", ") + "]"
    case .object(let fields): "{" + fields.keys.sorted().map { "\($0): \(plainValue(fields[$0] ?? .null))" }.joined(separator: ", ") + "}"
    }
}

private func firstLine(_ text: String) -> String {
    text.split(whereSeparator: \.isNewline).first.map { String($0).trimmingCharacters(in: .whitespaces) } ?? ""
}

// MARK: The filter and the fold

/// Whether a row of `category` shows in `mode` (the table on `ToolCallMode`).
func shows(_ category: Category, mode: ToolCallMode) -> Bool {
    switch (mode, category) {
    // A call's line is the conversation's alone (`conversation`), never beside the steps it tells.
    case (_, .narration): false
    case (.showAll, _): true
    case (_, .owner), (_, .note), (_, .answer(final: true)), (_, .writing), (_, .decision), (_, .runEnd): true
    case (.zen, .thought), (.hideAll, .thought): false
    case (_, .thought): true
    case (.hideSuccessful, .tool(let state)): state != .done
    case (.hideAll, .tool(let state)): state == .failed
    case (.zen, _): false
    case (.hideSuccessful, _), (.hideAll, _): true
    }
}

/// Whether a row goes into the fold of a finished run: its steps, its plan and its passing words; never the owner's
/// words, Dot's notes, the asks, the helpers or how it ended.
private func foldable(_ category: Category) -> Bool {
    switch category {
    case .tool, .plan, .answer(final: false), .thought: true
    default: false
    }
}

/// The tools whose calls the transcript shows as their own rows instead (an ask's anchor, the plan's card).
let silentTools: Set<String> = ["ask_user", "ask_secret", "update_plan", "mark_plan_step"]

/// The rows of one run, filtered by `mode`, and folded when the run has ended. In Hide all an ended run's fold holds all
/// of its steps (item 1769): the live line said the work while it ran, and the fold is the way to it afterwards.
func shaped(_ built: [Built], finished: Bool, mode: ToolCallMode) -> [TranscriptItem] {
    let kept: [Built] = built.filter { shows($0.category, mode: finished && mode == .hideAll ? .showAll : mode) }
    guard finished else { return kept.map(\.item) }
    var out: [TranscriptItem] = []
    var stretch: [Built] = []
    func flush() {
        guard !stretch.isEmpty else { return }
        let tools: [ToolEntry] = stretch.compactMap { if case .tool(let entry) = $0.item { entry } else { nil } }
        let start: Double = stretch.map(\.start).min() ?? 0
        let end: Double = stretch.map(\.end).max() ?? start
        out.append(.work(WorkFold(id: "f:\(stretch[0].item.id)", seconds: max(0, Int((end - start).rounded())), tally: .of(tools), items: stretch.map(\.item))))
        stretch = []
    }
    for row in kept {
        if foldable(row.category) {
            stretch.append(row)
        } else {
            flush()
            out.append(row.item)
        }
    }
    flush()
    return out
}

// MARK: What a run is doing now

/// What a run at work is doing this moment, for the capsule's word (a sign of life in every tool mode, Zen too): writing
/// its words, thinking, or running a tool; `nil` between steps (the model is being asked).
public enum RunActivity: Sendable, Equatable {
    case writing
    case thinking
    case tool(HelperStep)
}

/// What `run` of `chat` is doing now, from its live words and thinking and its stored events. Pure.
public func activity(of chat: ChatProjection, run: String) -> RunActivity? {
    if let words: String = chat.writing[run], !words.isEmpty { return .writing }
    if let thought: LiveThought = chat.thinking[run], !thought.text.isEmpty { return .thinking }
    let events: [EventRecord] = chat.runs[run]?.events ?? []
    guard !events.contains(where: { $0.kind == "run_finished" }) else { return nil }
    // The calls of the newest step that have no result yet: the first of them is what runs.
    let since: ArraySlice<EventRecord> = events[((events.lastIndex { $0.kind == "assistant" }) ?? -1) + 1..<events.count]
    let done: Set<String> = Set(since.filter { $0.kind == "tool_result" }.compactMap { $0.data["call_id"]?.string })
    for event in since where event.kind == "tool_call" {
        guard let step: HelperStep = helperStep(of: event), !silentTools.contains(step.tool) else { continue }
        if let call: String = event.data["call_id"]?.string, done.contains(call) { continue }
        return .tool(step)
    }
    return nil
}
