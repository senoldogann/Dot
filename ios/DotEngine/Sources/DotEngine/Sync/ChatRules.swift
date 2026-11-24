import Foundation

// The chat screen's pure rules (wave N2): the asks of the dock, the composer's buttons, the queue's edit, durations and
// the chat's one derived phase. The words are the app's; these say what is shown and in what order.

// MARK: Durations

/// A length of time as hours, minutes and seconds, the one shape every clock of the app is written in (§19: "41 dk 15
/// sn", the work fold's "2 dk 14 sn"): hours only from an hour, seconds dropped from ten hours.
public struct DurationParts: Sendable, Equatable {
    public let hours: Int
    public let minutes: Int
    public let seconds: Int
    /// Whether the seconds are said (not past ten hours).
    public let showsSeconds: Bool

    public static func of(_ seconds: Int) -> DurationParts {
        let total: Int = max(0, seconds)
        return DurationParts(hours: total / 3600, minutes: (total % 3600) / 60, seconds: total % 60, showsSeconds: total < 36_000)
    }
}

// MARK: Asks

/// What an ask is, by its severity and how it stands (`approvals.js`'s cards).
public enum AskKind: Sendable, Equatable {
    case approval
    case question
    /// A step handed to the owner (a captcha, a login): they do it, then say so.
    case handoff
    /// A key, a token or a password, typed into a secure field and sent to its own route (item 857, §20).
    case secret
}

/// An option of a question (item 192).
public struct AskOption: Sendable, Equatable, Hashable {
    public let label: String
    public let detail: String
    public let recommended: Bool
}

/// One ask as the dock shows it.
public struct Ask: Sendable, Equatable, Identifiable {
    public let id: String
    public let runId: String
    public let kind: AskKind
    public let tool: String
    /// The question's words, the secret's label, or the approval's summary: whole, never cut.
    public let text: String
    /// Why it is asked (a secret's `why`), empty when the text says it all.
    public let why: String
    /// What the action acts on, whole (a command, a path, a URL), shown in a code box.
    public let target: String
    public let options: [AskOption]
    /// A question's short topic label (`header`), empty when Dot gave none.
    public let topic: String
    public let multiple: Bool
    /// "Always allow in this chat" is offered (item 866: never for money, credentials, Dot's own files, a question).
    public let rememberable: Bool
    /// A step kept for the owner after nobody answered in time (item 482): "Allow once" / "I did it".
    public let kept: Bool
    /// A helper's ask (its run is not one of the chat's own).
    public let fromHelper: Bool
    public let createdAt: Double
}

/// Where an ask stands for the owner (plan §13 B1).
public enum AskPhase: Sendable, Equatable {
    case pending
    /// The answer is on its way.
    case submitting
    /// The answer is kept and goes once the phone is online.
    case queuedOffline
    /// Someone answered it on the Mac or elsewhere (it left the chat's asks without an answer from here).
    case answeredElsewhere
    /// Nobody answered in time.
    case expired
    /// The daemon refused the answer, with its words.
    case refused(String)
    /// The answer could not be sent; it can be tried again.
    case retry(String)

    /// Whether the controls take an answer now.
    public var answerable: Bool {
        switch self {
        case .pending, .retry: true
        default: false
        }
    }
}

public struct DockEntry: Sendable, Equatable, Identifiable {
    public let ask: Ask
    public let phase: AskPhase
    public var id: String { ask.id }
}

// What a "don't ask again in this chat" never covers (item 866, `NEVER_REMEMBERED` in policy/chat_permits.py).
private let neverRemembered: Set<String> = ["financial", "credential", "vault_fill", "self_change"]
// The words of a question a chat Dot opened on its own initiative asks (item 850): asked in every mode, never remembered.
private let initiativeWords: String = "On its own initiative, Dot asks before"

