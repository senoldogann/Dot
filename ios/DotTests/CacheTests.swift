import XCTest
@testable import Dot

final class ChatCacheTests: XCTestCase {
    private var directory: URL!

    override func setUp() {
        directory = FileManager.default.temporaryDirectory.appending(path: "dot-cache-\(UUID().uuidString)")
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: directory)
    }

    private func chat(events: Int, id: String = "thr_1") -> StoredChat {
        let evs: [EventRecord] = (1...max(1, events)).map { index in
            event(index, "run_1", index % 2 == 0 ? "tool_result" : "tool_call", [
                "call_id": .string("c\((index + 1) / 2)"), "tool": .string("shell"), "args": .object(["command": .string("echo \(index)")]),
                "ok": .bool(true), "text": .string(String(repeating: "x", count: 5000)), "images": .array([.string("a.png")]),
            ])
        }
        return StoredChat(threadId: id, runs: [makeRun()], helpers: [], events: evs, savedAt: 1)
    }

    func testAChatComesBackAsItWasSaved() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat(events: 10))
        let loaded: StoredChat? = await cache.loadChat("thr_1")
        XCTAssertEqual(loaded?.events.count, 10)
        XCTAssertEqual(loaded?.runs.first?.id, "run_1")
    }

    func testOnlyTheTailOfTheEventsIsKeptAndTheirBulkIsTrimmed() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat(events: StoredChat.tail + 50))
        let loadedChat: StoredChat? = await cache.loadChat("thr_1")
        let loaded: StoredChat = try XCTUnwrap(loadedChat)
        XCTAssertEqual(loaded.events.count, StoredChat.tail)
        XCTAssertEqual(loaded.events.first?.id, 51)
        XCTAssertEqual(loaded.events.last?.id, StoredChat.tail + 50)
        XCTAssertLessThanOrEqual(loaded.events[1].data["text"]?.string?.count ?? 0, 240)
        XCTAssertNil(loaded.events[1].data["images"])
    }

    func testTheStateIsBuiltFromTheCachedEvents() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat(events: 6))
        let loadedChat: StoredChat? = await cache.loadChat("thr_1")
        let state: ChatState = try XCTUnwrap(loadedChat).state()
        XCTAssertEqual(state.items.count, 4, "the message and three tool rows")
        XCTAssertEqual(state.lastEventId, 6)
    }

    func testAChatOpensFromTheCacheWellInsideAFrame() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat(events: StoredChat.tail))
        let clock: ContinuousClock = ContinuousClock()
        let storedChat: StoredChat? = await cache.loadChat("thr_1")
        let stored: StoredChat = try XCTUnwrap(storedChat)
        let readStart: ContinuousClock.Instant = clock.now
        _ = await cache.loadChat("thr_1")
        let read: Duration = readStart.duration(to: clock.now)
        let built: Duration = clock.measure { _ = stored.state() }
        print("cache read of \(StoredChat.tail) events: \(read); state build: \(built)")
        XCTAssertLessThan(built, .milliseconds(16), "the state of a full cache is built in under a frame")
        XCTAssertLessThan(read, .milliseconds(100))
    }

    func testAFileThatDoesNotReadIsDroppedNotShown() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        try "not json".write(to: directory.appending(path: "chat-thr_1.json"), atomically: true, encoding: .utf8)
        let loaded: StoredChat? = await cache.loadChat("thr_1")
        XCTAssertNil(loaded)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appending(path: "chat-thr_1.json").path))
    }

    func testAFileOfAnotherLayoutIsDropped() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        var old: StoredChat = chat(events: 3)
        old.schema = ChatCache.schema + 1
        try JSONEncoder().encode(old).write(to: directory.appending(path: "chat-thr_1.json"))
        let loaded: StoredChat? = await cache.loadChat("thr_1")
        XCTAssertNil(loaded)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appending(path: "chat-thr_1.json").path))
    }

    func testPruneKeepsTheChatsOfTheList() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat(events: 2, id: "keep"))
        await cache.saveChat(chat(events: 2, id: "gone"))
        await cache.prune(keeping: ["keep"])
        let kept: StoredChat? = await cache.loadChat("keep")
        let gone: StoredChat? = await cache.loadChat("gone")
        XCTAssertNotNil(kept)
        XCTAssertNil(gone)
    }

    func testTheListIsReadWithoutWaitingOnTheActor() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        let thread: ThreadSummary = ThreadSummary(
            id: "thr_1", title: "T", source: "ui", updatedAt: 1, pinnedAt: nil, runCount: 1, latestRunId: "run_1",
            latestStatus: "done", latestMode: "task", unread: true, actorId: nil, lastMessage: "hi", lastRole: "assistant",
            loopCron: nil, loopNextAt: nil)
        await cache.saveList(ListSnapshot(threads: [thread], agents: [], savedAt: 1))
        XCTAssertEqual(ChatCache.readList(in: directory)?.threads, [thread])
    }
}

