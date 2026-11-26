import Foundation

// The rules of a chat's message actions (package W4), pure so that they are tested without a screen. Each follows the
// web's own rule (`views/run.js`), so the phone offers what the web offers, on the same routes.

/// The sources of a run that carries no message of the owner's (`history.js`): a resumed run, Dot's welcome, a note
/// about a command in the background, a work order's attempt.
let runsWithoutOwnersWords: Set<String> = ["resume", "welcome", "background", "work_order"]

/// How a run whose model declined its task ended (`RunRecord.end_reason`, web `restartline.js` `MODEL_REFUSED`).
let modelRefused: String = "model_refused"

/// The run an owner's message of the transcript belongs to: `u:<run>` (its goal); `nil` for a message still on its way
/// (`p:`) or a steer into a run (`s:`), which no rewind reaches.
public func runOfOwnerMessage(_ itemId: String) -> String? {
    itemId.hasPrefix("u:") ? String(itemId.dropFirst(2)) : nil
}

/// The run an end card belongs to (`e:<run>`).
public func runOfEndCard(_ itemId: String) -> String? {
    itemId.hasPrefix("e:") ? String(itemId.dropFirst(2)) : nil
}

/// What the owner may do with a message of theirs: Edit from here and Retry need a chat that can go back to before it
/// (`rewindable`), a message of their own in a run of the chat's own (not a helper's), and no run at work in the chat
/// (the daemon refuses a rewind under a working run).
public struct OwnerMessageOffer: Equatable, Sendable {
    public let canRewind: Bool
    /// Why Edit from here is not offered now, in the engine's words for the screen to say; `nil` when it is.
    public let refusal: RewindRefusal?
}

public enum RewindRefusal: Equatable, Sendable {
    /// A run of the chat works: stop it or wait for it.
    case working
    /// The chat cannot go back to before this message (a helper's run, an older run, Dot's own note).
    case notRewindable
}

public func ownerMessageOffer(run: RunRecord, chatWorking: Bool) -> OwnerMessageOffer {
    let own: Bool = run.parentRunId == nil && run.followsReaction == nil && !runsWithoutOwnersWords.contains(run.source)
    guard own, run.rewindable == true else { return OwnerMessageOffer(canRewind: false, refusal: .notRewindable) }
    guard !chatWorking else { return OwnerMessageOffer(canRewind: false, refusal: .working) }
    return OwnerMessageOffer(canRewind: true, refusal: nil)
}

/// What the card at the end of a run that did not end done offers: Continue (`POST /runs/{id}/resume`) when the daemon
/// says the run can be resumed (cut off, blocked or failed, the newest of its chat) and its model did not decline the
/// task (item 1889: such a run is restarted on another model, never continued on the one that declined it), and Retry
/// (rewind, then the message again) whenever its message can be gone back to, as the web offers it under any answer of
/// such a message; `cutOff` names how a restart cut it off.
public struct EndOffer: Equatable, Sendable {
    public let resume: Bool
    public let retry: Bool
    public let cutOff: String?

    public init(resume: Bool, retry: Bool, cutOff: String?) {
        self.resume = resume
        self.retry = retry
        self.cutOff = cutOff
    }
}

/// `newest` is the chat's newest run as the view gives it; `run` the run the card ends.
public func endOffer(run: RunRecord, newest: RunDetail?, chatWorking: Bool) -> EndOffer {
    let isNewest: Bool = newest?.run.id == run.id
    let resume: Bool = isNewest && !chatWorking && newest?.resumable == true && run.endReason != modelRefused
    let retry: Bool = ownerMessageOffer(run: run, chatWorking: chatWorking).canRewind
    return EndOffer(resume: resume, retry: retry, cutOff: isNewest ? newest?.cutOff : nil)
}

/// Retry's new run (web `retry`, item 1185): the rewound message's words and files, with the choices the chat keeps, for
/// the same agent, in the same chat even when the rewind left no run in it: the phone stays on the chat it retried in
/// (the web opens a new chat then, which a phone would leave the owner looking at an empty one for). The kept effort
/// goes only when the chosen model offers it (`efforts`, the model's levels from `GET /api/models`), as the web checks
/// the level against the model's own: the daemon refuses a level its model does not offer.
public func retryRun(cut: RewindView, choices: ChatChoices?, past: RunRecord, efforts: [String], key: String) -> RetryRun {
    let effort: String? = choices?.effort.flatMap { efforts.contains($0) ? $0 : nil }
    return RetryRun(
        goal: cut.text, key: key, mode: choices?.mode ?? past.mode, model: choices?.model, effort: effort, speed: choices?.speed ?? "standard",
        approvals: choices?.approvals, goalMode: choices?.goalMode, threadId: cut.threadId, agentId: past.agentId,
        attachments: cut.files.map(\.id))
}

/// A model reference as the owner reads it: the provider apart from the model ("opencode_go:deepseek-v4.1-flash" →
/// "opencode_go", "deepseek-v4.1-flash"); a reference with no provider has none.
public struct ModelParts: Equatable, Sendable {
    public let provider: String?
    public let model: String
}

public func modelParts(_ ref: String) -> ModelParts {
    guard let colon = ref.firstIndex(of: ":"), colon != ref.startIndex else { return ModelParts(provider: nil, model: ref) }
    return ModelParts(provider: String(ref[..<colon]), model: String(ref[ref.index(after: colon)...]))
}

/// A provider's name as the owner reads it ("claude_code" → "Claude Code", "opencode_go" → "OpenCode Go"); one the
/// table does not know is its words with each first letter raised.
public func providerTitle(_ id: String) -> String {
    let known: [String: String] = [
        "claude_code": "Claude Code", "opencode": "OpenCode", "opencode_go": "OpenCode Go", "openrouter": "OpenRouter", "ollama": "Ollama",
        "deepseek": "DeepSeek", "anthropic": "Anthropic", "openai": "OpenAI", "codex": "Codex", "gemini": "Gemini", "lmstudio": "LM Studio",
    ]
    if let name = known[id] { return name }
    return id.split(separator: "_").map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
}
