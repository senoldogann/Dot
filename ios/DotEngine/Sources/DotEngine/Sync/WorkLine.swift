import Foundation

/// What a run at work does now, for the one live line under the last message (item 1769, the web's "What it is doing",
/// `workstatus.js` of items 663, 971 and 1091): drawn while the work log's steps are hidden (Hide all, Zen). Each state
/// comes from an event, so the line's words change only when the run does something else, never on a timer.
public enum WorkNow: Sendable, Equatable {
    /// Queued: the run has not started (no `run_started` yet).
    case queued
    /// Waiting for its model: the three typing dots and "Typing".
    case typing
    /// The model's thinking streams.
    case thinking
    /// A call at work, said with what it is about ("Running gh repo view").
    case tool(HelperStep)
    /// Work handed to an agent, by its name when the call names one.
    case handoff(agent: String)
    /// What the run's newest message said it does next, while that step's calls run (item 1131): its first line.
    case told(String)
    /// Words being written that are in sight: the line steps aside.
    case writing
    /// An approval or a question waits for the owner.
    case waitingForYou
    /// Stop was pressed and the run has not ended.
    case stopping
}

/// The tools whose call hands work to another run: the agent's name is said when the call names one.
let handoffTools: Set<String> = ["message_agent", "delegate"]
/// The calls the line says nothing of: the answer and the reaction say them (item 664).
let unsaidTools: Set<String> = ["finish", "react"]

/// What `run` of `chat` does now (`stopping`: the owner pressed Stop), or `nil` once it ended. Pure.
public func workNow(of chat: ChatProjection, run: String, stopping: Bool) -> WorkNow? {
    let events: [EventRecord] = chat.runs[run]?.events ?? []
    if events.contains(where: { $0.kind == "run_finished" }) { return nil }
    if stopping { return .stopping }
    // Queued by the run's own status, or while its only event is its `run_queued`: the window of a run's newest events
    // may leave its `run_started` out.
    let status: String? = chat.view?.runs.first { $0.id == run }?.status
    if status == "queued" || (!events.isEmpty && events.allSatisfy { $0.kind == "run_queued" }) { return .queued }
    let asked: Set<String> = Set(events.filter { $0.kind == "approval_requested" }.compactMap { $0.data["approval_id"]?.string })
    let decided: Set<String> = Set(events.filter { $0.kind == "approval_decided" }.compactMap { $0.data["approval_id"]?.string })
    if !asked.subtracting(decided).isEmpty { return .waitingForYou }
    if let words: String = chat.writing[run], !words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return .writing }
    if let thought: LiveThought = chat.thinking[run], !thought.text.isEmpty { return .thinking }
    let lastStep: Int? = events.lastIndex { $0.kind == "assistant" }
    // What the newest message said it does next stands while its calls run and the model is asked again (item 1131).
    if let step: Int = lastStep, let told: String = toldWords(events[step]) { return .told(told) }
    let since: ArraySlice<EventRecord> = events[((lastStep ?? -1) + 1)..<events.count]
    let answered: Set<String> = Set(since.filter { $0.kind == "tool_result" }.compactMap { $0.data["call_id"]?.string })
    let open: [EventRecord] = since.filter { event in
        guard event.kind == "tool_call", let call: String = event.data["call_id"]?.string else { return false }
        return !answered.contains(call)
    }
    // The model is asked again once the last of the step's calls came back: it is typing.
    guard let first: EventRecord = open.first(where: { !unsaidTools.contains($0.data["tool"]?.string ?? "") }) else { return .typing }
    guard let call: HelperStep = helperStep(of: first) else { return .typing }
    if handoffTools.contains(call.tool) { return .handoff(agent: first.data["args"]?["agent"]?.string ?? "") }
    return .tool(call)
}

/// The first line of what a step's message said it does next, without markdown's marks or its closing stop, when the
/// step also calls a tool that goes on with the work ("Kanıt netleşti. Son bir kontrol yapıp sonucu yazıyorum.").
private func toldWords(_ step: EventRecord) -> String? {
    let calls: [String] = (step.data["tool_calls"]?.array ?? []).compactMap(\.string)
    guard calls.contains(where: { !unsaidTools.contains($0) }), let text: String = step.data["text"]?.string else { return nil }
    let first: String = text.trimmingCharacters(in: .whitespacesAndNewlines).split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
    let plain: String = first.filter { !"*_`#>".contains($0) }.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    let trimmed: String = String(plain.reversed().drop { ".:;…".contains($0) }.reversed())
    return trimmed.isEmpty ? nil : trimmed
}
