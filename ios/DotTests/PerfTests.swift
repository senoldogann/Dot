import XCTest
@testable import Dot

/// Time to first content, measured against a `FakeDaemon` whose network can be made as bad as the plan names. Two
/// moments matter on a phone: when something is on the screen (drawn from the disk) and when what is on the screen is
/// what the Mac has now. Each test writes its numbers as lines `PERF <scenario> <metric> <seconds|none>` into the log
/// and, when `DOT_PERF_REPORT` names a file (give it as `TEST_RUNNER_DOT_PERF_REPORT` to `xcodebuild`), into that file.
///
/// `DOT_PERF_SCALE` (`TEST_RUNNER_DOT_PERF_SCALE`) multiplies the network's waits: the default 0.05 turns 2 to 8 s
/// into 0.1 to 0.4 s so that the suite stays quick; 1 is the real profile of the plan.
@MainActor
final class PerfTests: XCTestCase {
    private var scale: Double { ProcessInfo.processInfo.environment["DOT_PERF_SCALE"].flatMap(Double.init) ?? 0.05 }
    private var folders: [URL] = []

    override func tearDown() async throws {
        try? await Task.sleep(for: .milliseconds(700))
        for folder in folders { try? FileManager.default.removeItem(at: folder) }
        folders = []
    }

    private func folder() -> URL {
        let url: URL = FileManager.default.temporaryDirectory.appending(path: "dot-perf-\(UUID().uuidString)", directoryHint: .isDirectory)
        folders.append(url)
        return url
    }