/// An ask read from the daemon's record. `chatRuns` are the chat's own runs: an ask of another run is a helper's.
public func ask(of record: ApprovalRecord, chatRuns: Set<String>) -> Ask {
    let args: [String: JSONValue] = record.args ?? [:]
    let kind: AskKind =
        switch record.severity {
        case "question": .question
        case "handoff": .handoff
        case "secret": .secret
        default: .approval
        }
    let kept: Bool = record.keptUntil != nil
    let options: [AskOption] = (args["options"]?.array ?? []).compactMap { option in
        if let label: String = option.string { return AskOption(label: label, detail: "", recommended: false) }
        guard let label: String = option["label"]?.string else { return nil }
        return AskOption(label: label, detail: option["description"]?.string ?? "", recommended: option["recommended"]?.bool == true)
    }
    let flags: [String] = (args["flags"]?.array ?? []).compactMap(\.string)
    let typedShell: Bool = args["kind"]?.string == "shell" && record.tool != "shell"
    let rememberable: Bool =
        kind == .approval && !kept && !typedShell && !record.summary.contains(initiativeWords)
        && flags.allSatisfy { !neverRemembered.contains($0) }
    let text: String =
        switch kind {
        case .question: args["question"]?.string ?? record.summary
        case .secret: args["label"]?.string ?? record.summary
        case .approval, .handoff: record.summary
        }
    return Ask(
        id: record.id, runId: record.runId, kind: kind, tool: record.tool, text: text,
        why: kind == .secret ? (args["why"]?.string ?? "") : "",
        target: [args["target"]?.string, args["body"]?.string].compactMap { $0 }.filter { !$0.isEmpty && !text.contains($0) }.joined(separator: "\n\n"),
        options: options, topic: args["header"]?.string ?? "", multiple: args["multiple"]?.bool == true, rememberable: rememberable, kept: kept,
        fromHelper: !chatRuns.contains(record.runId), createdAt: record.createdAt)
}

/// The dock's entries (plan §13 B1): the asks waiting, oldest first (a stable order: a new ask never moves the one the
/// owner reads), each with its phase from the outbox and the phone's own sending (`local`, the secrets, which never go
/// through the outbox), and the asks the owner was looking at that left without an answer from here (`shown`), as
/// "answered elsewhere" or "expired" (`decided`: how the transcript says each ended) until the app lets them go. An ask
/// this phone answered (`answeredHere`) leaves at once, whatever came first: the outbox's end or the chat's new view.
public func dockEntries(
    asks: [ApprovalRecord], chatRuns: Set<String>, outbox: [OutboxItem], online: Bool, local: [String: AskPhase], shown: [DockEntry],
    decided: [String: String], answeredHere: Set<String>
) -> [DockEntry] {
    var answering: [String: OutboxItem] = [:]
    for item in outbox {
        if case .decision(let approval, _, _, _, _, _) = item.action { answering[approval] = item }
    }
    let waiting: [DockEntry] = asks.map { record in
        let phase: AskPhase
        if let item: OutboxItem = answering[record.id] {
            switch item.phase {
            case .failed:
                switch item.failure {
                case .refused(_, let detail): phase = .refused(detail)
                default: phase = .retry("")
                }
            case .sending: phase = .submitting
            case .waiting: phase = online ? .submitting : .queuedOffline
            }
        } else {
            phase = local[record.id] ?? .pending
        }
        return DockEntry(ask: ask(of: record, chatRuns: chatRuns), phase: phase)
    }
    let open: Set<String> = Set(asks.map(\.id))
    let left: [DockEntry] = shown.compactMap { entry in
        guard !open.contains(entry.id), answering[entry.id] == nil, !answeredHere.contains(entry.id) else { return nil }
        switch entry.phase {
        case .answeredElsewhere, .expired: return entry
        case .submitting, .queuedOffline: return nil
        default:
            if local[entry.id] == .submitting { return nil }
            return DockEntry(ask: entry.ask, phase: decided[entry.id] == "expired" ? .expired : .answeredElsewhere)
        }
    }
    return (waiting + left).sorted { ($0.ask.createdAt, $0.id) < ($1.ask.createdAt, $1.id) }
}

/// The one text that answers a question: the labels chosen joined by "; ", then what was written on a line of its own
/// (`chosen_answer` of dotagent.agent.questions). Empty when nothing was chosen or written.
public func chosenAnswer(chosen: [String], written: String) -> String {
    [chosen.joined(separator: "; "), written.trimmingCharacters(in: .whitespacesAndNewlines)].filter { !$0.isEmpty }.joined(separator: "\n")
}

