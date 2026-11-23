import Foundation

/// The reactions of the web's palette, in its order: the daemon's name, the sign and the words.
struct Reaction: Identifiable, Equatable, Sendable {
    let id: String
    let symbol: String
    let label: String

    static let all: [Reaction] = [
        Reaction(id: "good", symbol: "👍", label: "Good"),
        Reaction(id: "love", symbol: "❤️", label: "Love it"),
        Reaction(id: "wrong", symbol: "👎", label: "Not right"),
        Reaction(id: "again", symbol: "🔁", label: "Do it again"),
        Reaction(id: "remember", symbol: "📌", label: "Remember this"),
        Reaction(id: "explain", symbol: "❓", label: "Explain"),
        Reaction(id: "hold", symbol: "✋", label: "Hold on"),
    ]

    static func symbol(of name: String) -> String { all.first { $0.id == name }?.symbol ?? "•" }
}

/// What the owner may do with a row of the chat, from its id and kind (a row's id says which run and step it is):
/// copy its words, react to what Dot said, or take the chat back to before the owner's own message and edit it.
enum MessageActions {
    /// The words a Copy puts on the pasteboard, or `nil` for a row with nothing to copy (a card, a status note).
    static func copyText(of item: ChatItem) -> String? {
        switch item.kind {
        case .user(let text), .assistant(let text, _), .answer(let text, _): text.isEmpty ? nil : text
        case .tool(let row): row.line
        case .note(let text, _): text
        case .approval: nil
        }
    }

    /// The run and the message key a reaction goes on (`answer`, or `step-<n>` for what Dot said in step n), for what Dot
    /// said and finished writing; `nil` for the owner's words, a step line, a card, or words still being written.
    static func reactionTarget(of item: ChatItem) -> (runId: String, message: String)? {
        let parts: [Substring] = item.id.split(separator: ":", omittingEmptySubsequences: false)
        switch item.kind {
        case .answer:
            guard parts.count == 2, parts[0] == "r" else { return nil }
            return (String(parts[1]), "answer")
        case .assistant(_, let streaming):
            guard !streaming, parts.count == 3, parts[0] == "a", let step: Int = Int(parts[2]), step >= 1 else { return nil }
            return (String(parts[1]), "step-\(step)")
        default:
            return nil
        }
    }

    /// The run whose message `item` is, when it is the owner's own first message of that run (the only kind the daemon
    /// rewinds to: a message that steered a run is part of it); `nil` while a run works, since the chat is not rewound
    /// under a run.
    static func rewindTarget(of item: ChatItem, chatIsWorking: Bool) -> String? {
        guard !chatIsWorking, case .user = item.kind else { return nil }
        let parts: [Substring] = item.id.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, parts[0] == "u" else { return nil }
        return String(parts[1])
    }

    /// The reactions standing on a row, as the names the daemon gave.
    static func standing(on item: ChatItem, among all: [ReactionRecord]) -> [String] {
        guard let target: (runId: String, message: String) = reactionTarget(of: item) else { return [] }
        let names: Set<String> = Set(all.filter { $0.runId == target.runId && $0.message == target.message }.map(\.reaction))
        return Reaction.all.map(\.id).filter(names.contains)
    }
}
