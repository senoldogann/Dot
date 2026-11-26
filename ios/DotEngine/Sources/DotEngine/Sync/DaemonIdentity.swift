import Foundation

/// Which daemon a picture came from: its version and the id of its database (`GET /api/status`, `store_id`), which a
/// restart and an update keep and a reset (a new, empty Dot home on the Mac) changes. A daemon from before the id says
/// none.
public struct DaemonIdentity: Codable, Sendable, Hashable {
    public let version: String
    public let store: String?

    public init(version: String, store: String?) {
        self.version = version
        self.store = store
    }
}

/// How the daemon heard now stands to the one the phone's copies came from.
public enum DaemonChange: Sendable, Equatable {
    /// None was heard before, or the same one again (an id first heard from a daemon that had none is the same one).
    case same
    /// The same database under another version: the copies' cursors are dropped, the outbox is kept.
    case updated
    /// Another database (the Mac's Dot was reset): its chats, runs and revisions are not the copies', and what the
    /// outbox holds for those chats can reach nothing.
    case reset
}

/// A message a reset left unsent: its outbox key, its words and how many photos it had (those are not kept).
public struct UnsentWords: Codable, Sendable, Equatable {
    public let key: String
    public let text: String
    public let photos: Int

    public init(key: String, text: String, photos: Int) {
        self.key = key
        self.text = text
        self.photos = photos
    }
}

/// A reset the owner has not yet been told of, and the messages it left unsent (their words kept for the new chat's
/// composer: never dropped in silence). `taken` once the app put the words there, with the drafts of the old chats.
public struct DaemonReset: Codable, Sendable, Equatable {
    public let at: Double
    public let unsent: [UnsentWords]
    public let taken: Bool

    public init(at: Double, unsent: [UnsentWords], taken: Bool) {
        self.at = at
        self.unsent = unsent
        self.taken = taken
    }

    public var words: [String] { unsent.map(\.text).filter { !$0.isEmpty } }

    /// Whether photos of unsent messages were let go (the notice says so).
    public var photosLeft: Bool { unsent.contains { $0.photos > 0 } }
}

public enum DaemonRules {
    public static func change(from was: DaemonIdentity?, to next: DaemonIdentity) -> DaemonChange {
        guard let was else { return .same }
        if let old: String = was.store, let new: String = next.store, old != new { return .reset }
        return was.version == next.version ? .same : .updated
    }

    /// What of the outbox survives a reset: a new chat names nothing of the old daemon and goes to the new one (its
    /// photos uploaded again: the old daemon's ids mean nothing there); every other doing names a chat or a run of the
    /// old daemon and is dropped, a dropped message or steer kept, in order, for the owner (its words; its photos counted).
    public static func afterReset(_ items: [OutboxItem]) -> (kept: [OutboxItem], dropped: [OutboxItem], unsent: [UnsentWords]) {
        var kept: [OutboxItem] = []
        var dropped: [OutboxItem] = []
        var unsent: [UnsentWords] = []
        for item in items {
            if case .newChat = item.action {
                var fresh: OutboxItem = item.with(uploaded: [])
                if fresh.phase == .sending { fresh.phase = .waiting }
                kept.append(fresh)
                continue
            }
            dropped.append(item)
            if item.isMessage {
                unsent.append(UnsentWords(key: item.id, text: (item.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines), photos: item.images.count))
            }
        }
        return (kept, dropped, unsent)
    }

    /// The unsent messages of a reset told before and those found now, each once (a kill between keeping them and
    /// dropping them from the outbox finds them again).
    public static func merged(_ told: [UnsentWords], _ found: [UnsentWords]) -> [UnsentWords] {
        told + found.filter { one in !told.contains { $0.key == one.key } }
    }

    /// The new chat's draft with the words a reset left unsent after it, each apart from the next by a blank line.
    public static func draft(keeping existing: String, adding words: [String]) -> String {
        ([existing] + words).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }.joined(separator: "\n\n")
    }
}
