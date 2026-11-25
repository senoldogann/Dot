import Foundation

/// How current a picture is (plan §5, §9). `live` only when the view was read after the stream opened AND the stream
/// said `synchronized`; a picture never goes back to `empty` once it holds something.
public enum Freshness: String, Codable, Sendable, Equatable {
    /// Nothing held yet.
    case empty
    /// What a cache or an earlier connection holds; not known to be current.
    case cached
    /// A stream is open and the view is being caught up.
    case syncing
    case live
    /// The chat was deleted (a `thread_changed` with `deleted`, or a 404 on its view).
    case deleted
}

/// A run's stored events, one per id, in id order. Ids are one order for all runs but are not told in that order (a
/// `dotx` run is relayed late, two runs stored at once may come the other way round), so a late event with a lower id
/// is still taken: dedupe by id, never `id <= cursor` (plan §9).
public struct RunTimeline: Codable, Sendable, Equatable {
    public private(set) var events: [EventRecord]
    public private(set) var ids: Set<Int>

    public init() {
        events = []
        ids = []
    }

    /// The newest event id held: the run's cursor for the stream.
    public var cursor: Int { events.last?.id ?? 0 }

    /// The timeline with `event` in it; `nil` when it holds that id already.
    public func adding(_ event: EventRecord) -> RunTimeline? {
        guard event.id > 0, !ids.contains(event.id) else { return nil }
        var next: RunTimeline = self
        next.ids.insert(event.id)
        if let last: EventRecord = events.last, last.id > event.id {
            let at: Int = next.events.firstIndex { $0.id > event.id } ?? next.events.count
            next.events.insert(event, at: at)
        } else {
            next.events.append(event)
        }
        return next
    }

    /// The timeline that has gone past an id it could not read: the cursor moves, nothing is drawn.
    public func skipping(_ id: Int) -> RunTimeline {
        var next: RunTimeline = self
        next.ids.insert(id)
        return next
    }
}

/// The marks of one connection of a chat's stream: which connection, whether `synchronized` came on it, and which view
/// read belongs to it.
public struct SyncMarks: Codable, Sendable, Equatable {
    /// Counts the stream's connections; a frame or a view of an older one is not taken as this one's.
    public var connection: Int
    public var streamOpen: Bool
    /// The revision `synchronized` named on this connection; `nil` before it came.
    public var synchronizedRevision: Int?
    /// The connection the held view was asked on; `nil` when it came from the cache or an earlier connection.
    public var viewConnection: Int?
    /// A view read is under way.
    public var viewInFlight: Bool
    /// Another read is owed once the one under way ends (a change came while it was out).
    public var viewOwed: Bool

    public static let none: SyncMarks = SyncMarks(
        connection: 0, streamOpen: false, synchronizedRevision: nil, viewConnection: nil, viewInFlight: false, viewOwed: false)
}

/// What a step of the reducer asks the edges to do.
public enum SyncEffect: Sendable, Equatable {
    /// Read the chat's view (one call, `GET /api/threads/{id}/view`).
    case readView(thread: String)
    /// The chat is gone: close its stream.
    case closeStream(thread: String)
}

/// One open chat as the engine holds it: the view (small state at a revision), each run's events with its cursor, the
/// words being written of each run's open step, and how current it all is.
public struct ChatProjection: Codable, Sendable, Equatable {
    public let threadId: String
    public internal(set) var view: ThreadView?
    public internal(set) var runs: [String: RunTimeline]
    /// The words being written of a run's open step (id-0 deltas), by run id: shown until the step's stored event comes.
    public internal(set) var writing: [String: String]
    /// The thinking of a run's open step as the model writes it (id-0 `thinking` deltas), by run id: the step's stored
    /// `assistant` event keeps it from then on. Live only: never cached.
    public internal(set) var thinking: [String: LiveThought] = [:]
    public internal(set) var freshness: Freshness
    public internal(set) var marks: SyncMarks
    /// How many frames could not be read (the chat says some steps could not be shown).
    public internal(set) var unreadable: Int
    /// The newest revision the stream told (`thread_changed`, `synchronized`).
    public internal(set) var toldRevision: Int?

    enum CodingKeys: String, CodingKey {
        case threadId, view, runs, writing, freshness, marks, unreadable, toldRevision
    }

    public init(threadId: String) {
        self.threadId = threadId
        view = nil
        runs = [:]
        writing = [:]
        freshness = .empty
        marks = .none
        unreadable = 0
        toldRevision = nil
    }

