import XCTest

/// The chat list, a new chat's settings and voice typing against a throwaway daemon (`scripts/e2e_chats.sh`, PLAN §23
/// and §24): the owner's taps on the real engine and a phone's key; the script reads on the daemon's side what each one
/// did (the run's model, level and approvals, the pins, the deleted chats). Skipped unless the script names the daemon.
final class ChatListLiveUITests: XCTestCase {
    private func launch() throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_chats.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "chats", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-voice.transcribeOn", "mac",
        ]
        app.launch()
        return app
    }

    private func any(_ app: XCUIApplication, _ identifier: String) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(identifier: identifier)
    }

    private func row(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "identifier == 'chats.row' AND label BEGINSWITH %@", title)).firstMatch
    }

    /// A new chat with a model, level and approval mode picked before its first message: the run starts on them (the
    /// script reads the run record). The model is the one the script names (`DOTNEXT_E2E_MODEL`, a real model's label).
    func testANewChatRunsOnTheSettingsPickedBeforeItsFirstMessage() throws {
        let app: XCUIApplication = try launch()
        let label: String = ProcessInfo.processInfo.environment["DOTNEXT_E2E_MODEL"] ?? ""
        XCTAssertTrue(app.buttons["chats.new"].waitForExistence(timeout: 20))
        app.buttons["chats.new"].tap()
        let chip: XCUIElement = app.buttons["newChat.model"]
        XCTAssertTrue(chip.waitForExistence(timeout: 10))
        chip.tap()
        XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 10))
        // Found by the search, as the owner with hundreds of models would: its provider's group may be folded.
        let search: XCUIElement = app.textFields["modelCatalog.search"]
        XCTAssertTrue(search.waitForExistence(timeout: 15))
        search.tap()
        search.typeText(label)
        let model: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier == 'modelCatalog.model' AND label BEGINSWITH %@", label)).firstMatch
        XCTAssertTrue(model.waitForExistence(timeout: 15), "the model \(label) is offered")
        model.tap()
        app.buttons["Clear the search"].tap()
        if app.keyboards.count > 0 { app.swipeDown() }
        // The level, one tap on the chosen model's segments (the sheet's Options stand above the providers).
        let low: XCUIElement = app.buttons["Low"]
        for _ in 0..<6 where !low.isHittable { app.swipeDown() }
        XCTAssertTrue(low.exists, "the picked model offers its levels")
        low.tap()
        let approvals: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Approvals")).firstMatch
        for _ in 0..<6 where !approvals.isHittable { app.swipeDown() }
        approvals.tap()
        any(app, "chatSettings.approval.yolo").firstMatch.tap()
        app.navigationBars["Approvals"].buttons.element(boundBy: 0).tap()
        app.buttons["chatSettings.done"].tap()
        XCTAssertTrue(chip.label.contains(label), "the bar wears the picked model: \(chip.label)")

        let field: XCUIElement = any(app, "composer.field").firstMatch
        field.tap()
        field.typeText("[test] Reply with the single word pong.")
        app.buttons["composer.send"].tap()
        XCTAssertTrue(row(app, "[test] Reply").waitForExistence(timeout: 30), "the chat is in the list")
    }

    /// Select mode: two chats pinned at once go to Pinned on top; then all of them deleted after one confirmation.
    func testSeveralChatsArePinnedAndDeletedTogether() throws {
        let app: XCUIApplication = try launch()
        XCTAssertTrue(row(app, "[test] list one").waitForExistence(timeout: 20))
        app.buttons["chats.select"].tap()
        row(app, "[test] list one").tap()
        row(app, "[test] list two").tap()
        XCTAssertEqual(app.staticTexts["chats.pickedCount"].label, "2 selected")
        app.buttons["chats.pinPicked"].tap()
        XCTAssertTrue(app.staticTexts["Pinned"].waitForExistence(timeout: 15), "the pinned chats have their section")

        app.buttons["chats.select"].tap()
        app.buttons["chats.selectAll"].tap()
        app.buttons["chats.deletePicked"].tap()
        let confirm: XCUIElement = app.buttons["chats.confirmDelete"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: row(app, "[test] list one"))
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 20), .completed, "the chats left the list")
    }

    /// The microphone turns the composer into the recorder (✕, waveform, clock, ✓) on the Mac's dictation; ✕ brings
    /// the field back with nothing written.
    func testTheMicrophoneTurnsTheComposerIntoARecorder() throws {
        let app: XCUIApplication = try launch()
        XCTAssertTrue(app.buttons["chats.new"].waitForExistence(timeout: 20))
        app.buttons["chats.new"].tap()
        let mic: XCUIElement = app.buttons["composer.mic"]
        XCTAssertTrue(mic.waitForExistence(timeout: 10))
        mic.tap()
        // The system asks for the microphone once, unless the script granted it ahead.
        let springboard: XCUIApplication = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let allow: XCUIElement = springboard.buttons["Allow"]
        if allow.waitForExistence(timeout: 3) { allow.tap() }
        let clock: XCUIElement = any(app, "recorder.clock").firstMatch
        XCTAssertTrue(clock.waitForExistence(timeout: 10), "the recorder took the composer's place")
        XCTAssertTrue(app.buttons["recorder.confirm"].exists)
        let first: String = clock.label
        let moved: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label != %@", first), object: clock)
        XCTAssertEqual(XCTWaiter.wait(for: [moved], timeout: 3), .completed, "the clock runs")
        app.buttons["recorder.cancel"].tap()
        XCTAssertTrue(any(app, "composer.field").firstMatch.waitForExistence(timeout: 5), "the field is back")
        XCTAssertFalse(clock.exists)
    }
}
