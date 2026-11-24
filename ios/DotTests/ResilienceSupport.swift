import Foundation
import XCTest
@testable import Dot

/// Waits (polling) until `condition` holds, at most `timeout` seconds; true when it did.
func until(
    _ timeout: Double = 5, every: Double = 0.01, isolation: isolated (any Actor)? = #isolation, _ condition: () async -> Bool
) async -> Bool {
    let began: ContinuousClock.Instant = .now
    while began.duration(to: .now).inSeconds < timeout {
        if await condition() { return true }
        try? await Task.sleep(for: .seconds(every))
    }
    return await condition()
}

/// A retry policy that waits milliseconds, for tests of what comes after the waits.
let quickRetry: RetryPolicy = RetryPolicy(attempts: Int.max, base: 0.01, cap: 0.05, budget: Double.infinity)
let quickRead: RetryPolicy = RetryPolicy(attempts: 4, base: 0.01, cap: 0.04, budget: 5)

/// A folder of its own, removed when the test ends.
func scratchFolder(_ name: String = "dot-test") -> URL {
    let url: URL = FileManager.default.temporaryDirectory.appending(path: "\(name)-\(UUID().uuidString)", directoryHint: .isDirectory)
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// What a chat session has told so far, read by the test.
final class SessionLog: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var states: [ChatState] = []
    private var connections: [FeedState] = []
    private var firstAt: ContinuousClock.Instant?
    let began: ContinuousClock.Instant = .now

    var latest: ChatState? {
        lock.lock()
        defer { lock.unlock() }
        return states.last
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return states.count
    }

    var lastEventId: Int { latest?.lastEventId ?? 0 }

    var lastConnection: FeedState? {
        lock.lock()
        defer { lock.unlock() }
        return connections.last
    }

    func add(_ output: ChatOutput) {
        lock.lock()
        defer { lock.unlock() }
        switch output {
        case .state(let state):
            states.append(state)
            if firstAt == nil, !state.items.isEmpty { firstAt = .now }
        case .connection(let state): connections.append(state)
        default: break
        }
    }

    /// Seconds from the start of the log to the first state that had rows.
    var secondsToFirstRows: Double? { lock.withLock { firstAt.map { began.duration(to: $0).inSeconds } } }

    /// Reads a session's outputs into this log until the stream ends.
    func follow(_ session: ChatSession) async -> Task<Void, Never> {
        let stream: AsyncStream<ChatOutput> = await session.start()
        return Task { for await output in stream { self.add(output) } }
    }
}

extension FakeDaemon {
    /// Stores written for a chat read by a session: warms the disk as a good session leaves it.
    func warm(_ directory: URL, thread: String = "thr_1") async {
        let cache: ChatCache = ChatCache(directory: directory)
        let session: ChatSession = ChatSession(threadId: thread, api: api(), cache: cache)
        let log: SessionLog = SessionLog()
        let reading: Task<Void, Never> = await log.follow(session)
        let target: Int = newestEventId(thread: thread)
        _ = await until(10) { log.lastEventId >= target }
        await session.stop()
        reading.cancel()
        _ = await until(5) { await cache.loadChat(thread) != nil }
    }
}

/// Fails the test when `condition` does not come true within `timeout` seconds.
func expectSoon(
    _ timeout: Double = 5, _ message: String = "it did not come true in time", file: StaticString = #filePath, line: UInt = #line,
    isolation: isolated (any Actor)? = #isolation, _ condition: () async -> Bool
) async {
    let ok: Bool = await until(timeout, isolation: isolation, condition)
    XCTAssertTrue(ok, message, file: file, line: line)
}
