import XCTest
@testable import Dot

final class RetryPolicyTests: XCTestCase {
    func testTheWaitGrowsByDoublingAndStopsAtTheCap() {
        let policy: RetryPolicy = RetryPolicy(attempts: 9, base: 0.5, cap: 4, budget: 60)
        let waits: [Double] = (1...6).map { policy.wait(afterFailure: $0, jitter: 1).inSeconds }
        XCTAssertEqual(waits, [0.5, 1, 2, 4, 4, 4])
    }

    func testJitterTakesAShareOfTheWaitNeverMoreAndNeverBelowHalf() {
        let policy: RetryPolicy = RetryPolicy(attempts: 9, base: 1, cap: 8, budget: 60)
        for failure in 1...5 {
            let full: Double = policy.wait(afterFailure: failure, jitter: 1).inSeconds
            let low: Double = policy.wait(afterFailure: failure, jitter: 0).inSeconds
            let middle: Double = policy.wait(afterFailure: failure, jitter: 0.5).inSeconds
            XCTAssertEqual(low, full / 2, accuracy: 0.002)
            XCTAssertTrue(low <= middle && middle <= full)
        }
        XCTAssertEqual(policy.wait(afterFailure: 1, jitter: 7).inSeconds, 1, accuracy: 0.002, "a jitter past 1 is 1")
    }

    func testOnlyWhatMayPassIsRepeated() {
        func transport(_ code: URLError.Code) -> DaemonRequestError { .transport(path: "/x", underlying: URLError(code)) }
        XCTAssertTrue(RetryPolicy.isWorthRepeating(transport(.timedOut)))
        XCTAssertTrue(RetryPolicy.isWorthRepeating(transport(.networkConnectionLost)))
        XCTAssertTrue(RetryPolicy.isWorthRepeating(transport(.notConnectedToInternet)))
        XCTAssertFalse(RetryPolicy.isWorthRepeating(transport(.cancelled)))
        XCTAssertFalse(RetryPolicy.isWorthRepeating(transport(.cannotParseResponse)), "an answer that cannot be read will not read better")
        XCTAssertTrue(RetryPolicy.isWorthRepeating(DaemonRequestError.status(path: "/x", code: 503, body: "")))
        XCTAssertTrue(RetryPolicy.isWorthRepeating(DaemonRequestError.status(path: "/x", code: 429, body: "")))
        XCTAssertFalse(RetryPolicy.isWorthRepeating(DaemonRequestError.status(path: "/x", code: 422, body: "")))
        XCTAssertFalse(RetryPolicy.isWorthRepeating(DaemonRequestError.status(path: "/x", code: 401, body: "")))
        XCTAssertFalse(RetryPolicy.isWorthRepeating(CancellationError()))
    }

    func testNoLinkIsToldApartFromADaemonThatIsSlow() {
        XCTAssertTrue(RetryPolicy.isNoLink(DaemonRequestError.transport(path: "/x", underlying: URLError(.notConnectedToInternet))))
        XCTAssertFalse(RetryPolicy.isNoLink(DaemonRequestError.transport(path: "/x", underlying: URLError(.timedOut))))
    }
}

private func fail(_ code: URLError.Code = .timedOut) -> DaemonRequestError { .transport(path: "/x", underlying: URLError(code)) }

final class RetrierTests: XCTestCase {
    func testAReadIsTriedAgainUntilItAnswers() async throws {
        let tries: LockedValue<Int> = LockedValue(0)
        let answer: Int = try await Retrier.run(quickRead) {
            let nth: Int = tries.value + 1
            tries.set(nth)
            if nth < 3 { throw fail() }
            return 42
        }
        XCTAssertEqual(answer, 42)
        XCTAssertEqual(tries.value, 3)
    }

    func testAnswerThatWillNotChangeIsNotAskedAgain() async {
        let tries: LockedValue<Int> = LockedValue(0)
        do {
            _ = try await Retrier.run(quickRead) { () async throws -> Int in
                tries.set(tries.value + 1)
                throw DaemonRequestError.status(path: "/x", code: 404, body: "")
            }
            XCTFail("it should throw")
        } catch {
            XCTAssertEqual(tries.value, 1)
        }
    }

    func testTheLastFailureIsThrownWhenTheTriesRunOut() async {
        let tries: LockedValue<Int> = LockedValue(0)
        let policy: RetryPolicy = RetryPolicy(attempts: 3, base: 0.001, cap: 0.002, budget: 5)
        do {
            _ = try await Retrier.run(policy) { () async throws -> Int in
                tries.set(tries.value + 1)
                throw fail()
            }
            XCTFail("it should throw")
        } catch {
            XCTAssertEqual(tries.value, 3)
            XCTAssertTrue(error is DaemonRequestError)
        }
    }