    /// A chat drawn from a view and stored events with no stream behind it, in the given freshness: the debug build's
    /// fixtures (the screenshot harness and the UI tests), never the engine's own path.
    public static func drawn(view: ThreadView, events: [EventRecord], freshness: Freshness) -> ChatProjection {
        var chat: ChatProjection = ChatProjection(threadId: view.thread.id)
        chat.view = view
        for event in events {
            let timeline: RunTimeline = chat.runs[event.runId] ?? RunTimeline()
            chat.runs[event.runId] = timeline.adding(event) ?? timeline
        }
        chat.freshness = freshness
        return chat
    }

    /// The same chat with the words being written of `run` (a fixture's streaming answer).
    public func writing(_ words: String, run: String) -> ChatProjection {
        var next: ChatProjection = self
        next.writing[run] = words
        return next
    }

    /// The cursors the stream is opened with: each run held, its newest id (the newest 20 runs that hold events).
    public var cursors: [String: Int] {
        let held: [(String, RunTimeline)] = runs.filter { $0.value.cursor > 0 }.sorted { $0.value.cursor > $1.value.cursor }
        return Dictionary(uniqueKeysWithValues: held.prefix(20).map { ($0.0, $0.1.cursor) })
    }

    /// The `cursors` query of the chat's stream: `run_a:120,run_b:98`, in a stable order.
    public var cursorQuery: String {
        cursors.sorted { $0.key < $1.key }.map { "\($0.key):\($0.value)" }.joined(separator: ",")
    }

    /// The runs of the chat and its helpers the store follows.
    public var knownRunIds: Set<String> {
        var ids: Set<String> = Set(runs.keys)
        for run in view?.runs ?? [] { ids.insert(run.id) }
        for helper in view?.helpers ?? [] { ids.insert(helper.runId) }
        return ids
    }
}

/// The reducer of a chat: pure functions from a projection and what happened to the next projection and the effects.
public enum ChatReducer {
    /// A projection read back from the cache: what it held is shown as `cached`, its marks belong to no connection.
    public static func restored(_ cached: ChatProjection) -> ChatProjection {
        var next: ChatProjection = cached
        next.marks = .none
        next.writing = [:]
        next.thinking = [:]
        next.freshness = cached.freshness == .deleted ? .deleted : (cached.view == nil ? .empty : .cached)
        return next
    }

    /// A new connection of the stream is being opened: the view is read beside it (plan §9, R-f #4), not after.
    public static func opening(_ state: ChatProjection) -> (ChatProjection, [SyncEffect]) {
        guard state.freshness != .deleted else { return (state, []) }
        var next: ChatProjection = state
        next.marks.connection += 1
        next.marks.streamOpen = false
        next.marks.synchronizedRevision = nil
        next.marks.viewConnection = nil
        next.writing = [:]
        next.thinking = [:]
        next.freshness = state.view == nil ? .empty : .syncing
        return askView(next)
    }

    /// The stream answered (its headers came).
    public static func opened(_ state: ChatProjection, connection: Int) -> ChatProjection {
        guard connection == state.marks.connection, state.freshness != .deleted else { return state }
        var next: ChatProjection = state
        next.marks.streamOpen = true
        if next.freshness == .cached || next.freshness == .empty { next.freshness = next.view == nil ? .empty : .syncing }
        return next
    }

    /// The stream broke or was closed: what is held stays, as `cached` (transport health is told apart, plan §9).
    public static func broke(_ state: ChatProjection, connection: Int) -> ChatProjection {
        guard connection == state.marks.connection, state.freshness != .deleted else { return state }
        var next: ChatProjection = state
        next.marks.streamOpen = false
        next.marks.synchronizedRevision = nil
        next.writing = [:]
        next.thinking = [:]
        next.freshness = state.view == nil ? .empty : .cached
        return next
    }

    /// A view read is going out on `connection`.
    public static func viewAsked(_ state: ChatProjection, connection: Int) -> ChatProjection {
        var next: ChatProjection = state
        next.marks.viewInFlight = true
        next.marks.viewOwed = false
        next.marks.viewConnection = nil
        _ = connection
        return next
    }

