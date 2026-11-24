import XCTest
@testable import Dot

final class SSEParserTests: XCTestCase {
    private let stream: String = ": opening\n\nid: 4\nevent: tool_call\ndata: {\"a\":1}\n\n: keepalive\n\nid: 5\nevent: assistant\ndata: one\ndata: two\n\n"

    private func parse(_ chunks: [Data]) -> ([SSEMessage], String?) {
        var parser: SSEParser = SSEParser()
        var all: [SSEMessage] = []
        for chunk in chunks { all += parser.feed(chunk) }
        return (all, parser.lastEventId)
    }

    func testWholeStream() {
        let (messages, last) = parse([Data(stream.utf8)])
        XCTAssertEqual(messages, [
            SSEMessage(event: "tool_call", data: "{\"a\":1}", id: "4"),
            SSEMessage(event: "assistant", data: "one\ntwo", id: "5"),
        ])
        XCTAssertEqual(last, "5")
    }

    func testCommentsAndKeepalivesAreNoMessages() {
        let (messages, _) = parse([Data(": a\n\n: b\n\n".utf8)])
        XCTAssertTrue(messages.isEmpty)
    }

    func testEveryChunkBoundaryGivesTheSameMessages() {
        let bytes: [UInt8] = Array(stream.utf8)
        let whole: [SSEMessage] = parse([Data(bytes)]).0
        for cut in 0...bytes.count {
            XCTAssertEqual(parse([Data(bytes[..<cut]), Data(bytes[cut...])]).0, whole, "cut at \(cut)")
        }
        XCTAssertEqual(parse(bytes.map { Data([$0]) }).0, whole, "one byte at a time")
    }

    func testAMultiByteCharacterCutInTwoIsReadWhole() {
        let bytes: [UInt8] = Array("data: héllo ✓\n\n".utf8)
        for cut in 0...bytes.count {
            XCTAssertEqual(parse([Data(bytes[..<cut]), Data(bytes[cut...])]).0.first?.data, "héllo ✓", "cut at \(cut)")
        }
    }

    func testCarriageReturnLineEndings() {
        let (messages, _) = parse([Data("id: 1\r\ndata: a\r\n\r\ndata: b\r\rdata: c".utf8)])
        XCTAssertEqual(messages.map(\.data), ["a", "b"])  // "c" is not finished
        // A `\r` that ends a chunk waits for a possible `\n` after it.
        XCTAssertEqual(parse([Data("data: x\r".utf8), Data("\n\r\n".utf8)]).0.map(\.data), ["x"])
    }

    func testTheLastIdSurvivesMessagesWithNone() {
        var parser: SSEParser = SSEParser()
        _ = parser.feed(Data("id: 9\ndata: a\n\n".utf8))
        _ = parser.feed(Data("data: delta\n\n".utf8))
        XCTAssertEqual(parser.lastEventId, "9", "a live delta carries no id: the resume point stays the last stored event")
    }

    func testAFieldWithNoColonAndAnUnknownFieldAreHarmless() {
        let (messages, _) = parse([Data("data\n\nretry: 3000\ndata: ok\n\n".utf8)])
        XCTAssertEqual(messages.map(\.data), ["", "ok"], "a data line with no value is an empty message, an unknown field is skipped")
    }
}

final class ReconnectTests: XCTestCase {
    func testTheWaitDoublesFromHalfASecondToFifteen() {
        let waits: [Int] = (1...8).map { attempt in
            let delay: Duration = backoffDelay(attempt: attempt, jitter: 0)
            return Int(delay.components.seconds * 1000 + delay.components.attoseconds / 1_000_000_000_000_000)
        }
        XCTAssertEqual(waits, [500, 1000, 2000, 4000, 8000, 15000, 15000, 15000])
    }

    func testTheJitterOnlyLengthensTheWait() {
        let base: Duration = backoffDelay(attempt: 3, jitter: 0)
        let jittered: Duration = backoffDelay(attempt: 3, jitter: 0.2)
        XCTAssertGreaterThan(jittered, base)
        XCTAssertLessThanOrEqual(jittered, .milliseconds(2400))
    }

