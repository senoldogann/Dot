import Foundation
import XCTest

@testable import DotEngine

/// The outbox MOVED from the old app (plan §11) with its tests (`ios/DotTests/OutboxTests.swift`, item 1371), on the new
/// contract's fake daemon and a hand-moved clock: in order, once each, whatever was lost on the way.
final class OutboxTests: XCTestCase {
    private var folder: URL!
    private var daemon: FakeDaemon!
    private var clock: ManualClock!
    private let quick: RetryPolicy = RetryPolicy(attempts: Int.max, base: 1, cap: 1, budget: Double.infinity)

    override func setUp() async throws {
        folder = scratchFolder()
        daemon = FakeDaemon(version: "1.0")
        daemon.addChat("thr_1", title: "Chat")
        daemon.addRun("run_1", thread: "thr_1", goal: "Work", status: "running", parent: nil)
        clock = ManualClock(at: 1_800_000_000)
    }

    override func tearDown() async throws { try? FileManager.default.removeItem(at: folder) }

    private func box(_ link: LinkWatcher, mayStartChat: @escaping @Sendable () async -> Bool) -> Outbox {
        Outbox(
            directory: folder, api: daemon.api(), link: link, clock: clock, policy: quick, jitter: { 0 },
            maySend: { action in
                if case .newChat = action { return await mayStartChat() }
                return true
            })
    }

    private func message(_ text: String) -> OutboxItem.Action { .message(thread: "thr_1", text: text, images: [], uploaded: [], picks: .none) }

    private func onDisk() -> [OutboxItem] { Outbox.read(folder.appending(path: "outbox.json")) }