    /// The view read on `connection` came back.
    public static func viewLoaded(_ state: ChatProjection, view: ThreadView, connection: Int) -> (ChatProjection, [SyncEffect]) {
        guard state.freshness != .deleted else { return (state, []) }
        var next: ChatProjection = state
        next.marks.viewInFlight = false
        // An older answer than the one held (two reads crossed) is not taken over a newer one.
        if let held: ThreadView = state.view, held.revision > view.revision {
            return settle(next)
        }
        next.view = view
        next.marks.viewConnection = connection == state.marks.connection ? connection : nil
        if let newest: RunDetail = view.newest {
            var timeline: RunTimeline = next.runs[newest.run.id] ?? RunTimeline()
            for event in view.newestEvents {
                if let added: RunTimeline = timeline.adding(event) {
                    timeline = added
                    // A step the view brought before the stream did ends its live words and thinking as the stream's
                    // own event would (the stream's copy then adds nothing, and so ends nothing).
                    if endsWriting(event) {
                        next.writing[event.runId] = nil
                        next.thinking[event.runId] = nil
                    }
                }
            }
            next.runs[newest.run.id] = timeline
        }
        next.runs = pruned(next.runs, view: view)
        if next.freshness == .empty { next.freshness = next.marks.streamOpen ? .syncing : .cached }
        return settle(next)
    }

    /// The view read failed; `missing` when the daemon said the chat is not there (404).
    public static func viewFailed(_ state: ChatProjection, missing: Bool) -> (ChatProjection, [SyncEffect]) {
        var next: ChatProjection = state
        next.marks.viewInFlight = false
        if missing {
            next.freshness = .deleted
            return (next, [.closeStream(thread: state.threadId)])
        }
        return (next, [])
    }

    /// One frame of the chat's stream on `connection`.
    public static func frame(_ state: ChatProjection, _ frame: StreamFrame, connection: Int) -> (ChatProjection, [SyncEffect]) {
        guard connection == state.marks.connection, state.freshness != .deleted else { return (state, []) }
        var next: ChatProjection = state
        switch frame {
        case .event(let event):
            let timeline: RunTimeline = next.runs[event.runId] ?? RunTimeline()
            guard let added: RunTimeline = timeline.adding(event) else { return (state, []) }
            next.runs[event.runId] = added
            if endsWriting(event) {
                next.writing[event.runId] = nil
                next.thinking[event.runId] = nil
            }
            if let view: ThreadView = next.view, let at: Int = view.helpers.firstIndex(where: { $0.runId == event.runId }) {
                // One source for a helper's state (the lead's look at N1c): its own events move its step and, at its end,
                // its status, so the capsule never counts a helper the agents sheet shows as done.
                var helpers: [HelperView] = view.helpers
                if let step: HelperStep = helperStep(of: event) {
                    helpers[at] = helpers[at].doing(step)
                } else if event.kind == "run_finished" {
                    helpers[at] = helpers[at].ended(status: event.data["status"]?.string ?? "done", at: event.ts)
                }
                next.view = view.with(helpers: helpers)
            }
            return (next, [])
        case .delta(let event):
            next.writing = writing(next.writing, delta: event, runs: next.runs)
            next.thinking = thinking(next.thinking, delta: event, runs: next.runs)
            return (next, [])
        case .live:
            return (state, [])
        case .threadChanged(let change):
            guard change.threadId == state.threadId else { return (state, []) }
            if change.deleted {
                next.freshness = .deleted
                return (next, [.closeStream(thread: state.threadId)])
            }
            next.toldRevision = max(next.toldRevision ?? change.revision, change.revision)
            if next.view?.revision == change.revision { return (next, []) }
            if next.freshness == .live { next.freshness = .syncing }
            return askView(next)
        case .synchronized(let synced):
            next.marks.synchronizedRevision = synced.revision
            next.toldRevision = max(next.toldRevision ?? synced.revision, synced.revision)
            return settle(next)
        case .resync:
            if next.freshness == .live { next.freshness = .syncing }
            return askView(next)
        case .defect(let id, _):
            next.unreadable += 1
            if let id, id > 0, let run: String = newestFollowedRun(next) {
                next.runs[run] = (next.runs[run] ?? RunTimeline()).skipping(id)
            }
            if next.freshness == .live { next.freshness = .syncing }
            return askView(next)
        }
    }

    // MARK: Parts

    /// Whether the picture is live now, or what is owed to make it so.
    static func settle(_ state: ChatProjection) -> (ChatProjection, [SyncEffect]) {
        var next: ChatProjection = state
        guard next.freshness != .deleted else { return (next, []) }
        guard next.marks.streamOpen, let synced: Int = next.marks.synchronizedRevision else { return (next, owedRead(&next)) }
        guard let view: ThreadView = next.view else { return askView(next) }
        let told: Int = max(synced, next.toldRevision ?? synced)
        if view.revision >= told, next.marks.viewConnection == next.marks.connection {
            next.freshness = .live
            return (next, owedRead(&next))
        }
        if view.revision < told || next.marks.viewConnection != next.marks.connection {
            return askView(next)
        }
        return (next, [])
    }

