import Foundation

// What the owner does to a held run's page from the phone (`POST /api/runs/{id}/input`, `dotagent/browser/remote_input.py`):
// pointer, wheel and key events, each place a fraction of the picture, which the page's own size turns into a point, so
// that the cast's scaling does not move it. The same events the web's `control.js` sends.

/// A mouse button of the page.
public enum RemoteButton: String, Codable, Sendable, Equatable {
    case left
    case right
}

/// A place on the picture: 0 at its left or top edge, 1 at its right or bottom edge.
public struct PictureFraction: Codable, Sendable, Equatable {
    public let x: Double
    public let y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

/// One event of a batch the daemon takes.
public enum RemoteInputEvent: Sendable, Equatable, Encodable {
    case move(PictureFraction)
    case down(PictureFraction, button: RemoteButton, clicks: Int)
    case up(PictureFraction, button: RemoteButton, clicks: Int)
    /// A scroll by `dx`, `dy` CSS pixels at a place.
    case wheel(PictureFraction, dx: Double, dy: Double)
    /// A key or chord as Playwright names it ("Enter", "Backspace", "Shift+Tab").
    case key(String)
    /// Text typed (at most `remoteTextLimit` characters an event).
    case text(String)

    enum CodingKeys: String, CodingKey {
        case kind, x, y, button, clicks, dx, dy, key, text
    }

    public func encode(to encoder: any Encoder) throws {
        var out: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .move(let at):
            try out.encode("move", forKey: .kind)
            try out.encode(at.x, forKey: .x)
            try out.encode(at.y, forKey: .y)
        case .down(let at, let button, let clicks), .up(let at, let button, let clicks):
            try out.encode(isDown ? "down" : "up", forKey: .kind)
            try out.encode(at.x, forKey: .x)
            try out.encode(at.y, forKey: .y)
            try out.encode(button, forKey: .button)
            try out.encode(clicks, forKey: .clicks)
        case .wheel(let at, let dx, let dy):
            try out.encode("wheel", forKey: .kind)
            try out.encode(at.x, forKey: .x)
            try out.encode(at.y, forKey: .y)
            try out.encode(dx, forKey: .dx)
            try out.encode(dy, forKey: .dy)
        case .key(let key):
            try out.encode("key", forKey: .kind)
            try out.encode(key, forKey: .key)
        case .text(let text):
            try out.encode("text", forKey: .kind)
            try out.encode(text, forKey: .text)
        }
    }

    private var isDown: Bool {
        if case .down = self { return true }
        return false
    }
}

/// The most events one batch carries, and the most text one event carries (the daemon's own limits).
public let remoteBatchLimit: Int = 64
public let remoteTextLimit: Int = 500
/// The farthest one wheel event scrolls either way, in CSS pixels (the daemon's limit).
public let remoteWheelLimit: Double = 4000

/// What the page took: how many events, and the page's address and size in CSS pixels at the time.
public struct RemoteInputResult: Codable, Sendable, Equatable {
    public let accepted: Int
    public let url: String
    public let width: Int
    public let height: Int

    public init(accepted: Int, url: String, width: Int, height: Int) {
        self.accepted = accepted
        self.url = url
        self.width = width
        self.height = height
    }
}

struct RemoteInputBatch: Encodable, Sendable {
    let events: [RemoteInputEvent]
}

/// A tap on the picture as the page takes it: the pointer moves there, presses and lets go.
public func tapEvents(at place: PictureFraction, button: RemoteButton, clicks: Int) -> [RemoteInputEvent] {
    [.move(place), .down(place, button: button, clicks: clicks), .up(place, button: button, clicks: clicks)]
}

/// Text cut into events the daemon takes (each at most `remoteTextLimit` characters).
public func textEvents(_ text: String) -> [RemoteInputEvent] {
    var events: [RemoteInputEvent] = []
    var rest: Substring = Substring(text)
    while !rest.isEmpty {
        events.append(.text(String(rest.prefix(remoteTextLimit))))
        rest = rest.dropFirst(remoteTextLimit)
    }
    return events
}

/// The scroll of the page for a finger's drag of `dx`, `dy` points on a picture drawn `drawnWidth` points wide of a
/// page `pageWidth` CSS pixels wide: the page moves with the finger (a drag up scrolls down), as far as the finger went
/// on the page's own scale, within the daemon's limit.
public func scrollFor(dx: Double, dy: Double, drawnWidth: Double, pageWidth: Double) -> (dx: Double, dy: Double) {
    guard drawnWidth > 0, pageWidth > 0 else { return (0, 0) }
    let scale: Double = pageWidth / drawnWidth
    func clamp(_ value: Double) -> Double { min(remoteWheelLimit, max(-remoteWheelLimit, value)) }
    return (clamp(-dx * scale), clamp(-dy * scale))
}

/// The batches `events` go in, in order, each at most `remoteBatchLimit` events.
public func inputBatches(_ events: [RemoteInputEvent]) -> [[RemoteInputEvent]] {
    stride(from: 0, to: events.count, by: remoteBatchLimit).map { Array(events[$0..<min($0 + remoteBatchLimit, events.count)]) }
}
