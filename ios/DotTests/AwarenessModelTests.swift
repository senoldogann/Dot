import XCTest
@testable import Dot

/// The screens' model of the phone's awareness against a daemon that answers as the stub says (items 1441 to 1443): what is
/// asked, what is drawn, what is kept on disk, and what happens when the daemon does not answer.
@MainActor
final class AwarenessModelTests: XCTestCase {
    private var folder: URL = FileManager.default.temporaryDirectory

    /// Each case starts from a daemon that answers `{}` and a folder of its own (the class is on the main actor, so its
    /// `setUp` is not: the state is made here).
    private func begin() {
        BodyStub.reply = "{}"
        BodyStub.seen = []
        BodyStub.respond = nil
        BodyStub.status = 200
        let made: URL = FileManager.default.temporaryDirectory.appending(path: "awareness-\(UUID().uuidString)", directoryHint: .isDirectory)
        folder = made
        addTeardownBlock { try? FileManager.default.removeItem(at: made) }
    }

    private func make(link: LinkMonitor? = nil) -> AwarenessModel {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        let api: APIClient = APIClient(host: host, key: "phone.a.b", session: URLSession(configuration: configuration), readPolicy: .once)
        return AwarenessModel(api: api, cache: ChatCache(directory: folder), directory: folder, link: link)
    }

    /// Waits (up to two seconds) for `condition`: the model's writes run in tasks of their own.
    private func until(_ condition: @MainActor () -> Bool) async throws {
        for _ in 0..<200 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    private func writes() -> [BodyStub.Seen] { BodyStub.seen.filter { $0.method == "PUT" } }

    private func body(_ seen: BodyStub.Seen) throws -> [String: Bool] {
        let data: Data = try XCTUnwrap(seen.body)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Bool])
    }

    // MARK: Push switches

    func testReadingTheSwitchesShowsWhatTheDaemonHoldsAndKeepsItOnDisk() async throws {
        begin()
        BodyStub.reply = #"{"needs_you":true,"ready":false,"done":true}"#
        let model: AwarenessModel = make()
        XCTAssertFalse(model.push.isLive)
        await model.loadPush()
        XCTAssertEqual(BodyStub.seen.last?.method, "GET")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/phone/push-settings")
        XCTAssertEqual(model.push.shown, PushSettings(needsYou: true, ready: false, done: true))
        XCTAssertTrue(model.push.isLive)
        try await until { ChatCache.readAwareness(in: self.folder)?.pushSettings != nil }
        XCTAssertEqual(ChatCache.readAwareness(in: folder)?.pushSettings, PushSettings(needsYou: true, ready: false, done: true))
    }

    func testTheLastKnownSwitchesAreDrawnAtOnceFromDiskAndStayWhenTheDaemonDoesNotAnswer() async throws {
        begin()
        // A first launch's read, kept.
        BodyStub.reply = #"{"needs_you":false,"ready":true,"done":true}"#
        let first: AwarenessModel = make()
        await first.loadPush()
        try await until { ChatCache.readAwareness(in: self.folder) != nil }

        // The next launch, with the daemon out of reach.
        BodyStub.status = 500
        BodyStub.reply = #"{"message":"down"}"#
        let second: AwarenessModel = make()
        XCTAssertEqual(second.push.shown, PushSettings(needsYou: false, ready: true, done: true), "drawn before any network answer")
        XCTAssertFalse(second.push.isLive)
        await second.loadPush()
        XCTAssertEqual(second.push.shown, PushSettings(needsYou: false, ready: true, done: true), "the last known state stays")
        XCTAssertFalse(second.push.isLive, "and is dimmed")
        XCTAssertFalse(second.push.canFlip)
        second.flip(.done, to: false)
        XCTAssertEqual(writes().count, 0, "a switch that cannot be kept sends nothing")
    }

    func testAFlipIsWrittenAsAllThreeAndKept() async throws {
        begin()
        BodyStub.reply = #"{"needs_you":true,"ready":true,"done":true}"#
        let model: AwarenessModel = make()
        await model.loadPush()
        BodyStub.respond = { seen in
            seen.method == "PUT" ? (200, #"{"needs_you":true,"ready":false,"done":true}"#) : (200, "{}")
        }
        model.flip(.ready, to: false)
        XCTAssertEqual(model.push.shown.ready, false, "it shows before the daemon answers")
        try await until { !self.writes().isEmpty && model.push.sending == nil }
        let sent: [String: Bool] = try body(try XCTUnwrap(writes().last))
        XCTAssertEqual(sent, ["needs_you": true, "ready": false, "done": true], "all three together")
        XCTAssertEqual(writes().last?.authorization, "Bearer phone.a.b")
        XCTAssertEqual(writes().last?.path, "/api/phone/push-settings")
        XCTAssertEqual(model.push.confirmed, PushSettings(needsYou: true, ready: false, done: true))
        XCTAssertNil(model.push.failure)
        try await until { ChatCache.readAwareness(in: self.folder)?.pushSettings?.ready == false }
        XCTAssertEqual(ChatCache.readAwareness(in: folder)?.pushSettings?.ready, false)
    }

    func testAFlipTheDaemonRefusesGoesBackWithAPlainLine() async throws {
        begin()
        BodyStub.reply = #"{"needs_you":true,"ready":true,"done":true}"#
        let model: AwarenessModel = make()
        await model.loadPush()
        BodyStub.respond = { seen in seen.method == "PUT" ? (500, #"{"message":"no"}"#) : (200, "{}") }
        model.flip(.done, to: false)
        XCTAssertEqual(model.push.shown.done, false)
        try await until { model.push.failure != nil }
        XCTAssertEqual(model.push.shown, .all, "rolled back to what the daemon holds")
        XCTAssertEqual(model.push.failure, "Dot could not keep that, so the switches are back as they were.")
        XCTAssertNil(model.push.sending)
    }

    func testAPhoneTheDaemonNoLongerKnowsIsToldToPairAgain() async throws {
        begin()
        BodyStub.reply = #"{"needs_you":true,"ready":true,"done":true}"#
        let model: AwarenessModel = make()
        await model.loadPush()
        BodyStub.respond = { seen in seen.method == "PUT" ? (404, #"{"message":"that phone is not paired"}"#) : (200, "{}") }
        model.flip(.ready, to: false)
        try await until { model.push.failure != nil }
        XCTAssertEqual(model.push.failure, "Dot does not know this phone any more. Forget the key in Settings and pair again.")
    }

    func testTwoFlipsInARowAreTwoWritesInOrderTheSecondCarryingBoth() async throws {
        begin()
        BodyStub.reply = #"{"needs_you":true,"ready":true,"done":true}"#
        let model: AwarenessModel = make()
        await model.loadPush()
        BodyStub.respond = { seen in
            guard seen.method == "PUT", let data: Data = seen.body else { return (200, "{}") }
            return (200, String(decoding: data, as: UTF8.self))
        }
        model.flip(.ready, to: false)
        model.flip(.done, to: false)
        try await until { self.writes().count >= 2 && model.push.sending == nil }
        let all: [[String: Bool]] = try writes().map { try body($0) }
        XCTAssertEqual(all.count, 2)
        XCTAssertEqual(all[0], ["needs_you": true, "ready": false, "done": true])
        XCTAssertEqual(all[1], ["needs_you": true, "ready": false, "done": false], "the second write carries the latest of all three")
        XCTAssertEqual(model.push.shown, PushSettings(needsYou: true, ready: false, done: false))
    }
}