    /// A read that was owed while another was out goes once that one ended.
    private static func owedRead(_ state: inout ChatProjection) -> [SyncEffect] {
        guard state.marks.viewOwed, !state.marks.viewInFlight else { return [] }
        state.marks.viewOwed = false
        return [.readView(thread: state.threadId)]
    }

    /// One read at a time: a second ask while one is out is owed, not sent.
    static func askView(_ state: ChatProjection) -> (ChatProjection, [SyncEffect]) {
        var next: ChatProjection = state
        if next.marks.viewInFlight {
            next.marks.viewOwed = true
            return (next, [])
        }
        return (next, [.readView(thread: state.threadId)])
    }

    /// The runs a view no longer has (a rewind took them): a run newer than the window's oldest that the window lacks.
    static func pruned(_ runs: [String: RunTimeline], view: ThreadView) -> [String: RunTimeline] {
        guard let oldest: Double = view.runs.map(\.createdAt).min() else { return runs }
        let listed: Set<String> = Set(view.runs.map(\.id)).union(view.helpers.map(\.runId))
        return runs.filter { id, timeline in
            if listed.contains(id) { return true }
            guard let first: EventRecord = timeline.events.first else { return false }
            // Only a run of this chat is taken away by a rewind; a helper's events stay with their helper.
            let ownRun: Bool = first.data["parent_run_id"] == nil
            return !(ownRun && first.ts >= oldest)
        }
    }

    /// The run a defect is put on: the newest that is at work, else the newest held.
    private static func newestFollowedRun(_ state: ChatProjection) -> String? {
        if let working: RunRecord = state.view?.runs.last(where: \.isActive) { return working.id }
        return state.runs.max { $0.value.cursor < $1.value.cursor }?.key
    }

    /// The stored events that end the words being written of their step.
    static func endsWriting(_ event: EventRecord) -> Bool {
        ["assistant", "tool_call", "run_finished"].contains(event.kind)
    }

    /// The thinking being written goes only on the open step, as the words do (`writing`); a `reset` (the step is asked
    /// again) drops it, and a step after the one held starts it again.
    static func thinking(_ current: [String: LiveThought], delta: EventRecord, runs: [String: RunTimeline]) -> [String: LiveThought] {
        let kind: String? = delta.data["kind"]?.string
        if kind == "reset" {
            var next: [String: LiveThought] = current
            next[delta.runId] = nil
            return next
        }
        guard kind == "thinking", let piece: String = delta.data["text"]?.string, isOpen(delta, runs: runs) else { return current }
        let step: Int = delta.data["step"]?.int ?? 0
        var next: [String: LiveThought] = current
        let held: LiveThought? = current[delta.runId]
        next[delta.runId] = LiveThought(step: step, text: held?.step == step ? held!.text + piece : piece)
        return next
    }

    /// Whether a delta belongs to its run's open step: the run has not ended and the step's reply is not stored.
    static func isOpen(_ delta: EventRecord, runs: [String: RunTimeline]) -> Bool {
        let stored: [EventRecord] = runs[delta.runId]?.events ?? []
        if stored.contains(where: { $0.kind == "run_finished" }) { return false }
        if let step: Int = delta.data["step"]?.int,
            stored.contains(where: { $0.kind == "assistant" && ($0.data["step"]?.int ?? -1) >= step })
        {
            return false
        }
        return true
    }

    /// Words being written go only on the open step (plan §9): a run that has ended, or whose step was stored, takes none.
    static func writing(_ current: [String: String], delta: EventRecord, runs: [String: RunTimeline]) -> [String: String] {
        guard delta.data["kind"]?.string == "text", let piece: String = delta.data["text"]?.string else {
            if delta.data["kind"]?.string == "reset" {
                var next: [String: String] = current
                next[delta.runId] = nil
                return next
            }
            return current
        }
        // A step whose reply is stored is no longer open: words for it that come late are not drawn twice.
        guard isOpen(delta, runs: runs) else { return current }
        var next: [String: String] = current
        next[delta.runId, default: ""] += piece
        return next
    }
}

/// The thinking of a run's open step while the model writes it.
public struct LiveThought: Sendable, Equatable {
    public let step: Int
    public let text: String

    public init(step: Int, text: String) {
        self.step = step
        self.text = text
    }
}
