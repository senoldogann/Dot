import Foundation

/// How much of the work log a chat shows (D9, §20): a pure view filter, default Show all. Approvals, questions and
/// errors always show; Zen also hides the answers that are not a run's last.
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

/// One row of a chat's transcript, built from its projection (plan §4.1; the full chat UX is wave N2).
public enum TranscriptItem: Sendable, Equatable, Identifiable {
    /// The owner's words: a run's goal or a message sent into a run. `delivery` is set while it is not yet plain.
    case owner(id: String, text: String, delivery: Delivery?)
    /// A note of Dot's from another run (a helper's report among them).
    case note(id: String, text: String)
    /// Dot's words; `writing` while they are still being written (the stream's live words).
    case answer(id: String, text: String, writing: Bool)
    case tool(id: String, tool: String, target: String, state: ToolState)
    /// A run that ended other than done (`failed`, `cancelled`), with its words.
    case ended(id: String, status: String, detail: String)

    public var id: String {
        switch self {
        case .owner(let id, _, _), .note(let id, _), .answer(let id, _, _), .tool(let id, _, _, _), .ended(let id, _, _): id
        }
    }
}

/// The transcript of a chat: its runs oldest first, each its goal, its steps as the stream told them (or, for an older
/// run the window holds without events, its goal and result), then the owner's messages not yet shown in their place.
/// Pure: `mode` and `streaming` (§20: "Response streaming" off shows an answer only once it is whole) only filter.
public func transcript(of chat: ChatProjection, pending: [PendingMessage], mode: ToolCallMode, streaming: Bool) -> [TranscriptItem] {
    var items: [TranscriptItem] = []
    let listed: [RunRecord] = (chat.view?.runs ?? []).filter { $0.parentRunId == nil }
    // A run the stream told of (its `run_queued`) before the view was read again shows at once (the send's first event).
    let known: Set<String> = Set(listed.map(\.id)).union((chat.view?.helpers ?? []).map(\.runId))
    let told: [RunRecord] = chat.runs.compactMap { id, timeline in
        guard !known.contains(id), let first: EventRecord = timeline.events.first, first.kind == "run_queued",
            first.data["parent_run_id"] == nil || first.data["parent_run_id"] == .null
        else { return nil }
        return RunRecord(
            id: id, threadId: chat.threadId, goal: first.data["goal"]?.string ?? "", mode: "", model: "", status: "running",
            createdAt: first.ts, finishedAt: nil, result: "", parentRunId: nil, source: "", effort: nil)
    }
    let runs: [RunRecord] = (listed + told).sorted { $0.createdAt < $1.createdAt }
    for run in runs {
        let shown: [TranscriptItem] = runItems(run, timeline: chat.runs[run.id], writing: streaming ? chat.writing[run.id] : nil, mode: mode)
        items.append(contentsOf: shown)
    }
    for message in pending {
        items.append(.owner(id: "p:\(message.key)", text: message.text, delivery: message.delivery))
    }
    return items
}

private func runItems(_ run: RunRecord, timeline: RunTimeline?, writing: String?, mode: ToolCallMode) -> [TranscriptItem] {
    // A run a helper's report started (item 1351) begins with Dot's note, not the owner's words.
    let opening: TranscriptItem =
        run.source == "helper_report" ? .note(id: "u:\(run.id)", text: run.goal) : .owner(id: "u:\(run.id)", text: run.goal, delivery: nil)
    var items: [TranscriptItem] = [opening]
    var calls: [String: Int] = [:]
    var answers: [Int] = []
    let events: [EventRecord] = timeline?.events ?? []
    for event in events {
        switch event.kind {
        case "assistant":
            let text: String = event.data["text"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            guard !text.isEmpty else { continue }
            answers.append(items.count)
            items.append(.answer(id: "a:\(event.id)", text: text, writing: false))
        case "tool_call":
            guard let step: HelperStep = helperStep(of: event) else { continue }
            let call: String = event.data["call_id"]?.string ?? String(event.id)
            calls[call] = items.count
            items.append(.tool(id: "t:\(call)", tool: step.tool, target: step.target, state: .running))
        case "tool_result":
            guard let call: String = event.data["call_id"]?.string, let at: Int = calls[call],
                case .tool(let id, let tool, let target, _) = items[at]
            else { continue }
            items[at] = .tool(id: id, tool: tool, target: target, state: event.data["ok"]?.bool == false ? .failed : .done)
        case "steer":
            guard let text: String = event.data["text"]?.string, !text.isEmpty else { continue }
            if event.data["of_dot"]?.bool == true || event.data["vouches"]?.bool == false {
                items.append(.note(id: "n:\(event.id)", text: text))
            } else if let timeline {
                items.append(.owner(id: "s:\(event.id)", text: text, delivery: readsAfterStep(steer: event, in: timeline) ? .readsAfterStep : nil))
            }
        case "run_finished":
            let result: String = (event.data["result"]?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let status: String = event.data["status"]?.string ?? "done"
            // The replies that said the answer's words are the answer: it is shown once, at the end (as the old app does).
            let restated: Set<String> = Set(
                (event.data["restated"]?.array ?? []).compactMap { $0.string?.trimmingCharacters(in: .whitespacesAndNewlines) }
            ).union(result.isEmpty ? [] : [result])
            let kept: [TranscriptItem] = items.filter { item in
                if case .answer(_, let text, _) = item { return !restated.contains(text) }
                return true
            }
            items = kept
            answers = items.indices.filter { if case .answer = items[$0] { true } else { false } }
            if !result.isEmpty {
                answers.append(items.count)
                items.append(.answer(id: "r:\(run.id)", text: result, writing: false))
            }
            if status != "done" { items.append(.ended(id: "e:\(run.id)", status: status, detail: result)) }
        default:
            continue
        }
    }
    if events.isEmpty, !run.isActive {
        let result: String = run.result.trimmingCharacters(in: .whitespacesAndNewlines)
        if !result.isEmpty {
            answers.append(items.count)
            items.append(.answer(id: "r:\(run.id)", text: result, writing: false))
        }
        if run.status != "done" { items.append(.ended(id: "e:\(run.id)", status: run.status, detail: "")) }
    }
    if let writing, !writing.isEmpty, run.isActive {
        answers.append(items.count)
        items.append(.answer(id: "w:\(run.id)", text: writing, writing: true))
    }
    return filtered(items, answers: answers, mode: mode)
}

/// The tool-call modes as a pure filter of one run's items.
func filtered(_ items: [TranscriptItem], answers: [Int], mode: ToolCallMode) -> [TranscriptItem] {
    let last: Int? = answers.last
    return items.enumerated().compactMap { index, item in
        switch (mode, item) {
        case (.showAll, _): return item
        case (.hideSuccessful, .tool(_, _, _, let state)): return state == .done ? nil : item
        case (.hideAll, .tool), (.zen, .tool): return nil
        case (.zen, .answer): return index == last ? item : nil
        default: return item
        }
    }
}
