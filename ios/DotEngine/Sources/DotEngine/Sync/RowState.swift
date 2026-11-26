import Foundation

/// What a chat's row, or a bot's, says of where it stands (item 1920), strongest first: something waits on the owner
/// (an approval, a question, a secret), its run waits on helpers, its run works, something works for it in the
/// background (a helper let go, a background command, its loop), or nothing.
public enum RowState: Sendable, Hashable {
    case needsYou
    case waitingOnHelpers(Int)
    case working
    case waiting(String)
    case rest

    /// The order a bot's chats are weighed in: what waits on the owner first, then what works, then what waits.
    var rank: Int {
        switch self {
        case .needsYou: 0
        case .waitingOnHelpers, .working: 1
        case .waiting: 2
        case .rest: 3
        }
    }

    /// Whether a run of it is at work (the row's live mark).
    public var isLive: Bool {
        switch self {
        case .waitingOnHelpers, .working: true
        case .needsYou, .waiting, .rest: false
        }
    }
}

/// The run states of a chat whose latest run works (`queued` is about to).
let workingRunStates: Set<String> = ["queued", "running", "ending"]

/// Where a chat stands, from its row as the list holds it (pure): its asks and latest run's status, the helpers its
/// run waits on and its background work, all of which move the chat's revision, so the list's refresh keeps it true.
public func rowState(_ row: ThreadRow) -> RowState {
    if row.asks > 0 || row.summary.latestStatus == "blocked" { return .needsYou }
    if workingRunStates.contains(row.summary.latestStatus) {
        let helpers: Int = row.helpers ?? 0
        return helpers > 0 ? .waitingOnHelpers(helpers) : .working
    }
    if let work = row.background?.first { return .waiting(work.title) }
    return .rest
}

/// Where a bot stands (pure): something waits on the owner in any of its chats, else the strongest state of its
/// conversations (`isConversation`: Dot's own work does not make it look busy, item 1912), the newest first on a tie.
public func botState(_ bot: Bot) -> RowState {
    if bot.asks > 0 { return .needsYou }
    let states: [RowState] = bot.chats.filter { isConversation($0.summary) }.map(rowState)
    return states.min { $0.rank < $1.rank } ?? .rest
}
