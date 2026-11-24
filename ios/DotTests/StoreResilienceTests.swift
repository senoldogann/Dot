import XCTest
@testable import Dot

/// The store as the app lives it: an offline start, the app sent to the background and brought back, a link that goes and
/// returns, Low Data Mode, one read at a time, and the chat screen's own model with a message typed offline (item 1371).
@MainActor
final class StoreResilienceTests: XCTestCase {
    private var folder: URL!
    private var daemon: FakeDaemon!
    private var stores: [NativeStore] = []

    override func setUp() async throws {
        folder = scratchFolder("dot-store")
        daemon = FakeDaemon()
        daemon.addAgent()
        daemon.addChat("thr_1", title: "First", runCount: 3, steps: 4, updatedAt: 500)
        for number in 2...8 { daemon.addChat("thr_\(number)", title: "Chat \(number)", runCount: 2, steps: 2, updatedAt: 500 - Double(number)) }
    }

    override func tearDown() async throws {
        for store in stores { store.stop() }
        stores = []
        try await Task.sleep(for: .milliseconds(100))
        try? FileManager.default.removeItem(at: folder)
    }

    private func makeStore(link: LinkMonitor, readPolicy: RetryPolicy = quickRead) -> NativeStore {
        let store: NativeStore = NativeStore(
            host: daemon.host, key: "k", session: daemon.session(), directory: folder, link: link, uploadSession: daemon.session())
        stores.append(store)
        return store
    }

    /// What a good session leaves on disk: the list, the models and the chats read ahead.
    private func seed() async {
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        await store.refreshList()
        await store.loadModels()
        _ = await until(10) {
            let first: StoredChat? = await store.cache.loadChat("thr_1")
            let sixth: StoredChat? = await store.cache.loadChat("thr_6")
            return first != nil && sixth != nil
        }
        store.stop()
        stores = []
        try? await Task.sleep(for: .milliseconds(300))
    }

    // MARK: Offline start

