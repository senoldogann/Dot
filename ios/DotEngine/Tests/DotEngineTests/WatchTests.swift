import Foundation
import XCTest

@testable import DotEngine

/// Watching from the phone (items 1837-1839): the live stream's frames, the cast's frames, the input the page takes, and
/// the rules of what a run drives, its hold, its action now, smoothness and quality.
final class LiveVideoTests: XCTestCase {
    /// A frame as `dotagent/desktop/live.py`'s `avc_message` writes it.
    private func message(_ kind: UInt8, micros: UInt64, config: Data, frame: Data) -> Data {
        var payload: Data = Data()
        if kind == 1 {
            payload.append(contentsOf: [UInt8(config.count >> 8), UInt8(config.count & 0xFF)])
            payload.append(config)
        }
        payload.append(frame)
        var out: Data = Data([kind])
        out.append(contentsOf: withUnsafeBytes(of: UInt32(payload.count).bigEndian, Array.init))
        out.append(contentsOf: withUnsafeBytes(of: micros.bigEndian, Array.init))
        out.append(payload)
        return out
    }

    /// An `avcC` record with one SPS and one PPS and 4-byte lengths.
    private let avcc: Data = Data([1, 0x64, 0, 0x32, 0xFF, 0xE1, 0, 3, 0x67, 0x64, 0x32, 1, 0, 2, 0x68, 0xEE])

    private var stream: Data {
        message(1, micros: 1_500_000, config: avcc, frame: Data([0, 0, 0, 1, 0x65]))
            + message(2, micros: 1_516_666, config: Data(), frame: Data([0, 0, 0, 1, 0x41]))
    }

    func testTheFramesAreReadWhateverTheChunksAre() throws {
        let bytes: Data = stream
        let whole: [LiveFrame] = try splitLiveFrames(bytes).frames
        XCTAssertEqual(whole.map(\.kind), [.key, .delta])
        XCTAssertEqual(whole.map(\.micros), [1_500_000, 1_516_666])
        for cut in 0...bytes.count {
            var rest: Data = Data()
            var frames: [LiveFrame] = []
            for chunk in [bytes.prefix(cut), bytes.suffix(from: cut)] {
                let split = try splitLiveFrames(rest + chunk)
                frames += split.frames
                rest = split.rest
            }
            XCTAssertEqual(frames, whole, "cut at \(cut)")
            XCTAssertTrue(rest.isEmpty)
        }
    }

    func testAKeyFrameCarriesTheDecodersConfiguration() throws {
        let key: LiveFrame = try XCTUnwrap(try splitLiveFrames(stream).frames.first)
        let split = try XCTUnwrap(splitKeyPayload(key.payload))
        XCTAssertEqual(split.avcc, avcc)
        XCTAssertEqual(split.frame, Data([0, 0, 0, 1, 0x65]))
        let configuration: AVCConfiguration = try XCTUnwrap(avcConfiguration(split.avcc))
        XCTAssertEqual(configuration.sequenceSets, [Data([0x67, 0x64, 0x32])])
        XCTAssertEqual(configuration.pictureSets, [Data([0x68, 0xEE])])
        XCTAssertEqual(configuration.nalLengthSize, 4)
        XCTAssertNil(avcConfiguration(avcc.prefix(9)), "a record cut short is no configuration")
    }

    func testAStreamThatIsNotTheDaemonsEnds() {
        var broken: Data = stream
        broken[0] = 9
        XCTAssertThrowsError(try splitLiveFrames(broken))
    }
}

final class WatchRulesTests: XCTestCase {
    private func call(_ id: Int, _ tool: String, _ args: [String: JSONValue], call: String) -> EventRecord {
        EventRecord(id: id, runId: "run_1", ts: Double(id), kind: "tool_call", data: ["tool": .string(tool), "args": .object(args), "call_id": .string(call)])
    }

