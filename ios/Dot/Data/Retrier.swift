import Foundation

/// Tries a request again as its `RetryPolicy` says. A failure that will not change (a refusal, an answer that cannot
/// be read, a cancelled task) is thrown at once. When the link is down there is no point in asking again: it waits for
/// the link (within the budget) instead of spending a try. Cancelling the task ends it between tries.
enum Retrier {
    static func run<T: Sendable>(
        _ policy: RetryPolicy, link: LinkMonitor? = nil,
        sleep: @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        jitter: @Sendable () -> Double = { Double.random(in: 0...1) },
        operation: @Sendable () async throws -> T
    ) async throws -> T {
        let began: ContinuousClock.Instant = .now
        var failures: Int = 0
        while true {
            try Task.checkCancellation()
            do {
                return try await operation()
            } catch {
                failures += 1
                guard RetryPolicy.isWorthRepeating(error), failures < policy.attempts, !Task.isCancelled else { throw error }
                let left: Double = policy.budget - began.duration(to: .now).inSeconds
                guard left > 0 else { throw error }
                if let link, !link.state.online {
                    guard await link.waitForOnline(timeout: .milliseconds(Int(min(left, 30) * 1000))) else { throw error }
                    continue
                }
                let pause: Duration = min(policy.wait(afterFailure: failures, jitter: jitter()), .milliseconds(Int(min(left, 3600) * 1000)))
                try await sleep(pause)
            }
        }
    }
}

/// One request in flight for a resource: a second ask for the same key while one runs waits for that one's end instead
/// of starting another (a pull to refresh, a change of the network and a lifecycle message arriving together read the
/// list once).
actor Coalescer {
    private var running: [String: Task<Void, Never>] = [:]

    func run(_ key: String, _ work: @escaping @Sendable () async -> Void) async {
        if let task: Task<Void, Never> = running[key] {
            await task.value
            return
        }
        let task: Task<Void, Never> = Task { await work() }
        running[key] = task
        await task.value
        if running[key] == task { running[key] = nil }
    }

    func isRunning(_ key: String) -> Bool { running[key] != nil }

    /// Ends what reads for `key` (a screen was left).
    func cancel(_ key: String) {
        running[key]?.cancel()
        running[key] = nil
    }

    func cancelAll() {
        for task in running.values { task.cancel() }
        running = [:]
    }
}

extension Duration {
    /// The duration in seconds, as a number.
    var inSeconds: Double { Double(components.seconds) + Double(components.attoseconds) / 1e18 }
}