    func testAResumeRequestNamesTheLastEventTwice() throws {
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        let api: APIClient = APIClient(host: host, key: "phone.a.b", session: URLSession.shared)
        let resumed: URLRequest = runStreamRequest(api: api, runId: "run_9", after: 42)
        XCTAssertEqual(resumed.url?.absoluteString, "https://mac.example.ts.net/api/runs/run_9/stream?after=42")
        XCTAssertEqual(resumed.value(forHTTPHeaderField: "Last-Event-ID"), "42")
        XCTAssertEqual(resumed.value(forHTTPHeaderField: "Authorization"), "Bearer phone.a.b")
        XCTAssertEqual(resumed.value(forHTTPHeaderField: "Accept"), "text/event-stream")
        let first: URLRequest = runStreamRequest(api: api, runId: "run_9", after: 0)
        XCTAssertNil(first.value(forHTTPHeaderField: "Last-Event-ID"))
    }
}

/// A stand-in for the network: each request gets the next scripted answer.
final class StubProtocol: URLProtocol, @unchecked Sendable {
    struct Answer {
        var status: Int = 200
        var chunks: [String] = []
        /// After the chunks the connection fails, as a dropped network does; otherwise it ends cleanly.
        var failsAfter: Bool = false
        /// No answer at all, as a path that died while the phone slept: the request waits.
        var hangs: Bool = false
        /// More chunks, each after its delay in seconds, counted from the answer (a daemon's keepalives).
        var trickle: [(after: Double, chunk: String)] = []
        /// The connection stays open after its chunks and its trickle, saying nothing more (a socket that is dead).
        var staysOpen: Bool = false
    }

    nonisolated(unsafe) static var answers: [Answer] = []
    nonisolated(unsafe) static var requests: [URLRequest] = []
    private static let lock: NSLock = NSLock()

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lock.lock()
        Self.requests.append(request)
        let answer: Answer = Self.answers.isEmpty ? Answer(status: 500) : Self.answers.removeFirst()
        Self.lock.unlock()
        if answer.hangs { return }
        let response: HTTPURLResponse = HTTPURLResponse(
            url: request.url!, statusCode: answer.status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "text/event-stream"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        for chunk in answer.chunks { client?.urlProtocol(self, didLoad: Data(chunk.utf8)) }
        for (delay, chunk) in answer.trickle {
            DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [self] in client?.urlProtocol(self, didLoad: Data(chunk.utf8)) }
        }
        if answer.staysOpen { return }
        // A dropped network fails after the bytes it had delivered were read, not in the same breath.
        DispatchQueue.global().asyncAfter(deadline: .now() + (answer.failsAfter ? 0.15 : 0)) { [self] in
            if answer.failsAfter {
                client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
            } else {
                client?.urlProtocolDidFinishLoading(self)
            }
        }
    }

    override func stopLoading() {}
}

final class EventFeedTests: XCTestCase {
    private func frame(_ id: Int, _ kind: String, run: String = "run_1", data: String = "{}") -> String {
        "id: \(id)\nevent: \(kind)\ndata: {\"id\":\(id),\"run_id\":\"\(run)\",\"ts\":1,\"kind\":\"\(kind)\",\"data\":\(data)}\n\n"
    }

    override func setUp() {
        StubProtocol.answers = []
        StubProtocol.requests = []
    }