    private func result(_ id: Int, call: String) -> EventRecord {
        EventRecord(id: id, runId: "run_1", ts: Double(id), kind: "tool_result", data: ["call_id": .string(call)])
    }

    private func event(_ id: Int, _ kind: String, _ data: [String: JSONValue]) -> EventRecord {
        EventRecord(id: id, runId: "run_1", ts: Double(id), kind: kind, data: data)
    }

    func testTheSurfaceIsTheNewestStepsOwn() {
        XCTAssertNil(runSurface([call(1, "shell", [:], call: "a")]))
        XCTAssertEqual(runSurface([call(1, "browser_act", [:], call: "a"), call(2, "shell", [:], call: "b")]), .browser)
        XCTAssertEqual(runSurface([call(1, "browser_act", [:], call: "a"), call(2, "desktop_act", [:], call: "b")]), .desktop)
        // web_fetch is the web, not a browser the phone can show.
        XCTAssertNil(runSurface([call(1, "web_fetch", [:], call: "a")]))
    }

    func testTheHoldIsTheNewestTakeoverOrHandback() {
        XCTAssertEqual(runHold([]), .free)
        XCTAssertEqual(runHold([event(1, "takeover", ["by": .string("screen")])]), .held(onScreen: true))
        XCTAssertEqual(runHold([event(1, "takeover", ["source": .string("ui")])]), .held(onScreen: false))
        XCTAssertEqual(runHold([event(1, "takeover", ["by": .string("screen")]), event(2, "handback", [:])]), .free)
    }

    func testTheActionIsTheNewestUnansweredStepOfThePage() {
        let steps: JSONValue = .array([
            .object(["action": .string("fill"), "ref": .string("e5"), "text": .string("Ada\nLovelace")]),
            .object(["action": .string("click"), "ref": .string("e9")]),
        ])
        let acting: [EventRecord] = [call(1, "browser_act", ["steps": steps], call: "a")]
        XCTAssertEqual(liveAction(acting), LiveAction(kind: .type, detail: "Ada Lovelace", steps: 2))
        XCTAssertNil(liveAction(acting + [result(2, call: "a")]), "between steps there is no action")
        XCTAssertNil(liveAction(acting + [event(2, "run_finished", [:])]))
        let opening: [EventRecord] = [call(1, "browser_new_tab", ["url": .string("https://example.com")], call: "b")]
        XCTAssertEqual(liveAction(opening), LiveAction(kind: .open, detail: "https://example.com", steps: 1))
        // The tool a real run opened its page with (claude_code:haiku, 2026-11-25).
        let opened: [EventRecord] = [call(1, "browser_open", ["url": .string("http://127.0.0.1:49905/")], call: "e")]
        XCTAssertEqual(liveAction(opened)?.kind, .open)
        let desktop: [EventRecord] = [call(1, "desktop_act", ["steps": .array([.object(["action": .string("key"), "key": .string("cmd+s")])])], call: "c")]
        XCTAssertEqual(liveAction(desktop), LiveAction(kind: .key, detail: "cmd+s", steps: 1))
        XCTAssertNil(liveAction([call(1, "shell", ["command": .string("ls")], call: "d")]))
    }

    func testACastMessageCarriesTheTabsAddressAndPicture() {
        let jpeg: Data = Data([0xFF, 0xD8, 0xFF, 0xD9])
        let message: SSEMessage = SSEMessage(
            event: "frame", data: "{\"url\": \"https://example.com/\", \"jpeg_b64\": \"\(jpeg.base64EncodedString())\"}", id: nil)
        XCTAssertEqual(castFrame(of: message), CastFrame(url: "https://example.com/", jpeg: jpeg))
        XCTAssertNil(castFrame(of: SSEMessage(event: "opening", data: "{}", id: nil)))
    }

