import Foundation

/// One frame of a chat's or the list's stream, read (plan §9). Every frame is an event as `/api/runs/{id}/stream` sends
/// it; the stream's own kinds (`thread_changed`, `synchronized`) carry their payload in the event's `data`, and
/// `resync` carries none.
public enum StreamFrame: Sendable, Equatable {
    /// A stored event of a run (id > 0).
    case event(EventRecord)
    /// Words being written (id 0, kind `delta`): they only add to the step being written.
    case delta(EventRecord)
    /// Another live-only event (id 0) the store has no rule for: dropped.
    case live(EventRecord)
    case threadChanged(ThreadChanged)
    case synchronized(Synchronized)
    /// The list's stream fell behind: read the list again.
    case resync
    /// A frame that does not read as the contract says. `id` is its SSE id when it had one, so the run's cursor can go
    /// past it; the view is read again (plan §9: a decode defect = re-read).
    case defect(id: Int?, kind: String?)
}

/// The stream's own kinds, as `thread_sync.py` names them.
public enum StreamKind {
    public static let threadChanged: String = "thread_changed"
    public static let synchronized: String = "synchronized"
    public static let resync: String = "resync"
    public static let delta: String = "delta"
}

/// Reads one SSE message as a frame. Pure.
public func readFrame(_ message: SSEMessage) -> StreamFrame {
    let sseId: Int? = message.id.flatMap { Int($0) }
    if message.event == StreamKind.resync { return .resync }
    let decoder: JSONDecoder = JSONDecoder()
    guard let data: Data = message.data.data(using: .utf8), let event: EventRecord = try? decoder.decode(EventRecord.self, from: data) else {
        return .defect(id: sseId, kind: message.event)
    }
    switch event.kind {
    case StreamKind.threadChanged:
        guard let changed: ThreadChanged = decodeData(ThreadChanged.self, event.data) else { return .defect(id: sseId, kind: event.kind) }
        return .threadChanged(changed)
    case StreamKind.synchronized:
        guard let synced: Synchronized = decodeData(Synchronized.self, event.data) else { return .defect(id: sseId, kind: event.kind) }
        return .synchronized(synced)
    default:
        if event.id > 0 { return .event(event) }
        return event.kind == StreamKind.delta ? .delta(event) : .live(event)
    }
}

private func decodeData<T: Decodable>(_ type: T.Type, _ data: [String: JSONValue]) -> T? {
    guard let encoded: Data = try? JSONEncoder().encode(data) else { return nil }
    return try? JSONDecoder().decode(type, from: encoded)
}
