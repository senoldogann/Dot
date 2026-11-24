import Foundation

/// The one thin line that says the phone is not talking to the Mac: offline, trying again, or its key refused. It never
/// blocks reading: what the phone holds stays on the screen under it. Nothing is said while all is well.
struct ConnectionBanner: Equatable, Sendable {
    enum Kind: Equatable, Sendable {
        case offline
        case reconnecting
        case unauthorized
    }

    var kind: Kind
    var text: String

    /// What to say for how things stand, or nothing. `waiting` is how many doings the outbox holds for the Mac.
    static func make(link: LinkState, connection: Connection, lastUpdated: Date?, waiting: Int, now: Date) -> ConnectionBanner? {
        if connection == .unauthorized {
            return ConnectionBanner(kind: .unauthorized, text: "This phone's key was refused. Pair again in Settings.")
        }
        if !link.online {
            // One line: what waits to be sent is worth more of it than the words that say what is shown.
            var words: String = waiting > 0 ? "Offline — \(waiting) waiting to send" : "Offline — showing saved chats"
            if let lastUpdated { words += " · last updated \(ago(lastUpdated, now: now))" }
            return ConnectionBanner(kind: .offline, text: words)
        }
        if connection == .reconnecting {
            var words: String = "Reconnecting…"
            if waiting > 0 { words += " · \(waiting) waiting to send" }
            return ConnectionBanner(kind: .reconnecting, text: words)
        }
        return nil
    }

    /// "just now", "3 min ago", "2 h ago", "4 d ago": coarse on purpose, a line that is not read twice.
    static func ago(_ then: Date, now: Date) -> String {
        let seconds: Int = max(0, Int(now.timeIntervalSince(then)))
        if seconds < 45 { return "just now" }
        if seconds < 90 { return "1 min ago" }
        if seconds < 3600 { return "\((seconds + 30) / 60) min ago" }
        if seconds < 86_400 { return "\(seconds / 3600) h ago" }
        return "\(seconds / 86_400) d ago"
    }
}

/// Which chats a list refresh reads ahead of the owner's tap: those the list says moved on past what the phone holds
/// (or that it holds nothing of), newest first, as many as the link's budget allows (a link the system asks to spare
/// gets none).
enum PrefetchRules {
    static func needsReading(stored: StoredChat?, thread: ThreadSummary) -> Bool {
        guard let stored else { return true }
        let newest: RunRecord? = stored.runs.filter { $0.parentRunId == nil }.max { $0.createdAt < $1.createdAt }
        if newest?.id != thread.latestRunId || newest?.status != thread.latestStatus { return true }
        return thread.updatedAt > (stored.listUpdatedAt ?? 0)
    }

    static func chosen(from threads: [ThreadSummary], open: Set<String>, budget: Int, held: (String) -> StoredChat?) -> [ThreadSummary] {
        guard budget > 0 else { return [] }
        var picked: [ThreadSummary] = []
        for thread in threads.prefix(max(budget * 3, 12)) where !open.contains(thread.id) {
            if needsReading(stored: held(thread.id), thread: thread) { picked.append(thread) }
            if picked.count == budget { break }
        }
        return picked
    }
}
