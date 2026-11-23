import Foundation

/// How a request that failed for the network's sake is tried again: how many tries, the wait between them (exponential
/// from `base`, at most `cap`, half of it fixed and half of it a random share so that a phone and a Mac waking together
/// do not step on each other), and the most time all the waits may take together.
struct RetryPolicy: Sendable, Equatable {
    var attempts: Int
    var base: Double
    var cap: Double
    /// Seconds the waits and the tries may take together; a read that cannot be had in this time is the cache's.
    var budget: Double

    /// A read: three tries over about ten seconds, then the screen keeps what it has.
    static let read: RetryPolicy = RetryPolicy(attempts: 3, base: 0.4, cap: 3, budget: 20)
    /// A request that is tried once (a screen that has its own button to try again).
    static let once: RetryPolicy = RetryPolicy(attempts: 1, base: 0, cap: 0, budget: 0)
    /// What the outbox sends: tried again for as long as the message is wanted, never faster than every half minute.
    static let delivery: RetryPolicy = RetryPolicy(attempts: Int.max, base: 1, cap: 30, budget: Double.infinity)

    /// The wait after the `failure`-th failure (1 for the first). `jitter` is a number in 0...1 (a random one in use).
    func wait(afterFailure failure: Int, jitter: Double) -> Duration {
        guard cap > 0 else { return .zero }
        let grown: Double = min(cap, base * pow(2, Double(max(0, failure - 1))))
        let seconds: Double = grown / 2 + grown / 2 * min(1, max(0, jitter))
        return .milliseconds(Int(seconds * 1000))
    }

    /// Whether `error` may go away by itself: the network failed, or the daemon said it was busy or broken. An answer
    /// the app cannot read, a refusal (4xx but 408 and 429) and a cancelled request will not change when asked again.
    static func isWorthRepeating(_ error: any Error) -> Bool {
        if error is CancellationError { return false }
        guard let failure: DaemonRequestError = error as? DaemonRequestError else { return false }
        if case .transport(_, let underlying) = failure {
            switch underlying.code {
            case .cancelled, .cannotParseResponse, .badURL, .unsupportedURL, .userAuthenticationRequired: return false
            default: return true
            }
        }
        return failure.isRetryable
    }

    /// Whether the request was cancelled (the screen was left, the app was told to stop): neither a failure of the network nor
    /// something to say.
    static func isCancellation(_ error: DaemonRequestError) -> Bool {
        if case .transport(_, let underlying) = error { return underlying.code == .cancelled }
        return false
    }

    /// Whether the failure says the phone has no link at all (as against a daemon that is slow or a link that drops).
    static func isNoLink(_ error: any Error) -> Bool {
        guard case DaemonRequestError.transport(_, let underlying) = error else { return false }
        return underlying.code == .notConnectedToInternet || underlying.code == .dataNotAllowed || underlying.code == .internationalRoamingOff
    }
}
