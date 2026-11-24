import XCTest
@testable import Dot

/// The rules of the activity lease (item 1440): when the app says it is here, and what it says. No clock and no network:
/// each case hands the time in.
final class ActivityLeaseRulesTests: XCTestCase {
    private func rules(paired: Bool = true) -> ActivityLeaseRules { ActivityLeaseRules(paired: paired) }

    func testTheSceneBecomingActiveSaysForegroundAtOnce() {
        var lease: ActivityLeaseRules = rules()
        let report: ActivityReport? = lease.apply(.scene(.active), now: 100)
        XCTAssertEqual(report, ActivityReport(state: .foreground, chat: nil, ttl: 45))
        XCTAssertTrue(lease.held)
    }

    func testTheLeaseIsSaidAgainEvery25SecondsWhileTheSceneStaysActive() {
        var lease: ActivityLeaseRules = rules()
        _ = lease.apply(.scene(.active), now: 100)
        XCTAssertEqual(lease.secondsToRenewal(now: 100), 25)
        XCTAssertEqual(lease.secondsToRenewal(now: 110), 15)
        XCTAssertEqual(lease.secondsToRenewal(now: 140), 0, "a renewal that is late is due now, not never")
        XCTAssertEqual(lease.apply(.renewal, now: 125)?.state, .foreground)
        XCTAssertEqual(lease.secondsToRenewal(now: 125), 25, "the 25 seconds count from the last one said")
    }

    func testTheLeaseOutlivesOneRenewalThatDoesNotGetThrough() {
        XCTAssertGreaterThan(Double(ActivityLeaseRules.ttl), ActivityLeaseRules.renewEvery, "one lost renewal must not end it")
        XCTAssertLessThan(Double(ActivityLeaseRules.ttl), 2 * ActivityLeaseRules.renewEvery, "and a lost app must not hold it long")
    }

    func testInactiveEndsTheLeaseAtOnceAndBackgroundAfterItSaysNothingMore() {
        var lease: ActivityLeaseRules = rules()
        _ = lease.apply(.scene(.active), now: 0)
        let ended: ActivityReport? = lease.apply(.scene(.inactive), now: 5)
        XCTAssertEqual(ended?.state, .background)
        XCTAssertNil(ended?.chat)
        XCTAssertFalse(lease.held)
        XCTAssertNil(lease.apply(.scene(.background), now: 6), "the lease was ended once: a second background says nothing new")
        XCTAssertNil(lease.secondsToRenewal(now: 6), "no renewal is due while the app is out of sight")
        XCTAssertNil(lease.apply(.renewal, now: 30), "and a renewal that fires late says nothing")
    }

    func testBackgroundWithoutALeaseSaysNothing() {
        var lease: ActivityLeaseRules = rules()
        XCTAssertNil(lease.apply(.scene(.background), now: 0))
        XCTAssertNil(lease.apply(.scene(.inactive), now: 1))
    }

    func testAnActiveSceneAfterAnInactiveOneSaysForegroundAgain() {
        var lease: ActivityLeaseRules = rules()
        _ = lease.apply(.scene(.active), now: 0)
        _ = lease.apply(.scene(.inactive), now: 3)
        XCTAssertEqual(lease.apply(.scene(.active), now: 4)?.state, .foreground, "back in front after a system sheet")
        XCTAssertEqual(lease.secondsToRenewal(now: 4), 25)
    }

    func testTheChatOnTheScreenChangingSaysForegroundAtOnceWithTheNewChat() {
        var lease: ActivityLeaseRules = rules()
        _ = lease.apply(.scene(.active), now: 0)
        let opened: ActivityReport? = lease.apply(.chat("thr_1"), now: 10)
        XCTAssertEqual(opened, ActivityReport(state: .foreground, chat: "thr_1", ttl: 45))
        XCTAssertEqual(lease.secondsToRenewal(now: 10), 25, "a report said now is a renewal too")
        XCTAssertNil(lease.apply(.chat("thr_1"), now: 11), "the same chat again says nothing")
        XCTAssertEqual(lease.apply(.chat("thr_2"), now: 12)?.chat, "thr_2")
        XCTAssertEqual(
            lease.apply(.chat(nil), now: 13), ActivityReport(state: .foreground, chat: nil, ttl: 45), "back to the list: no chat")
        XCTAssertEqual(lease.apply(.renewal, now: 38), ActivityReport(state: .foreground, chat: nil, ttl: 45))
    }

    func testAChatOpenedWhileTheAppIsNotActiveIsKeptAndRidesTheNextForeground() {
        var lease: ActivityLeaseRules = rules()
        XCTAssertNil(lease.apply(.chat("thr_9"), now: 0), "nothing is said for an app that is not in front")
        XCTAssertEqual(lease.apply(.scene(.active), now: 1)?.chat, "thr_9")
    }

