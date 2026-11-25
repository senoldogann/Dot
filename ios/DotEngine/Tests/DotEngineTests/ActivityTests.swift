import Foundation
import XCTest

@testable import DotEngine

/// The Activity tab's calls (item 1826): each reaches its route with its method and body, and decodes what the daemon
/// answered (the answers below were read from a real daemon, `ios/Next/scripts/e2e_daemon.py --seed-activity`).
final class ActivityTests: XCTestCase {
    private static let host: String = "activity.test"

    override func setUp() {
        super.setUp()
        ActivityDaemon.reset()
    }

    private func api() -> DaemonAPI {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ActivityDaemon.self]
        let session: URLSession = URLSession(configuration: configuration)
        return DaemonAPI(host: DaemonHost(name: Self.host, origin: URL(string: "https://\(Self.host)")!), key: "k", session: session, streamSession: session)
    }

    func testTheActivityAndTheAwayViewDecodeAsTheDaemonSendsThem() async throws {
        ActivityDaemon.answer("GET /api/activity", 200, Answers.activity)
        ActivityDaemon.answer("GET /api/away", 200, Answers.away)
        let activity: ActivityView = try await api().activity()
        XCTAssertEqual(activity.recent.map(\.status), ["failed", "done"])
        XCTAssertEqual(activity.due.first?.kind, "schedule")
        XCTAssertNil(activity.due.first?.runId)
        XCTAssertEqual(activity.due.first?.actorId, "sch_mwvofvpd_52ba77", "the face a row wears, for an agent's profile")
        XCTAssertNil(activity.recent.first?.actorId)
        XCTAssertFalse(activity.isEmpty)
        let away: AwayView = try await api().away(since: 1000)
        XCTAssertEqual([away.done, away.failed, away.drafts], [1, 1, 1])
        XCTAssertEqual(away.decisions.yoloApproved, 2)
        XCTAssertEqual(away.decisionRuns.first?.reviewer, 1)
        XCTAssertEqual(away.runs.first?.title, "Nightly backup")
        XCTAssertEqual(away.runs.first?.campaign, false)
        XCTAssertNil(away.runs.first?.agentId)
        XCTAssertEqual(ActivityDaemon.seen.last?.query, "since=1000.0")
    }

    func testMarkingReadSendsTheMomentShown() async throws {
        ActivityDaemon.answer("POST /api/away/read", 200, #"{"read_at":1795584148.06}"#)
        let read: AwayRead = try await api().markAwayRead(until: 1_795_584_148.06)
        XCTAssertEqual(read.readAt, 1_795_584_148.06)
        let body: [String: Double] = try JSONDecoder().decode([String: Double].self, from: XCTUnwrap(ActivityDaemon.seen.last?.body))
        XCTAssertEqual(body, ["until": 1_795_584_148.06])
    }

    func testTheBriefAndTheCheckinAreReadAndSetWhole() async throws {
        ActivityDaemon.answer("PUT /api/brief", 200, Answers.brief)
        ActivityDaemon.answer("GET /api/checkin", 200, Answers.checkin)
        let brief: BriefView = try await api().changeBrief(BriefChange(enabled: true, time: "07:30", days: "weekdays", lines: ["The roof repair"]))
        XCTAssertEqual(brief.scheduleId, "sch_mwvofvpd_52ba77")
        XCTAssertEqual(brief.lines, ["The roof repair"])
        let sent: [String: JSONValue] = try JSONDecoder().decode([String: JSONValue].self, from: XCTUnwrap(ActivityDaemon.seen.last?.body))
        XCTAssertEqual(Set(sent.keys), ["enabled", "time", "days", "lines"])
        let checkin: CheckinView = try await api().checkin()
        XCTAssertFalse(checkin.enabled)
        XCTAssertEqual([checkin.time, checkin.days], ["18:00", "every"])
        XCTAssertNil(checkin.scheduleId)
    }

    func testTheInitiativeListAndItsActions() async throws {
        ActivityDaemon.answer("GET /api/initiative", 200, Answers.initiative)
        ActivityDaemon.answer("POST /api/initiative/todos/todo_1/drop", 200, Answers.initiative)
        ActivityDaemon.answer("POST /api/initiative/todos", 201, Answers.initiative)
        let view: InitiativeView = try await api().initiative()
        XCTAssertEqual(view.todos.map(\.status), ["open", "open", "dropped"])
        XCTAssertEqual(view.waiting, 2)
        XCTAssertEqual(view.approvals, "yolo")
        _ = try await api().dropTodo("todo_1")
        XCTAssertEqual(ActivityDaemon.seen.last?.line, "POST /api/initiative/todos/todo_1/drop")
        _ = try await api().addTodo(title: "Renew the domain")
        let body: [String: String] = try JSONDecoder().decode([String: String].self, from: XCTUnwrap(ActivityDaemon.seen.last?.body))
        XCTAssertEqual(body, ["title": "Renew the domain", "why": ""])
    }

    func testAWatcherIsSwitchedWithTheRestOfItsChoicesAsTheyAre() async throws {
        ActivityDaemon.answer("GET /api/watchers", 200, Answers.watchers)
        let watchers: [WatcherView] = try await api().watchers()
        XCTAssertEqual(watchers.map(\.kind), ["github", "mail"])
        XCTAssertNil(watchers[0].lookedAt)
        let landlord: WatcherView = watchers[1]
        // The first watcher of the list alone, as a row's route answers it.
        let watcher: String = String(Answers.watchers.dropFirst().dropLast()).components(separatedBy: "},{")[0] + "}"
        ActivityDaemon.answer("PATCH /api/watchers/\(landlord.id)", 200, watcher)
        _ = try await api().changeWatcher(landlord.id, landlord.request(enabled: false))
        let sent: WatcherRequest = try JSONDecoder().decode(WatcherRequest.self, from: XCTUnwrap(ActivityDaemon.seen.last?.body))
        XCTAssertEqual(sent, WatcherRequest(name: "Landlord", kind: "mail", enabled: false, account: "me@example.com", words: "my landlord", minutes: 5, action: "tell", goal: ""))
    }

    func testTheSelfImprovementProposalsAreTheNewAndTriedVersions() async throws {
        ActivityDaemon.answer("GET /api/harness", 200, Answers.harness)
        ActivityDaemon.answer("POST /api/harness/v/adopt", 422, #"{"message":"a version with a link is not put in use","context":{}}"#)
        let view: HarnessView = try await api().harness(limit: 100)
        XCTAssertEqual(ActivityDaemon.seen.last?.query, "limit=100")
        XCTAssertEqual(view.proposals.map(\.state), view.versions.filter { $0.state != "adopted" && $0.state != "retired" }.map(\.state))
        XCTAssertFalse(view.proposals.isEmpty)
        let outside: HarnessVersion = try XCTUnwrap(view.proposals.first { view.fromOutside[$0.id] != nil })
        XCTAssertFalse(try XCTUnwrap(view.fromOutside[outside.id]).isEmpty)
        do {
            _ = try await api().adoptHarness("v")
            XCTFail("a refused version is not put in use")
        } catch {
            XCTAssertEqual(error.statusCode, 422)
        }
    }

    func testTheDraftsThatWaitAndASendWithTheCardsWords() async throws {
        ActivityDaemon.answer("GET /api/drafts", 200, Answers.drafts)
        let drafts: [DraftView] = try await api().waitingDrafts(limit: 50)
        XCTAssertEqual(drafts.map(\.status), ["failed", "waiting"])
        XCTAssertTrue(drafts.allSatisfy(\.isOpen), "a failed send still waits for the owner")
        let sent: DraftView = DraftView(id: "d", runId: "r", channel: "mail", to: [], cc: [], subject: "", body: "", status: "sent", createdAt: 0, error: nil)
        XCTAssertFalse(sent.isOpen)
        XCTAssertEqual(drafts[0].error, "Messages could not reach the number.")
        XCTAssertTrue(drafts[1].body.contains("\n\n"), "the body keeps its lines")
        XCTAssertEqual(ActivityDaemon.seen.last?.query, "limit=50")
        ActivityDaemon.answer("POST /api/drafts/d/send", 409, #"{"message":"the draft no longer waits","context":{}}"#)
        do {
            _ = try await api().sendDraft("d", words: DraftWords(to: ["a@b.c"], cc: [], subject: "S", body: "B"))
            XCTFail("a draft answered elsewhere is refused")
        } catch {
            XCTAssertEqual(error.statusCode, 409)
        }
        let words: DraftWords = try JSONDecoder().decode(DraftWords.self, from: XCTUnwrap(ActivityDaemon.seen.last?.body))
        XCTAssertEqual(words, DraftWords(to: ["a@b.c"], cc: [], subject: "S", body: "B"))
    }
}

/// The daemon of these tests: canned answers by "METHOD /path", and every request it was sent.
final class ActivityDaemon: URLProtocol, @unchecked Sendable {
    struct Seen: Sendable {
        let line: String
        let query: String?
        let body: Data?
    }

    private static let lock: NSLock = NSLock()
    nonisolated(unsafe) private static var answers: [String: (Int, String)] = [:]
    nonisolated(unsafe) private static var log: [Seen] = []

    static func reset() { lock.withLock { answers = [:]; log = [] } }
    static func answer(_ line: String, _ code: Int, _ body: String) { lock.withLock { answers[line] = (code, body) } }
    static var seen: [Seen] { lock.withLock { log } }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        let url: URL = request.url!
        let line: String = "\(request.httpMethod ?? "GET") \(url.path())"
        let body: Data? = request.httpBody ?? request.httpBodyStream.map(Self.read)
        let (code, text): (Int, String) = Self.lock.withLock {
            Self.log.append(Seen(line: line, query: url.query(), body: body))
            return Self.answers[line] ?? (404, #"{"detail":"Not Found"}"#)
        }
        let response: HTTPURLResponse = HTTPURLResponse(url: url, statusCode: code, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(text.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    private static func read(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var data: Data = Data()
        var buffer: [UInt8] = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count: Int = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

/// What a daemon seeded by `e2e_daemon.py --seed-activity` answered (2026-11-25).
private enum Answers {
    static let activity: String = #"""
{"waiting":[],"working":[],"ended_delegated":[],"recent":[{"id":"run_mwvofpd4_ffa37c","section":"recent","kind":"run","title":"Back up the projects folder.","detail":"","channel":"","since":1795584139.817495,"href":"#/runs/run_mwvofpd4_ffa37c","run_id":"run_mwvofpd4_ffa37c","status":"failed","minutes":0.000012032190958658855,"tokens":0,"steps":0,"parent_run_id":null,"actor_id":null,"root_thread_id":null},{"id":"run_mwvofpd1_6c5f10","section":"recent","kind":"run","title":"Read new client mail and draft replies.","detail":"","channel":"","since":1795584139.814574,"href":"#/runs/run_mwvofpd1_6c5f10","run_id":"run_mwvofpd1_6c5f10","status":"done","minutes":0.000015882651011149087,"tokens":0,"steps":0,"parent_run_id":null,"actor_id":null,"root_thread_id":null}],"learned_today":[],"due":[{"id":"due-sch_mwvofvpd_52ba77","section":"due","kind":"schedule","title":"Morning brief","detail":"fires at 07:30","channel":"","since":1795584600.0,"href":"#/settings/schedules","run_id":null,"status":"scheduled","minutes":null,"tokens":null,"steps":null,"parent_run_id":null,"actor_id":"sch_mwvofvpd_52ba77","root_thread_id":null}],"generated_at":1795584148.045768}
"""#
    static let away: String = #"""
{"since":1794979348.064328,"until":1795584148.064328,"done":1,"failed":1,"cancelled":0,"working":0,"campaign_sessions":0,"runs":[{"run_id":"run_mwvofpd4_ffa37c","title":"Nightly backup","source":"schedule","campaign":false,"agent_id":null,"status":"failed","steps":0,"created_at":1795584139.8167732,"finished_at":1795584139.817495,"answer":"The backup disk was not mounted."},{"run_id":"run_mwvofpd1_6c5f10","title":"Client mail check","source":"schedule","campaign":false,"agent_id":null,"status":"done","steps":0,"created_at":1795584139.813621,"finished_at":1795584139.814574,"answer":"Two clients wrote; both replies are drafted for you."}],"decisions":{"reviewer_approved":1,"reviewer_denied":0,"yolo_approved":2,"yolo_denied":0},"decision_runs":[{"run_id":"run_mwvofpd1_6c5f10","title":"Client mail check","reviewer":1,"yolo":2,"denied":0}],"drafts":1}
"""#
    static let brief: String = #"""
{"enabled":true,"time":"07:30","days":"weekdays","custom":false,"cron":"30 7 * * 1-5","lines":["The roof repair"],"zone":"Europe/Helsinki","schedule_id":"sch_mwvofvpd_52ba77","next_local":"Wed 2026-11-25 07:30","last_local":null,"last_status":"","last_run_id":null,"accounts":0}
"""#
    static let checkin: String = #"""
{"enabled":false,"time":"18:00","days":"every","zone":"Europe/Helsinki","schedule_id":null,"next_local":null,"last_local":null,"last_run_id":null}
"""#
    static let initiative: String = #"""
{"enabled":false,"looked_at":null,"todos":[{"id":"todo_mwvofpdc_4a2052","title":"Tidy the Downloads folder","why":"It holds 2,000 files from the last year.","source":"run","status":"open","attempts":0,"run_id":null,"note":"","updated_at":1795584139.8244},{"id":"todo_mwvofvme_f9b319","title":"Renew the domain before December","why":"","source":"owner","status":"open","attempts":0,"run_id":null,"note":"","updated_at":1795584147.926933},{"id":"todo_mwvofvmp_68e79e","title":"Sort the photo library","why":"","source":"owner","status":"dropped","attempts":0,"run_id":null,"note":"You took it off the list.","updated_at":1795584147.986083}],"idle_readable":true,"approvals":"yolo","peak_hours_utc":[],"look_model":"","costly_tokens":5000000}
"""#
    static let watchers: String = #"""
[{"id":"wch_mwvofvob_dd0d32","name":"Reviews asked of me","kind":"github","enabled":true,"account":"","words":"","minutes":10,"action":"tell","goal":"","created_at":1795584147.9958038,"primed":false,"looked_at":null,"next_look_at":0.0,"error":null,"error_at":null,"last":"","told":0},{"id":"wch_mwvofvol_a4cc58","name":"Landlord","kind":"mail","enabled":true,"account":"me@example.com","words":"my landlord","minutes":5,"action":"tell","goal":"","created_at":1795584148.005696,"primed":false,"looked_at":null,"next_look_at":0.0,"error":null,"error_at":null,"last":"","told":0}]
"""#
    static let drafts: String = #"""
[{"id":"drf_mwvofpda_c099d0","run_id":"run_mwvofpd1_6c5f10","channel":"message","to":["+90 555 111 22 33"],"cc":[],"subject":"","body":"Running ten minutes late, see you at the café.","status":"failed","created_at":1795584139.822308,"decided_at":1795584139.8236501,"error":"Messages could not reach the number."},{"id":"drf_mwvofpd9_fb8b74","run_id":"run_mwvofpd1_6c5f10","channel":"mail","to":["ayse@example.com"],"cc":[],"subject":"Re: The March invoice","body":"Hi Ayşe,\n\nThanks, the invoice is paid; the receipt is attached.\n\nŞenol","status":"waiting","created_at":1795584139.821794,"decided_at":null,"error":null}]
"""#
    static let harness: String = #"""
{"adopted":null,"versions":[{"id":"hrn_mwvommio_ffa725","parent_id":null,"made_by":"proposer","state":"candidate","summary":"Read the README of a repository before editing it.","habits":"- Read a repository's README before the first edit.","tool_guidance":"","notes":"{\"tainted\": true, \"tainted_by\": [\"a web page\"]}","created_at":1795584462.7200909,"adopted_at":null,"retired_at":null},{"id":"hrn_mwvommin_ffb07c","parent_id":null,"made_by":"proposer","state":"candidate","summary":"Run the tests of what changed before the whole suite.","habits":"- Run the tests of the files a change touched first.","tool_guidance":"","notes":"{\"scope\": \"tests\"}","created_at":1795584462.71935,"adopted_at":null,"retired_at":null}],"from_outside":{"hrn_mwvommio_ffa725":["its run read a web page"]},"refused":{},"versions_total":2,"version_counts":{"candidate":2},"habits_chars":1200,"tool_guidance_chars":1500,"component_lines":16,"step_time":"off","step_time_set_on":"settings","step_minutes":90.0,"step_tokens":8000000,"main_model":"ollama:scripted","scoring_tasks":["impossible-missing-file","injection-in-invoice"],"final_tasks":["fix-add-bug","recover-wrong-extension","report-failing-tests","injection-in-notes"],"pool_size":33,"headroom":[],"untried":33,"headroom_least":2,"step_repeats":2,"step_margin":2,"adopt":"owner","keep_days":180,"steps_total":0,"experiences_total":0}
"""#
}
