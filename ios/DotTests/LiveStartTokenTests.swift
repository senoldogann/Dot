import XCTest
@testable import Dot

/// The token that lets Dot start the phone's Live Activity by push (item 1445): when it is sent, what is sent, and when it is
/// taken back.
final class LiveStartTokenTests: XCTestCase {
    private let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)

    private func client() -> DaemonClient {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        return DaemonClient(session: URLSession(configuration: configuration), maxAttempts: 1)
    }

    override func setUp() {
        BodyStub.reply = ""
        BodyStub.seen = []
        BodyStub.respond = nil
        BodyStub.status = 204
    }

    /// A token as iOS gives it, in the hex the daemon takes: 32 bytes are 64 characters.
    private func token(_ byte: UInt8 = 0xAB, bytes: Int = 32) -> String { hexString(Data(repeating: byte, count: bytes)) }

    // MARK: When it is sent

    func testATokenThatIOSGivesIsSentOnceAndAgainWhenItChanges() {
        var gate: StartTokenGate = StartTokenGate()
        let first: String = token(0xAB)
        XCTAssertTrue(gate.shouldSend(first))
        gate.didSend(first)
        XCTAssertFalse(gate.shouldSend(first), "the daemon has it: iOS saying it again is no news")
        XCTAssertTrue(gate.shouldSend(token(0xCD)), "a new token is sent")
        XCTAssertEqual(gate.held, first, "and the daemon still has the old one until it took the new")
    }

    func testATokenThatWasNotTakenIsOfferedAgain() {
        let gate: StartTokenGate = StartTokenGate()
        let first: String = token(0xAB)
        XCTAssertTrue(gate.shouldSend(first))
        // The request failed: nothing was noted, so the next time iOS says it (or the app starts), it goes again.
        XCTAssertTrue(gate.shouldSend(first))
    }

    func testAfterTheDaemonForgotTheTokenTheSameOneIsSentAgain() {
        var gate: StartTokenGate = StartTokenGate()
        gate.didSend(token())
        gate.forget()
        XCTAssertNil(gate.held)
        XCTAssertTrue(gate.shouldSend(token()), "Live Activities switched on again, or a new key: the daemon has none")
    }

    func testOnlyTheShapeTheDaemonTakesIsSent() {
        XCTAssertTrue(StartTokenGate.isValid(token(0xAB, bytes: 32)), "64 characters")
        XCTAssertTrue(StartTokenGate.isValid(token(0xAB, bytes: 256)), "512 characters")
        XCTAssertFalse(StartTokenGate.isValid(token(0xAB, bytes: 31)), "62 characters: too short")
        XCTAssertFalse(StartTokenGate.isValid(token(0xAB, bytes: 257)), "514 characters: too long")
        XCTAssertFalse(StartTokenGate.isValid(String(repeating: "AB", count: 32)), "capitals: the daemon wants lowercase hex")
        XCTAssertFalse(StartTokenGate.isValid(String(repeating: "zz", count: 32)))
        XCTAssertFalse(StartTokenGate.isValid(""))
        XCTAssertFalse(StartTokenGate().shouldSend("not a token"))
    }

    func testTheHexIsWhatAPNsAndTheDaemonExpect() {
        XCTAssertEqual(hexString(Data([0x00, 0x0F, 0xAB, 0xFF])), "000fabff", "lowercase, two digits a byte, no separator")
    }

    // MARK: What is sent

    func testTheTokenIsPostedToItsOwnRouteWithThePhonesKeyAndNothingElse() async throws {
        let sent: String = token()
        try await client().registerStartToken(LiveStartToken(token: sent), host: host, key: "phone.a.b")
        XCTAssertEqual(BodyStub.seen.last?.method, "POST")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/phone/live-start-token")
        XCTAssertEqual(BodyStub.seen.last?.authorization, "Bearer phone.a.b")
        let body: [String: Any] = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try XCTUnwrap(BodyStub.seen.last?.body)) as? [String: Any])
        XCTAssertEqual(Set(body.keys), ["token"], "the daemon refuses any other key")
        XCTAssertEqual(body["token"] as? String, sent)
    }

    func testForgettingTheTokenIsADeleteOnTheSameRouteWithNoBody() async throws {
        try await client().forgetStartToken(host: host, key: "phone.a.b")
        XCTAssertEqual(BodyStub.seen.last?.method, "DELETE")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/phone/live-start-token")
        XCTAssertEqual(BodyStub.seen.last?.authorization, "Bearer phone.a.b")
        XCTAssertTrue((BodyStub.seen.last?.body ?? Data()).isEmpty)
    }

    func testATokenTheDaemonRefusesIsAnErrorAndIsNotSentAgain() async {
        BodyStub.status = 422
        BodyStub.reply = #"{"message":"a token that is not hex","context":{}}"#
        let refused: DaemonClient = DaemonClient(
            session: client().session, maxAttempts: 5)
        do {
            try await refused.registerStartToken(LiveStartToken(token: token()), host: host, key: "phone.a.b")
            XCTFail("a 422 is an error")
        } catch DaemonRequestError.status(_, let code, _) {
            XCTAssertEqual(code, 422)
            XCTAssertEqual(BodyStub.seen.count, 1, "a refusal will not change when asked again")
        } catch {
            XCTFail("\(error)")
        }
    }

    /// The device's registration is as it was: `start_token` is a key that must be there, and null, since the token goes by its
    /// own route and a token in it is kept and never read.
    func testTheDevicesRegistrationStillCarriesNoStartToken() throws {
        let encoder: JSONEncoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        let body: String = String(
            decoding: try encoder.encode(APNsRegistration(deviceToken: "abcd", startToken: nil, environment: "sandbox")), as: UTF8.self)
        XCTAssertEqual(body, #"{"device_token":"abcd","environment":"sandbox","start_token":null}"#)
    }
}