    func testTheMeterCountsTheLastTwoSeconds() {
        var meter: LiveFrameMeter = LiveFrameMeter(opened: 10)
        XCTAssertNil(meter.firstFrameMs)
        for frame in 0..<90 { meter = meter.seeing(at: 10.4 + Double(frame) / 30) }
        XCTAssertEqual(meter.firstFrameMs, 400)
        XCTAssertEqual(meter.fps(at: 10.4 + 89.0 / 30), 30, accuracy: 0.6)
        XCTAssertEqual(meter.fps(at: 20), 0)
        XCTAssertEqual(meter.frames, 90)
    }

    func testAPictureThatFallsBehindMakesTheLinkTooSlow() {
        var lag: LagMeter = LagMeter()
        // On time: arrival keeps pace with capture.
        for frame in 0..<30 { lag = lag.seeing(micros: UInt64(frame) * 33_333, arrival: 100 + Double(frame) / 30) }
        XCTAssertFalse(lag.tooSlow)
        // Then each frame comes 1.5 s later than its capture would have it.
        for frame in 30..<41 { lag = lag.seeing(micros: UInt64(frame) * 33_333, arrival: 101.5 + Double(frame) / 30) }
        XCTAssertFalse(lag.tooSlow, "eleven late frames are not yet a slow link")
        lag = lag.seeing(micros: 41 * 33_333, arrival: 101.5 + 41.0 / 30)
        XCTAssertTrue(lag.tooSlow)
        lag = lag.seeing(micros: 42 * 33_333, arrival: 100 + 42.0 / 30)
        XCTAssertFalse(lag.tooSlow, "one on time resets the count")
    }

    func testAutoReadsLightOnACostlyLinkOrAfterFallingBehind() {
        XCTAssertEqual(watchQuality(choice: .auto, costlyLink: false, fellBehindInHD: false), .hd)
        XCTAssertEqual(watchQuality(choice: .auto, costlyLink: true, fellBehindInHD: false), .light)
        XCTAssertEqual(watchQuality(choice: .auto, costlyLink: false, fellBehindInHD: true), .light)
        XCTAssertEqual(watchQuality(choice: .hd, costlyLink: true, fellBehindInHD: true), .hd)
        XCTAssertEqual(watchQuality(choice: .light, costlyLink: false, fellBehindInHD: false), .light)
    }
}

final class RemoteInputTests: XCTestCase {
    private func json(_ events: [RemoteInputEvent]) throws -> [[String: JSONValue]] {
        let data: Data = try JSONEncoder().encode(RemoteInputBatch(events: events))
        let batch: [String: JSONValue] = try JSONDecoder().decode([String: JSONValue].self, from: data)
        return try XCTUnwrap(batch["events"]?.array).compactMap(\.object)
    }

    func testEventsAreWrittenAsTheDaemonTakesThem() throws {
        let place: PictureFraction = PictureFraction(x: 0.25, y: 0.5)
        let written: [[String: JSONValue]] = try json(
            tapEvents(at: place, button: .left, clicks: 1) + [.wheel(place, dx: 0, dy: 120), .key("Enter"), .text("hi")])
        XCTAssertEqual(written.map { $0["kind"]?.string }, ["move", "down", "up", "wheel", "key", "text"])
        XCTAssertEqual(written[1], ["kind": .string("down"), "x": .double(0.25), "y": .double(0.5), "button": .string("left"), "clicks": .int(1)])
        XCTAssertEqual(written[3]["dy"]?.double, 120)
        XCTAssertEqual(written[4]["key"]?.string, "Enter")
        XCTAssertEqual(written[5]["text"]?.string, "hi")
    }

    func testLongTextAndManyEventsAreCutToTheDaemonsLimits() {
        let text: String = String(repeating: "a", count: 1200)
        let events: [RemoteInputEvent] = textEvents(text)
        XCTAssertEqual(events.count, 3)
        XCTAssertEqual(events.last, .text(String(repeating: "a", count: 200)))
        XCTAssertEqual(inputBatches(Array(repeating: .key("a"), count: 130)).map(\.count), [64, 64, 2])
    }

