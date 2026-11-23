import XCTest

/// The native screens driven by taps in the simulator, against a scratch daemon with a scripted model. The daemon writes
/// its address and a phone key to `/tmp/dot-ui-daemon.json` (see `ios/README.md`); without it every test is skipped.
/// Screenshots go to `/tmp/dot-ui-shots/`.
final class DotUITests: XCTestCase {
    struct Daemon: Decodable {
        let origin: String
        let key: String
    }

    /// A message no other run of the scratch daemon holds, so that the chat it starts is found by it.
    let newChatText: String = "Plan the week \(Int(Date().timeIntervalSince1970))"
    var daemon: Daemon!
    var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        let data: Data = try XCTUnwrap(
            try? Data(contentsOf: URL(fileURLWithPath: "/tmp/dot-ui-daemon.json")), "no scratch daemon: tests skipped") 
        daemon = try JSONDecoder().decode(Daemon.self, from: data)
        try? FileManager.default.createDirectory(atPath: "/tmp/dot-ui-shots", withIntermediateDirectories: true)
        app = XCUIApplication()
    }

    override func tearDown() {
        if (testRun?.totalFailureCount ?? 0) > 0 { shot("FAILED-\(name.split(separator: " ").last?.dropLast() ?? "test")") }
    }

    /// `clearDrafts` is for the first launch of a test: what an earlier test left in a box is not this test's.
    func launch(open chat: String? = nil, extra: [String] = [], clearDrafts: Bool = true) {
        app.launchArguments = ["-DotDevOrigin", daemon.origin, "-DotDevKey", daemon.key] + (chat.map { ["-DotOpenChat", $0] } ?? []) + extra
            + (clearDrafts ? ["-DotClearDrafts", "1"] : [])
        app.launch()
    }

    func shot(_ name: String) {
        let png: Data = XCUIScreen.main.screenshot().pngRepresentation
        try? png.write(to: URL(fileURLWithPath: "/tmp/dot-ui-shots/\(name).png"))
    }

    /// What the daemon says (a GET), for what the screen did.
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

    /// The composer's box of the screen showing now (a text view or a field, by how the OS builds the vertical field).
    var box: XCUIElement { app.descendants(matching: .any)["composer-field"] }

    /// Types `text` into the box, after clearing what a draft left there.
    func type(_ text: String, into field: XCUIElement) {
        field.tap()
        if let old: String = field.value as? String, !old.isEmpty, old != "Message Dot" {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: old.count))
        }
        field.typeText(text)
    }

    /// Scrolls the screen up a little at a time until `element` is on it.
    func reveal(_ element: XCUIElement) {
        for _ in 0..<6 where !element.waitForExistence(timeout: 2) { app.swipeUp() }
    }

    // MARK: Older messages

    func testScrollingUpReadsOlderMessagesPageByPage() throws {
        launch(open: "Summarize")
        let transcript: XCUIElement = app.scrollViews.firstMatch
        XCTAssertTrue(transcript.waitForExistence(timeout: 20))
        sleep(2)
        let first: XCUIElement = transcript.staticTexts["Summarize what changed in my folders this week"]
        XCTAssertFalse(first.exists, "the oldest message is not held yet")
        shot("older-1-before")
        for _ in 0..<12 where !app.buttons["load-older"].isHittable { transcript.swipeDown() }
        // Reaching the top reads a page by itself; the button is the way when it did not.
        if app.buttons["load-older"].waitForExistence(timeout: 2), app.buttons["load-older"].isHittable { app.buttons["load-older"].tap() }
        shot("older-2-after-first-page")
        for _ in 0..<14 where !first.exists { transcript.swipeDown() }
        let oldest: XCUIElement = first
        XCTAssertTrue(oldest.waitForExistence(timeout: 10), "the first message of the chat was read page by page")
        shot("older-3-oldest")
        // The page after the last one holds nothing: the line that offered more is gone.
        XCTAssertTrue(app.buttons["load-older"].waitForNonExistence(timeout: 10))
    }

    // MARK: A step's detail

    func testATapOnAToolStepOpensItsArgumentsAndResult() throws {
        launch(open: "demo:tools")
        let step: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'tool-'")).firstMatch
        XCTAssertTrue(step.waitForExistence(timeout: 20))
        shot("tool-1-chat")
        step.tap()
        XCTAssertTrue(app.staticTexts["Arguments"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Result"].waitForExistence(timeout: 10))
        shot("tool-2-detail")
    }

    // MARK: Message actions

    func testLongPressCopiesAndReactsAndTheReactionStands() throws {
        launch(open: "Summarize")
        let transcript: XCUIElement = app.scrollViews.firstMatch
        XCTAssertTrue(transcript.waitForExistence(timeout: 20))
        sleep(2)
        let all: XCUIElementQuery = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'I looked at the three folders'"))
        XCTAssertTrue(all.firstMatch.waitForExistence(timeout: 10))
        // The newest of them, the one at the bottom of the screen.
        let words: XCUIElement = all.element(boundBy: all.count - 1)
        words.press(forDuration: 1.2)
        XCTAssertTrue(app.buttons["Copy"].waitForExistence(timeout: 5))
        shot("actions-1-menu")
        app.buttons["React"].tap()
        let good: XCUIElement = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Good'")).firstMatch
        XCTAssertTrue(good.waitForExistence(timeout: 5))
        shot("actions-2-react")
        good.tap()
        XCTAssertTrue(app.descendants(matching: .any)["reactions"].firstMatch.waitForExistence(timeout: 30))
        shot("actions-3-standing")
        let standing: [[String: Any]] = (try api("/api/threads/\(try thread("Summarize"))/reactions") as? [[String: Any]]) ?? []
        XCTAssertEqual(standing.first?["reaction"] as? String, "good", "the daemon holds the reaction")
    }

    func thread(_ prefix: String) throws -> String {
        let all: [[String: Any]] = try XCTUnwrap(try api("/api/threads?limit=100") as? [[String: Any]])
        return try XCTUnwrap(all.first { ($0["title"] as? String ?? "").hasPrefix(prefix) }?["id"] as? String)
    }

    // MARK: New chat

    func testANewChatStartsOnTheModelAndLevelPicked() throws {
        launch()
        let compose: XCUIElement = app.buttons["new-chat"]
        XCTAssertTrue(compose.waitForExistence(timeout: 20))
        compose.tap()
        let field: XCUIElement = box
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        type(newChatText, into: field)
        shot("new-1-typed")
        app.buttons["model-pill"].tap()
        let deep: XCUIElement = app.buttons["model-ollama:deep"]
        reveal(deep)
        XCTAssertTrue(deep.exists)
        deep.tap()
        let high: XCUIElement = app.buttons["effort-high"]
        XCTAssertTrue(high.waitForExistence(timeout: 5))
        high.tap()
        shot("new-2-picked")
        app.buttons["Done"].tap()
        app.buttons["send"].tap()
        // The chat opens with its first message.
        XCTAssertTrue(app.staticTexts[newChatText].waitForExistence(timeout: 20))
        shot("new-3-opened")
        let runs: [[String: Any]] = try XCTUnwrap(try api("/api/runs?limit=5") as? [[String: Any]])
        let mine: [String: Any]? = runs.first { ($0["goal"] as? String) == newChatText }
        XCTAssertEqual(mine?["model"] as? String, "ollama:deep", "the run records the model picked")
        let all: [[String: Any]] = try XCTUnwrap(try api("/api/threads?limit=100") as? [[String: Any]])
        XCTAssertEqual(all.filter { ($0["title"] as? String ?? "").hasPrefix(newChatText) }.count, 1, "a chat of its own")
    }

    func testAnAgentWithNoChatStartsItsOwnFromTheStrip() throws {
        launch()
        let strip: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Research'")).firstMatch
        XCTAssertTrue(strip.waitForExistence(timeout: 20))
        strip.tap()
        let field: XCUIElement = box
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        shot("agent-1-empty")
        type("Read the news", into: field)
        app.buttons["send"].tap()
        XCTAssertTrue(app.staticTexts["Read the news"].waitForExistence(timeout: 20))
        shot("agent-2-started")
        let runs: [[String: Any]] = try XCTUnwrap(try api("/api/runs?limit=5") as? [[String: Any]])
        let mine: [String: Any]? = runs.first { ($0["goal"] as? String) == "Read the news" }
        XCTAssertNotNil(mine?["agent_id"] as? String, "the run belongs to the agent")
    }

    // MARK: Model and effort of a chat

    func testTheHeaderSheetPicksTheChatsModelAndLevelAndTheDaemonKeepsThem() throws {
        launch(open: "Summarize")
        XCTAssertTrue(app.buttons["Run details"].waitForExistence(timeout: 20))
        app.buttons["Run details"].tap()
        let row: XCUIElement = app.buttons["chat-model"]
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        shot("chat-model-1-sheet")
        row.tap()
        let deep: XCUIElement = app.buttons["model-ollama:deep"]
        reveal(deep)
        XCTAssertTrue(deep.exists)
        deep.tap()
        let low: XCUIElement = app.buttons["effort-low"]
        XCTAssertTrue(low.waitForExistence(timeout: 5))
        low.tap()
        shot("chat-model-2-picked")
        sleep(1)
        let thread: String = try thread("Summarize")
        let runs: [[String: Any]] = try XCTUnwrap(try api("/api/runs?thread_id=\(thread)&limit=1") as? [[String: Any]])
        let detail: [String: Any] = try XCTUnwrap(try api("/api/runs/\(try XCTUnwrap(runs.first?["id"] as? String))") as? [String: Any])
        let choices: [String: Any] = try XCTUnwrap(detail["choices"] as? [String: Any])
        XCTAssertEqual(choices["model"] as? String, "ollama:deep")
        XCTAssertEqual(choices["effort"] as? String, "low")
    }

    // MARK: Drafts

    func testADraftComesBackAtTheNextLaunch() throws {
        launch(open: "Summarize")
        let field: XCUIElement = box
        XCTAssertTrue(field.waitForExistence(timeout: 20))
        type("a half written thought", into: field)
        sleep(2)
        app.terminate()
        launch(open: "Summarize", clearDrafts: false)
        let again: XCUIElement = box
        XCTAssertTrue(again.waitForExistence(timeout: 20))
        sleep(1)
        shot("draft-restored")
        XCTAssertEqual(again.value as? String, "a half written thought")
    }

    // MARK: A link of the Live Activity

    func testALiveActivityLinkOpensThatRunsChat() throws {
        let thread: String = try thread("Summarize")
        let runs: [[String: Any]] = try XCTUnwrap(try api("/api/runs?thread_id=\(thread)&limit=1") as? [[String: Any]])
        let id: String = try XCTUnwrap(runs.first?["id"] as? String)
        launch(extra: ["-DotOpenURL", "dotagent://run/\(id)"])
        // The chat's own box is on the screen: the link opened that chat from the list.
        XCTAssertTrue(app.descendants(matching: .any)["composer-field"].waitForExistence(timeout: 20))
        XCTAssertTrue(app.buttons["Run details"].exists)
        shot("link-opened")
    }

    // MARK: The microphone

    /// Run after `xcrun simctl privacy <device> reset all dev.dotagent.phone`: the system asks for the speech permission,
    /// the test refuses it (the left button of the alert, in any language), and the press ends in a line that says why
    /// and offers Settings, never a button that does nothing.
    func testTheMicrophoneWithNoPermissionIsALineWithTheWayToSettings() throws {
        launch(open: "Summarize")
        let mic: XCUIElement = app.buttons["mic"]
        XCTAssertTrue(mic.waitForExistence(timeout: 20))
        mic.tap()
        let failure: XCUIElement = app.descendants(matching: .any)["dictation-failure"]
        // The system's own prompt: refused with its left button (the refusal, in any language).
        let springboard: XCUIApplication = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for _ in 0..<4 where !failure.exists {
            let alert: XCUIElement = springboard.alerts.firstMatch
            if alert.waitForExistence(timeout: 6) {
                let buttons: [XCUIElement] = alert.buttons.allElementsBoundByIndex.sorted { $0.frame.minX < $1.frame.minX }
                buttons.first?.tap()
            }
            _ = failure.waitForExistence(timeout: 3)
        }
        XCTAssertTrue(failure.exists, "a refused start is a line over the box")
        XCTAssertTrue(app.buttons["Settings"].exists, "a refused permission offers the app's page in Settings")
        shot("mic-denied")
    }

    /// Run after `xcrun simctl privacy <device> reset all dev.dotagent.phone`, alone: the test allows both prompts (the right
    /// button of each). On a simulator, which has no microphone and no on-device recognizer, the press then ends in a line;
    /// on a device it listens. Either way the button is never silent.
    func testTheMicrophoneWithPermissionListensOrSaysWhyNot() throws {
        launch(open: "Summarize")
        let mic: XCUIElement = app.buttons["mic"]
        XCTAssertTrue(mic.waitForExistence(timeout: 20))
        mic.tap()
        let failure: XCUIElement = app.descendants(matching: .any)["dictation-failure"]
        let listening: XCUIElement = app.staticTexts["Listening… tap the microphone to stop"]
        let springboard: XCUIApplication = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let deadline: Date = Date().addingTimeInterval(40)
        while Date() < deadline, !failure.exists, !listening.exists {
            let alert: XCUIElement = springboard.alerts.firstMatch
            if alert.waitForExistence(timeout: 3) {
                let buttons: [XCUIElement] = alert.buttons.allElementsBoundByIndex.sorted { $0.frame.minX < $1.frame.minX }
                buttons.last?.tap()
            }
            usleep(500_000)
        }
        shot("mic-granted")
        XCTAssertTrue(failure.exists || listening.exists, "it listens or it says why not")
        try? Data((failure.exists ? "failure" : "listening").utf8).write(to: URL(fileURLWithPath: "/tmp/dot-ui-shots/mic-granted.txt"))
    }
}