    func testAnOfflineStartShowsTheSavedListAtOnceWithTheBannerAndAsksNothing() async throws {
        await seed()
        daemon.set(.down)
        let before: Int = daemon.requests.count
        let began: ContinuousClock.Instant = .now
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .down))
        XCTAssertTrue(store.hasList, "the first frame has the list")
        XCTAssertEqual(store.threads.count, 8)
        XCTAssertLessThan(began.duration(to: .now).inSeconds, 0.25)
        let banner: ConnectionBanner? = store.banner()
        XCTAssertEqual(banner?.kind, .offline)
        XCTAssertTrue(banner?.text.hasPrefix("Offline — showing saved chats · last updated ") ?? false, banner?.text ?? "none")
        XCTAssertNotNil(store.models, "the models too")
        store.start()
        try await Task.sleep(for: .milliseconds(400))
        // Nothing of the chats is read ahead, and the list read that start asks for waits on a link that is not there.
        XCTAssertEqual(daemon.requests.dropFirst(before).filter { $0.path.contains("/runs") }.count, 0)
    }

    func testTheBannerGoesWhenTheLinkAndTheDaemonAreBack() async throws {
        await seed()
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let store: NativeStore = makeStore(link: link)
        store.start()
        XCTAssertEqual(store.banner()?.kind, .offline)
        link.report(.up)
        await expectSoon(10, "the link is back and the list was read: no line") { store.banner() == nil }
        await expectSoon(5) { (store.lastUpdated ?? .distantPast).timeIntervalSinceNow > -5 }
    }

    func testTheModelsAndSchedulesComeFromTheDiskBeforeTheNetworkAnswers() async throws {
        await seed()
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .down))
        XCTAssertEqual(store.models?.main, "ollama:scripted")
    }

    // MARK: One read at a time, and what is read ahead

    func testSixAsksForTheListAreOneRequest() async throws {
        daemon.set(NetworkFaults(latency: 0.2...0.2, scale: 1))
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<6 { group.addTask { await store.refreshList() } }
        }
        XCTAssertEqual(daemon.count(method: "GET", pathContaining: "/api/threads"), 1)
        XCTAssertEqual(store.threads.count, 8)
    }

    func testTheChatsThatMovedOnAreReadAheadWithinTheBudgetAndTheOthersAreNot() async throws {
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        await store.refreshList()
        await expectSoon(10, "the first six are read ahead") { await store.cache.loadChat("thr_6") != nil }
        let seventh: StoredChat? = await store.cache.loadChat("thr_7")
        XCTAssertNil(seventh, "the budget is six")
        XCTAssertNotNil(store.warmState(for: "thr_1"), "and a chat that was read ahead opens drawn")
        // Nothing moved on: a second read of the list reads no chat again.
        let before: Int = daemon.requests.filter { $0.path == "/api/runs" }.count
        await store.refreshList()
        try await Task.sleep(for: .milliseconds(500))
        let readAgain: Int = daemon.requests.filter { $0.path == "/api/runs" }.count - before
        XCTAssertEqual(readAgain, 2, "only the two the first read left out (the seventh and the eighth) are read, not the six held")
        await store.refreshList()
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(daemon.requests.filter { $0.path == "/api/runs" }.count - before, 2, "a third read of the list reads no chat")
    }

    func testLowDataModeReadsNothingAheadAndACostlyLinkReadsFew() async throws {
        let constrained: NativeStore = makeStore(link: LinkMonitor(initial: LinkState(online: true, constrained: true, expensive: true)))
        await constrained.refreshList()
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(daemon.requests.filter { $0.path == "/api/runs" }.count, 0, "Low Data Mode: the list, and no chat ahead of a tap")
        constrained.stop()
        stores = []
        try? FileManager.default.removeItem(at: folder)
        folder = scratchFolder("dot-store")
        let costly: NativeStore = makeStore(link: LinkMonitor(initial: LinkState(online: true, constrained: false, expensive: true)))
        await costly.refreshList()
        await expectSoon(10) { await costly.cache.loadChat("thr_3") != nil }
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(daemon.requests.filter { $0.path == "/api/runs" }.count, 3, "a link that costs reads three")
    }

    // MARK: Background and foreground

    func testABackgroundRefreshReadsTheChatsThatMovedOnEvenThoughTheAppWentToTheBackgroundBefore() async throws {
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        store.graceSeconds = 60
        store.enterBackground()
        let reached: Bool = await store.refreshInBackground(seconds: 10)
        XCTAssertTrue(reached)
        let first: StoredChat? = await store.cache.loadChat("thr_1")
        let third: StoredChat? = await store.cache.loadChat("thr_3")
        let fourth: StoredChat? = await store.cache.loadChat("thr_4")
        XCTAssertNotNil(first, "the chats the list says moved on were read and written")
        XCTAssertNotNil(third)
        XCTAssertNil(fourth, "three of them: the budget of a refresh the system gave a moment for")
    }

    func testABackgroundRefreshInLowDataModeReadsTheListAndNoChat() async throws {
        let store: NativeStore = makeStore(link: LinkMonitor(initial: LinkState(online: true, constrained: true, expensive: true)))
        store.graceSeconds = 60
        store.enterBackground()
        let reached: Bool = await store.refreshInBackground(seconds: 5)
        XCTAssertTrue(reached, "the list was read")
        XCTAssertEqual(daemon.requests.filter { $0.path == "/api/runs" }.count, 0, "no chat is read ahead on a link the system asks to spare")
    }

    func testTheAppsOwnReadingAheadStopsWhileItIsOutOfSight() async throws {
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        store.graceSeconds = 60
        store.enterBackground()
        await store.refreshList()
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertEqual(daemon.requests.filter { $0.path == "/api/runs" }.count, 0)
    }

    func testABackgroundRefreshOnADeadLinkSaysItReachedNothingAndOnALiveOneSaysItDid() async throws {
        await seed()
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let store: NativeStore = makeStore(link: link)
        let asked: Int = daemon.requests.count
        let dead: Bool = await store.refreshInBackground(seconds: 2)
        XCTAssertFalse(dead, "nothing was asked, so nothing was reached: the system is told so")
        XCTAssertEqual(daemon.requests.count, asked)
        link.report(.up)
        let alive: Bool = await store.refreshInBackground(seconds: 5)
        XCTAssertTrue(alive)
    }

    func testTheStreamsCloseAfterTheGraceAndComeBackWithinASecondOfComingToTheFront() async throws {
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        store.graceSeconds = 0.3
        store.start()
        await expectSoon(5, "the lifecycle stream is open while the app is shown") { daemon.openStreamCount == 1 }
        await store.refreshList()
        store.enterBackground()
        await expectSoon(3, "after the grace nothing is held open") { daemon.openStreamCount == 0 }
        let quiet: Int = daemon.requests.count
        try await Task.sleep(for: .milliseconds(700))
        XCTAssertEqual(daemon.requests.count, quiet, "no timer, no read: nothing runs while the app is out of sight")
        daemon.addChat("thr_9", title: "Came meanwhile", runCount: 1, steps: 1, updatedAt: 900)
        let began: ContinuousClock.Instant = .now
        store.enterForeground()
        await expectSoon(2) { daemon.openStreamCount == 1 && store.threads.contains { $0.id == "thr_9" } }
        XCTAssertLessThan(began.duration(to: .now).inSeconds, 1.0, "the stream is back and the list is the Mac's within a second")
    }

    func testAShortTripToTheBackgroundKeepsTheStreamsAndTheForegroundOnlyKicksThem() async throws {
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        store.graceSeconds = 5
        store.start()
        await expectSoon(5) { daemon.openStreamCount == 1 }
        store.enterBackground()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(daemon.openStreamCount, 1, "inside the grace the stream stays")
        store.enterForeground()
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertEqual(daemon.openStreamCount, 1)
    }

    func testAChatOpenWhenTheAppGoesAwayIsSuspendedAndReadsAfterWhenItIsBack() async throws {
        daemon.addChat("thr_1", title: "First", runCount: 2, steps: 3, working: true, updatedAt: 600)
        let store: NativeStore = makeStore(link: LinkMonitor(initial: .up))
        store.graceSeconds = 0.2
        store.start()
        await store.refreshList()
        let thread: ThreadSummary = try XCTUnwrap(store.threads.first { $0.id == "thr_1" })
        let model: ChatModel = ChatModel(thread: thread, store: store)
        model.start()
        await expectSoon(5, "the lifecycle stream and the run's stream") { model.loaded && daemon.openStreamCount >= 2 }
        store.enterBackground()
        await expectSoon(3) { daemon.openStreamCount == 0 }
        daemon.say("assistant", ["step": .int(80), "text": .string("said while away")])
        let target: Int = daemon.newestEventId()
        store.enterForeground()
        await expectSoon(5, "the chat read what came after") { model.state.lastEventId >= target }
        await expectSoon(5, "and follows the run again") { daemon.openStreamCount >= 2 }
        model.stop()
    }

    // MARK: A message typed offline

    func testAMessageTypedOfflineShowsAsWaitingKeepsItsPlaceAndGoesOnceWhenTheLinkIsBack() async throws {
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let store: NativeStore = makeStore(link: link)
        store.start()
        let thread: ThreadSummary = try XCTUnwrap(store.threads.first ?? daemon.threadNamed("thr_1"))
        let model: ChatModel = ChatModel(thread: thread, store: store)
        model.start()
        model.draft = "typed in a lift"
        model.send()
        model.draft = "and a second one"
        model.send()
        XCTAssertEqual(model.pending.map(\.shown), ["typed in a lift", "and a second one"])
        XCTAssertTrue(model.pending.allSatisfy { $0.phase == .waiting })
        XCTAssertEqual(model.draft, "", "the box is empty: the words are kept elsewhere")
        XCTAssertEqual(store.banner()?.text.contains("2 waiting to send"), true)
        link.report(.up)
        await expectSoon(10, "sent, in its turn") { model.pending.isEmpty }
        XCTAssertEqual(daemon.queuedTexts, ["typed in a lift", "and a second one"])
        XCTAssertEqual(daemon.accepted, 2)
        model.stop()
    }

    func testAnAnswerToACardGivenOfflineIsSentOnceBackAndTheCardSaysHowItStands() async throws {
        daemon.addChat("thr_1", title: "First", runCount: 2, steps: 2, working: true, updatedAt: 600)
        daemon.ask("apr_1")
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let store: NativeStore = makeStore(link: link)
        store.start()
        await store.refreshList()
        let thread: ThreadSummary = try XCTUnwrap(store.threads.first { $0.id == "thr_1" })
        let model: ChatModel = ChatModel(thread: thread, store: store)
        model.start()
        await expectSoon(5) { !model.state.pendingApprovals.isEmpty }
        let card: ApprovalCard = try XCTUnwrap(model.state.pendingApprovals.first)
        link.report(.down)
        daemon.set(.down)
        model.decide(card, approve: true)
        XCTAssertEqual(model.notice, "Your answer will be sent when you're back online.")
        link.report(.up)
        daemon.set(.good)
        await expectSoon(10) { daemon.approvalStatus("apr_1") == "approved" }
        await expectSoon(5, "the card no longer waits") { model.state.pendingApprovals.isEmpty }
        model.stop()
    }

    func testAnAnswerToACardThatExpiredWhileAwaySaysSo() async throws {
        daemon.addChat("thr_1", title: "First", runCount: 2, steps: 2, working: true, updatedAt: 600)
        daemon.ask("apr_1")
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let store: NativeStore = makeStore(link: link)
        store.start()
        await store.refreshList()
        let thread: ThreadSummary = try XCTUnwrap(store.threads.first { $0.id == "thr_1" })
        let model: ChatModel = ChatModel(thread: thread, store: store)
        model.start()
        await expectSoon(5) { !model.state.pendingApprovals.isEmpty }
        let card: ApprovalCard = try XCTUnwrap(model.state.pendingApprovals.first)
        link.report(.down)
        model.decide(card, approve: true)
        daemon.expire("apr_1")
        link.report(.up)
        await expectSoon(10) { model.notice?.hasPrefix("That question") == true }
        XCTAssertEqual(model.notice, "That question had expired when your answer reached Dot, so it was not used.")
        XCTAssertEqual(daemon.approvalStatus("apr_1"), "expired")
        model.stop()
    }
}

extension FakeDaemon {
    /// A chat's row, for a test whose store has not read the list.
    func threadNamed(_ id: String) -> ThreadSummary? {
        ThreadSummary(
            id: id, title: "T", source: "ui", updatedAt: 1, pinnedAt: nil, runCount: 1, latestRunId: "run_\(id)_0", latestStatus: "done",
            latestMode: "task", unread: false, actorId: nil, lastMessage: "", lastRole: "", loopCron: nil, loopNextAt: nil)
    }
}
