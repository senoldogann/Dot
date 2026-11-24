import XCTest
@testable import Dot

/// A chat that comes back after the phone slept, was killed, lost its link or had a bad one (item 1371): it draws from the
/// disk first, then reads only what came after, and a gap too long to replay is read as the newest events.
final class ResumeTests: XCTestCase {
    private var folder: URL!
    private var daemon: FakeDaemon!

    override func setUp() {
        folder = scratchFolder("dot-resume")
        daemon = FakeDaemon()
    }

    override func tearDown() { try? FileManager.default.removeItem(at: folder) }

    private func session(link: LinkMonitor? = nil, readPolicy: RetryPolicy = quickRead, thread: String = "thr_1") -> ChatSession {
        ChatSession(threadId: thread, api: daemon.api(link: link, readPolicy: readPolicy), cache: ChatCache(directory: folder), link: link)
    }

    private func eventsRequests() -> [FakeDaemon.Seen] { daemon.requests.filter { $0.path.hasSuffix("/events") } }

    // MARK: A gap

    func testAGapOfThousandsOfEventsIsReadAsTheNewestFewHundredNotReplayed() async throws {
        daemon.addChat("thr_1", runCount: 2, steps: 5, working: true)
        await daemon.warm(folder)
        let before: Int = daemon.requests.count
        daemon.sayMany(1_300)
        let target: Int = daemon.newestEventId()
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(10, "the chat is current") { log.lastEventId >= target }
        let asked: [FakeDaemon.Seen] = Array(daemon.requests.dropFirst(before)).filter { $0.path.hasSuffix("/events") }
        XCTAssertEqual(asked.filter { $0.query.contains("tail=") }.count, 1, "one read of the end")
        XCTAssertFalse(asked.contains { $0.query.contains("limit=2000") }, "no page after page of the whole gap")
        XCTAssertTrue(log.latest?.items.contains { item in
            if case .note(let words, _) = item.kind { return words.contains("not shown here") }
            return false
        } ?? false, "the chat says the steps before the tail are not shown")
        await chat.stop()
        reading.cancel()
        let held: StoredChat? = await ChatCache(directory: folder).loadChat("thr_1")
        XCTAssertLessThanOrEqual(held?.events.count ?? 999, StoredChat.tail, "what is kept stays within the tail")
    }

    func testADaemonThatDoesNotKnowTailIsReadByPagesAsBefore() async throws {
        // A run that ended: no stream fills in what the pages leave, so every event of the gap must come from them.
        daemon.addChat("thr_1", runCount: 2, steps: 5, working: false)
        await daemon.warm(folder)
        daemon.supportsTail = false
        daemon.sayMany(900)
        let target: Int = daemon.newestEventId()
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(10, "every event of the gap was read") { log.lastEventId >= target }
        XCTAssertTrue(eventsRequests().contains { $0.query.contains("tail=") }, "it asked, and found no header to say it was understood")
        let rows: Int = log.latest?.items.count ?? 0
        XCTAssertGreaterThanOrEqual(rows, 900, "all 900 steps of the gap are rows: the read did not stop at its first page")
        await chat.stop()
        reading.cancel()
    }

    func testASmallGapIsReadWithoutTail() async throws {
        daemon.addChat("thr_1", runCount: 2, steps: 5, working: true)
        await daemon.warm(folder)
        daemon.sayMany(30)
        let target: Int = daemon.newestEventId()
        let before: Int = daemon.requests.count
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(10) { log.lastEventId >= target }
        XCTAssertFalse(daemon.requests.dropFirst(before).contains { $0.query.contains("tail=") })
        await chat.stop()
        reading.cancel()
    }

    // MARK: Starting offline

    func testAChatOpensFromTheDiskWithNoLinkAndCatchesUpByItselfWhenTheLinkReturns() async throws {
        daemon.addChat("thr_1", runCount: 3, steps: 4, working: false)
        await daemon.warm(folder)
        daemon.sayMany(10)
        let target: Int = daemon.newestEventId()
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let before: Int = daemon.requests.count
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session(link: link)
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(2, "the first frame is the disk's") { !(log.latest?.items.isEmpty ?? true) }
        XCTAssertLessThan(log.secondsToFirstRows ?? 99, 0.5)
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertEqual(daemon.requests.count, before, "nothing is asked of a link that is not there")
        XCTAssertLessThan(log.lastEventId, target)
        link.report(.up)
        await expectSoon(10, "it read what came after, by itself") { log.lastEventId >= target }
        await chat.stop()
        reading.cancel()
    }

    func testTheFirstRowsAreOnTheScreenBeforeTheNetworkAnswersAtAll() async throws {
        daemon.addChat("thr_1", runCount: 6, steps: 20, working: false)
        await daemon.warm(folder)
        daemon.set(NetworkFaults(latency: 3...3, scale: 1))
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(2) { !(log.latest?.items.isEmpty ?? true) }
        XCTAssertLessThan(log.secondsToFirstRows ?? 99, 0.5, "with every answer three seconds away, the rows are there at once")
        await chat.stop()
        reading.cancel()
    }

