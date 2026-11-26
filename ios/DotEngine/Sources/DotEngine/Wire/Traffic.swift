import Foundation

/// What the engine said to the daemon and heard back since the app started: the bytes of the bodies (requests sent,
/// answers and stream lines read; headers are not counted), the requests made, the streams opened and those that broke.
/// The app's own performance record (Settings › Maintenance) and the streaming check read it; nothing else does.
///
/// One counter for the process: every `DaemonAPI` and the supervisor's stream readers add to it. Counting is a lock and
/// an addition, so a stream's line costs nothing that shows.
public final class Traffic: @unchecked Sendable {
    public static let shared: Traffic = Traffic()

    private let lock: NSLock = NSLock()
    private var held: TrafficCounts = TrafficCounts(bytesIn: 0, bytesOut: 0, requests: 0, streamOpens: 0, streamBreaks: 0)

    public init() {}

    /// The counts as they stand.
    public var counts: TrafficCounts { lock.withLock { held } }

    func received(_ bytes: Int) {
        lock.withLock { held = held.adding(TrafficCounts(bytesIn: bytes, bytesOut: 0, requests: 0, streamOpens: 0, streamBreaks: 0)) }
    }

    func requested(sending bytes: Int) {
        lock.withLock { held = held.adding(TrafficCounts(bytesIn: 0, bytesOut: bytes, requests: 1, streamOpens: 0, streamBreaks: 0)) }
    }

    func streamOpened() {
        lock.withLock { held = held.adding(TrafficCounts(bytesIn: 0, bytesOut: 0, requests: 0, streamOpens: 1, streamBreaks: 0)) }
    }

    func streamBroke() {
        lock.withLock { held = held.adding(TrafficCounts(bytesIn: 0, bytesOut: 0, requests: 0, streamOpens: 0, streamBreaks: 1)) }
    }
}

/// The counts of `Traffic` at one moment.
public struct TrafficCounts: Codable, Equatable, Sendable {
    public let bytesIn: Int
    public let bytesOut: Int
    public let requests: Int
    public let streamOpens: Int
    public let streamBreaks: Int

    public init(bytesIn: Int, bytesOut: Int, requests: Int, streamOpens: Int, streamBreaks: Int) {
        self.bytesIn = bytesIn
        self.bytesOut = bytesOut
        self.requests = requests
        self.streamOpens = streamOpens
        self.streamBreaks = streamBreaks
    }

    /// The sum of both. Pure.
    public func adding(_ other: TrafficCounts) -> TrafficCounts {
        TrafficCounts(
            bytesIn: bytesIn + other.bytesIn, bytesOut: bytesOut + other.bytesOut, requests: requests + other.requests,
            streamOpens: streamOpens + other.streamOpens, streamBreaks: streamBreaks + other.streamBreaks)
    }

    /// What was counted since `earlier`. Pure.
    public func since(_ earlier: TrafficCounts) -> TrafficCounts {
        TrafficCounts(
            bytesIn: bytesIn - earlier.bytesIn, bytesOut: bytesOut - earlier.bytesOut, requests: requests - earlier.requests,
            streamOpens: streamOpens - earlier.streamOpens, streamBreaks: streamBreaks - earlier.streamBreaks)
    }

    enum CodingKeys: String, CodingKey {
        case bytesIn = "bytes_in"
        case bytesOut = "bytes_out"
        case requests
        case streamOpens = "stream_opens"
        case streamBreaks = "stream_breaks"
    }
}
