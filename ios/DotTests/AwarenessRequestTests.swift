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
}