    func testTheBudgetEndsTheTriesBeforeTheirCount() async {
        let tries: LockedValue<Int> = LockedValue(0)
        let policy: RetryPolicy = RetryPolicy(attempts: 1000, base: 0.05, cap: 0.05, budget: 0.15)
        let began: ContinuousClock.Instant = .now
        do {
            _ = try await Retrier.run(policy) { () async throws -> Int in
                tries.set(tries.value + 1)
                throw fail()
            }
            XCTFail("it should throw")
        } catch {
            XCTAssertLessThan(began.duration(to: .now).inSeconds, 1.0, "the budget ended it, not the count of tries")
            XCTAssertLessThan(tries.value, 1000)
            XCTAssertGreaterThanOrEqual(tries.value, 2)
        }
    }

    func testWhenTheLinkIsDownItWaitsForTheLinkInsteadOfSpendingTries() async throws {
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let tries: LockedValue<Int> = LockedValue(0)
        let policy: RetryPolicy = RetryPolicy(attempts: 2, base: 0.001, cap: 0.002, budget: 5)
        let run: Task<Int, Error> = Task {
            try await Retrier.run(policy, link: link) {
                tries.set(tries.value + 1)
                if !link.state.online { throw fail(.notConnectedToInternet) }
                return 7
            }
        }
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertEqual(tries.value, 1, "one try found no link; the second waits for it")
        link.report(.up)
        let answer: Int = try await run.value
        XCTAssertEqual(answer, 7)
        XCTAssertEqual(tries.value, 2)
    }

    func testCancellingEndsItBetweenTries() async {
        let tries: LockedValue<Int> = LockedValue(0)
        let policy: RetryPolicy = RetryPolicy(attempts: 1000, base: 0.2, cap: 0.2, budget: 100)
        let run: Task<Int, Error> = Task {
            try await Retrier.run(policy) { () async throws -> Int in
                tries.set(tries.value + 1)
                throw fail()
            }
        }
        try? await Task.sleep(for: .milliseconds(100))
        run.cancel()
        let began: ContinuousClock.Instant = .now
        _ = try? await run.value
        XCTAssertLessThan(began.duration(to: .now).inSeconds, 0.5)
        XCTAssertLessThan(tries.value, 5)
    }
}

final class CoalescerTests: XCTestCase {
    func testTwoAsksForOneResourceReadItOnce() async {
        let coalescer: Coalescer = Coalescer()
        let reads: LockedValue<Int> = LockedValue(0)
        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<6 {
                group.addTask {
                    await coalescer.run("list") {
                        reads.set(reads.value + 1)
                        try? await Task.sleep(for: .milliseconds(150))
                    }
                }
            }
        }
        XCTAssertEqual(reads.value, 1)
        await coalescer.run("list") { reads.set(reads.value + 1) }
        XCTAssertEqual(reads.value, 2, "a later ask reads again")
    }

    func testLeavingAScreenCancelsWhatItReads() async {
        let coalescer: Coalescer = Coalescer()
        let ended: LockedValue<Bool> = LockedValue(false)
        let asker: Task<Void, Never> = Task {
            await coalescer.run("chat") {
                do { try await Task.sleep(for: .seconds(10)) } catch { ended.set(true) }
            }
        }
        try? await Task.sleep(for: .milliseconds(50))
        await coalescer.cancel("chat")
        _ = await until(2) { ended.value }
        XCTAssertTrue(ended.value)
        asker.cancel()
    }
}

final class LinkMonitorTests: XCTestCase {
    func testWatchersAreToldTheStateFirstAndEveryChange() async {
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let seen: LockedValue<[LinkState]> = LockedValue([])
        let watching: Task<Void, Never> = Task { for await state in link.updates() { seen.set(seen.value + [state]) } }
        _ = await until(1) { seen.value.count == 1 }
        link.report(.down)
        link.report(.down)
        link.report(LinkState(online: true, constrained: true, expensive: false))
        _ = await until(1) { seen.value.count == 3 }
        watching.cancel()
        XCTAssertEqual(seen.value.map(\.online), [true, false, true])
        XCTAssertTrue(seen.value[2].constrained)
    }

    func testWaitingForTheLinkEndsWhenItComesOrTheTimeIsUp() async {
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let early: Task<Bool, Never> = Task { await link.waitForOnline(timeout: .seconds(5)) }
        try? await Task.sleep(for: .milliseconds(50))
        link.report(.up)
        let came: Bool = await early.value
        XCTAssertTrue(came)
        link.report(.down)
        let began: ContinuousClock.Instant = .now
        let missed: Bool = await link.waitForOnline(timeout: .milliseconds(120))
        XCTAssertFalse(missed)
        XCTAssertLessThan(began.duration(to: .now).inSeconds, 1)
    }

    func testLowDataModeAndACostlyLinkReadLessAhead() {
        XCTAssertEqual(LinkState.up.prefetchBudget, 6)
        XCTAssertEqual(LinkState(online: true, constrained: false, expensive: true).prefetchBudget, 3)
        XCTAssertEqual(LinkState(online: true, constrained: true, expensive: true).prefetchBudget, 0)
        XCTAssertEqual(LinkState.down.prefetchBudget, 0)
    }
}

final class ConnectionBannerTests: XCTestCase {
    private let now: Date = Date(timeIntervalSince1970: 100_000)