    private func note(_ scenario: String, _ metric: String, _ seconds: Double?) {
        let value: String = seconds.map { String(format: "%.4f", $0) } ?? "none"
        let line: String = "PERF \(scenario) \(metric) \(value)"
        print(line)
        guard let path: String = ProcessInfo.processInfo.environment["DOT_PERF_REPORT"] else { return }
        let url: URL = URL(fileURLWithPath: path)
        let data: Data = Data((line + "\n").utf8)
        if let handle: FileHandle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: url)
        }
    }

    /// A daemon with a long chat (12 runs of 40 steps, about a thousand events) and eleven shorter ones.
    private func busyDaemon() -> FakeDaemon {
        let daemon: FakeDaemon = FakeDaemon()
        daemon.addAgent()
        daemon.addChat("thr_1", title: "The long chat", runCount: 12, steps: 40, updatedAt: 5_000)
        for number in 2...12 { daemon.addChat("thr_\(number)", title: "Chat \(number)", runCount: 2, steps: 3, updatedAt: 5_000 - Double(number)) }
        return daemon
    }

    /// What the phone holds after a good session: the list and the long chat on disk.
    private func warmDisk(_ daemon: FakeDaemon, in directory: URL) async {
        let store: NativeStore = NativeStore(host: daemon.host, key: "k", session: daemon.session(), directory: directory)
        await store.refreshList()
        let session: ChatSession = ChatSession(threadId: "thr_1", api: daemon.api(), cache: store.cache)
        let target: Int = daemon.newestEventId()
        _ = await Self.watch(session, timeout: 20) { $0.lastEventId >= target }
        await session.stop()
        try? await Task.sleep(for: .milliseconds(600))
    }

    /// Opens a chat as the screen does and says when the first rows were there and when the rows were the Mac's.
    private static func watch(
        _ session: ChatSession, timeout: Double, fresh: @escaping @Sendable (ChatState) -> Bool
    ) async -> (first: Double?, fresh: Double?) {
        let began: ContinuousClock.Instant = .now
        let stream: AsyncStream<ChatOutput> = await session.start()
        let first: LockedValue<Double?> = LockedValue(nil)
        let current: LockedValue<Double?> = LockedValue(nil)
        await withTaskGroup(of: Void.self) { group in
            group.addTask {
                for await output in stream {
                    guard case .state(let state) = output else { continue }
                    let at: Double = began.duration(to: .now).inSeconds
                    if first.value == nil, !state.items.isEmpty { first.set(at) }
                    if first.value != nil, fresh(state) {
                        current.set(at)
                        return
                    }
                }
            }
            group.addTask { try? await Task.sleep(for: .seconds(timeout)) }
            // The first of the two to finish: a reader that saw both marks, or the clock.
            await group.next()
            group.cancelAll()
            await session.stop()
        }
        return (first.value, current.value)
    }

    // MARK: Scenarios

    private func launch(_ name: String, faults: NetworkFaults, stale: Bool) async {
        let directory: URL = folder()
        let daemon: FakeDaemon = busyDaemon()
        await warmDisk(daemon, in: directory)
        if stale {
            // The app was killed; meanwhile the Mac went on: a run finished and a chat was added.
            for step in 0..<10 { daemon.say("tool_call", ["call_id": .string("late\(step)"), "tool": .string("shell"), "args": .object([:])]) }
            daemon.addChat("thr_13", title: "New chat", runCount: 1, steps: 1, updatedAt: 9_000)
        }
        daemon.set(faults)
        let newest: Int = daemon.newestEventId()
        let began: ContinuousClock.Instant = .now
        let store: NativeStore = NativeStore(host: daemon.host, key: "k", session: daemon.session(), directory: directory)
        while store.threads.isEmpty, began.duration(to: .now) < .seconds(5) { try? await Task.sleep(for: .milliseconds(1)) }
        note(name, "list_on_screen_s", store.threads.isEmpty ? nil : began.duration(to: .now).inSeconds)
        let reading: Task<Void, Never> = Task { await store.refreshList() }
        let session: ChatSession = ChatSession(threadId: "thr_1", api: store.api, cache: store.cache)
        let marks = await Self.watch(session, timeout: max(3, 60 * scale)) { $0.lastEventId >= newest }
        note(name, "chat_on_screen_s", marks.first)
        note(name, "chat_current_s", marks.fresh)
        await reading.value
        XCTAssertNotNil(marks.first, "\(name): the chat must be on the screen from the disk")
        store.stop()
    }

    func testColdLaunchWithAWarmDiskOnAGoodNetwork() async { await launch("cold_good", faults: .good, stale: false) }

    func testRelaunchAfterTheAppWasKilledOnAGoodNetwork() async { await launch("relaunch_good", faults: .good, stale: true) }

    func testColdLaunchOnTheWorstNetwork() async { await launch("cold_bad", faults: .bad(scale: scale), stale: false) }

    func testRelaunchAfterKillOnTheWorstNetwork() async { await launch("relaunch_bad", faults: .bad(scale: scale), stale: true) }

    func testColdLaunchOffline() async { await launch("cold_offline", faults: .down, stale: false) }

    /// The app was in the background for ten minutes: its streams are dead and the Mac went on. Time from the app
    /// coming to the front to the chat being the Mac's.
    private func foreground(_ name: String, faults: NetworkFaults) async {
        let directory: URL = folder()
        let daemon: FakeDaemon = busyDaemon()
        daemon.say("assistant", ["step": .int(99), "text": .string("started")])
        let store: NativeStore = NativeStore(host: daemon.host, key: "k", session: daemon.session(), directory: directory)
        await store.refreshList()
        let session: ChatSession = ChatSession(threadId: "thr_1", api: store.api, cache: store.cache)
        let stream: AsyncStream<ChatOutput> = await session.start()
        let seen: LockedValue<Int> = LockedValue(0)
        let reader: Task<Void, Never> = Task {
            for await output in stream { if case .state(let state) = output { seen.set(state.lastEventId) } }
        }
        try? await Task.sleep(for: .milliseconds(800))
        // Ten minutes pass with the phone asleep: the Mac says more, the network may be bad when the phone wakes.
        for step in 0..<30 { daemon.say("tool_call", ["call_id": .string("bg\(step)"), "tool": .string("shell"), "args": .object([:])]) }
        daemon.dropStreams()
        daemon.set(faults)
        let newest: Int = daemon.newestEventId()
        let began: ContinuousClock.Instant = .now
        await session.kick()
        let limit: Duration = .seconds(max(3, 60 * scale))
        while seen.value < newest, began.duration(to: .now) < limit { try? await Task.sleep(for: .milliseconds(5)) }
        note(name, "chat_current_s", seen.value >= newest ? began.duration(to: .now).inSeconds : nil)
        reader.cancel()
        await session.stop()
        store.stop()
    }

    func testForegroundAfterTenMinutesOnAGoodNetwork() async { await foreground("foreground_good", faults: .good) }

    func testForegroundAfterTenMinutesOnTheWorstNetwork() async { await foreground("foreground_bad", faults: .bad(scale: scale)) }
}

/// A value read from one task and written by another.
final class LockedValue<Value: Sendable>: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var stored: Value
    init(_ value: Value) { stored = value }
    var value: Value {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }
    func set(_ next: Value) {
        lock.lock()
        stored = next
        lock.unlock()
    }
}
