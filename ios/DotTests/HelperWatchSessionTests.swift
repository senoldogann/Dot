import XCTest
@testable import Dot

/// A chat that is open watches the helpers its run left at work (item 1466): the daemon's list of helpers is asked for again every
/// few seconds (here every 80 ms) while one is live and for nothing else, a helper that ended makes the chat read whole, and the
/// watch stops when none is live. The daemon is a script: what it says of the chat's runs and helpers changes as the test says.
final class HelperWatchSessionTests: XCTestCase {
    private var directory: URL!

    override func setUp() {
        directory = scratchFolder("dot-helper-watch")
        BodyStub.seen = []
        BodyStub.respond = nil
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: directory)
        BodyStub.respond = nil
        BodyStub.seen = []
    }

    /// What the scripted daemon says now, and how often each kind of thing was asked.
    private final class Script: @unchecked Sendable {
        private let lock: NSLock = NSLock()
        private var live: Int
        private var report: Bool = false
        private var asked: [String: Int] = [:]

        init(live: Int) { self.live = live }

        func set(live: Int, reportRun: Bool = false) {
            lock.withLock {
                self.live = live
                report = reportRun
            }
        }

        func note(_ path: String) -> (live: Int, report: Bool) {
            lock.withLock {
                asked[path, default: 0] += 1
                return (live, report)
            }
        }

        func count(_ path: String) -> Int { lock.withLock { asked[path] ?? 0 } }
        var total: Int { lock.withLock { asked.values.reduce(0, +) } }
    }

    private static let helpersPath: String = "/api/threads/thr_1/helpers"

    private static func runJSON(_ id: String, status: String, parent: String? = nil, created: Int) -> String {
        let parentJSON: String = parent.map { "\"\($0)\"" } ?? "null"
        let finished: String = ["done", "failed", "cancelled"].contains(status) ? String(created + 10) : "null"
        return #"{"id":"\#(id)","thread_id":"thr_1","goal":"goal \#(id)","mode":"task","model":"m","status":"\#(status)","created_at":\#(created),"finished_at":\#(finished),"result":"","steps":3,"parent_run_id":\#(parentJSON),"source":"ui","agent_id":null,"follows_reaction":null}"#
    }

    private static func detailJSON(_ run: String) -> String {
        #"{"run":\#(run),"title":"A chat","children":[],"active":false,"questions":[],"choices":null}"#
    }

    /// The chat: its run ended done; three helpers it started, `live` of them running (the newest first), and, when the script says,
    /// the run the daemon started to tell the chat a helper's report.
    private func install(_ script: Script) {
        BodyStub.respond = { seen in
            let state: (live: Int, report: Bool) = script.note(seen.path)
            let first: String = Self.runJSON("run_1", status: "done", created: 1000)
            switch seen.path {
            case "/api/runs":
                let second: String = Self.runJSON("run_2", status: "running", created: 1100)
                return (200, "[" + (state.report ? [second, first] : [first]).joined(separator: ",") + "]")
            case Self.helpersPath:
                let helpers: [String] = (0..<3).map { index in
                    Self.runJSON("h\(index)", status: index < state.live ? "running" : "done", parent: "run_1", created: 1001 + index)
                }
                return (200, "[" + helpers.reversed().joined(separator: ",") + "]")
            case "/api/runs/run_1": return (200, Self.detailJSON(first))
            case "/api/runs/run_2": return (200, Self.detailJSON(Self.runJSON("run_2", status: "running", created: 1100)))
            case "/api/approvals": return (200, "[]")
            default:
                return seen.path.hasSuffix("/events") ? (200, "[]") : (404, "{}")
            }
        }
    }

    private func session(link: LinkMonitor? = nil) -> ChatSession {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        let api: APIClient = APIClient(host: host, key: "phone.a.b", session: URLSession(configuration: configuration), link: link)
        return ChatSession(threadId: "thr_1", api: api, cache: ChatCache(directory: directory), link: link, watchEvery: .milliseconds(80))
    }

    func testAChatWhoseRunEndedAsksForItsHelpersAgainAndAgainAndForNothingElseUntilOneEnds() async {
        let script: Script = Script(live: 3)
        install(script)
        let chat: ChatSession = session()
        let log: SessionLog = SessionLog()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5, "the chat says that three helpers work although its own run is done") {
            log.latest?.line == .helpersInBackground(count: 3)
        }
        XCTAssertEqual(log.latest?.isWorking, false)
        let others: Int = script.total - script.count(Self.helpersPath)
        let asked: Int = script.count(Self.helpersPath)
        try? await Task.sleep(for: .milliseconds(600))
        XCTAssertGreaterThanOrEqual(script.count(Self.helpersPath) - asked, 3, "it asks again and again while they work")
        XCTAssertEqual(script.total - script.count(Self.helpersPath), others, "and asks for nothing else while none has ended: one small request a tick")
        await chat.stop()
        reading.cancel()
    }

    func testTheCountGoesDownAsHelpersEndAndTheAskingStopsWhenNoneIsLive() async {
        let script: Script = Script(live: 3)
        install(script)
        let chat: ChatSession = session()
        let log: SessionLog = SessionLog()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5, "three helpers work") { log.latest?.line == .helpersInBackground(count: 3) }
        script.set(live: 2)
        await expectSoon(5, "one ended: the count is two") { log.latest?.line == .helpersInBackground(count: 2) }
        script.set(live: 0)
        await expectSoon(5, "the last ones ended: the chat is at rest") { log.latest?.line == .idle }
        // Let what was under way end, then nothing is asked any more.
        try? await Task.sleep(for: .milliseconds(300))
        let settled: Int = script.total
        try? await Task.sleep(for: .milliseconds(600))
        XCTAssertEqual(script.total, settled, "no helper is live: the watch has stopped")
        await chat.stop()
        reading.cancel()
    }

    func testAHelperThatEndedMakesTheChatFollowTheRunItsReportStarted() async {
        let script: Script = Script(live: 1)
        install(script)
        let chat: ChatSession = session()
        let log: SessionLog = SessionLog()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5, "one helper works") { log.latest?.line == .helpersInBackground(count: 1) }
        XCTAssertEqual(script.count("/api/runs/run_2/stream"), 0)
        // The helper ends and the daemon starts a run in the chat to tell its report.
        script.set(live: 0, reportRun: true)
        await expectSoon(5, "the chat holds the new run, and works") {
            log.latest?.rootRunIds.last == "run_2" && log.latest?.isWorking == true
        }
        await expectSoon(5, "and follows its events") { script.count("/api/runs/run_2/stream") >= 1 }
        await chat.stop()
        reading.cancel()
    }

    func testWithNoLinkNothingIsAskedAndTheLastCountStaysUntilTheLinkIsBack() async {
        let link: LinkMonitor = LinkMonitor()
        let script: Script = Script(live: 3)
        install(script)
        let chat: ChatSession = session(link: link)
        let log: SessionLog = SessionLog()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5, "three helpers work") { log.latest?.line == .helpersInBackground(count: 3) }
        link.report(.down)
        script.set(live: 0)
        try? await Task.sleep(for: .milliseconds(200))
        let settled: Int = script.total
        try? await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(script.total, settled, "with no link it asks nothing")
        XCTAssertEqual(log.latest?.line, .helpersInBackground(count: 3), "the count last read stays")
        link.report(.up)
        await chat.kick()
        await expectSoon(5, "the link is back and the chat is read again") { log.latest?.line == .idle }
        await chat.stop()
        reading.cancel()
    }

    func testAChatTheAppLeftAsksNothing() async {
        let script: Script = Script(live: 3)
        install(script)
        let chat: ChatSession = session()
        let log: SessionLog = SessionLog()
        let reading: Task<Void, Never> = await log.follow(chat)
        await expectSoon(5, "three helpers work") { log.latest?.line == .helpersInBackground(count: 3) }
        await chat.suspend()
        try? await Task.sleep(for: .milliseconds(200))
        let settled: Int = script.total
        try? await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(script.total, settled, "the app went to the background: nothing is asked until it is back")
        await chat.kick()
        await expectSoon(5, "back in front, the chat asks again") { script.total > settled }
        await chat.stop()
        reading.cancel()
    }
}
