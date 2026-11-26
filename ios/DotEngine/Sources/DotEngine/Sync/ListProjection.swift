import Foundation

/// The chat list as the engine holds it: the rows of `GET /api/threads/view`, kept current by the list's stream
/// (lifecycle kinds + `thread_changed`). The list stream has no `synchronized`: the list is live once its view was read
/// on the stream's present connection.
public struct ListProjection: Codable, Sendable, Equatable {
    public internal(set) var rows: [ThreadRow]
    public internal(set) var revision: Int?
    public internal(set) var freshness: Freshness
    public internal(set) var marks: SyncMarks
    /// Chats told deleted, at the revision they were told at: an older view read meanwhile does not bring them back.
    public internal(set) var deleted: [String: Int]

    public init() {
        rows = []
        revision = nil
        freshness = .empty
        marks = .none
        deleted = [:]
    }

    /// A list drawn from rows with no stream behind it (the debug build's fixtures).
    public static func drawn(rows: [ThreadRow]) -> ListProjection {
        var list: ListProjection = ListProjection()
        list.rows = rows
        list.freshness = .live
        return list
    }

    /// The rows in the list's order: pinned first, then by the newest run.
    public var ordered: [ThreadRow] {
        rows.sorted { left, right in
            let leftPin: Double = left.summary.pinnedAt ?? 0
            let rightPin: Double = right.summary.pinnedAt ?? 0
            if (leftPin > 0) != (rightPin > 0) { return leftPin > 0 }
            if leftPin != rightPin { return leftPin > rightPin }
            return left.summary.latestCreatedAt > right.summary.latestCreatedAt
        }
    }

    /// How many chats wait on the owner (the Chats badge and the Needs-you section, one source, review S1).
    public var needsYou: Int { rows.filter { $0.asks > 0 }.count }
}

/// What a step of the list's reducer asks of the edges.
public enum ListEffect: Sendable, Equatable {
    case readList
}

public enum ListReducer {
    public static func restored(_ cached: ListProjection) -> ListProjection {
        var next: ListProjection = cached
        next.marks = .none
        next.freshness = cached.rows.isEmpty && cached.revision == nil ? .empty : .cached
        return next
    }

    public static func opening(_ state: ListProjection) -> (ListProjection, [ListEffect]) {
        var next: ListProjection = state
        next.marks.connection += 1
        next.marks.streamOpen = false
        next.marks.viewConnection = nil
        if next.freshness == .live { next.freshness = .syncing }
        return ask(next)
    }

    public static func opened(_ state: ListProjection, connection: Int) -> ListProjection {
        guard connection == state.marks.connection else { return state }
        var next: ListProjection = state
        next.marks.streamOpen = true
        if next.freshness == .cached { next.freshness = .syncing }
        return settle(next).0
    }

    public static func broke(_ state: ListProjection, connection: Int) -> ListProjection {
        guard connection == state.marks.connection else { return state }
        var next: ListProjection = state
        next.marks.streamOpen = false
        next.freshness = state.revision == nil ? .empty : .cached
        return next
    }

    public static func asked(_ state: ListProjection) -> ListProjection {
        var next: ListProjection = state
        next.marks.viewInFlight = true
        next.marks.viewOwed = false
        return next
    }

    public static func loaded(_ state: ListProjection, view: ThreadsView, connection: Int) -> (ListProjection, [ListEffect]) {
        var next: ListProjection = state
        next.marks.viewInFlight = false
        if let held: Int = state.revision, held > view.revision {
            return settle(next)
        }
        next.rows = view.threads.filter { row in
            guard let at: Int = next.deleted[row.summary.id] else { return true }
            return row.revision > at
        }
        next.revision = view.revision
        next.marks.viewConnection = connection == state.marks.connection ? connection : nil
        if next.freshness == .empty { next.freshness = .cached }
        return settle(next)
    }

    public static func failed(_ state: ListProjection) -> ListProjection {
        var next: ListProjection = state
        next.marks.viewInFlight = false
        return next
    }

    public static func frame(_ state: ListProjection, _ frame: StreamFrame, connection: Int) -> (ListProjection, [ListEffect]) {
        guard connection == state.marks.connection else { return (state, []) }
        var next: ListProjection = state
        switch frame {
        case .threadChanged(let change):
            if change.deleted {
                next.rows.removeAll { $0.summary.id == change.threadId }
                next.deleted[change.threadId] = change.revision
                return (next, [])
            }
            if let row: ThreadRow = next.rows.first(where: { $0.summary.id == change.threadId }), row.revision == change.revision {
                return (next, [])
            }
            return ask(next)
        case .resync, .defect:
            if next.freshness == .live { next.freshness = .syncing }
            return ask(next)
        case .event, .delta, .live, .synchronized:
            // A run queued, started or ended moves its chat's revision too: the `thread_changed` that follows says it.
            return (state, [])
        }
    }

    static func settle(_ state: ListProjection) -> (ListProjection, [ListEffect]) {
        var next: ListProjection = state
        if next.marks.streamOpen, next.marks.viewConnection == next.marks.connection, next.revision != nil {
            next.freshness = .live
        }
        if next.marks.viewOwed, !next.marks.viewInFlight {
            next.marks.viewOwed = false
            return (next, [.readList])
        }
        return (next, [])
    }

    static func ask(_ state: ListProjection) -> (ListProjection, [ListEffect]) {
        var next: ListProjection = state
        if next.marks.viewInFlight {
            next.marks.viewOwed = true
            return (next, [])
        }
        return (next, [.readList])
    }
}

/// The chats to mark seen on the Mac (item 1905): those open on the phone whose row says unread, each once per revision
/// of its row (`sent` holds the revision each was last marked at), as the web's sidebar marks its open chat (pure).
public func chatsToMarkSeen(rows: [ThreadRow], open: Set<String>, sent: [String: Int]) -> [ThreadRow] {
    rows.filter { open.contains($0.summary.id) && $0.summary.unread && sent[$0.summary.id] != $0.revision }
}