    /// Waits until the outbox's items satisfy `holds` (its own change stream wakes the wait; 5 s guard).
    private func items(_ outbox: Outbox, _ what: String, _ holds: @escaping @Sendable ([OutboxItem]) -> Bool) async throws {
        let changes: AsyncStream<[OutboxItem]> = await outbox.changes()
        let ok: Bool = await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                for await items in changes where holds(items) { return true }
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
            let left: [OutboxItem] = await outbox.current
            XCTFail("timed out: \(what); items=\(left)")
            throw TimedOut(what: what)
        }
    }

    private func collect(_ outbox: Outbox) async -> Collected {
        let collected: Collected = Collected()
        let stream: AsyncStream<OutboxOutcome> = await outbox.outcomes()
        collected.task = Task { for await outcome in stream { collected.add(outcome) } }
        return collected
    }

    // MARK: Messages

    func testMessagesMadeOfflineAreKeptOnDiskAndSentInOrderOnceWhenTheLinkReturns() async throws {
        let link: LinkWatcher = LinkWatcher(initial: .down)
        let first: Outbox = box(link, mayStartChat: { true })
        for text in ["one", "two", "three"] { try await first.enqueue(message(text), id: newMessageKey(), images: []) }
        XCTAssertEqual(onDisk().count, 3, "kept on disk before any link")
        XCTAssertEqual(daemon.count("POST", "/queue"), 0, "nothing was asked of a network that is not there")
        // The app is killed here: a new outbox reads the file.
        await first.suspend()
        let second: Outbox = box(link, mayStartChat: { true })
        let held: [OutboxItem] = await second.current
        XCTAssertEqual(held.map(\.kind), [.queue, .queue, .queue])
        XCTAssertTrue(held.allSatisfy { $0.phase == .waiting })
        link.report(.up)
        await second.start()
        try await items(second, "all sent") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["one", "two", "three"], "in the order they were made")
        XCTAssertTrue(onDisk().isEmpty, "what was sent leaves the file")
    }

    func testVideoAndDocumentKeepTheirNamesTypesAndBytesAcrossAnOfflineRestart() async throws {
        let link: LinkWatcher = LinkWatcher(initial: .down)
        let first: Outbox = box(link, mayStartChat: { true })
        let files: [OutgoingAttachment] = [
            OutgoingAttachment(name: "Walk.mp4", mediaType: "video/mp4", data: Data([0, 0, 0, 20, 102, 116, 121, 112])),
            OutgoingAttachment(name: "Notes.txt", mediaType: "text/plain", data: Data("walk notes".utf8)),
        ]
        try await first.enqueue(message("Two attachments"), id: newMessageKey(), attachments: files)
        await first.suspend()
        let next: Outbox = box(link, mayStartChat: { true })
        link.report(.up)
        await next.start()
        try await items(next, "message delivered") { $0.isEmpty }

        let uploads: [FakeDaemon.Seen] = daemon.requests.filter { $0.path == "/api/attachments" && $0.method == "POST" }
        XCTAssertEqual(uploads.count, files.count)
        for (request, file) in zip(uploads, files) {
            let body: [String: String] = try JSONDecoder().decode([String: String].self, from: XCTUnwrap(request.body))
            guard case .bytes(let data) = file.content else { return XCTFail("a document is held as bytes") }
            XCTAssertEqual(body, ["name": file.name, "media_type": file.mediaType, "data_b64": data.base64EncodedString()])
        }
        let sent: FakeDaemon.Seen = try XCTUnwrap(daemon.requests.first { $0.path == "/api/threads/thr_1/queue" && $0.method == "POST" })
        let message: QueueMessage = try JSONDecoder().decode(QueueMessage.self, from: XCTUnwrap(sent.body))
        XCTAssertEqual(message.attachments, ["att_1", "att_2"])
    }

    func testAVideoFileIsKeptBesideItsRecordAndSentAsTheRawBodyOfTheStreamedUpload() async throws {
        let link: LinkWatcher = LinkWatcher(initial: .down)
        let first: Outbox = box(link, mayStartChat: { true })
        let bytes: Data = Data((0..<300_000).map { UInt8($0 % 251) })
        let source: URL = folder.appending(path: "source-movie.mp4")
        try bytes.write(to: source)
        let video: OutgoingAttachment = OutgoingAttachment(name: "Walk.mp4", mediaType: "video/mp4", file: source, byteCount: bytes.count)
        let key: String = newMessageKey()
        try await first.enqueue(message("A video"), id: key, attachments: [video])
        // The composer's own file may go (its chip is removed, the system clears its temporary folder): the outbox kept a copy.
        try FileManager.default.removeItem(at: source)
        await first.suspend()
        let kept: URL = folder.appending(path: "outbox-images/\(key)-0.media")
        XCTAssertEqual(try Data(contentsOf: kept), bytes, "the video stays in the outbox's folder, not in its record")
        let record: Data = try Data(contentsOf: folder.appending(path: "outbox-images/\(key)-0.attachment"))
        XCTAssertLessThan(record.count, 2_000, "the record holds a name and no bytes")

        let next: Outbox = box(link, mayStartChat: { true })
        link.report(.up)
        await next.start()
        try await items(next, "message delivered") { $0.isEmpty }
        let uploads: [FakeDaemon.Seen] = daemon.requests.filter { $0.path == "/api/attachments/stream" }
        XCTAssertEqual(uploads.count, 1)
        let upload: FakeDaemon.Seen = try XCTUnwrap(uploads.first)
        XCTAssertEqual(upload.method, "PUT")
        XCTAssertEqual(upload.query, ["name": "Walk.mp4", "media_type": "video/mp4"])
        XCTAssertEqual(upload.body, bytes, "the file is the body as it is, not base64 in JSON")
        XCTAssertTrue(daemon.requests.allSatisfy { $0.path != "/api/attachments" }, "nothing went as JSON")
        XCTAssertFalse(FileManager.default.fileExists(atPath: kept.path), "what was sent leaves the folder")
    }

    func testCancellingAVideoUploadKeepsItsFileForRetry() async throws {
        daemon.holdUploads = true
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        let source: URL = folder.appending(path: "movie.mp4")
        try Data(repeating: 7, count: 50_000).write(to: source)
        let key: String = newMessageKey()
        try await outbox.enqueue(message("A video"), id: key, attachments: [OutgoingAttachment(name: "Walk.mp4", mediaType: "video/mp4", file: source, byteCount: 50_000)])
        try await items(outbox, "uploading") { $0.first?.attachmentUpload?.count == 1 }
        await outbox.cancelUpload(key)
        try await items(outbox, "cancelled") { $0.first?.phase == .failed }
        XCTAssertTrue(FileManager.default.fileExists(atPath: folder.appending(path: "outbox-images/\(key)-0.media").path))

        daemon.holdUploads = false
        await outbox.retry(key)
        try await items(outbox, "delivered on retry") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["A video"])
        XCTAssertTrue(daemon.requests.contains { $0.path == "/api/attachments/stream" && $0.body?.count == 50_000 }, "the retry sent the kept file whole")
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.appending(path: "outbox-images/\(key)-0.media").path))
    }

    func testCancellingAnAttachmentStopsTheMessageAndKeepsItForRetry() async throws {
        daemon.holdUploads = true
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        let key: String = newMessageKey()
        let file: OutgoingAttachment = OutgoingAttachment(name: "Notes.txt", mediaType: "text/plain", data: Data("notes".utf8))
        try await outbox.enqueue(message("Read these notes"), id: key, attachments: [file])
        try await items(outbox, "uploading") { $0.first?.attachmentUpload?.count == 1 }
        await outbox.cancelUpload(key)
        try await items(outbox, "cancelled") { $0.first?.phase == .failed && $0.first?.attachmentUpload == nil }
        XCTAssertEqual(onDisk().first?.failure, .uploadCancelled)
        XCTAssertEqual(daemon.count("POST", "/queue"), 0)

        daemon.holdUploads = false
        await outbox.retry(key)
        try await items(outbox, "delivered on retry") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["Read these notes"])
        let sent: FakeDaemon.Seen = try XCTUnwrap(daemon.requests.first { $0.path.hasSuffix("/queue") && $0.method == "POST" })
        let queued: QueueMessage = try JSONDecoder().decode(QueueMessage.self, from: XCTUnwrap(sent.body))
        XCTAssertEqual(queued.attachments.count, 1)
    }

    func testAnAnswerLostAfterTheDaemonTookTheMessageDoesNotSendItTwice() async throws {
        daemon.loseNextAnswers(2)
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        let outcomes: Collected = await collect(outbox)
        try await outbox.enqueue(message("exactly once"), id: newMessageKey(), images: [])
        try await clock.advancePastSleep(of: 0.5, count: 1, since: 0)
        try await clock.advancePastSleep(of: 0.5, count: 2, since: 0)
        try await items(outbox, "delivered") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["exactly once"], "the daemon took it once, by its key")
        XCTAssertEqual(daemon.count("POST", "/api/threads/thr_1/queue"), 3, "two answers lost, one heard")
        try await outcomes.wait { $0.count == 1 }
        guard case .delivered(_, let queue)? = outcomes.all.first else { return XCTFail("it was delivered") }
        XCTAssertEqual(queue.messages.map(\.text), ["exactly once"])
    }

    func testAMessageBeingSentAlreadyOnTheMacIsWaitedOutNotRefused() async throws {
        daemon.failNext("/api/threads/thr_1/queue", status: 409, body: #"{"detail":"the message is being sent already"}"#)
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        try await outbox.enqueue(message("patient"), id: newMessageKey(), images: [])
        try await clock.advancePastSleep(of: 0.5, count: 1, since: 0)
        try await items(outbox, "delivered") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["patient"])
    }

    func testAMessageTheDaemonRefusesStaysAsNotSentWithItsReasonAndDoesNotBlockTheNext() async throws {
        daemon.failNext("/api/threads/thr_1/queue", status: 409, body: #"{"detail":"the chat is being deleted"}"#)
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        try await outbox.enqueue(message("refused"), id: newMessageKey(), images: [])
        try await outbox.enqueue(message("fine"), id: newMessageKey(), images: [])
        try await items(outbox, "the refused one stays") { $0.count == 1 && $0.first?.phase == .failed }
        XCTAssertEqual(daemon.acceptedTexts, ["fine"])
        let current: [OutboxItem] = await outbox.current
        let held: OutboxItem = try XCTUnwrap(current.first)
        XCTAssertEqual(held.failure, .refused(code: 409, detail: "the chat is being deleted"), "a 409 other than 'being sent' is a refusal")
        XCTAssertEqual(held.text, "refused", "the words stay, for the composer")
        await outbox.retry(held.id)
        try await items(outbox, "sent on retry") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["fine", "refused"])
    }

    func testAMessageThatWaitedMoreThanADayIsNotSentByItselfAndIsSentAsANewOneWhenAsked() async throws {
        daemon.offline = true
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        let key: String = newMessageKey()
        try await outbox.enqueue(message("old"), id: key, images: [])
        try await clock.advancePastSleep(of: 0.5, count: 1, since: 0)
        clock.advance(25 * 3600)
        try await items(outbox, "failed as too old") { $0.first?.phase == .failed }
        let failure: OutboxItem.Failure? = await outbox.current.first?.failure
        XCTAssertEqual(failure, .tooOld)
        daemon.offline = false
        XCTAssertEqual(daemon.acceptedTexts, [], "not sent by itself: the daemon forgot its key")
        await outbox.retry(key)
        try await items(outbox, "sent as a new one") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["old"])
        let sent: QueueMessage? = daemon.requests.last { $0.path.hasSuffix("/queue") }?.body.flatMap { try? JSONDecoder().decode(QueueMessage.self, from: $0) }
        XCTAssertNotEqual(sent?.key, key, "with a new key")
    }

    func testPhotosAreKeptBesideTheFileUploadedOnceAndLetGoWhenSent() async throws {
        daemon.loseNextAnswers(1)
        let link: LinkWatcher = LinkWatcher(initial: .down)
        let outbox: Outbox = box(link, mayStartChat: { true })
        try await outbox.enqueue(message("(photo)"), id: newMessageKey(), images: [Data("jpeg-bytes".utf8)])
        let images: URL = folder.appending(path: "outbox-images")
        XCTAssertEqual((try? FileManager.default.contentsOfDirectory(atPath: images.path))?.count, 1)
        link.report(.up)
        await outbox.nudge()
        try await clock.advancePastSleep(of: 0.5, count: 1, since: 0)
        try await items(outbox, "sent") { $0.isEmpty }
        XCTAssertEqual(daemon.acceptedTexts, ["(photo)"])
        XCTAssertEqual(daemon.uploadCount, 1, "the photo was kept once though the message was sent twice")
        XCTAssertEqual((try? FileManager.default.contentsOfDirectory(atPath: images.path)) ?? [], [])
    }

    func testAMessageWhosePhotoIsGoneIsKeptWithTheReasonNeverSentShort() async throws {
        let link: LinkWatcher = LinkWatcher(initial: .down)
        let outbox: Outbox = box(link, mayStartChat: { true })
        try await outbox.enqueue(message("two photos"), id: newMessageKey(), images: [Data("a".utf8), Data("b".utf8)])
        let images: URL = folder.appending(path: "outbox-images")
        let first: String = try XCTUnwrap(try FileManager.default.contentsOfDirectory(atPath: images.path).sorted().first)
        try FileManager.default.removeItem(at: images.appending(path: first))
        link.report(.up)
        await outbox.nudge()
        try await items(outbox, "refused") { $0.first?.phase == .failed }
        let left: [OutboxItem] = await outbox.current
        XCTAssertEqual(left.first?.failure, .photoMissing(name: first))
        XCTAssertEqual(daemon.acceptedTexts, [], "not sent short of a photo")
        XCTAssertEqual(daemon.uploadCount, 0)
    }

    func testEditTakesAMessageBackWithItsPhotos() async throws {
        let outbox: Outbox = box(LinkWatcher(initial: .down), mayStartChat: { true })
        let key: String = newMessageKey()
        try await outbox.enqueue(message("words"), id: key, images: [Data("a".utf8), Data("b".utf8)])
        let back: (item: OutboxItem, photos: [Data])? = await outbox.discard(key)
        XCTAssertEqual(back?.photos.map { String(decoding: $0, as: UTF8.self) }, ["a", "b"])
        XCTAssertEqual(back?.item.text, "words")
        let left: [OutboxItem] = await outbox.current
        XCTAssertTrue(left.isEmpty)
        XCTAssertTrue(onDisk().isEmpty)
    }

    func testAFileThatCannotBeReadIsKeptAsideNotLost() throws {
        try Data("not json".utf8).write(to: folder.appending(path: "outbox.json"))
        XCTAssertEqual(Outbox.read(folder.appending(path: "outbox.json")), [])
        XCTAssertTrue(FileManager.default.fileExists(atPath: folder.appending(path: "outbox.unreadable.json").path))
    }

    // MARK: Doings that say the same twice

    func testAnAnswerAlreadyGivenElsewhereIsAnOutcomeNotAFailure() async throws {
        daemon.decideElsewhere("apr_mac", status: "denied")
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        let outcomes: Collected = await collect(outbox)
        try await outbox.enqueue(.decision(approval: "apr_ok", run: "run_1", thread: "thr_1", approve: true, note: "", remember: false), id: newMessageKey(), images: [])
        try await outbox.enqueue(.decision(approval: "apr_mac", run: "run_1", thread: "thr_1", approve: true, note: "", remember: false), id: newMessageKey(), images: [])
        try await outcomes.wait { $0.count == 2 }
        guard case .answered(_, "approved") = outcomes.all[0] else { return XCTFail("answered: \(outcomes.all[0])") }
        guard case .expired(_, "denied") = outcomes.all[1] else { return XCTFail("already decided on the Mac: \(outcomes.all[1])") }
    }

    func testAStopOfARunAlreadyOverIsDoneNotFailed() async throws {
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { true })
        let outcomes: Collected = await collect(outbox)
        try await outbox.enqueue(.stop(run: "run_gone", thread: "thr_1"), id: newMessageKey(), images: [])
        try await outcomes.wait { $0.count == 1 }
        guard case .done = outcomes.all[0] else { return XCTFail("done: \(outcomes.all[0])") }
    }

    func testANewChatWaitsUntilTheListIsLive() async throws {
        let live: Flag = Flag()
        let outbox: Outbox = box(LinkWatcher(initial: .up), mayStartChat: { live.value })
        try await outbox.enqueue(.newChat(text: "A new chat", images: [], uploaded: [], start: nil, agent: nil), id: newMessageKey(), images: [])
        try await clock.advancePastSleep(of: 5, count: 1, since: 0)
        XCTAssertEqual(daemon.count("POST", "/api/runs"), 0, "not sent onto a list that is not live")
        let kind: SendingKind? = await outbox.current.first?.kind
        XCTAssertEqual(kind, .run)
    }
}