    // MARK: A bad network

    func testAFlakyNetworkConvergesWithoutAnyoneKickingIt() async throws {
        daemon.addChat("thr_1", runCount: 3, steps: 5, working: false)
        await daemon.warm(folder)
        daemon.sayMany(25)
        let target: Int = daemon.newestEventId()
        daemon.set(NetworkFaults(latency: 0.01...0.05, dropRate: 0.5, scale: 1, seed: 3))
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(30, "half the requests are lost and the chat still gets there") { log.lastEventId >= target }
        await chat.stop()
        reading.cancel()
    }

    func testAChatThatCouldNotBeReadSaysItIsReconnectingAndKeepsItsRows() async throws {
        daemon.addChat("thr_1", runCount: 3, steps: 5, working: false)
        await daemon.warm(folder)
        daemon.set(.down)
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5) { if case .reconnecting? = log.lastConnection { true } else { false } }
        XCTAssertFalse(log.latest?.items.isEmpty ?? true, "the saved rows stay")
        daemon.set(.good)
        await expectSoon(10) { if case .live? = log.lastConnection { true } else if case .ended? = log.lastConnection { true } else { false } }
        await chat.stop()
        reading.cancel()
    }

    // MARK: Following a run

    func testAStreamThatDropsGoesOnAfterTheLastEventItReadWithTheHeaderAndTheQuery() async throws {
        daemon.addChat("thr_1", runCount: 2, steps: 3, working: true)
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5) { daemon.openStreamCount >= 1 }
        daemon.say("assistant", ["step": .int(50), "text": .string("before the drop")])
        await expectSoon(5) { log.lastEventId >= self.daemon.newestEventId() }
        let readUpTo: Int = log.lastEventId
        daemon.dropStreams()
        daemon.say("assistant", ["step": .int(51), "text": .string("after the drop")])
        await expectSoon(10, "the chat has what came while the stream was down") { log.lastEventId > readUpTo }
        let streams: [FakeDaemon.Seen] = daemon.requests.filter { $0.path.hasSuffix("/stream") }
        XCTAssertGreaterThanOrEqual(streams.count, 2)
        XCTAssertTrue(streams.last?.query.contains("last-event-id=\(readUpTo)") ?? false, "it went on from the last id")
        XCTAssertTrue(streams.last?.query.contains("after=\(readUpTo)") ?? false)
        await chat.stop()
        reading.cancel()
    }

    func testAFeedWaitsForTheLinkInsteadOfTryingAgainstNothing() async throws {
        daemon.addChat("thr_1", runCount: 2, steps: 3, working: true)
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let api: APIClient = daemon.api(link: link)
        let feed: EventFeed = EventFeed(
            session: APIClient.makeStreamSession(like: api.session), after: 0, link: link,
            isFinal: { $0.kind == "run_finished" }, open: { after in runStreamRequest(api: api, runId: "run_thr_1_1", after: after) })
        let states: LockedValue<[FeedState]> = LockedValue([])
        let stream: AsyncStream<FeedUpdate> = await feed.start()
        let reading: Task<Void, Never> = Task { for await update in stream { if case .state(let state) = update { states.set(states.value + [state]) } } }
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertEqual(daemon.started(pathContaining: "/stream"), 0, "no request is made while there is no link")
        XCTAssertTrue(states.value.contains { if case .reconnecting = $0 { true } else { false } })
        link.report(.up)
        await expectSoon(5, "the stream opens as soon as the link is back") { daemon.openStreamCount == 1 }
        await feed.stop()
        reading.cancel()
    }

    // MARK: Leaving

    func testLeavingAChatCancelsWhatItWasReading() async throws {
        daemon.addChat("thr_1", runCount: 2, steps: 3, working: false)
        daemon.set(NetworkFaults(latency: 5...5, scale: 1))
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session(readPolicy: .once)
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(3) { daemon.started(pathContaining: "/api/runs") >= 1 }
        await chat.stop()
        await expectSoon(3, "the request was cancelled, not left to finish for nobody") { daemon.cancelled >= 1 }
        reading.cancel()
    }

    func testAnUnchangedChatIsNotHandedToTheScreenAgain() async throws {
        daemon.addChat("thr_1", runCount: 2, steps: 3, working: false)
        await daemon.warm(folder)
        let log: SessionLog = SessionLog()
        let chat: ChatSession = session()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5) { log.count >= 1 }
        _ = await chat.refresh()
        _ = await chat.refresh()
        _ = await chat.refresh()
        XCTAssertEqual(log.count, 1, "three reads that found nothing new draw nothing new")
        await chat.stop()
        reading.cancel()
    }
}
