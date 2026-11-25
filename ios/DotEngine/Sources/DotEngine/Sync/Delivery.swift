import Foundation

/// Where an owner's message stands (plan §13 B3, §10.6), one word for the app to say. "Read" is never claimed: the
/// daemon sends no consumption signal.
public enum Delivery: Sendable, Equatable {
    /// In the outbox, no link.
    case offline
    /// In the outbox, going out.
    case sending
    /// Accepted into the chat's queue at `position` (1 = next); starts after the run at work.
    case queued(position: Int)
    /// Accepted into a held queue: it waits for "Resume queue".
    case held(position: Int)
    /// Handed to the running task.
    case sentToRun
    /// Handed to the running task while a tool of it is open: Dot reads it once that step ends.
    case readsAfterStep
    /// Taken by the daemon; the chat shows it in its place.
    case delivered
    /// Not taken; the reason is the item's.
    case rejected(OutboxItem.Failure)
}

/// An owner's message the daemon took and the stream has not shown yet: drawn as an overlay keyed by the client id
/// (R-b §8 #9), never as an edit of the daemon's records.
public struct AcceptedMessage: Codable, Sendable, Equatable {
    public let key: String
    public let thread: String?
    public let text: String
    public let kind: SendingKind
    /// The queued message it became, when it was queued.
    public let queuedId: String?
    /// The run it started or was handed to.
    public let runId: String?
    public let at: Double
    /// The chat's runs when it was taken: a run with its words that is not among them is the one it started (no clock of
    /// the phone is compared with the Mac's).
    public let runsBefore: Set<String>

    public init(
        key: String, thread: String?, text: String, kind: SendingKind, queuedId: String?, runId: String?, at: Double, runsBefore: Set<String>
    ) {
        self.runsBefore = runsBefore
        self.key = key
        self.thread = thread
        self.text = text
        self.kind = kind
        self.queuedId = queuedId
        self.runId = runId
        self.at = at
    }
}

/// The accepted message an outcome makes; `nil` for an outcome that is no message. Pure.
public func accepted(from outcome: OutboxOutcome, at: Double, runsBefore: Set<String>) -> AcceptedMessage? {
    switch outcome {
    case .delivered(let item, let queue):
        guard let text: String = item.text else { return nil }
        // The newest queued message with these words is the one this sending made (a message starts at once when no run works).
        let queued: QueuedMessageView? = queue.messages.last { $0.text == text }
        return AcceptedMessage(
            key: item.id, thread: item.thread, text: text, kind: .queue, queuedId: queued?.id, runId: queue.besideRunId, at: at,
            runsBefore: runsBefore)
    case .steered(let item):
        guard let text: String = item.text, case .steer(let run, _, _, _, _) = item.action else { return nil }
        return AcceptedMessage(key: item.id, thread: item.thread, text: text, kind: .steer, queuedId: nil, runId: run, at: at, runsBefore: runsBefore)
    case .started(let item, let run):
        guard let text: String = item.text else { return nil }
        return AcceptedMessage(key: item.id, thread: run.threadId, text: text, kind: .run, queuedId: nil, runId: run.id, at: at, runsBefore: runsBefore)
    default:
        return nil
    }
}

/// An accepted queued message with the words its queued message holds now: an edit (on this phone or the Mac) changes
/// the words the run it starts will have, so the overlay follows them and is dropped once that run shows. Pure.
public func following(_ message: AcceptedMessage, in chat: ChatProjection) -> AcceptedMessage {
    guard message.kind == .queue, let id: String = message.queuedId,
        let queued: QueuedMessageView = chat.view?.queue.messages.first(where: { $0.id == id }), queued.text != message.text
    else { return message }
    return AcceptedMessage(
        key: message.key, thread: message.thread, text: queued.text, kind: message.kind, queuedId: message.queuedId, runId: message.runId,
        at: message.at, runsBefore: message.runsBefore)
}

/// Where an accepted message stands in the chat's picture, or `nil` once the chat shows it in its own place (the overlay
/// is then dropped). Pure.
public func delivery(of message: AcceptedMessage, in chat: ChatProjection) -> Delivery? {
    switch message.kind {
    case .queue:
        if let view: ThreadView = chat.view, let id: String = message.queuedId,
            let at: Int = view.queue.messages.firstIndex(where: { $0.id == id })
        {
            return view.queue.held ? .held(position: at + 1) : .queued(position: at + 1)
        }
        // Not queued (or no longer): it became a run of the chat; once that run is seen, the transcript shows it.
        return startedRunSeen(message, chat) ? nil : .delivered
    case .run:
        return startedRunSeen(message, chat) ? nil : .delivered
    case .steer:
        guard let run: String = message.runId, let timeline: RunTimeline = chat.runs[run] else { return .sentToRun }
        return timeline.events.contains { $0.kind == "steer" && $0.data["text"]?.string == message.text } ? nil : .sentToRun
    }
}

private func startedRunSeen(_ message: AcceptedMessage, _ chat: ChatProjection) -> Bool {
    if let run: String = message.runId, chat.runs[run] != nil || chat.view?.runs.contains(where: { $0.id == run }) == true { return true }
    if chat.view?.runs.contains(where: { $0.goal == message.text && !message.runsBefore.contains($0.id) }) == true { return true }
    // Its `run_queued` came on the stream before the view was read again.
    return chat.runs.contains { id, timeline in
        guard !message.runsBefore.contains(id), let first: EventRecord = timeline.events.first else { return false }
        return first.kind == "run_queued" && first.data["goal"]?.string == message.text
    }
}

/// Where an outbox item stands before the daemon took it. Pure.
public func delivery(of item: OutboxItem, online: Bool) -> Delivery {
    switch item.phase {
    case .failed: .rejected(item.failure ?? .unreadable)
    case .sending: .sending
    case .waiting: online ? .sending : .offline
    }
}

/// A steer stored in a run while a tool of the run was open (a `tool_call` with no `tool_result` after it yet): Dot reads it
/// after that step (R-e §4 b3). The `steer` event is the harness's own word that it handed the message over. Pure.
public func readsAfterStep(steer: EventRecord, in timeline: RunTimeline) -> Bool {
    var open: Set<String> = []
    for event in timeline.events {
        if event.id >= steer.id { break }
        let call: String = event.data["call_id"]?.string ?? event.data["id"]?.string ?? ""
        if event.kind == "tool_call" { open.insert(call) }
        if event.kind == "tool_result" { open.remove(call) }
    }
    guard !open.isEmpty else { return false }
    // Once a result or a reply follows the steer, the step has ended and the message was read with it.
    return !timeline.events.contains { $0.id > steer.id && ($0.kind == "tool_result" || $0.kind == "assistant" || $0.kind == "run_finished") }
}