    func testNothingIsSaidWhileAllIsWell() {
        XCTAssertNil(ConnectionBanner.make(link: .up, connection: .live, lastUpdated: now, waiting: 0, now: now))
        XCTAssertNil(ConnectionBanner.make(link: .up, connection: .connecting, lastUpdated: nil, waiting: 0, now: now))
    }

    func testOfflineSaysThatWhatIsShownWasSavedAndHowOld() {
        let banner: ConnectionBanner? = ConnectionBanner.make(
            link: .down, connection: .live, lastUpdated: now.addingTimeInterval(-180), waiting: 0, now: now)
        XCTAssertEqual(banner?.kind, .offline)
        XCTAssertEqual(banner?.text, "Offline — showing saved chats · last updated 3 min ago")
    }

    func testOfflineWithMessagesWaitingSaysSo() {
        let banner: ConnectionBanner? = ConnectionBanner.make(link: .down, connection: .live, lastUpdated: nil, waiting: 2, now: now)
        XCTAssertEqual(banner?.text, "Offline — 2 waiting to send")
    }

    func testReconnectingIsTheLinkBeingUpWithTheMacOutOfReach() {
        let banner: ConnectionBanner? = ConnectionBanner.make(link: .up, connection: .reconnecting, lastUpdated: now, waiting: 0, now: now)
        XCTAssertEqual(banner?.kind, .reconnecting)
        XCTAssertEqual(banner?.text, "Reconnecting…")
    }

    func testARefusedKeyIsSaidWhateverTheLinkDoes() {
        XCTAssertEqual(ConnectionBanner.make(link: .down, connection: .unauthorized, lastUpdated: nil, waiting: 0, now: now)?.kind, .unauthorized)
    }

    func testAgeIsToldCoarsely() {
        func ago(_ seconds: Double) -> String { ConnectionBanner.ago(now.addingTimeInterval(-seconds), now: now) }
        XCTAssertEqual(ago(5), "just now")
        XCTAssertEqual(ago(60), "1 min ago")
        XCTAssertEqual(ago(600), "10 min ago")
        XCTAssertEqual(ago(3 * 3600 + 100), "3 h ago")
        XCTAssertEqual(ago(3 * 86_400 + 5), "3 d ago")
        XCTAssertEqual(ConnectionBanner.ago(now.addingTimeInterval(500), now: now), "just now", "a clock that is behind is not a time to come")
    }
}

final class PrefetchRulesTests: XCTestCase {
    private func thread(_ id: String, run: String, status: String = "done", updated: Double = 10) -> ThreadSummary {
        ThreadSummary(
            id: id, title: id, source: "ui", updatedAt: updated, pinnedAt: nil, runCount: 1, latestRunId: run, latestStatus: status,
            latestMode: "task", unread: false, actorId: nil, lastMessage: "", lastRole: "", loopCron: nil, loopNextAt: nil)
    }

    private func stored(run: String, status: String = "done", listed: Double? = 10) -> StoredChat {
        var chat: StoredChat = StoredChat(threadId: "t", runs: [makeRun(run, status: status)], helpers: [], events: [], savedAt: 1)
        chat.listUpdatedAt = listed
        return chat
    }

    func testAChatWithNothingHeldIsRead() {
        XCTAssertTrue(PrefetchRules.needsReading(stored: nil, thread: thread("a", run: "run_1")))
    }

    func testAChatThatMovedOnIsReadAndOneThatDidNotIsNot() {
        XCTAssertFalse(PrefetchRules.needsReading(stored: stored(run: "run_1"), thread: thread("a", run: "run_1")))
        XCTAssertTrue(PrefetchRules.needsReading(stored: stored(run: "run_1"), thread: thread("a", run: "run_2")), "a newer run")
        XCTAssertTrue(PrefetchRules.needsReading(stored: stored(run: "run_1", status: "running"), thread: thread("a", run: "run_1")), "it ended")
        XCTAssertTrue(PrefetchRules.needsReading(stored: stored(run: "run_1"), thread: thread("a", run: "run_1", updated: 99)), "the list says it changed")
    }

    func testTheBudgetAndTheOpenChatsDecideWhichAreRead() {
        let threads: [ThreadSummary] = (1...10).map { thread("t\($0)", run: "run_\($0)") }
        let none: [ThreadSummary] = PrefetchRules.chosen(from: threads, open: [], budget: 0) { _ in nil }
        XCTAssertTrue(none.isEmpty, "Low Data Mode reads nothing ahead")
        let three: [ThreadSummary] = PrefetchRules.chosen(from: threads, open: ["t1"], budget: 3) { _ in nil }
        XCTAssertEqual(three.map(\.id), ["t2", "t3", "t4"], "the open chat reads itself")
        let held: [ThreadSummary] = PrefetchRules.chosen(from: threads, open: [], budget: 3) { id in
            id == "t1" || id == "t2" ? self.stored(run: "run_\(id.dropFirst())") : nil
        }
        XCTAssertEqual(held.map(\.id), ["t3", "t4", "t5"], "a chat that is held and current is passed over")
    }
}