    func testADroppedStreamIsResumedFromItsLastEventAndEndsWithTheRun() async throws {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let session: URLSession = URLSession(configuration: configuration)
        StubProtocol.answers = [
            .init(chunks: [": opening\n\n", frame(5, "tool_call"), frame(6, "tool_result")], failsAfter: true),
            .init(chunks: [frame(7, "assistant"), frame(8, "run_finished")]),
        ]
        let api: APIClient = APIClient(
            host: DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!), key: "k", session: session)
        let feed: EventFeed = EventFeed(
            session: session, after: 4, isFinal: { $0.kind == "run_finished" },
            open: { after in runStreamRequest(api: api, runId: "run_1", after: after) })
        var ids: [Int] = []
        let started: ContinuousClock.Instant = ContinuousClock.now
        for await update in await feed.start() {
            if case .events(let events) = update { ids += events.map(\.id) }
        }
        XCTAssertEqual(ids, [5, 6, 7, 8], "every event once, in order, across the drop")
        XCTAssertEqual(StubProtocol.requests.count, 2)
        XCTAssertTrue(StubProtocol.requests[0].url!.absoluteString.hasSuffix("after=4"))
        XCTAssertTrue(StubProtocol.requests[1].url!.absoluteString.hasSuffix("after=6"), "the second connection resumes after the last event read")
        XCTAssertEqual(StubProtocol.requests[1].value(forHTTPHeaderField: "Last-Event-ID"), "6")
        XCTAssertLessThan(started.duration(to: .now), .seconds(3), "the first wait is under a second")
    }

    func testARefusedKeyEndsTheFeedAndIsNotTriedAgain() async throws {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let session: URLSession = URLSession(configuration: configuration)
        StubProtocol.answers = [.init(status: 401), .init(chunks: [frame(1, "run_finished")])]
        let api: APIClient = APIClient(
            host: DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!), key: "k", session: session)
        let feed: EventFeed = EventFeed(
            session: session, after: 0, isFinal: { $0.kind == "run_finished" },
            open: { after in runStreamRequest(api: api, runId: "run_1", after: after) })
        var states: [FeedState] = []
        for await update in await feed.start() {
            if case .state(let state) = update { states.append(state) }
        }
        XCTAssertTrue(states.contains(.reconnecting(attempt: 1, unauthorized: true)))
        XCTAssertEqual(StubProtocol.requests.count, 1, "a refused key is not retried every fifteen seconds")
    }

    func testAnEventThatDoesNotReadIsToldAndTheStreamGoesOn() async throws {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let session: URLSession = URLSession(configuration: configuration)
        StubProtocol.answers = [.init(chunks: ["id: 1\nevent: x\ndata: {not json\n\n", frame(2, "run_finished")])]
        let api: APIClient = APIClient(
            host: DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!), key: "k", session: session)
        let feed: EventFeed = EventFeed(
            session: session, after: 0, isFinal: { $0.kind == "run_finished" },
            open: { after in runStreamRequest(api: api, runId: "run_1", after: after) })
        var ids: [Int] = []
        var unread: [Int?] = []
        for await update in await feed.start() {
            if case .events(let events) = update { ids += events.map(\.id) }
            if case .unreadable(let eventId) = update { unread.append(eventId) }
        }
        XCTAssertEqual(ids, [2])
        XCTAssertEqual(unread, [1], "the screen is told which message could not be read, by its id")
    }

    func testAnUnreadFrameIsPassedByAReconnect() async throws {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let session: URLSession = URLSession(configuration: configuration)
        // The stream drops after a frame that does not read; the reconnect must start after it, not at it.
        StubProtocol.answers = [
            .init(chunks: [frame(5, "tool_call"), "id: 6\nevent: x\ndata: {not json\n\n"], failsAfter: true),
            .init(chunks: [frame(7, "run_finished")]),
        ]
        let api: APIClient = APIClient(
            host: DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!), key: "k", session: session)
        let feed: EventFeed = EventFeed(
            session: session, after: 4, isFinal: { $0.kind == "run_finished" },
            open: { after in runStreamRequest(api: api, runId: "run_1", after: after) })
        for await _ in await feed.start() {}
        XCTAssertEqual(StubProtocol.requests.count, 2)
        XCTAssertTrue(StubProtocol.requests[1].url!.absoluteString.hasSuffix("after=6"), "the unread frame 6 is passed")
    }

    func testAKickReconnectsAtOnceWithoutWaiting() async throws {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let session: URLSession = URLSession(configuration: configuration)
        // The first answer fails, so the feed is in its wait; the kick must end that wait.
        StubProtocol.answers = [
            .init(status: 503),
            .init(chunks: [frame(1, "run_finished")]),
        ]
        let api: APIClient = APIClient(
            host: DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!), key: "k", session: session)
        let feed: EventFeed = EventFeed(
            session: session, after: 0, isFinal: { $0.kind == "run_finished" },
            open: { after in runStreamRequest(api: api, runId: "run_1", after: after) })
        let updates: AsyncStream<FeedUpdate> = await feed.start()
        let reader: Task<[Int], Never> = Task {
            var ids: [Int] = []
            for await update in updates { if case .events(let events) = update { ids += events.map(\.id) } }
            return ids
        }
        try await Task.sleep(for: .milliseconds(100))
        let kicked: ContinuousClock.Instant = ContinuousClock.now
        await feed.kick()
        let ids: [Int] = await reader.value
        XCTAssertEqual(ids, [1])
        XCTAssertLessThan(kicked.duration(to: .now), .milliseconds(400), "a kick does not wait out the backoff")
    }
}