/// What the owner chose and wrote for one question while the dock shows it (PLAN §21: choices are picked, one Submit
/// sends every answered question).
public struct QuestionDraft: Sendable, Equatable {
    public var chosen: [String]
    public var written: String

    public init(chosen: [String], written: String) {
        self.chosen = chosen
        self.written = written
    }

    public static let empty: QuestionDraft = QuestionDraft(chosen: [], written: "")

    /// The draft with `option` picked: alone for a question of one answer, added or taken away for one of several.
    public func picking(_ option: String, multiple: Bool) -> QuestionDraft {
        guard multiple else { return QuestionDraft(chosen: chosen == [option] ? [] : [option], written: written) }
        let next: [String] = chosen.contains(option) ? chosen.filter { $0 != option } : chosen + [option]
        return QuestionDraft(chosen: next, written: written)
    }
}

/// A question's options as the card shows them: the recommended first, the others in the order Dot gave.
public func shownOptions(_ options: [AskOption]) -> [AskOption] {
    options.filter(\.recommended) + options.filter { !$0.recommended }
}

/// The answers one Submit sends (PLAN §21): each question of the dock that can take an answer and has one, in the
/// dock's order, with its text (the labels chosen in the order Dot gave them, then what was written).
public func submittedAnswers(_ entries: [DockEntry], drafts: [String: QuestionDraft]) -> [(ask: Ask, answer: String)] {
    entries.compactMap { entry in
        guard entry.ask.kind == .question, entry.phase.answerable, let draft: QuestionDraft = drafts[entry.id] else { return nil }
        let picked: [String] = entry.ask.options.map(\.label).filter { draft.chosen.contains($0) }
        let answer: String = chosenAnswer(chosen: picked, written: draft.written)
        return answer.isEmpty ? nil : (entry.ask, answer)
    }
}

/// The shortest secret a card takes (`SECRET_LEAST_CHARS`, held_secrets.py): a shorter one goes as a message instead.
public let secretLeastCharacters: Int = 8

// MARK: The composer

/// What a follow-up does while a run works (D10): queued after the run (default) or sent into it.
public enum FollowUp: String, CaseIterable, Codable, Sendable {
    case queue
    case steer
}

/// What a press of the composer's button does.
public enum ComposerAction: Sendable, Equatable {
    case send
    case queue
    case steer
    case stop
    /// Keeps the edit of a queued message (§20: the ✓ in place of Send).
    case confirmEdit
}

/// The composer's button and the other way to send, which a visible menu and a VoiceOver action offer.
public struct ComposerButtons: Sendable, Equatable {
    public let primary: ComposerAction
    public let alternate: ComposerAction?
    /// Whether the primary button takes a press now.
    public let enabled: Bool
}

/// The composer's buttons (plan §4.1, D10): an empty composer over a run at work is Stop; words over a run at work go
/// by the follow-up setting with the other way beside it; words with no run at work are sent; an edit is confirmed.
public func composerButtons(hasContent: Bool, runActive: Bool, followUp: FollowUp, editing: Bool) -> ComposerButtons {
    if editing { return ComposerButtons(primary: .confirmEdit, alternate: nil, enabled: hasContent) }
    if runActive {
        if !hasContent { return ComposerButtons(primary: .stop, alternate: nil, enabled: true) }
        return followUp == .queue
            ? ComposerButtons(primary: .queue, alternate: .steer, enabled: true)
            : ComposerButtons(primary: .steer, alternate: .queue, enabled: true)
    }
    return ComposerButtons(primary: .send, alternate: nil, enabled: hasContent)
}

/// The text with the word being typed after its mark replaced by `chosen` and a space.
public func completing(_ text: String, with chosen: String) -> String {
    var words: [Substring] = text.split(separator: " ", omittingEmptySubsequences: false)
    guard !words.isEmpty else { return chosen + " " }
    words.removeLast()
    return (words.map(String.init) + [chosen]).joined(separator: " ") + " "
}

// MARK: The queue's edit