/// What an outbox told, for the test to read.
final class Collected: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var seen: [OutboxOutcome] = []
    private var watchers: [UUID: AsyncStream<Void>.Continuation] = [:]
    var task: Task<Void, Never>?

    deinit { task?.cancel() }

    var all: [OutboxOutcome] { lock.withLock { seen } }

    func add(_ outcome: OutboxOutcome) {
        let tell: [AsyncStream<Void>.Continuation] = lock.withLock {
            seen.append(outcome)
            return Array(watchers.values)
        }
        for one in tell { one.yield() }
    }

    func wait(_ holds: @escaping @Sendable ([OutboxOutcome]) -> Bool) async throws {
        let (stream, continuation): (AsyncStream<Void>, AsyncStream<Void>.Continuation) = AsyncStream.makeStream(of: Void.self)
        let id: UUID = UUID()
        lock.withLock { watchers[id] = continuation }
        defer { lock.withLock { watchers[id] = nil } }
        let ok: Bool = await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                if holds(self.all) { return true }
                for await _ in stream where holds(self.all) { return true }
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
            XCTFail("timed out; outcomes=\(all)")
            throw TimedOut(what: "outcomes")
        }
    }
}

final class Flag: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var on: Bool = false
    var value: Bool {
        get { lock.withLock { on } }
        set { lock.withLock { on = newValue } }
    }
}

