import Foundation

/// When a chat on the screen reads its helpers again (item 1466). A run that ends done leaves the helpers it started at work
/// (item 1351), and its stream ends with it, so nothing tells the phone that one of them has ended: the count would stay
/// where the last read left it. So, while the chat is on the screen and a helper is live, the chat asks the daemon for its
/// list of helpers (`GET /api/threads/{id}/helpers`, one small request) every `seconds`, and no more; a helper that was live and is
/// not any more means the chat is read whole (its report may have started a run there, which the chat then follows); when none
/// is live it stops asking; with no link it asks nothing and keeps the last count, drawn dimmed. The app coming to the
/// front and a message of the daemon's lifecycle stream read the chat at once, as they always did.
enum HelperWatch {
    /// How often the list is asked for while a helper is live.
    static let seconds: Double = 8
    static var interval: Duration { .seconds(seconds) }

    /// Whether the watch goes on at all: while a helper is live.
    static func goesOn(live: Int) -> Bool { live > 0 }

    /// Whether a tick asks the daemon: a helper is live and the phone has a link (with none the last count stays).
    static func asks(live: Int, online: Bool) -> Bool { goesOn(live: live) && online }

    /// Whether what a read of the list found asks for the chat to be read whole: a helper that was live ended.
    static func readsWhole(live before: Set<String>, then after: Set<String>) -> Bool { !before.subtracting(after).isEmpty }

    /// Whether what the capsule says of the helpers is only the last count read: the phone has no link, or Dot cannot be reached.
    static func isLastKnown(online: Bool, reconnecting: Bool) -> Bool { !online || reconnecting }
}

/// How many helpers of a chat were live when the phone last read them, and the chat's newest run at that time.
struct HelperCount: Equatable, Sendable {
    var live: Int
    var newestRun: String?
}

/// What a chat's row in the list says of its helpers (item 1466). The daemon's list of chats (`GET /api/threads`) says only how its
/// newest run stands (`latest_status`), nothing of the helpers that run left at work, so a chat whose run ended done reads as at
/// rest while they work. The daemon is not changed by this item; the row says what the phone itself last read of the chat's
/// helpers, and only while that was read at the chat's newest run: a chat that has moved on since (its report started a run) is
/// the list's own to say.
enum ListHelpers {
    static func live(_ known: HelperCount?, thread: ThreadSummary) -> Int {
        guard let known, known.live > 0, known.newestRun == thread.latestRunId else { return 0 }
        return known.live
    }
}

/// How the helpers at work are said where the chat's own run is not (item 1466).
enum HelperWords {
    /// The line under the chat's title: "5 helpers working" once the chat's own run is over (it said "Done · 1 min ago" while they
    /// worked), "5 helpers in the background" while the run goes on without waiting for them.
    static func header(count: Int, runWorking: Bool) -> String {
        let noun: String = DockSummary.helperWord(count)
        return runWorking ? "\(count) \(noun) in the background" : "\(count) \(noun) working"
    }
}

/// What the composer's box says (item 1466 made "waiting on you" possible while the chat's own run is over, when a live helper asks).
enum ComposerWords {
    /// While a question waits the box can queue a note behind the run that works; with no run at work a message starts a new one.
    static func placeholder(waitingOnYou: Bool, working: Bool) -> String {
        if waitingOnYou { return working ? "Answer above, or queue a note" : "Answer above, or send a message" }
        return working ? "Queue a message" : "Message Dot"
    }
}
