import XCTest
@testable import Dot

/// The outbox keeps what the owner did while the Mac could not be reached and sends it when it can: in order, once each,
/// whatever was lost on the way (item 1372).
final class OutboxTests: XCTestCase {
    private var folder: URL!
    private var daemon: FakeDaemon!

    override func setUp() {
        folder = scratchFolder("dot-outbox")
        daemon = FakeDaemon()
        daemon.addChat("thr_1", title: "Chat", runCount: 1, steps: 1)
    }

    override func tearDown() { try? FileManager.default.removeItem(at: folder) }

    private func box(
        link: LinkMonitor, policy: RetryPolicy = quickRetry, clock: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 }
    ) -> Outbox {
        Outbox(directory: folder, api: daemon.api(link: link), link: link, policy: policy, clock: clock)
    }

    private func message(_ text: String, thread: String = "thr_1") -> OutboxItem.Action {
        .message(thread: thread, text: text, typed: text, shown: text, choices: .none, images: [], uploaded: [])
    }

    private func onDisk() -> [OutboxItem] { Outbox.read(folder.appending(path: "outbox.json")) }

    private func collect(_ box: Outbox) async -> (LockedValue<[OutboxOutcome]>, Task<Void, Never>) {
        let seen: LockedValue<[OutboxOutcome]> = LockedValue([])
        let stream: AsyncStream<OutboxOutcome> = await box.outcomes()
        let task: Task<Void, Never> = Task { for await outcome in stream { seen.set(seen.value + [outcome]) } }
        return (seen, task)
    }

    // MARK: Messages

    func testMessagesMadeOfflineAreKeptOnDiskAndSentInOrderOnceWhenTheLinkReturns() async throws {
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let first: Outbox = box(link: link)
        for text in ["one", "two", "three"] { await first.enqueue(message(text)) }
        XCTAssertEqual(onDisk().count, 3, "kept on disk before any link")
        XCTAssertEqual(daemon.count(method: "POST", pathContaining: "queue"), 0, "nothing was asked of a network that is not there")
        // The app is killed here: a new outbox reads the file.
        await first.suspend()
        let second: Outbox = box(link: link)
        let held: [OutboxItem] = await second.current
        XCTAssertEqual(held.map(\.isMessage), [true, true, true])
        XCTAssertTrue(held.allSatisfy { $0.phase == .waiting })
        link.report(.up)
        await second.start()
        let sent: Bool = await until(5) { self.daemon.queuedTexts.count == 3 }
        XCTAssertTrue(sent)
        XCTAssertEqual(daemon.queuedTexts, ["one", "two", "three"], "in the order they were made")
        XCTAssertEqual(daemon.accepted, 3)
        await expectSoon(2, "what was sent leaves the file") { self.onDisk().isEmpty }
    }

    func testAnAnswerLostAfterTheDaemonTookTheMessageDoesNotSendItTwice() async throws {
        daemon.loseNextAnswers(2)
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let outbox: Outbox = box(link: link)
        let (outcomes, watcher): (LockedValue<[OutboxOutcome]>, Task<Void, Never>) = await collect(outbox)
        await outbox.enqueue(message("exactly once"))
        await expectSoon(5) { !outcomes.value.isEmpty }
        watcher.cancel()
        XCTAssertEqual(daemon.queuedTexts, ["exactly once"])
        XCTAssertEqual(daemon.accepted, 1, "the daemon took it once, by its key")
        XCTAssertEqual(daemon.count(method: "POST", pathContaining: "queue"), 3, "two answers lost, one heard")
        guard case .delivered(_, let queue)? = outcomes.value.first else { return XCTFail("it was delivered") }
        XCTAssertEqual(queue.messages.map(\.text), ["exactly once"])
    }

    func testAnAppKilledWhileASendWasUnansweredSendsItAgainWithTheSameKeyAndNotTwice() async throws {
        daemon.loseNextAnswers(1)
        let link: LinkMonitor = LinkMonitor(initial: .up)
        // The first outbox waits long between tries, so that the kill comes while the answer is still unheard.
        let first: Outbox = box(link: link, policy: RetryPolicy(attempts: Int.max, base: 60, cap: 60, budget: Double.infinity))
        await first.enqueue(message("survives a kill"))
        await expectSoon(5) { self.daemon.count(method: "POST", pathContaining: "queue") >= 1 }
        await first.suspend()
        let keyBefore: String? = onDisk().first?.id
        XCTAssertNotNil(keyBefore, "the message is still in the file: its answer was never heard")
        let second: Outbox = box(link: link)
        let (outcomes, watcher): (LockedValue<[OutboxOutcome]>, Task<Void, Never>) = await collect(second)
        await second.start()
        await expectSoon(5) { !outcomes.value.isEmpty }
        watcher.cancel()
        XCTAssertEqual(daemon.queuedTexts, ["survives a kill"])
        XCTAssertEqual(daemon.accepted, 1)
        XCTAssertEqual(daemon.requests.compactMap { try? JSONSerialization.jsonObject(with: $0.body ?? Data()) as? [String: Any] }.compactMap { $0["key"] as? String }.filter { $0 == keyBefore }.count, 2)
    }

    func testAMessageBeingSentAlreadyOnTheMacIsWaitedOutNotRefused() async throws {
        daemon.failNext("/api/threads/thr_1/queue", status: 409, body: #"{"detail":"the message is being sent already"}"#)
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let outbox: Outbox = box(link: link)
        let (outcomes, watcher): (LockedValue<[OutboxOutcome]>, Task<Void, Never>) = await collect(outbox)
        await outbox.enqueue(message("patient"))
        await expectSoon(5) { !outcomes.value.isEmpty }
        watcher.cancel()
        guard case .delivered? = outcomes.value.first else { return XCTFail("a 409 that says the key is under way is waited out") }
        XCTAssertEqual(daemon.queuedTexts, ["patient"])
    }

    func testADaemonThatCannotBeReachedHoldsTheLaterMessagesBackSoTheOrderHolds() async throws {
        daemon.set(.down)
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let outbox: Outbox = box(link: link)
        await outbox.enqueue(message("A"))
        await outbox.enqueue(message("B"))
        await expectSoon(5) { self.daemon.started(pathContaining: "/queue") >= 3 }
        XCTAssertEqual(daemon.queuedTexts, [])
        daemon.set(.good)
        await expectSoon(5) { self.daemon.queuedTexts.count == 2 }
        XCTAssertEqual(daemon.queuedTexts, ["A", "B"])
    }

    func testAMessageTheDaemonRefusesStaysAsNotSentAndDoesNotBlockTheNext() async throws {
        daemon.failNext("/api/threads/thr_1/queue", status: 422, body: #"{"detail":"that model is not available"}"#)
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let outbox: Outbox = box(link: link)
        await outbox.enqueue(message("refused"))
        await outbox.enqueue(message("fine"))
        await expectSoon(5) { self.daemon.queuedTexts == ["fine"] }
        let held: [OutboxItem] = await outbox.current
        XCTAssertEqual(held.count, 1)
        XCTAssertEqual(held.first?.phase, .failed)
        XCTAssertEqual(held.first?.note, "that model is not available")
        // Retry sends it again, now that the daemon takes it.
        await outbox.retry(held[0].id)
        await expectSoon(5) { self.daemon.queuedTexts == ["fine", "refused"] }
    }

    func testAMessageThatWaitedMoreThanADayIsNotSentByItselfAndIsSentAsANewOneWhenAsked() async throws {
        daemon.set(.down)
        let now: LockedValue<Double> = LockedValue(Date().timeIntervalSince1970)
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let outbox: Outbox = box(link: link, clock: { now.value })
        await outbox.enqueue(message("old"))
        await expectSoon(5) { self.daemon.started(pathContaining: "/queue") >= 1 }
        now.set(now.value + 25 * 3600)
        await expectSoon(5) { await outbox.current.first?.phase == .failed }
        let stale: OutboxItem? = await outbox.current.first
        XCTAssertEqual(stale?.note, "This message waited more than a day. Send it again if you still want it.")
        daemon.set(.good)
        let sentBefore: Int = daemon.queuedTexts.count
        XCTAssertEqual(sentBefore, 0, "it was not sent by itself, for the daemon forgot its key")
        await outbox.retry(try XCTUnwrap(stale).id)
        await expectSoon(5) { self.daemon.queuedTexts == ["old"] }
    }

    func testPhotosAreKeptBesideTheFileUploadedOnceAndLetGoWhenSent() async throws {
        daemon.loseNextAnswers(1)
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let outbox: Outbox = box(link: link)
        await outbox.enqueue(
            .message(thread: "thr_1", text: "(photo)", typed: "", shown: "Photo", choices: .none, images: [], uploaded: []),
            images: [Data("jpeg-bytes".utf8)])
        let imageFolder: URL = folder.appending(path: "outbox-images")
        XCTAssertEqual((try? FileManager.default.contentsOfDirectory(atPath: imageFolder.path))?.count, 1)
        link.report(.up)
        await expectSoon(5) { self.daemon.queuedTexts == ["(photo)"] }
        XCTAssertEqual(daemon.attachments, 1, "the photo was kept once though the message was sent twice")
        await expectSoon(2) { ((try? FileManager.default.contentsOfDirectory(atPath: imageFolder.path)) ?? []).isEmpty }
    }

    func testEditTakesAMessageBackWithItsPhotos() async throws {
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let outbox: Outbox = box(link: link)
        await outbox.enqueue(
            .message(thread: "thr_1", text: "words", typed: "words", shown: "words", choices: .none, images: [], uploaded: []),
            images: [Data("a".utf8), Data("b".utf8)])
        let current: [OutboxItem] = await outbox.current
        let item: OutboxItem = try XCTUnwrap(current.first)
        let back: OutboxItem? = await outbox.discard(item.id)
        let photos: [Data] = await outbox.photos(of: try XCTUnwrap(back))
        XCTAssertEqual(photos.map { String(decoding: $0, as: UTF8.self) }, ["a", "b"])
        let left: [OutboxItem] = await outbox.current
        XCTAssertTrue(left.isEmpty)
        XCTAssertTrue(onDisk().isEmpty)
    }

    func testAFileThatCannotBeReadIsKeptAsideNotLost() throws {
        try Data("not json".utf8).write(to: folder.appending(path: "outbox.json"))
        XCTAssertEqual(Outbox.read(folder.appending(path: "outbox.json")), [])
        XCTAssertTrue(FileManager.default.fileExists(atPath: folder.appending(path: "outbox.unreadable.json").path))
    }

    // MARK: Answers to cards

    func testAnAnswerGivenOfflineIsSentWhenBackAndOneThatCameTooLateSaysItExpired() async throws {
        daemon.ask("apr_ok")
        daemon.ask("apr_late")
        daemon.ask("apr_elsewhere")
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let outbox: Outbox = box(link: link)
        let (outcomes, watcher): (LockedValue<[OutboxOutcome]>, Task<Void, Never>) = await collect(outbox)
        await outbox.enqueue(.decision(approval: "apr_ok", run: "run_thr_1_0", thread: "thr_1", approve: true, note: ""))
        await outbox.enqueue(.decision(approval: "apr_late", run: "run_thr_1_0", thread: "thr_1", approve: true, note: ""))
        await outbox.enqueue(.decision(approval: "apr_elsewhere", run: "run_thr_1_0", thread: "thr_1", approve: false, note: ""))
        daemon.expire("apr_late")
        daemon.ask("apr_elsewhere", status: "approved")
        XCTAssertEqual(daemon.approvalStatus("apr_ok"), "pending", "nothing was sent while there was no link")
        link.report(.up)
        await expectSoon(5) { outcomes.value.count == 3 }
        watcher.cancel()
        var answered: [String: String] = [:]
        var expired: [String: String] = [:]
        for outcome in outcomes.value {
            switch outcome {
            case .answered(let item, let status):
                if case .decision(let approval, _, _, _, _) = item.action { answered[approval] = status }
            case .expired(let item, let status):
                if case .decision(let approval, _, _, _, _) = item.action { expired[approval] = status }
            default: XCTFail("unexpected \(outcome)")
            }
        }
        XCTAssertEqual(answered, ["apr_ok": "approved"])
        XCTAssertEqual(expired, ["apr_late": "expired", "apr_elsewhere": "approved"], "it says how the card stands, which is the owner's answer")
        XCTAssertEqual(daemon.approvalStatus("apr_ok"), "approved")
        XCTAssertEqual(daemon.approvalStatus("apr_late"), "expired", "an expired card was not decided")
    }

    // MARK: Stops and the queue

    func testAStopAndAChangeOfTheQueueThatFindEverythingAlreadyDoneAreDoneNotFailed() async throws {
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let outbox: Outbox = box(link: link)
        let (outcomes, watcher): (LockedValue<[OutboxOutcome]>, Task<Void, Never>) = await collect(outbox)
        daemon.failNext("/api/runs/run_thr_1_0/cancel", status: 409)
        daemon.failNext("/api/queue/q_gone", status: 404)
        await outbox.enqueue(.stop(run: "run_thr_1_0", thread: "thr_1"))
        await outbox.enqueue(.removeQueued(thread: "thr_1", message: "q_gone"))
        await outbox.enqueue(.reorder(thread: "thr_1", ids: ["a", "b"]))
        await expectSoon(5) { outcomes.value.count == 3 }
        watcher.cancel()
        XCTAssertTrue(outcomes.value.allSatisfy { if case .done = $0 { true } else { false } })
        let order: Data? = daemon.requests.last(where: { $0.method == "PUT" })?.body
        XCTAssertEqual((try? JSONSerialization.jsonObject(with: order ?? Data()) as? [String: Any])?["ids"] as? [String], ["a", "b"])
    }

    func testAnOrderTheDaemonRefusesIsToldAndDropped() async throws {
        daemon.failNext("/api/threads/thr_1/queue", status: 422, body: #"{"detail":"the queue changed"}"#)
        let link: LinkMonitor = LinkMonitor(initial: .up)
        let outbox: Outbox = box(link: link)
        let (outcomes, watcher): (LockedValue<[OutboxOutcome]>, Task<Void, Never>) = await collect(outbox)
        await outbox.enqueue(.reorder(thread: "thr_1", ids: ["a"]))
        await expectSoon(5) { !outcomes.value.isEmpty }
        watcher.cancel()
        guard case .refused(_, let words)? = outcomes.value.first else { return XCTFail("refused") }
        XCTAssertEqual(words, "the queue changed")
        let left: [OutboxItem] = await outbox.current
        XCTAssertTrue(left.isEmpty, "a change that cannot be made is not kept to be refused again")
    }
}

/// The outbox as the screens read it.
@MainActor
final class OutboxModelTests: XCTestCase {
    func testAMessageShowsTheMomentItIsMadeAndGoesWhenTheLinkAllows() async throws {
        let folder: URL = scratchFolder("dot-outbox-model")
        defer { try? FileManager.default.removeItem(at: folder) }
        let daemon: FakeDaemon = FakeDaemon()
        daemon.addChat("thr_1", runCount: 1, steps: 1)
        let link: LinkMonitor = LinkMonitor(initial: .down)
        let box: Outbox = Outbox(directory: folder, api: daemon.api(link: link), link: link, policy: quickRetry)
        let model: OutboxModel = OutboxModel(box: box, initial: [])
        model.watch()
        model.enqueue(.message(thread: "thr_1", text: "hi", typed: "hi", shown: "hi", choices: .none, images: [], uploaded: []))
        XCTAssertEqual(model.items(for: "thr_1").count, 1, "drawn before the actor has written it")
        XCTAssertEqual(model.items(for: "thr_2").count, 0)
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertEqual(model.items.count, 1, "the actor's own list does not make it twice")
        link.report(.up)
        await expectSoon(5) { daemon.queuedTexts == ["hi"] }
        await expectSoon(2) { model.items.isEmpty }
        model.stop()
    }
}
