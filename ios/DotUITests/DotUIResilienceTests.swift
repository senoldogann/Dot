import XCTest

/// The phone on a bad link, driven by taps in the simulator (item 1372): a chat that comes back drawn from the disk with no
/// link, the one line that says the phone is offline, a message typed there that waits and goes by itself when the link is
/// back, and a chat that opens at once on a link that takes seconds to answer. It needs the scratch daemon of `DotUITests`
/// and `ios/scripts/flaky_proxy.py` in front of it (`/tmp/dot-ui-res-daemon.json` names the daemon and a phone key,
/// `/tmp/dot-ui-res-proxy.json` the proxy's address, its mode file and the link file the debug build reads); it is skipped
/// without them. Screenshots go to `/tmp/dot-ui-res-shots/`.
final class DotUIResilienceTests: XCTestCase {
    struct Daemon: Decodable {
        let origin: String
        let key: String
    }

    struct Proxy: Decodable {
        let origin: String
        let modeFile: String
        let linkFile: String
    }

    var daemon: Daemon!
    var proxy: Proxy!
    var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        let data: Data = try XCTUnwrap(try? Data(contentsOf: URL(fileURLWithPath: "/tmp/dot-ui-res-daemon.json")), "no scratch daemon: skipped")
        daemon = try JSONDecoder().decode(Daemon.self, from: data)
        let wrapped: Data = try XCTUnwrap(try? Data(contentsOf: URL(fileURLWithPath: "/tmp/dot-ui-res-proxy.json")), "no proxy: skipped")
        let decoder: JSONDecoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        proxy = try decoder.decode(Proxy.self, from: wrapped)
        try? FileManager.default.createDirectory(atPath: "/tmp/dot-ui-res-shots", withIntermediateDirectories: true)
        app = XCUIApplication()
        setLink(up: true, mode: "pass")
    }

    override func tearDown() {
        setLink(up: true, mode: "pass")
        if (testRun?.totalFailureCount ?? 0) > 0 { shot("FAILED-resilience") }
    }

    func setLink(up: Bool, mode: String) {
        try? (up ? "up" : "down").write(toFile: proxy.linkFile, atomically: true, encoding: .utf8)
        try? mode.write(toFile: proxy.modeFile, atomically: true, encoding: .utf8)
    }

    func launch(open chat: String) {
        app.launchArguments = [
            "-DotDevOrigin", proxy.origin, "-DotDevKey", daemon.key, "-DotLinkFile", proxy.linkFile, "-DotOpenChat", chat,
        ]
        app.launch()
    }

    func shot(_ name: String) {
        let png: Data = XCUIScreen.main.screenshot().pngRepresentation
        try? png.write(to: URL(fileURLWithPath: "/tmp/dot-ui-res-shots/\(name).png"))
    }

    func api(_ path: String) throws -> Any {
        var request: URLRequest = URLRequest(url: URL(string: daemon.origin + path)!)
        request.setValue("Bearer \(daemon.key)", forHTTPHeaderField: "Authorization")
        let box: Box = Box()
        let done: XCTestExpectation = expectation(description: path)
        URLSession.shared.dataTask(with: request) { data, _, _ in
            box.data = data
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 10)
        return try JSONSerialization.jsonObject(with: try XCTUnwrap(box.data))
    }

    final class Box: @unchecked Sendable { var data: Data? }

    var banner: XCUIElement { app.descendants(matching: .any)["connection-banner"] }

    // MARK: A chat with no link

    func testAChatReadBeforeComesBackWithNoLinkWithTheLineThatSaysSoAndAMessageTypedThereGoesWhenTheLinkIsBack() throws {
        // 1. Online: the chat is read, so that the phone holds it.
        launch(open: "demo:tools")
        XCTAssertTrue(app.scrollViews.firstMatch.waitForExistence(timeout: 30))
        sleep(4)
        shot("resilience-1-online-chat")
        XCTAssertFalse(banner.exists, "no line while all is well")
        app.terminate()

        // 2. The app was killed and the phone has no link at all: the chat is there at once, from the disk.
        setLink(up: false, mode: "down")
        let began: Date = Date()
        launch(open: "demo:tools")
        let transcript: XCUIElement = app.scrollViews.firstMatch
        XCTAssertTrue(transcript.waitForExistence(timeout: 10))
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'List the invoices folder'")).firstMatch.waitForExistence(timeout: 5),
            "the saved messages are on the screen with no link")
        let secondsToRows: TimeInterval = Date().timeIntervalSince(began)
        XCTAssertTrue(banner.waitForExistence(timeout: 5))
        XCTAssertTrue(banner.label.hasPrefix("Offline — showing saved chats"), banner.label)
        print("RESILIENCE time to the saved chat with no link (launch included): \(secondsToRows) s; banner: \(banner.label)")
        shot("resilience-2-offline-restored-chat")

        // 3. A message typed on the dead link is kept and says it waits.
        let field: XCUIElement = app.descendants(matching: .any)["composer-field"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        let words: String = "Sent from a dead link \(Int(Date().timeIntervalSince1970))"
        field.typeText(words)
        app.buttons["send"].tap()
        XCTAssertTrue(app.staticTexts[words].waitForExistence(timeout: 5))
        let waiting: XCUIElement = app.descendants(matching: .any)["pending-status"]
        XCTAssertTrue(waiting.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Waiting to send"].exists)
        XCTAssertTrue(banner.label.contains("1 waiting to send"), banner.label)
        shot("resilience-3-sending-offline")

        // 4. The link comes back: it goes by itself, once.
        setLink(up: true, mode: "pass")
        let until: Date = Date().addingTimeInterval(40)
        while waiting.exists, Date() < until { sleep(1) }
        XCTAssertFalse(waiting.exists, "the message left the outbox")
        sleep(2)
        shot("resilience-4-delivered")
        let runs: [[String: Any]] = try XCTUnwrap(try api("/api/runs?limit=20") as? [[String: Any]])
        let threads: [[String: Any]] = try XCTUnwrap(try api("/api/threads?limit=100") as? [[String: Any]])
        let thread: String = try XCTUnwrap(threads.first { ($0["title"] as? String ?? "").hasPrefix("demo:tools") }?["id"] as? String)
        let queueView: [String: Any] = try XCTUnwrap(try api("/api/threads/\(thread)/queue") as? [String: Any])
        let queued: Int = (queueView["messages"] as? [[String: Any]] ?? []).filter { ($0["text"] as? String) == words }.count
        let started: Int = runs.filter { ($0["goal"] as? String) == words }.count
        XCTAssertEqual(started + queued, 1, "the daemon holds the message once")
        XCTAssertFalse(banner.exists, "and the line is gone")
    }

    // MARK: A bad link

    func testOnALinkThatTakesSecondsToAnswerTheChatIsThereAtOnceAndTheLineSaysItIsTrying() throws {
        launch(open: "demo:tools")
        XCTAssertTrue(app.scrollViews.firstMatch.waitForExistence(timeout: 30))
        sleep(4)
        app.terminate()
        setLink(up: true, mode: "bad:1")
        let began: Date = Date()
        launch(open: "demo:tools")
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'List the invoices folder'")).firstMatch.waitForExistence(timeout: 8),
            "the saved messages are there while every answer is seconds away")
        print("RESILIENCE time to the saved chat on the worst link (launch included): \(Date().timeIntervalSince(began)) s")
        shot("resilience-5-bad-network-chat")
        // The line says the phone is trying, once it has been trying for a moment.
        _ = banner.waitForExistence(timeout: 20)
        shot("resilience-6-bad-network-line")
    }
}