    func testAChatIdTheDaemonWouldRefuseIsNotSent() {
        XCTAssertNil(ActivityLeaseRules.valid(chat: ""))
        XCTAssertNil(ActivityLeaseRules.valid(chat: String(repeating: "x", count: 65)))
        XCTAssertEqual(ActivityLeaseRules.valid(chat: String(repeating: "x", count: 64))?.count, 64)
        var lease: ActivityLeaseRules = rules()
        _ = lease.apply(.scene(.active), now: 0)
        XCTAssertNil(lease.apply(.chat(String(repeating: "x", count: 65)), now: 1), "an id that is not valid is no change from none")
    }

    func testAnUnpairedAppSaysNothingAtAll() {
        var lease: ActivityLeaseRules = rules(paired: false)
        XCTAssertNil(lease.apply(.scene(.active), now: 0))
        XCTAssertNil(lease.apply(.chat("thr_1"), now: 1))
        XCTAssertNil(lease.apply(.renewal, now: 30))
        XCTAssertNil(lease.apply(.scene(.inactive), now: 31), "not even a background: it has no key to say it with")
        XCTAssertNil(lease.secondsToRenewal(now: 31))
    }

    func testPairingWhileTheAppIsInFrontStartsTheLeaseAndForgettingTheKeyEndsIt() {
        var lease: ActivityLeaseRules = rules(paired: false)
        _ = lease.apply(.scene(.active), now: 0)
        XCTAssertEqual(lease.apply(.paired(true), now: 5)?.state, .foreground)
        XCTAssertNil(lease.apply(.paired(true), now: 6), "paired again is no change")
        XCTAssertNil(lease.apply(.paired(false), now: 7), "an app that lost its key says nothing")
        XCTAssertNil(lease.secondsToRenewal(now: 7))
        XCTAssertFalse(lease.held)
    }

    func testTheReportIsTheJSONTheDaemonTakes() throws {
        let encoder: JSONEncoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        let shown: String = String(decoding: try encoder.encode(ActivityReport(state: .foreground, chat: "thr_1", ttl: 45)), as: UTF8.self)
        XCTAssertEqual(shown, #"{"chat":"thr_1","state":"foreground","ttl_s":45}"#)
        let none: String = String(decoding: try encoder.encode(ActivityReport(state: .background, chat: nil, ttl: 45)), as: UTF8.self)
        XCTAssertEqual(none, #"{"chat":null,"state":"background","ttl_s":45}"#, "no chat is said as null")
    }
}

/// A clock the test moves by hand, and the waits that go with it.
final class ManualClock: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var current: Double = 1_000
    private var sleepers: [(id: UUID, due: Double, resume: @Sendable (Bool) -> Void)] = []

    var now: Double {
        lock.lock()
        defer { lock.unlock() }
        return current
    }

    func sleep(_ seconds: Double) async -> Bool {
        let id: UUID = UUID()
        return await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
                lock.lock()
                sleepers.append((id, current + seconds, { continuation.resume(returning: $0) }))
                lock.unlock()
            }
        } onCancel: {
            lock.lock()
            let found: (id: UUID, due: Double, resume: @Sendable (Bool) -> Void)? = sleepers.first { $0.id == id }
            sleepers.removeAll { $0.id == id }
            lock.unlock()
            found?.resume(false)
        }
    }

    func advance(by seconds: Double) {
        lock.lock()
        current += seconds
        let due: [(id: UUID, due: Double, resume: @Sendable (Bool) -> Void)] = sleepers.filter { $0.due <= current }
        sleepers.removeAll { $0.due <= current }
        lock.unlock()
        for sleeper in due { sleeper.resume(true) }
    }
}

/// What the sender was given, in order.
final class Said: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var all: [ActivityReport] = []

    func add(_ report: ActivityReport) {
        lock.lock()
        all.append(report)
        lock.unlock()
    }

    var reports: [ActivityReport] {
        lock.lock()
        defer { lock.unlock() }
        return all
    }
}

/// A door a send waits at until the test opens it: a link too poor to answer.
actor Gate {
    private var isOpen: Bool = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if isOpen { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        isOpen = true
        for waiter in waiters { waiter.resume() }
        waiters = []
    }
}

/// The scheduler of the lease (item 1440): the rules above with a clock, a timer and a sender that the test replaces.
@MainActor
final class ActivityLeaseSchedulerTests: XCTestCase {
    /// Time for what must NOT happen to have had its chance.
    private func settle() async throws { try await Task.sleep(for: .milliseconds(60)) }