    func testADragUpScrollsThePageDownOnItsOwnScale() {
        let scroll = scrollFor(dx: 0, dy: -100, drawnWidth: 390, pageWidth: 1280)
        XCTAssertEqual(scroll.dy, 328.2, accuracy: 0.1)
        XCTAssertEqual(scroll.dx, 0)
        XCTAssertEqual(scrollFor(dx: 0, dy: -100_000, drawnWidth: 390, pageWidth: 1280).dy, 4000)
    }
}

/// The calls against a stand-in daemon: the quality asked, the chunks read, a refusal's words, the input's answer.
final class WatchAPITests: XCTestCase {
    override func setUp() {
        super.setUp()
        WatchStandIn.reset()
    }

    private func api() -> DaemonAPI {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [WatchStandIn.self]
        let session: URLSession = URLSession(configuration: configuration)
        return DaemonAPI(host: DaemonHost(name: "watch.ts.net", origin: URL(string: "https://watch.ts.net")!), key: "k", session: session, streamSession: session)
    }

    func testTheScreenIsReadInTheQualityAskedAndInChunks() async throws {
        WatchStandIn.answer = (200, Data([1, 2, 3, 4, 5]))
        var read: Data = Data()
        for try await chunk in api().screenLive(quality: .light) { read.append(chunk) }
        XCTAssertEqual(read, Data([1, 2, 3, 4, 5]))
        XCTAssertEqual(WatchStandIn.seen.first, "GET /api/screen/live?quality=light")
    }

    func testARefusedStreamSaysTheDaemonsWords() async {
        WatchStandIn.answer = (403, Data(#"{"message": "macOS has not granted Screen Recording to the app that runs Dot"}"#.utf8))
        do {
            for try await _ in api().screenLive(quality: .hd) {}
            XCTFail("a refused stream ends with its refusal")
        } catch let error as DaemonRequestError {
            XCTAssertEqual(error.statusCode, 403)
            XCTAssertEqual(error.detail, "macOS has not granted Screen Recording to the app that runs Dot")
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testInputIsPostedAndThePagesSizeRead() async throws {
        WatchStandIn.answer = (200, Data(#"{"accepted": 3, "url": "https://example.com/", "width": 1280, "height": 720}"#.utf8))
        let taken: RemoteInputResult = try await api().sendInput(run: "run_1", events: tapEvents(at: PictureFraction(x: 0.5, y: 0.5), button: .left, clicks: 1))
        XCTAssertEqual(taken, RemoteInputResult(accepted: 3, url: "https://example.com/", width: 1280, height: 720))
        XCTAssertEqual(WatchStandIn.seen, ["POST /api/runs/run_1/input"])
    }

    func testATakeoverTheDaemonRefusesSaysWhy() async {
        WatchStandIn.answer = (409, Data(#"{"message": "using the page from the screen is switched off: [browser] remote_input, in Settings › Browser"}"#.utf8))
        do {
            try await api().takeOverOnScreen(run: "run_1")
            XCTFail("refused")
        } catch {
            XCTAssertEqual(error.statusCode, 409)
            XCTAssertTrue(error.detail.hasPrefix("using the page from the screen is switched off"))
        }
        XCTAssertEqual(WatchStandIn.seen, ["POST /api/runs/run_1/takeover/screen"])
    }
}

/// Answers every request with one status and body, and keeps what was asked.
final class WatchStandIn: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var answer: (Int, Data) = (200, Data())
    nonisolated(unsafe) static var seen: [String] = []
    private static let lock: NSLock = NSLock()

    static func reset() {
        lock.withLock {
            answer = (200, Data())
            seen = []
        }
    }

    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "watch.ts.net" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url: URL = request.url else { return }
        let (status, body): (Int, Data) = Self.lock.withLock {
            Self.seen.append("\(request.httpMethod ?? "GET") \(url.path)\(url.query.map { "?\($0)" } ?? "")")
            return Self.answer
        }
        let response: HTTPURLResponse = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
