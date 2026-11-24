import Foundation
import XCTest

@testable import DotEngine

/// A clock the test moves by hand: nothing in a test waits on real time (plan §5).
final class ManualClock: EngineClock, @unchecked Sendable {
    private struct Sleeper {
        let id: UUID
        let until: Double
        let continuation: CheckedContinuation<Void, any Error>
    }

    private let lock: NSLock = NSLock()
    private var time: Double
    private var sleepers: [Sleeper] = []
    private var cancelled: Set<UUID> = []
    private var asked: [Double] = []
    private var listeners: [UUID: AsyncStream<Double>.Continuation] = [:]

    init(at start: Double) {
        time = start
    }

    func now() -> Double { lock.withLock { time } }

    func sleep(seconds: Double) async throws {
        guard seconds > 0 else { return }
        let id: UUID = UUID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                let (refused, tell): (Bool, [AsyncStream<Double>.Continuation]) = lock.withLock {
                    if cancelled.remove(id) != nil { return (true, []) }
                    sleepers.append(Sleeper(id: id, until: time + seconds, continuation: continuation))
                    asked.append(seconds)
                    return (false, Array(listeners.values))
                }
                if refused { continuation.resume(throwing: CancellationError()) }
                for listener in tell { listener.yield(seconds) }
            }
        } onCancel: {
            let found: Sleeper? = lock.withLock {
                if let at: Int = sleepers.firstIndex(where: { $0.id == id }) { return sleepers.remove(at: at) }
                cancelled.insert(id)
                return nil
            }
            found?.continuation.resume(throwing: CancellationError())
        }
    }

    /// Moves the clock; every sleep that is due ends.
    func advance(_ seconds: Double) {
        let due: [Sleeper] = lock.withLock {
            time += seconds
            let ready: [Sleeper] = sleepers.filter { $0.until <= time }
            sleepers.removeAll { $0.until <= time }
            return ready
        }
        for sleeper in due { sleeper.continuation.resume() }
    }

    var sleeping: Int { lock.withLock { sleepers.count } }

    /// Waits until a sleep of `seconds` was asked for after `since` sleeps had been (`asked` counts them), then moves the
    /// clock past it: the reconnect wait is due only once the supervisor waits on it. A guard of 5 s against a hang.
    func advancePastSleep(of seconds: Double, count: Int, since: Int, file: StaticString = #filePath, line: UInt = #line) async throws {
        try await waitForSleep(of: seconds, count: count, since: since, file: file, line: line)
        advance(seconds)
    }

    /// Waits until `count` sleeps of `seconds` were asked for after `since`, without moving the clock.
    func waitForSleep(of seconds: Double, count: Int, since: Int, file: StaticString = #filePath, line: UInt = #line) async throws {
        let (stream, continuation): (AsyncStream<Double>, AsyncStream<Double>.Continuation) = AsyncStream.makeStream(of: Double.self)
        let id: UUID = UUID()
        let already: Bool = lock.withLock {
            listeners[id] = continuation
            return asked.dropFirst(since).filter { abs($0 - seconds) < 0.0001 }.count >= count
        }
        defer { lock.withLock { listeners[id] = nil } }
        if !already {
            let seen: Bool = await withTaskGroup(of: Bool.self) { group in
                group.addTask {
                    for await _ in stream where self.lock.withLock({ self.asked.dropFirst(since).filter { abs($0 - seconds) < 0.0001 }.count >= count }) {
                        return true
                    }
                    return false
                }
                group.addTask {
                    try? await Task.sleep(for: .seconds(5))
                    return false
                }
                let first: Bool = await group.next() ?? false
                group.cancelAll()
                return first
            }
            if !seen {
                XCTFail("no sleep of \(seconds) s was asked for; asked: \(lock.withLock { asked })", file: file, line: line)
                throw TimedOut(what: "a sleep of \(seconds) s")
            }
        }
    }

    var debugAsked: [Double] { lock.withLock { asked } }

    /// How many sleeps were asked for so far.
    var askedCount: Int { lock.withLock { asked.count } }
}

/// A folder of its own for a test.
func scratchFolder() -> URL {
    let url: URL = FileManager.default.temporaryDirectory.appending(path: "dotengine-\(UUID().uuidString)", directoryHint: .isDirectory)
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

struct TimedOut: Error {
    let what: String
}

/// Waits for the first snapshot of `store` that `holds`. The 5 s is a guard against a hang, never a wait the test needs:
/// the store's own updates wake it.
func snapshot(
    of store: SyncStore, _ what: String, file: StaticString = #filePath, line: UInt = #line, _ holds: @escaping @Sendable (EngineSnapshot) -> Bool
) async throws -> EngineSnapshot {
    let updates: AsyncStream<EngineSnapshot> = await store.updates()
    let found: EngineSnapshot? = await withTaskGroup(of: EngineSnapshot?.self) { group in
        group.addTask {
            for await snapshot in updates where holds(snapshot) { return snapshot }
            return nil
        }
        group.addTask {
            try? await Task.sleep(for: .seconds(5))
            return nil
        }
        let first: EngineSnapshot? = await group.next() ?? nil
        group.cancelAll()
        return first
    }
    guard let found else {
        let last: EngineSnapshot = await store.snapshot
        XCTFail("timed out waiting for: \(what); last transport=\(last.transport) chats=\(last.chats.mapValues(\.freshness))", file: file, line: line)
        throw TimedOut(what: what)
    }
    return found
}

/// Waits until `holds` is true of the fake daemon (it tells every change of what it saw). A guard of 5 s against a hang.
func daemonState(
    _ daemon: FakeDaemon, _ what: String, file: StaticString = #filePath, line: UInt = #line, _ holds: @escaping @Sendable (FakeDaemon) -> Bool
) async throws {
    let changes: AsyncStream<Void> = daemon.changes()
    let ok: Bool = await withTaskGroup(of: Bool.self) { group in
        group.addTask {
            if holds(daemon) { return true }
            for await _ in changes where holds(daemon) { return true }
            return false
        }
        group.addTask {
            try? await Task.sleep(for: .seconds(5))
            return false
        }
        let first: Bool = await group.next() ?? false
        group.cancelAll()
        return first
    }
    if !ok {
        XCTFail("timed out waiting for the daemon: \(what); requests=\(daemon.requests.map { "\($0.method) \($0.path)" })", file: file, line: line)
        throw TimedOut(what: what)
    }
}

/// Lets the actors run what is ready (the frames a fake stream just wrote): a number of yields, no time.
func settle() async {
    for _ in 0..<50 { await Task.yield() }
}

/// Builds an engine on a fake daemon, a manual clock and a scratch folder.
struct Rig {
    let daemon: FakeDaemon
    let clock: ManualClock
    let link: LinkWatcher
    let folder: URL
    let engine: Engine

    init(daemon: FakeDaemon, folder: URL) {
        self.daemon = daemon
        self.clock = ManualClock(at: 1_800_000_000)
        self.link = LinkWatcher(initial: .up)
        self.folder = folder
        self.engine = Engine(api: daemon.api(), folder: folder, link: link, clock: clock, jitter: { 0 })
    }

    var store: SyncStore { engine.store }
    var supervisor: Supervisor { engine.supervisor }
}
