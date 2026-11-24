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

    /// The numbers put on the app's icon, in order.
    private final class Badges: @unchecked Sendable {
        private let lock: NSLock = NSLock()
        private var all: [Int] = []
        func add(_ count: Int) {
            lock.lock()
            all.append(count)
            lock.unlock()
        }
        var counts: [Int] {
            lock.lock()
            defer { lock.unlock() }
            return all
        }
    }

    private func make(
        link: LinkMonitor? = nil, badges: Badges = Badges(), persist: (@Sendable (AwarenessSnapshot) async -> Void)? = nil
    ) -> AwarenessModel {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        let api: APIClient = APIClient(host: host, key: "phone.a.b", session: URLSession(configuration: configuration), readPolicy: .once)
        // The badge is the test's own: the system's would need a running app.
        return AwarenessModel(
            api: api, cache: ChatCache(directory: folder), directory: folder, link: link, badge: { badges.add($0) }, persist: persist)
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

    /// What the writer was given, in the order it finished.
    private final class Finished: @unchecked Sendable {
        private let lock: NSLock = NSLock()
        private var all: [Int] = []
        func add(_ count: Int) {
            lock.lock()
            all.append(count)
            lock.unlock()
        }
        var counts: [Int] {
            lock.lock()
            defer { lock.unlock() }
            return all
        }
    }

    /// The snapshot's writes finish in the order they were made, also when an older one is slow (the pre-push review): written
    /// unordered, the slow old one would end last and the file would hold a state that was already past. Here the older
    /// snapshot (one thing waits) takes 150 ms and the newer (two) none; the writer must still finish old, then new.
    func testSnapshotsAreWrittenOneAfterAnotherEvenWhenAnOlderOneIsSlow() async throws {
        begin()
        let finished: Finished = Finished()
        let model: AwarenessModel = make(persist: { snapshot in
            if snapshot.needsYou?.count == 1 { try? await Task.sleep(for: .milliseconds(150)) }
            finished.add(snapshot.needsYou?.count ?? -1)
        })
        func waiting(_ count: Int) -> String {
            let items: [String] = (0..<count).map {
                #"{"id":"apr_\#($0)","kind":"approval","run_id":"run_1","thread_id":"thr_1","agent":"Dot","title":"Run: ls","since":\#($0).0,"route":"dotagent://run/run_1"}"#
            }
            return #"{"count":\#(count),"items":[\#(items.joined(separator: ","))]}"#
        }
        BodyStub.reply = waiting(1)
        await model.refreshNeedsYou()
        BodyStub.reply = waiting(2)
        await model.refreshNeedsYou()
        XCTAssertEqual(model.needsYou.count, 2, "both reads reached the model")
        try await until { finished.counts.count >= 2 }
        XCTAssertEqual(finished.counts, [1, 2], "the older write finished before the newer began: the file ends as the newest state")
    }

    /// A finished write is not kept by the model: nothing waits on it (the review's second note).
    func testAFinishedWriteIsNotKeptByTheModel() async throws {
        begin()
        let finished: Finished = Finished()
        let model: AwarenessModel = make(persist: { snapshot in finished.add(snapshot.needsYou?.count ?? -1) })
        BodyStub.reply = #"{"count":0,"items":[]}"#
        await model.refreshNeedsYou()
        try await until { finished.counts.count >= 1 }
        try await until { !model.hasWriteInFlight }
        XCTAssertFalse(model.hasWriteInFlight)
    }

    // MARK: What waits on the owner

    private static let waiting: String = """
        {"count":2,"items":[
        {"id":"apr_2","kind":"question","run_id":"run_1","thread_id":"thr_1","agent":"Scout","title":"Which day should it ship?",
         "since":200.0,"route":"dotagent://run/run_1","choices":["Friday","Monday"],"recommended":"Friday"},
        {"id":"apr_1","kind":"approval","run_id":"run_1","thread_id":"thr_1","agent":"Dot","title":"Run: make deploy",
         "since":100.0,"route":"dotagent://run/run_1"}]}
        """

    func testWhatWaitsOnTheOwnerIsReadAndTheBadgeFollowsTheCount() async throws {
        begin()
        BodyStub.reply = Self.waiting
        let badges: Badges = Badges()
        let model: AwarenessModel = make(badges: badges)
        XCTAssertEqual(model.needsYou.count, 0)
        await model.refreshNeedsYou()
        XCTAssertEqual(BodyStub.seen.last?.method, "GET")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/needs-you")
        XCTAssertEqual(model.needsYou.count, 2)
        XCTAssertEqual(model.needsYou.items.map(\.id), ["apr_2", "apr_1"])
        XCTAssertTrue(model.needsYouIsLive)
        XCTAssertEqual(badges.counts, [2], "the icon's number is the count of every read")
        try await until { ChatCache.readAwareness(in: self.folder)?.needsYou?.count == 2 }
        XCTAssertEqual(ChatCache.readAwareness(in: folder)?.needsYou?.items.count, 2)

        // Answered on the Mac: the next read lowers the number.
        BodyStub.reply = #"{"count":0,"items":[]}"#
        await model.refreshNeedsYou()
        XCTAssertEqual(model.needsYou, .empty)
        XCTAssertEqual(badges.counts, [2, 0])
    }

    func testTheInboxIsDrawnFromDiskAtOnceAndAFailedReadLeavesItButSaysItIsOld() async throws {
        begin()
        BodyStub.reply = Self.waiting
        let first: AwarenessModel = make()
        await first.refreshNeedsYou()
        try await until { ChatCache.readAwareness(in: self.folder)?.needsYou != nil }

        BodyStub.status = 503
        BodyStub.reply = #"{"message":"down"}"#
        let badges: Badges = Badges()
        let second: AwarenessModel = make(badges: badges)
        XCTAssertEqual(second.needsYou.count, 2, "drawn before any network answer")
        XCTAssertFalse(second.needsYouIsLive)
        await second.refreshNeedsYou()
        XCTAssertEqual(second.needsYou.count, 2, "what the phone held stays")
        XCTAssertFalse(second.needsYouIsLive, "and says it was not read now")
        XCTAssertEqual(badges.counts, [], "a read that failed does not change the icon's number")
    }

    func testTwoReadsAtOnceAreOneRequest() async throws {
        begin()
        BodyStub.reply = Self.waiting
        let model: AwarenessModel = make()
        async let first: Void = model.refreshNeedsYou()
        async let second: Void = model.refreshNeedsYou()
        _ = await (first, second)
        XCTAssertEqual(BodyStub.seen.filter { $0.path == "/api/needs-you" }.count, 1, "a pull to refresh and a push arriving read it once")
    }

    // MARK: What Dot did while the owner was away

    private static let awayExample: String = """
        {"since":1000.0,"until":2000.0,"done":2,"failed":0,"cancelled":0,"working":0,"campaign_sessions":0,
         "runs":[{"run_id":"run_1","title":"Morning brief","source":"schedule","status":"done","steps":3,"created_at":1100.0,
                  "finished_at":1200.0,"answer":"Two mails need you."}],
         "decisions":{"reviewer_approved":0,"reviewer_denied":0,"yolo_approved":0,"yolo_denied":0},"decision_runs":[],"drafts":0}
        """

    /// A daemon that answers the three away routes: `mark` is where the shared mark stands (`nil`: no device has set it).
    private func awayDaemon(mark: Double?, view: String = AwarenessModelTests.awayExample) {
        BodyStub.respond = { seen in
            switch (seen.method, seen.path) {
            case ("GET", "/api/away/read"): return (200, mark.map { #"{"read_at":\#($0)}"# } ?? #"{"read_at":null}"#)
            case ("GET", "/api/away"): return (200, view)
            case ("POST", "/api/away/read"): return (200, #"{"read_at":2000.0}"#)
            default: return (200, "{}")
            }
        }
    }

    private func requests(_ method: String, _ path: String) -> [BodyStub.Seen] { BodyStub.seen.filter { $0.method == method && $0.path == path } }

    func testTheFirstVisitFromAnyDeviceMarksTheMomentAndShowsNothing() async throws {
        begin()
        awayDaemon(mark: nil)
        let model: AwarenessModel = make()
        await model.refreshAway()
        XCTAssertNil(model.away, "the card never pours out history")
        XCTAssertEqual(requests("GET", "/api/away").count, 0, "nothing is asked of a past that was never marked")
        let marks: [BodyStub.Seen] = requests("POST", "/api/away/read")
        XCTAssertEqual(marks.count, 1)
        let until: Double = try XCTUnwrap((try JSONSerialization.jsonObject(with: try XCTUnwrap(marks[0].body)) as? [String: Double])?["until"])
        XCTAssertEqual(until, Date().timeIntervalSince1970, accuracy: 30, "now")
    }

    func testWhatDotDidSinceTheMarkIsTheCardAndIsKeptOnDisk() async throws {
        begin()
        awayDaemon(mark: 1000)
        let model: AwarenessModel = make()
        await model.refreshAway()
        let asked: BodyStub.Seen = try XCTUnwrap(requests("GET", "/api/away").first)
        XCTAssertEqual(asked.query, "since=1000.0", "read against the mark Dot keeps")
        XCTAssertEqual(model.away?.until, 2000)
        XCTAssertEqual(model.away?.runs.map(\.title), ["Morning brief"])
        try await until { ChatCache.readAwareness(in: self.folder)?.away != nil }
        XCTAssertEqual(ChatCache.readAwareness(in: folder)?.away?.until, 2000)
    }

    func testNothingDoneSinceTheMarkIsNoCard() async throws {
        begin()
        awayDaemon(mark: 1000, view: #"{"since":1000.0,"until":2000.0,"done":0,"failed":0,"cancelled":0,"working":0,"campaign_sessions":0,"runs":[]}"#)
        let model: AwarenessModel = make()
        await model.refreshAway()
        XCTAssertNil(model.away)
    }

    func testPuttingTheCardAwayMarksItsMomentReadOnDotAndHidesItAtOnce() async throws {
        begin()
        awayDaemon(mark: 1000)
        let model: AwarenessModel = make()
        await model.refreshAway()
        XCTAssertNotNil(model.away)
        model.dismissAway()
        XCTAssertNil(model.away, "it goes before Dot has answered")
        try await until { !self.requests("POST", "/api/away/read").isEmpty }
        let body: [String: Double] = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: try XCTUnwrap(requests("POST", "/api/away/read").last?.body)) as? [String: Double])
        XCTAssertEqual(body, ["until": 2000], "the until of the card that was shown")
        try await until { ChatCache.readAwareness(in: self.folder)?.awayMarkPending == nil && ChatCache.readAwareness(in: self.folder) != nil }
        XCTAssertNil(ChatCache.readAwareness(in: folder)?.away)
        XCTAssertNil(ChatCache.readAwareness(in: folder)?.awayMarkPending, "told, so nothing waits")
    }

    func testACardPutAwayWithNoAnswerFromDotStaysAwayAndIsToldWhenTheNextReadGoes() async throws {
        begin()
        awayDaemon(mark: 1000)
        let model: AwarenessModel = make()
        await model.refreshAway()
        // Dot does not take the mark: the card is away on the phone all the same, and the mark is kept to send.
        BodyStub.respond = { seen in seen.method == "POST" ? (503, #"{"message":"down"}"#) : (200, "{}") }
        model.dismissAway()
        try await until { self.requests("POST", "/api/away/read").count >= 1 }
        try await Task.sleep(for: .milliseconds(60))
        XCTAssertNil(model.away)
        try await until { ChatCache.readAwareness(in: self.folder)?.awayMarkPending == 2000 }
        XCTAssertEqual(ChatCache.readAwareness(in: folder)?.awayMarkPending, 2000, "kept on disk until Dot has heard it")

        // The next launch: the card is not drawn from disk, and the read begins by telling Dot what was put away.
        awayDaemon(mark: 2000, view: #"{"since":2000.0,"until":3000.0,"done":0,"failed":0,"cancelled":0,"working":0,"campaign_sessions":0,"runs":[]}"#)
        let next: AwarenessModel = make()
        XCTAssertNil(next.away)
        BodyStub.seen = []
        await next.refreshAway()
        XCTAssertEqual(BodyStub.seen.first.map { "\($0.method) \($0.path)" }, "POST /api/away/read", "the mark the owner set goes first")
        XCTAssertEqual(requests("GET", "/api/away").first?.query, "since=2000.0")
        try await until { ChatCache.readAwareness(in: self.folder)?.awayMarkPending == nil }
        XCTAssertNil(ChatCache.readAwareness(in: folder)?.awayMarkPending)
    }

    func testAReadThatFailsLeavesTheCardThePhoneHeld() async throws {
        begin()
        awayDaemon(mark: 1000)
        let first: AwarenessModel = make()
        await first.refreshAway()
        try await until { ChatCache.readAwareness(in: self.folder)?.away != nil }

        BodyStub.respond = nil
        BodyStub.status = 503
        BodyStub.reply = #"{"message":"down"}"#
        let second: AwarenessModel = make()
        XCTAssertEqual(second.away?.runs.map(\.title), ["Morning brief"], "drawn before any network answer")
        await second.refreshAway()
        XCTAssertEqual(second.away?.runs.map(\.title), ["Morning brief"], "and kept when Dot does not answer")
    }
}