/// A queued message being edited in the composer (§20): what the composer held before (the live draft, restored on
/// Cancel) and the words being written.
public struct QueueEdit: Sendable, Equatable {
    public let messageId: String
    public let original: String
    public let liveDraft: String
    public var text: String

    public init(message: QueuedMessageView, liveDraft: String) {
        messageId = message.id
        original = message.text
        self.liveDraft = liveDraft
        text = message.text
    }
}

/// What the ✓ of an edit does.
public enum QueueEditOutcome: Sendable, Equatable {
    /// The new words go to the queued message (its files kept).
    case save(message: String, text: String)
    /// Nothing changed: the edit just closes.
    case unchanged
    /// The message left the queue meanwhile (it started, or was removed elsewhere): the words stay in the composer as
    /// a new message for the owner to send or drop.
    case gone(text: String)
    /// Emptied: nothing to keep (the owner removes a message from the sheet).
    case empty
}

public func confirm(_ edit: QueueEdit, queue: QueueView) -> QueueEditOutcome {
    let words: String = edit.text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard queue.messages.contains(where: { $0.id == edit.messageId }) else { return .gone(text: edit.text) }
    if words.isEmpty { return .empty }
    if words == edit.original.trimmingCharacters(in: .whitespacesAndNewlines) { return .unchanged }
    return .save(message: edit.messageId, text: words)
}

/// The queue in a new order: the message `id` moved to `offset` (0 = next), the others in their order.
public func reordered(_ ids: [String], moving id: String, to offset: Int) -> [String] {
    guard let from: Int = ids.firstIndex(of: id) else { return ids }
    var next: [String] = ids
    next.remove(at: from)
    next.insert(id, at: min(max(0, offset), next.count))
    return next
}

// MARK: The chat's phase

/// The one state a chat's header and capsule say (plan §4.1 States): an ask or a hand-off is "Waiting on you", never
/// "Working".
public enum ChatPhase: Sendable, Equatable {
    case idle
    case starting
    case working(since: Double)
    case waitingOnYou
    case failed
    case stopped
    case done
}

public func chatPhase(of view: ThreadView?) -> ChatPhase {
    guard let view else { return .idle }
    if !view.asks.isEmpty { return .waitingOnYou }
    let own: [RunRecord] = view.runs.filter { $0.parentRunId == nil }
    guard let newest: RunRecord = own.max(by: { $0.createdAt < $1.createdAt }) else { return .idle }
    switch newest.status {
    case "blocked": return .waitingOnYou
    case "queued": return .starting
    case "running", "ending": return .working(since: newest.createdAt)
    case "failed": return .failed
    case "cancelled": return .stopped
    case "done": return .done
    default: return .idle
    }
}

// MARK: Scrolling

/// Whether the transcript follows new words: only while the owner reads near its end (plan §13 S5).
public func followsNewWords(distanceFromEnd: Double, viewport: Double) -> Bool {
    distanceFromEnd <= max(80, viewport * 0.25)
}

// MARK: Titles

/// A short title of a long text: its first sentence, run, thread and hex ids taken out, at most `40` characters cut at
/// a word with an ellipsis, the first letter capital. Pure.
public func shortTitle(_ text: String) -> String {
    let flat: String = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    let ids: String = flat.replacingOccurrences(of: #"\b(run|thr|apr|q|msg)_[A-Za-z0-9]+\b|\b[0-9a-f]{12,}\b"#, with: "", options: .regularExpression)
        .split(separator: " ").joined(separator: " ")
    var sentence: String = ids
    if let end: Range<String.Index> = ids.range(of: #"[.!?:;](\s|$)"#, options: .regularExpression) { sentence = String(ids[..<end.lowerBound]) }
    sentence = sentence.trimmingCharacters(in: CharacterSet(charactersIn: " ,-–—"))
    guard let first: Character = sentence.first else { return "" }
    sentence = first.uppercased() + sentence.dropFirst()
    let limit: Int = 40
    guard sentence.count > limit else { return sentence }
    let cut: Substring = sentence.prefix(limit - 1)
    let word: Substring = cut.lastIndex(of: " ").map { cut[..<$0] } ?? cut
    return word.trimmingCharacters(in: CharacterSet(charactersIn: " ,")) + "…"
}
