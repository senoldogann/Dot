import Foundation

/// Time as the engine reads and waits on it, injected so that the tests move it by hand and never sleep (plan §5).
public protocol EngineClock: Sendable {
    /// Seconds since 1970.
    func now() -> Double
    /// Waits `seconds`; throws `CancellationError` when the task is cancelled.
    func sleep(seconds: Double) async throws
}

/// The system's clock.
public struct SystemClock: EngineClock {
    public init() {}

    public func now() -> Double { Date().timeIntervalSince1970 }

    public func sleep(seconds: Double) async throws {
        try await Task.sleep(for: .milliseconds(Int(max(0, seconds) * 1000)))
    }
}

/// The supervisor's numbers (R-f §4 #3).
public enum StreamRules {
    /// The new streams write a keepalive every 5 s: one silent for 12 s is broken.
    public static let silenceLimit: Double = 12
    /// A stream whose last byte is younger than this is alive when the app comes back.
    public static let probeAge: Double = 6
    /// The backoff starts again only after a connection stayed up this long.
    public static let stableAfter: Double = 30
    /// Away at least this long, every stream is opened again from its cursors on return.
    public static let resubscribeAfter: Double = 10
    public static let firstWait: Double = 0.5
    public static let longestWait: Double = 15
}

/// The wait before the `attempt`-th try to open a stream again (1 for the first): from 0.5 s doubling up to 15 s, plus up
/// to a fifth of it at random (`jitter` in 0...1), so that streams that broke together do not knock together. Pure.
public func reconnectWait(attempt: Int, jitter: Double) -> Double {
    let grown: Double = min(StreamRules.longestWait, StreamRules.firstWait * pow(2, Double(max(0, attempt - 1))))
    return grown * (1 + 0.2 * min(1, max(0, jitter)))
}