    /// Waits (up to two seconds) until the sender has been given `count` reports.
    private func until(_ said: Said, count: Int) async throws {
        for _ in 0..<200 {
            if said.reports.count >= count { return }
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    private func make(paired: Bool = true, clock: ManualClock, said: Said, gate: Gate? = nil) -> ActivityLease {
        ActivityLease(
            paired: paired, clock: { clock.now }, sleep: { await clock.sleep($0) },
            send: { report in
                said.add(report)
                await gate?.wait()
            })
    }

    func testAnActiveAppSaysItIsHereEvery25SecondsAndStopsAtOnceWhenItLeaves() async throws {
        let clock: ManualClock = ManualClock()
        let said: Said = Said()
        let lease: ActivityLease = make(clock: clock, said: said)
        lease.scene(.active)
        try await until(said, count: 1)
        XCTAssertEqual(said.reports.map(\.state), [.foreground])
        clock.advance(by: 24)
        try await settle()
        XCTAssertEqual(said.reports.count, 1, "24 seconds is not yet a renewal")
        clock.advance(by: 1)
        try await until(said, count: 2)
        XCTAssertEqual(said.reports.count, 2)
        clock.advance(by: 25)
        try await until(said, count: 3)
        XCTAssertEqual(said.reports.count, 3)
        lease.scene(.inactive)
        try await until(said, count: 4)
        XCTAssertEqual(said.reports.last?.state, .background, "the lease ends the moment the scene goes inactive")
        clock.advance(by: 300)
        try await settle()
        XCTAssertEqual(said.reports.count, 4, "nothing more is said after it left, however long it stays away")
        XCTAssertFalse(lease.isHeld)
    }

    func testTheChatOnTheScreenIsSaidAtOnceAndTheRenewalCountsFromThere() async throws {
        let clock: ManualClock = ManualClock()
        let said: Said = Said()
        let lease: ActivityLease = make(clock: clock, said: said)
        lease.scene(.active)
        try await until(said, count: 1)
        clock.advance(by: 20)
        lease.showing(chat: "thr_7")
        try await until(said, count: 2)
        XCTAssertEqual(said.reports.map(\.chat), [nil, "thr_7"])
        clock.advance(by: 24)
        try await settle()
        XCTAssertEqual(said.reports.count, 2, "the 25 seconds run from the report of the chat, not from the first")
        clock.advance(by: 1)
        try await until(said, count: 3)
        XCTAssertEqual(said.reports.map(\.chat), [nil, "thr_7", "thr_7"])
    }

    func testAnUnpairedAppSendsNothing() async throws {
        let clock: ManualClock = ManualClock()
        let said: Said = Said()
        let lease: ActivityLease = make(paired: false, clock: clock, said: said)
        lease.scene(.active)
        lease.showing(chat: "thr_1")
        clock.advance(by: 100)
        try await settle()
        XCTAssertEqual(said.reports, [])
    }

    /// A link too poor to answer must not pile requests up, and the daemon must hear the last thing the app said: a
    /// report waits behind the one on its way, only the newest.
    func testASlowSendDoesNotPileUpAndTheNewestIsTheOneThatFollows() async throws {
        let clock: ManualClock = ManualClock()
        let said: Said = Said()
        let gate: Gate = Gate()
        let lease: ActivityLease = make(clock: clock, said: said, gate: gate)
        lease.scene(.active)
        try await until(said, count: 1)
        lease.showing(chat: "thr_a")
        lease.showing(chat: "thr_b")
        lease.showing(chat: "thr_c")
        try await settle()
        XCTAssertEqual(said.reports.count, 1, "one send is on its way and nothing else starts")
        await gate.open()
        try await until(said, count: 2)
        try await settle()
        XCTAssertEqual(said.reports.map(\.chat), [nil, "thr_c"], "the chats between were never said: the newest was")
    }

    func testALeaseThatEndsWhileASendIsOnItsWayIsTheLastThingSaid() async throws {
        let clock: ManualClock = ManualClock()
        let said: Said = Said()
        let gate: Gate = Gate()
        let lease: ActivityLease = make(clock: clock, said: said, gate: gate)
        lease.scene(.active)
        try await until(said, count: 1)
        lease.showing(chat: "thr_a")
        lease.scene(.inactive)
        await gate.open()
        try await until(said, count: 2)
        try await settle()
        XCTAssertEqual(said.reports.map(\.state), [.foreground, .background], "background is never overtaken by an older foreground")
    }

    func testStoppingTheLeaseSilencesItAndItsTimer() async throws {
        let clock: ManualClock = ManualClock()
        let said: Said = Said()
        let lease: ActivityLease = make(clock: clock, said: said)
        lease.scene(.active)
        try await until(said, count: 1)
        lease.stop()
        clock.advance(by: 100)
        lease.scene(.active)
        lease.showing(chat: "thr_1")
        try await settle()
        XCTAssertEqual(said.reports.count, 1, "a key that was forgotten says nothing more")
    }
}