/// A delete the daemon refuses (a chat with a run or a helper at work, 409) is kept in the snapshot for the list to say
/// (item 1800): the outbox drops a refused doing and would otherwise tell nobody.
final class RefusedDeleteTests: XCTestCase {
    /// The review of item 1964: the Mac answered the delete before its notice came on the list's stream (or with the
    /// stream down): the chats stay out of the list, and a read of the list from before does not bring them back.
    func testChatsTheMacDeletedStayOutOfTheListBeforeItsNotice() async throws {
        let folder: URL = scratchFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let store: SyncStore = SyncStore(cache: SyncCache(folder: folder), clock: ManualClock(at: 1_800_000_000))
        let row: (String) -> ThreadRow = { id in
            ThreadRow(
                summary: ThreadSummary(
                    id: id, title: id, source: "ui", createdAt: 1, updatedAt: 1, pinnedAt: nil, runCount: 1, latestRunId: "r",
                    latestStatus: "done", latestCreatedAt: 1, unread: false, actorId: nil, lastMessage: "", lastRole: "user"),
                revision: 4, asks: 0, helpers: 0, background: [])
        }
        let before: ListProjection = ListProjection.drawn(rows: [row("thr_a"), row("thr_b"), row("thr_c")])
        let after: ListProjection = ListReducer.deletedHere(before, threads: ["thr_a", "thr_b"])
        XCTAssertEqual(after.rows.map(\.summary.id), ["thr_c"])
        let stale: ListProjection = ListReducer.loaded(
            ListReducer.asked(after), view: ThreadsView(revision: 5, threads: [row("thr_a"), row("thr_b"), row("thr_c")]), connection: after.marks.connection).0
        XCTAssertEqual(stale.rows.map(\.summary.id), ["thr_c"], "a read from before the deletion does not bring them back")
        // The store's own list: the delete seen in the outbox, the outbox emptied before the outcome is handled.
        let (connection, _): (Int, [ListEffect]) = await store.listOpening()
        await store.listOpened(connection)
        _ = await store.listAsked()
        _ = await store.listLoaded(ThreadsView(revision: 5, threads: [row("thr_a"), row("thr_b"), row("thr_c")]), connection: connection)
        let item: OutboxItem = OutboxItem(id: "k1", action: .deleteChats(threads: ["thr_a", "thr_b"]), createdAt: 1)
        await store.outbox([item])
        await store.outbox([])
        let between: EngineSnapshot = await store.snapshot
        XCTAssertEqual(between.shownList.rows.map(\.summary.id), ["thr_c"], "dropped from the outbox, not yet handled: still hidden")
        await store.outcome(.done(item))
        let answered: EngineSnapshot = await store.snapshot
        XCTAssertEqual(answered.list.rows.map(\.summary.id), ["thr_c"], "the store's list holds them deleted")
        XCTAssertTrue(answered.deletesAwaiting.isEmpty)
    }

    func testARefusedDeleteIsKeptUntilTheListToldIt() async throws {
        let folder: URL = scratchFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let store: SyncStore = SyncStore(cache: SyncCache(folder: folder), clock: ManualClock(at: 1_800_000_000))
        let item: OutboxItem = OutboxItem(id: "k1", action: .delete(thread: "thr_busy"), createdAt: 1)
        await store.outcome(.refused(item, failure: .refused(code: 409, detail: "a run of the chat is at work")))
        let refused: [String: String] = await store.snapshot.refusedDeletes
        XCTAssertEqual(refused, ["thr_busy": "a run of the chat is at work"])
        await store.outcome(.done(OutboxItem(id: "k2", action: .delete(thread: "thr_free"), createdAt: 1)))
        let still: [String: String] = await store.snapshot.refusedDeletes
        XCTAssertEqual(still.keys.sorted(), ["thr_busy"], "a delete that went through is not one")
        await store.forgetRefusedDeletes()
        let after: [String: String] = await store.snapshot.refusedDeletes
        XCTAssertEqual(after, [:])
    }
}