/// A chat session whose daemon refuses the phone's key must not ask again and again.
final class ChatSessionKeyTests: XCTestCase {
    func testARefusedKeyStopsTheSessionFromOpeningStreamsAndTheLineStaysTheKeysLine() async throws {
        let directory: URL = FileManager.default.temporaryDirectory.appending(path: "dot-session-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache: ChatCache = ChatCache(directory: directory)
        // The phone holds a chat whose run was working: the state that makes a session follow a stream.
        await cache.saveChat(StoredChat(threadId: "thr_1", runs: [makeRun(status: "running")], helpers: [], events: [], savedAt: 1))
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        BodyStub.seen = []
        BodyStub.status = 401
        BodyStub.reply = "no"
        defer { BodyStub.status = 200 }
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        let api: APIClient = APIClient(host: host, key: "k", session: URLSession(configuration: configuration))
        let session: ChatSession = ChatSession(threadId: "thr_1", api: api, cache: cache)
        let outputs: AsyncStream<ChatOutput> = await session.start()
        let reader: Task<[FeedState], Never> = Task {
            var seen: [FeedState] = []
            for await output in outputs { if case .connection(let state) = output { seen.append(state) } }
            return seen
        }
        try await Task.sleep(for: .seconds(2))
        await session.stop()
        let lines: [FeedState] = await reader.value
        XCTAssertEqual(BodyStub.seen.filter { $0.path == "/api/runs" }.count, 1, "one read, then nothing until the app comes back")
        XCTAssertFalse(BodyStub.seen.contains { $0.path.hasSuffix("/stream") }, "no stream is opened with a key that was refused")
        XCTAssertEqual(lines.last, .reconnecting(attempt: 0, unauthorized: true), "the line stays the key's")
    }
}

final class LossyTests: XCTestCase {
    private func api(reply: String) -> APIClient {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        BodyStub.status = 200
        BodyStub.reply = reply
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        return APIClient(host: host, key: "k", session: URLSession(configuration: configuration))
    }

    func testAnEventThatDoesNotReadIsLeftOutOfAPageNotTheWholePageAndIsCounted() async throws {
        let page: EventsPage = try await api(reply: #"[{"id":1,"run_id":"r","ts":1,"kind":"a","data":{}},{"id":2},{"id":3,"run_id":"r","ts":1,"kind":"b","data":{}}]"#)
            .events(run: "r", after: 0)
        XCTAssertEqual(page.events.map(\.id), [1, 3])
        XCTAssertEqual(page.size, 3)
        XCTAssertEqual(page.droppedIds, [2], "the caller is told which, to say it to the owner once")
    }

    func testTheCursorGoesPastAnUnreadEventThatEndsThePage() async throws {
        let page: EventsPage = try await api(reply: #"[{"id":1,"run_id":"r","ts":1,"kind":"a","data":{}},{"id":2,"kind":"broken"}]"#)
            .events(run: "r", after: 0)
        XCTAssertEqual(page.events.map(\.id), [1])
        XCTAssertEqual(page.topId, 2, "the next page starts after the unread event, not before it")
    }

    func testAnEventWithNoReadableIdIsTheUnknownOne() async throws {
        let page: EventsPage = try await api(reply: #"[{"id":"x"}]"#).events(run: "r", after: 0)
        XCTAssertEqual(page.droppedIds, [UnreadableEvents.unknownId])
        XCTAssertNil(page.topId, "a page that names no id cannot be walked past: the read ends")
    }
}

final class UnreadableEventsTests: XCTestCase {
    func testTheSameEventMetAgainIsCountedOnce() {
        var log: UnreadableEvents = UnreadableEvents()
        XCTAssertEqual(log.add([7], run: "r"), 1)
        XCTAssertEqual(log.add([7], run: "r"), 0, "a reconnect or a later catch-up hands it over again")
        XCTAssertEqual(log.add([7, 9], run: "r"), 1)
        XCTAssertEqual(log.count(run: "r"), 2)
        XCTAssertEqual(log.count(run: "other"), 0)
    }

    func testEventsWithNoIdAreOneUnknown() {
        var log: UnreadableEvents = UnreadableEvents()
        log.add([UnreadableEvents.unknownId], run: "r")
        log.add([UnreadableEvents.unknownId], run: "r")
        XCTAssertEqual(log.count(run: "r"), 1)
    }

    func testItIsKeptWithTheChat() async throws {
        let directory: URL = FileManager.default.temporaryDirectory.appending(path: "dot-unreadable-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache: ChatCache = ChatCache(directory: directory)
        var log: UnreadableEvents = UnreadableEvents()
        log.add([4, 5], run: "run_1")
        var chat: StoredChat = StoredChat(threadId: "thr_1", runs: [makeRun()], helpers: [], events: [], savedAt: 1)
        chat.unreadable = log
        await cache.saveChat(chat)
        let loaded: StoredChat? = await cache.loadChat("thr_1")
        let state: ChatState = try XCTUnwrap(loaded).state()
        XCTAssertTrue(state.items.contains { $0.id == "n:unreadable:run_1" }, "the row is still there after a reopen")
    }
}
