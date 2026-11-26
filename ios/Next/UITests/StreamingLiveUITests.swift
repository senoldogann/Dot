import XCTest

/// A chat's streaming against a throwaway daemon (`scripts/e2e_streaming.sh`): a new chat stays open when its first
/// message is sent and turns into the real chat, and a run that thinks, reads and answers for about half a minute is
/// streamed while the app's frame meter counts the frames (the script reads its record). Skipped unless the script
/// names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class StreamingLiveUITests: XCTestCase {
    private func launch(scene: String) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_streaming.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", scene, "-DotNextHost", host, "-DotNextKey", key, "-DotNextFrameMeter", "1", "-AppleLanguages", "(en)", "-AppleLocale",
            "en_US",
        ]
        app.launch()
        return app
    }

    private func type(_ app: XCUIApplication, _ words: String) {
        let field: XCUIElement = app.textFields["composer.field"].exists ? app.textFields["composer.field"] : app.textViews["composer.field"]
        XCTAssertTrue(field.waitForExistence(timeout: 15))
        field.tap()
        field.typeText(words)
        app.buttons["composer.send"].tap()
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testANewChatStaysOpenAndBecomesTheRealChat() throws {
        let app: XCUIApplication = try launch(scene: "chats")
        let pencil: XCUIElement = app.buttons["New chat"]
        XCTAssertTrue(pencil.waitForExistence(timeout: 20))
        pencil.tap()
        type(app, "Say hello for the new chat")
        // By its start: on a loaded Mac the simulator's keyboard can drop the last letters typed.
        let mine: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "You: Say hello")).firstMatch
        XCTAssertTrue(mine.waitForExistence(timeout: 10), "the new chat opens with the owner's message")
        let starting: CGRect = mine.frame
        // The daemon's answer comes in the same screen: the chat became the real one and was never left.
        let answer: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Echo: Say hello")).firstMatch
        // The owner's words stay on screen through the swap: never a "Syncing…" blank in between.
        let deadline: Date = Date().addingTimeInterval(30)
        while !answer.exists, Date() < deadline {
            XCTAssertTrue(mine.exists, "the owner's message left the screen before the answer came")
            usleep(200_000)
        }
        XCTAssertTrue(answer.exists, "the run's answer in the opened chat")
        XCTAssertTrue(mine.exists, "still the same chat")
        // The design bar: the bubble stands where the real chat draws it, no jump at the swap (over 1 pt is a bug).
        let real: CGRect = mine.frame
        let measured: String = "starting \(starting), real \(real)"
        add(XCTAttachment(string: measured))
        XCTAssertEqual(starting.minX, real.minX, accuracy: 1, measured)
        XCTAssertEqual(starting.minY, real.minY, accuracy: 1, measured)
        XCTAssertEqual(starting.width, real.width, accuracy: 1, measured)
        XCTAssertEqual(starting.height, real.height, accuracy: 1, measured)
        XCTAssertFalse(app.navigationBars["Chats"].exists, "never thrown back to the chat list")
        shot(app, "new-chat-open")
        // Back leads to the list, where the chat has its one row.
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.navigationBars["Chats"].waitForExistence(timeout: 5))
    }

    /// Item 1919: the moment a message is sent, the stream moves (the starting line, then the run's own line), within
    /// half a second, though the scripted model answers only after 8 s.
    func testASentMessageMovesTheStreamAtOnce() throws {
        let app: XCUIApplication = try launch(scene: "live-chat")
        let field: XCUIElement = app.textFields["composer.field"].exists ? app.textFields["composer.field"] : app.textViews["composer.field"]
        XCTAssertTrue(field.waitForExistence(timeout: 15))
        field.tap()
        field.typeText("slow: take your time")
        let moving: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "identifier IN %@", ["work.starting", "work.line"])).firstMatch
        app.buttons["composer.send"].tap()
        let sent: Date = Date()
        XCTAssertTrue(moving.waitForExistence(timeout: 0.5), "motion within 0.5 s of the send")
        add(XCTAttachment(string: "seen after \(Date().timeIntervalSince(sent)) s"))
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "starting-line"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testAStreamingRunIsMeasured() throws {
        let app: XCUIApplication = try launch(scene: "live-chat")
        type(app, "stream: where do we stand")
        let end: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "the queue is empty")).firstMatch
        XCTAssertTrue(end.waitForExistence(timeout: 120), "the long answer streamed to its end")
        let done: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "placeholderValue == %@ OR value == %@", "Message Dot", "Message Dot")).firstMatch
        _ = done.waitForExistence(timeout: 30)
        sleep(2)
        shot(app, "streamed")
    }
}