final class QueueOrderTests: XCTestCase {
    func testAMessageMovesUpAndDownOnePlace() {
        let ids: [String] = ["a", "b", "c"]
        XCTAssertEqual(queueOrder(ids, moving: "b", by: -1), ["b", "a", "c"])
        XCTAssertEqual(queueOrder(ids, moving: "b", by: 1), ["a", "c", "b"])
    }

    func testTheEndsHaveNoFurtherToGo() {
        let ids: [String] = ["a", "b", "c"]
        XCTAssertNil(queueOrder(ids, moving: "a", by: -1))
        XCTAssertNil(queueOrder(ids, moving: "c", by: 1))
    }

    func testAnUnknownMessageOrNoStepMovesNothing() {
        XCTAssertNil(queueOrder(["a", "b"], moving: "z", by: 1))
        XCTAssertNil(queueOrder(["a", "b"], moving: "a", by: 0))
        XCTAssertNil(queueOrder([], moving: "a", by: 1))
    }

    /// A drag of the handles (item 1643): a list's `onMove` says which rows go before which.
    func testADragMovesRowsBeforeTheRowTheyAreDroppedOn() {
        let ids: [String] = ["a", "b", "c", "d"]
        XCTAssertEqual(queueOrder(ids, moving: IndexSet(integer: 3), to: 0), ["d", "a", "b", "c"], "the last to the top")
        XCTAssertEqual(queueOrder(ids, moving: IndexSet(integer: 0), to: 4), ["b", "c", "d", "a"], "the first to the end")
        XCTAssertEqual(queueOrder(ids, moving: IndexSet(integer: 0), to: 2), ["b", "a", "c", "d"], "down one place: before the row after it")
        XCTAssertEqual(queueOrder(ids, moving: IndexSet([0, 1]), to: 4), ["c", "d", "a", "b"], "two rows together keep their order")
    }

    func testADropWhereTheRowStoodMovesNothing() {
        let ids: [String] = ["a", "b", "c"]
        XCTAssertNil(queueOrder(ids, moving: IndexSet(integer: 1), to: 1))
        XCTAssertNil(queueOrder(ids, moving: IndexSet(integer: 1), to: 2), "just below its own place is its own place")
        XCTAssertNil(queueOrder(ids, moving: IndexSet(), to: 0))
        XCTAssertNil(queueOrder(ids, moving: IndexSet(integer: 5), to: 0), "a row that is not there")
        XCTAssertNil(queueOrder(ids, moving: IndexSet(integer: 0), to: 9), "a place that is not there")
    }

    func testEveryMessageStaysOnceWhateverIsMoved() {
        let ids: [String] = ["a", "b", "c", "d"]
        for id in ids {
            for step in [-1, 1] {
                if let moved: [String] = queueOrder(ids, moving: id, by: step) {
                    XCTAssertEqual(moved.sorted(), ids.sorted())
                }
            }
        }
    }
}
