import XCTest
@testable import Dot

/// The requests the native screens make, as the daemon's routes take them.
final class APIClientTests: XCTestCase {
    private func api() -> APIClient {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        return APIClient(host: host, key: "phone.a.b", session: URLSession(configuration: configuration))
    }

    override func setUp() {
        BodyStub.reply = "{}"
        BodyStub.seen = []
    }

    private func lastBody() throws -> [String: Any] {
        let data: Data = try XCTUnwrap(BodyStub.seen.last?.body)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testTheQueueIsReorderedWithTheIdsInTheNewOrder() async throws {
        BodyStub.reply = #"{"held":false,"messages":[]}"#
        _ = try await api().reorderQueue(thread: "thr_1", ids: ["b", "a"])
        let request: BodyStub.Seen = try XCTUnwrap(BodyStub.seen.last)
        XCTAssertEqual(request.method, "PUT")
        XCTAssertEqual(request.path, "/api/threads/thr_1/queue")
        XCTAssertEqual(try lastBody()["ids"] as? [String], ["b", "a"])
        XCTAssertEqual(request.authorization, "Bearer phone.a.b")
    }

    func testAQueuedMessageIsRemovedByItsId() async throws {
        BodyStub.reply = #"{"held":false,"messages":[]}"#
        _ = try await api().removeQueued("q_1")
        XCTAssertEqual(BodyStub.seen.last?.method, "DELETE")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/queue/q_1")
    }

    func testAMessageGoesToTheChatsQueueWithItsKey() async throws {
        BodyStub.reply = #"{"held":false,"messages":[]}"#
        _ = try await api().send(message: Message(text: "hi", key: "0123456789abcdef-key", model: nil, attachments: []), thread: "thr_1")
        XCTAssertEqual(BodyStub.seen.last?.method, "POST")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/threads/thr_1/queue")
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(body["text"] as? String, "hi")
        XCTAssertEqual(body["key"] as? String, "0123456789abcdef-key")
        XCTAssertNil(body["model"])
    }

    func testAnApprovalIsDecidedWithItsNote() async throws {
        BodyStub.reply = #"{"id":"apr_1","run_id":"run_1","status":"approved","tool":"shell","summary":"x","severity":"r","args":{}}"#
        let record: ApprovalRecord = try await api().decide(approval: "apr_1", approve: true, note: "the invoices one")
        XCTAssertEqual(record.status, "approved", "the daemon's own answer says how the card stands")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/approvals/apr_1/decision")
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(body["approve"] as? Bool, true)
        XCTAssertEqual(body["note"] as? String, "the invoices one")
    }

    func testPairingSendsTheCodeAndNoKey() async throws {
        BodyStub.reply = #"{"token":"phone.id.secret"}"#
        let key: String = try await api().pair(code: "7KQ2-M9XD")
        XCTAssertEqual(key, "phone.id.secret")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/phone/pair")
        XCTAssertNil(BodyStub.seen.last?.authorization, "the code is the credential: it needs no token")
        XCTAssertEqual(try lastBody()["code"] as? String, "7KQ2-M9XD")
    }

    func testAScheduleIsSwitchedOnAndOffOnItsOwnRoutes() async throws {
        BodyStub.reply = #"{"record":{"id":"s","name":"n","cron":null,"run_at":null,"goal":"g","enabled":true,"next_run_at":null,"last_run_at":null,"thread_id":null,"last_status":""},"when":"x","next_local":null,"agent":null,"brief":false}"#
        _ = try await api().setSchedule("sch_1", on: true)
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/schedules/sch_1/enable")
        _ = try await api().setSchedule("sch_1", on: false)
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/schedules/sch_1/disable")
    }

    func testARefusedRequestIsAStatusErrorWithItsBody() async {
        BodyStub.status = 409
        BodyStub.reply = "nope"
        defer { BodyStub.status = 200 }
        do {
            _ = try await api().reorderQueue(thread: "t", ids: ["a"])
            XCTFail("a 409 is an error")
        } catch DaemonRequestError.status(_, let code, let body) {
            XCTAssertEqual(code, 409)
            XCTAssertEqual(body, "nope")
        } catch {
            XCTFail("\(error)")
        }
    }
}

/// Keeps what each request carried, and answers with `reply`.
final class BodyStub: URLProtocol, @unchecked Sendable {
    struct Seen {
        var method: String
        var path: String
        var authorization: String?
        var body: Data?
    }

    nonisolated(unsafe) static var reply: String = "{}"
    nonisolated(unsafe) static var status: Int = 200
    nonisolated(unsafe) static var seen: [Seen] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var body: Data? = request.httpBody
        if body == nil, let stream: InputStream = request.httpBodyStream {
            stream.open()
            var collected: Data = Data()
            var buffer: [UInt8] = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count: Int = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                collected.append(buffer, count: count)
            }
            stream.close()
            body = collected
        }
        Self.seen.append(Seen(
            method: request.httpMethod ?? "", path: request.url?.path ?? "", authorization: request.value(forHTTPHeaderField: "Authorization"),
            body: body))
        let response: HTTPURLResponse = HTTPURLResponse(url: request.url!, statusCode: Self.status, httpVersion: "HTTP/1.1", headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(Self.reply.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
