import XCTest
@testable import Dot

/// A socket that is dead and does not say so (item 1391). A phone that comes back on a path that died while it slept, or
/// one whose Wi-Fi gave way to the cellular link, holds a connection the system still believes in for 45 seconds: the
/// feed does not wait for that. A connection that answers nothing within `connectTimeout` and a stream that says nothing
/// for `idleTimeout` (the daemon writes a keepalive every 15 s) are dropped, and the feed tries again on a new one.
final class SilentSocketTests: XCTestCase {
    private func frame(_ id: Int, _ kind: String) -> String {
        "id: \(id)\nevent: \(kind)\ndata: {\"id\":\(id),\"run_id\":\"run_1\",\"ts\":1,\"kind\":\"\(kind)\",\"data\":{}}\n\n"
    }

    override func setUp() {
        StubProtocol.answers = []
        StubProtocol.requests = []
    }

    private func feed(connect: Duration = .seconds(8), idle: Duration = .seconds(35)) -> (EventFeed, URLSession) {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let session: URLSession = URLSession(configuration: configuration)
        let api: APIClient = APIClient(
            host: DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!), key: "k", session: session)
        let feed: EventFeed = EventFeed(
            session: session, after: 0, connectTimeout: connect, idleTimeout: idle, isFinal: { $0.kind == "run_finished" },
            open: { after in runStreamRequest(api: api, runId: "run_1", after: after) })
        return (feed, session)
    }

    private func read(_ feed: EventFeed) async -> (ids: [Int], states: [FeedState]) {
        var ids: [Int] = []
        var states: [FeedState] = []
        for await update in await feed.start() {
            if case .events(let events) = update { ids += events.map(\.id) }
            if case .state(let state) = update { states.append(state) }
        }
        return (ids, states)
    }

    func testAStreamThatGoesSilentIsReplacedByANewOneAfterTheIdleTimeout() async {
        StubProtocol.answers = [
            .init(chunks: [": open\n\n", frame(1, "assistant")], staysOpen: true),
            .init(chunks: [frame(2, "assistant"), frame(3, "run_finished")]),
        ]
        let (feed, _): (EventFeed, URLSession) = feed(idle: .milliseconds(600))
        let started: ContinuousClock.Instant = ContinuousClock.now
        let result: (ids: [Int], states: [FeedState]) = await read(feed)
        XCTAssertEqual(result.ids, [1, 2, 3], "every event once across the replacement")
        XCTAssertEqual(StubProtocol.requests.count, 2, "a second connection took over")
        XCTAssertTrue(StubProtocol.requests[1].url!.absoluteString.hasSuffix("after=1"), "it goes on after the last event read")
        XCTAssertLessThan(started.duration(to: .now), .seconds(4), "far under the 45 s the system would wait")
    }

    func testAConnectionThatAnswersNothingIsReplacedAfterTheConnectTimeout() async {
        StubProtocol.answers = [.init(hangs: true), .init(chunks: [": open\n\n", frame(1, "run_finished")])]
        let (feed, _): (EventFeed, URLSession) = feed(connect: .milliseconds(500))
        let started: ContinuousClock.Instant = ContinuousClock.now
        let result: (ids: [Int], states: [FeedState]) = await read(feed)
        XCTAssertEqual(result.ids, [1])
        XCTAssertEqual(StubProtocol.requests.count, 2)
        XCTAssertTrue(result.states.contains { if case .reconnecting = $0 { true } else { false } }, "the screen is told it reconnects")
        XCTAssertLessThan(started.duration(to: .now), .seconds(4))
    }

    func testKeepalivesKeepAnOpenStreamOpen() async {
        // A stream that says nothing but a keepalive every 0.25 s outlives an idle timeout of 0.6 s many times over.
        let keepalives: [(after: Double, chunk: String)] = (1...8).map { (Double($0) * 0.25, ": keepalive\n\n") }
        StubProtocol.answers = [
            .init(chunks: [": open\n\n"], trickle: keepalives + [(2.3, frame(1, "run_finished"))], staysOpen: true)
        ]
        let (feed, _): (EventFeed, URLSession) = feed(idle: .milliseconds(600))
        let result: (ids: [Int], states: [FeedState]) = await read(feed)
        XCTAssertEqual(result.ids, [1])
        XCTAssertEqual(StubProtocol.requests.count, 1, "a stream that keeps its keepalives is never replaced")
    }

    func testAStreamThatIsSilentFromTheStartIsReplacedToo() async {
        StubProtocol.answers = [.init(staysOpen: true), .init(chunks: [frame(1, "run_finished")])]
        let (feed, _): (EventFeed, URLSession) = feed(idle: .milliseconds(500))
        let result: (ids: [Int], states: [FeedState]) = await read(feed)
        XCTAssertEqual(result.ids, [1])
        XCTAssertEqual(StubProtocol.requests.count, 2)
    }

    func testAKickStillReconnectsAtOnceAndIsNotTakenForASilence() async {
        StubProtocol.answers = [
            .init(chunks: [": open\n\n"], staysOpen: true),
            .init(chunks: [frame(1, "run_finished")]),
        ]
        let (feed, _): (EventFeed, URLSession) = feed()
        let started: ContinuousClock.Instant = ContinuousClock.now
        Task {
            try? await Task.sleep(for: .milliseconds(300))
            await feed.kick()
        }
        let result: (ids: [Int], states: [FeedState]) = await read(feed)
        XCTAssertEqual(result.ids, [1])
        XCTAssertEqual(StubProtocol.requests.count, 2)
        XCTAssertLessThan(started.duration(to: .now), .seconds(3), "the kick did not wait for any timeout")
    }
}
