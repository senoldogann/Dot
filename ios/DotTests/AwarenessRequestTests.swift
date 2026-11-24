import XCTest
@testable import Dot

/// The requests of the phone's awareness (items 1440 to 1445), as the daemon's routes take them (`docs/phone.md`, "The
/// iPhone app's calls"): the paths, the methods, the phone's key and the bodies.
final class AwarenessRequestTests: XCTestCase {
    private func api() -> APIClient {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        return APIClient(host: host, key: "phone.a.b", session: URLSession(configuration: configuration))
    }

    override func setUp() {
        BodyStub.reply = "{}"
        BodyStub.seen = []
        BodyStub.respond = nil
        BodyStub.status = 200
    }

    private func lastBody() throws -> [String: Any] {
        let data: Data = try XCTUnwrap(BodyStub.seen.last?.body)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    // MARK: Item 1440, the activity lease

    func testTheLeaseIsPostedWithThePhonesKeyAndTheChatOnTheScreen() async throws {
        BodyStub.reply = #"{"state":"foreground","chat":"thr_1","ttl_s":45.0,"expires_at":1764000045.0}"#
        try await api().report(activity: ActivityReport(state: .foreground, chat: "thr_1", ttl: 45))
        XCTAssertEqual(BodyStub.seen.last?.method, "POST")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/phone/activity")
        XCTAssertEqual(BodyStub.seen.last?.authorization, "Bearer phone.a.b")
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(body["state"] as? String, "foreground")
        XCTAssertEqual(body["chat"] as? String, "thr_1")
        XCTAssertEqual(body["ttl_s"] as? Int, 45)
        XCTAssertEqual(Set(body.keys), ["state", "chat", "ttl_s"], "no key the daemon does not name: it refuses one with a 422")
    }

    func testALeaseThatEndsIsPostedAsBackgroundWithNoChat() async throws {
        try await api().report(activity: ActivityReport(state: .background, chat: nil, ttl: 45))
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(body["state"] as? String, "background")
        XCTAssertTrue(body["chat"] is NSNull)
    }

    func testAReportTheDaemonRefusesOrDoesNotAnswerIsAnErrorTheCallerLetsGo() async {
        BodyStub.status = 422
        BodyStub.reply = #"{"message":"a body of another shape","context":{}}"#
        do {
            try await api().report(activity: ActivityReport(state: .foreground, chat: nil, ttl: 45))
            XCTFail("a 422 is an error: the sender logs it and the lease ends by itself")
        } catch DaemonRequestError.status(_, let code, _) {
            XCTAssertEqual(code, 422)
        } catch {
            XCTFail("\(error)")
        }
    }

    func testTheLeasesSessionAsksOnceAndDoesNotWaitForALink() {
        let base: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        base.waitsForConnectivity = true
        base.protocolClasses = [BodyStub.self]
        let made: URLSession = APIClient.makeLeaseSession(like: URLSession(configuration: base))
        XCTAssertFalse(made.configuration.waitsForConnectivity, "a report that waits for a link reaches the daemon late")
        XCTAssertEqual(made.configuration.timeoutIntervalForRequest, 8)
        XCTAssertLessThan(made.configuration.timeoutIntervalForResource, ActivityLeaseRules.renewEvery)
        XCTAssertTrue(made.configuration.protocolClasses?.contains { $0 == BodyStub.self } ?? false, "made like the session it came from")
    }

    // MARK: Item 1441, the push switches

    func testTheSwitchesAreReadFromTheirRouteWithThePhonesKey() async throws {
        BodyStub.reply = #"{"needs_you":true,"ready":false,"done":true}"#
        let read: PushSettings = try await api().pushSettings()
        XCTAssertEqual(BodyStub.seen.last?.method, "GET")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/phone/push-settings")
        XCTAssertEqual(BodyStub.seen.last?.authorization, "Bearer phone.a.b")
        XCTAssertEqual(read, PushSettings(needsYou: true, ready: false, done: true))
    }

    func testTheSwitchesAreWrittenAllThreeTogetherAndTheAnswerIsWhatWasKept() async throws {
        BodyStub.reply = #"{"needs_you":false,"ready":false,"done":true}"#
        let kept: PushSettings = try await api().setPushSettings(PushSettings(needsYou: false, ready: false, done: true))
        XCTAssertEqual(BodyStub.seen.last?.method, "PUT")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/phone/push-settings")
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(Set(body.keys), ["needs_you", "ready", "done"], "the daemon refuses a missing switch and any other key")
        XCTAssertEqual(body["needs_you"] as? Bool, false)
        XCTAssertEqual(body["ready"] as? Bool, false)
        XCTAssertEqual(body["done"] as? Bool, true)
        XCTAssertEqual(kept, PushSettings(needsYou: false, ready: false, done: true))
    }

    // MARK: Item 1442, what waits on the owner

    func testWhatWaitsIsReadFromItsRouteWithThePhonesKey() async throws {
        BodyStub.reply = #"{"count":1,"items":[{"id":"apr_1","kind":"approval","run_id":"run_1","thread_id":"thr_1","agent":"Dot","title":"Run: ls","since":5.0,"route":"dotagent://run/run_1"}]}"#
        let list: NeedsYouList = try await api().needsYou()
        XCTAssertEqual(BodyStub.seen.last?.method, "GET")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/needs-you")
        XCTAssertEqual(BodyStub.seen.last?.authorization, "Bearer phone.a.b")
        XCTAssertEqual(list.count, 1)
        XCTAssertEqual(list.items.first?.kind, .approval)
    }

    // MARK: Item 1443, what Dot did while the owner was away

    func testWhatDotDidIsAskedSinceTheMark() async throws {
        BodyStub.reply = #"{"since":1764000000.0,"until":1764003600.0,"done":1,"runs":[]}"#
        let view: AwayView = try await api().away(since: 1_764_000_000)
        XCTAssertEqual(BodyStub.seen.last?.method, "GET")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/away")
        XCTAssertEqual(BodyStub.seen.last?.query, "since=1764000000.0")
        XCTAssertEqual(BodyStub.seen.last?.authorization, "Bearer phone.a.b")
        XCTAssertEqual(view.done, 1)
    }

    func testTheMarkIsReadAndSetOnItsOwnRoute() async throws {
        BodyStub.reply = #"{"read_at": 1764003000.0}"#
        let mark: AwayMark = try await api().awayMark()
        XCTAssertEqual(BodyStub.seen.last?.method, "GET")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/away/read")
        XCTAssertEqual(mark.readAt, 1_764_003_000)
        let set: AwayMark = try await api().markAway(until: 1_764_003_600)
        XCTAssertEqual(BodyStub.seen.last?.method, "POST")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/away/read")
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(Set(body.keys), ["until"], "the daemon refuses any other key")
        XCTAssertEqual(body["until"] as? Double, 1_764_003_600)
        XCTAssertEqual(set.readAt, 1_764_003_000, "the answer says where the mark stands")
    }
}
