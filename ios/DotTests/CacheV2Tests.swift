import XCTest
@testable import Dot

/// The disk cache's promises (item 1370): a write is whole or absent, a file that cannot be read is dropped and not
/// shown, a layout of another version is told apart, and the folder stays within its size with the chats read longest ago
/// going first.
final class CacheV2Tests: XCTestCase {
    private var directory: URL!

    override func setUp() { directory = scratchFolder("dot-cache2") }
    override func tearDown() { try? FileManager.default.removeItem(at: directory) }

    private func chat(_ id: String = "thr_1", events: Int = 20, marker: String = "a", bytes: Int = 200) -> StoredChat {
        let rows: [EventRecord] = (1...max(1, events)).map { index in
            event(index, "run_1", "assistant", ["step": .int(index), "text": .string("\(marker) " + String(repeating: "x", count: bytes))])
        }
        return StoredChat(threadId: id, runs: [makeRun()], helpers: [], events: rows, savedAt: 1)
    }

    private func age(_ id: String, days: Double) throws {
        let url: URL = directory.appending(path: "chat-\(id).json")
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(-days * 86_400)], ofItemAtPath: url.path)
    }

    // MARK: Atomic writes

    func testAReaderNeverSeesAHalfWrittenChat() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat(marker: "first"))
        let folder: URL = directory
        let reader: Task<Int, Never> = Task.detached {
            var unreadable: Int = 0
            for _ in 0..<400 {
                if ChatCache.readChat("thr_1", in: folder) == nil { unreadable += 1 }
                try? await Task.sleep(for: .milliseconds(1))
            }
            return unreadable
        }
        for round in 0..<60 {
            await cache.saveChat(chat(events: 30 + round, marker: round % 2 == 0 ? "even" : "odd", bytes: 2000))
        }
        let unreadable: Int = await reader.value
        XCTAssertEqual(unreadable, 0, "the file is replaced whole: a read finds the old or the new, never a part")
        let leftovers: [String] = try FileManager.default.contentsOfDirectory(atPath: directory.path).filter { !$0.hasPrefix("chat-") }
        XCTAssertEqual(leftovers, [], "no temporary file is left behind")
    }

    func testAWriteAfterTheFolderWasRemovedMakesTheFolderAgain() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        try FileManager.default.removeItem(at: directory)
        await cache.saveChat(chat())
        let loaded: StoredChat? = await cache.loadChat("thr_1")
        XCTAssertNotNil(loaded)
    }

    func testBytesThatDidNotChangeAreNotWrittenAgain() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        let url: URL = directory.appending(path: "chat-thr_1.json")
        await cache.saveChat(chat())
        // A mark left in the file shows whether the next save wrote it: the same bytes are not written again, others are.
        let written: Data = try Data(contentsOf: url)
        try (written + Data(" ".utf8)).write(to: url)
        await cache.saveChat(chat())
        XCTAssertEqual(try Data(contentsOf: url), written + Data(" ".utf8), "an identical chat was not written again")
        await cache.saveChat(chat(marker: "changed"))
        XCTAssertNotEqual(try Data(contentsOf: url), written + Data(" ".utf8))
        let back: StoredChat? = await cache.loadChat("thr_1")
        XCTAssertNotNil(back, "and what was written reads")
    }

    // MARK: Corruption

    func testAFileCutShortIsDroppedAndTheOthersAreUntouched() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat("good"))
        await cache.saveChat(chat("cut"))
        await cache.saveList(ListSnapshot(threads: [], agents: [], savedAt: 1))
        let url: URL = directory.appending(path: "chat-cut.json")
        let whole: Data = try Data(contentsOf: url)
        try whole.prefix(whole.count / 2).write(to: url)
        let cut: StoredChat? = await cache.loadChat("cut")
        let good: StoredChat? = await cache.loadChat("good")
        XCTAssertNil(cut)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        XCTAssertNotNil(good)
        XCTAssertNotNil(ChatCache.readList(in: directory))
    }

    func testEmptyAndForeignFilesAreDroppedToo() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        try Data().write(to: directory.appending(path: "chat-empty.json"))
        try Data("{\"threadId\": 4}".utf8).write(to: directory.appending(path: "chat-foreign.json"))
        try Data("garbage".utf8).write(to: directory.appending(path: "list.json"))
        try Data("garbage".utf8).write(to: directory.appending(path: "extras.json"))
        let empty: StoredChat? = await cache.loadChat("empty")
        let foreign: StoredChat? = await cache.loadChat("foreign")
        let extras: ExtrasSnapshot? = await cache.loadExtras()
        XCTAssertNil(empty)
        XCTAssertNil(foreign)
        XCTAssertNil(ChatCache.readList(in: directory))
        XCTAssertNil(extras)
    }

    // MARK: Schema versions

    func testAFileOfTheFirstLayoutStillReadsAndOneOfALaterLayoutIsDropped() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        var old: StoredChat = chat("old")
        old.schema = 1
        try JSONEncoder().encode(old).write(to: directory.appending(path: "chat-old.json"))
        var newer: StoredChat = chat("newer")
        newer.schema = ChatCache.schema + 1
        try JSONEncoder().encode(newer).write(to: directory.appending(path: "chat-newer.json"))
        let readOld: StoredChat? = await cache.loadChat("old")
        let readNewer: StoredChat? = await cache.loadChat("newer")
        XCTAssertEqual(readOld?.events.count, 20)
        XCTAssertNil(readOld?.queue, "a field the old layout lacks reads as none")
        XCTAssertNil(readNewer)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appending(path: "chat-newer.json").path))
    }

    func testAChatKeepsWhatStandsBesideItsTranscript() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        var held: StoredChat = chat()
        held.queue = QueueView(held: false, messages: [QueuedMessage(id: "q_1", position: 0, text: "later", createdAt: 1)], besideRunId: nil, besideThreadId: nil)
        held.listUpdatedAt = 77
        await cache.saveChat(held)
        let back: StoredChat? = await cache.loadChat("thr_1")
        XCTAssertEqual(back?.queue?.messages.first?.text, "later")
        XCTAssertEqual(back?.listUpdatedAt, 77)
        XCTAssertEqual(back?.schema, ChatCache.schema)
    }

    func testTheModelsAndTheSchedulesComeBack() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveExtras(ExtrasSnapshot(models: ModelChoices(main: "a:b", review: "c:d"), providers: nil, schedules: nil, savedAt: 5))
        let back: ExtrasSnapshot? = ChatCache.readExtras(in: directory)
        XCTAssertEqual(back?.models?.main, "a:b")
        XCTAssertEqual(back?.savedAt, 5)
    }

    // MARK: Size cap

    func testTheFolderIsBroughtUnderItsLimitByLettingGoTheChatsReadLongestAgo() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        let ids: [String] = (1...8).map { "c\($0)" }
        for id in ids { await cache.saveChat(chat(id, events: 60, bytes: 1500)) }
        await cache.saveList(ListSnapshot(threads: [], agents: [], savedAt: 1))
        await cache.saveExtras(ExtrasSnapshot(models: nil, providers: nil, schedules: nil, savedAt: 1))
        for (number, id) in ids.enumerated() { try age(id, days: Double(10 - number)) }
        // c1 is the oldest; reading it makes it the newest.
        XCTAssertNotNil(ChatCache.readChat("c1", in: directory))
        let total: Int = await cache.size()
        let each: Int = (try Data(contentsOf: directory.appending(path: "chat-c2.json"))).count
        let removed: Int = await cache.compact(limit: total - 3 * each)
        XCTAssertGreaterThanOrEqual(removed, 3)
        let kept: [String] = ids.filter { FileManager.default.fileExists(atPath: directory.appending(path: "chat-\($0).json").path) }
        XCTAssertTrue(kept.contains("c1"), "the chat read last is the last to go")
        XCTAssertFalse(kept.contains("c2"), "the oldest unread goes first")
        XCTAssertFalse(kept.contains("c3"))
        let after: Int = await cache.size()
        XCTAssertLessThanOrEqual(after, total - 3 * each)
        XCTAssertNotNil(ChatCache.readList(in: directory), "the list is never let go")
        XCTAssertNotNil(ChatCache.readExtras(in: directory), "nor the models and schedules")
    }

    func testAFolderWithinItsLimitLosesNothing() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        await cache.saveChat(chat("a"))
        await cache.saveChat(chat("b"))
        let removed: Int = await cache.compact(limit: ChatCache.byteLimit)
        XCTAssertEqual(removed, 0)
    }

    func testPruneDropsTheChatsTheListLostAndKeepsTheRest() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        for id in ["a", "b", "c"] { await cache.saveChat(chat(id)) }
        await cache.prune(keeping: ["a", "c"])
        let gone: StoredChat? = await cache.loadChat("b")
        let kept: StoredChat? = await cache.loadChat("a")
        XCTAssertNil(gone)
        XCTAssertNotNil(kept)
    }

    func testTheCacheFolderHoldsWhatTheFirstFrameNeedsInOneReadEach() async throws {
        let cache: ChatCache = ChatCache(directory: directory)
        let threads: [ThreadSummary] = (1...100).map { number in
            ThreadSummary(
                id: "thr_\(number)", title: "Chat \(number)", source: "ui", updatedAt: Double(number), pinnedAt: nil, runCount: 3,
                latestRunId: "run_\(number)", latestStatus: "done", latestMode: "task", unread: false, actorId: nil,
                lastMessage: String(repeating: "m", count: 120), lastRole: "assistant", loopCron: nil, loopNextAt: nil)
        }
        await cache.saveList(ListSnapshot(threads: threads, agents: [], savedAt: 1))
        let began: ContinuousClock.Instant = .now
        let list: ListSnapshot? = ChatCache.readList(in: directory)
        let took: Double = began.duration(to: .now).inSeconds
        XCTAssertEqual(list?.threads.count, 100)
        XCTAssertLessThan(took, 0.05, "a list of a hundred chats is read in well under a frame's budget of three")
    }
}
