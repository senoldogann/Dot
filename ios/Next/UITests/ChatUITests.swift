import XCTest

/// The ask dock and the queue's edit on the real chat screen, drawn from the debug build's fixtures (wave N2).
final class ChatUITests: XCTestCase {
    private func launch(_ scene: String) -> XCUIApplication {
        launch(scene, defaults: [])
    }

    /// `defaults` are `UserDefaults` given as arguments (`-key value`), over what an earlier test left on the simulator.
    private func launch(_ scene: String, defaults: [String]) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", scene, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"] + defaults
        app.launch()
        return app
    }

    /// The owner's report of 2026-11-25: the capsule's clock froze between events. The fixture's run gets no event at
    /// all, yet the clock goes on every second; and the capsule says what the run does (here: writing its words).
    func testTheCapsuleClockTicksWithNoEventsAndSaysWhatTheRunDoes() {
        let app: XCUIApplication = launch("n2-chat")
        let status: XCUIElement = app.descendants(matching: .any).matching(identifier: "capsule.status").firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 10))
        let first: String = status.label
        // Item 1769: the capsule says Working and its clock; what the run does is the live line's.
        XCTAssertTrue(first.hasPrefix("Working, "), "the capsule says Working: \(first)")
        let second: String = nextLabel(of: status, after: first, within: 2.5)
        XCTAssertNotEqual(second, first, "the clock moved on its own: \(first) -> \(second)")
        let third: String = nextLabel(of: status, after: second, within: 2.5)
        XCTAssertNotEqual(third, second, "and again a second later: \(second) -> \(third)")
    }

    /// The element's label once it differs from `label`, read every 100 ms (an `XCTNSPredicateExpectation` reads it about
    /// once a second, so a tick could be seen a second late, past the wait on a slow runner); the last label read when
    /// nothing changed within `seconds`.
    private func nextLabel(of element: XCUIElement, after label: String, within seconds: TimeInterval) -> String {
        let deadline: Date = Date().addingTimeInterval(seconds)
        var now: String = element.label
        while now == label, Date() < deadline {
            usleep(100_000)
            now = element.label
        }
        return now
    }

    /// Item 1769: with the steps hidden (Hide all, the default), one live line under the last message says what the run
    /// does; a tap shows the steps and a second folds them back.
    func testTheLiveLineSaysTheWorkAndATapShowsTheSteps() {
        let app: XCUIApplication = launch("n2-asks", defaults: ["-toolCallMode", "hideAll"])
        let line: XCUIElement = app.buttons["work.line"]
        XCTAssertTrue(line.waitForExistence(timeout: 10))
        XCTAssertEqual(line.label, "Waiting for you")
        let step: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Browsed")).firstMatch
        XCTAssertFalse(step.exists, "the steps are hidden")
        line.tap()
        XCTAssertTrue(step.waitForExistence(timeout: 3), "a tap shows the steps")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "work-line-steps"
        shot.lifetime = .keepAlways
        add(shot)
        line.tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: step)
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 3), .completed, "a second tap folds them back")
    }

    func testTheDockShowsEachAskInTurnAndFoldsBackToTheComposer() {
        let app: XCUIApplication = launch("n2-asks")
        XCTAssertTrue(app.staticTexts["Your answer is needed"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["1 of 2"].exists, "a count for several asks")
        XCTAssertTrue(app.buttons["Allow once"].exists)
        XCTAssertTrue(app.buttons["Always allow in this chat"].exists)
        XCTAssertFalse(app.textFields["composer.field"].exists, "the dock stands in the composer's place")
        app.buttons["Next ask"].tap()
        XCTAssertTrue(app.staticTexts["2 of 2"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Which day suits you for the dentist?"].exists)
        XCTAssertTrue(app.buttons["Stop"].exists, "Stop stays reachable")
        app.buttons["Fold what Dot asked"].tap()
        XCTAssertTrue(app.textFields["composer.field"].waitForExistence(timeout: 3), "folded, the composer is back")
    }

    func testQuestionsArePickedAndSentWithOneSubmit() {
        let app: XCUIApplication = launch("n2-question")
        XCTAssertTrue(app.staticTexts["Fill in the pending answers"].waitForExistence(timeout: 10))
        let submit: XCUIElement = app.buttons["ask.submit"]
        XCTAssertFalse(submit.isEnabled, "nothing picked yet")
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Close both (Recommended)")).firstMatch.tap()
        XCTAssertTrue(submit.isEnabled)
        XCTAssertEqual(submit.label, "Submit answer")
        app.buttons["Next ask"].tap()
        let tuesday: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Tuesday afternoon (Recommended)")).firstMatch
        XCTAssertTrue(tuesday.waitForExistence(timeout: 3))
        tuesday.tap()
        XCTAssertEqual(submit.label, "Submit 2 answers", "the pick of the first question is kept while paging")
    }

    func testSlashOpensTheCommandsPanelFilteredAsTheOwnerTypes() {
        let app: XCUIApplication = launch("n2-chat")
        // The field is a text field at rest and a text view once it grows: found by its identifier, whatever its kind.
        let field: XCUIElement = app.descendants(matching: .any).matching(identifier: "composer.field").firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        field.typeText("/")
        XCTAssertTrue(app.staticTexts["COMMANDS"].waitForExistence(timeout: 3) || app.staticTexts["Commands"].exists)
        XCTAssertEqual(app.buttons.matching(identifier: "picker.row").count, 6)
        // A row puts its token in the text (tapped on the bare "/": XCUITest's typed letters stay as the keyboard's
        // candidate, which a tap does not replace; a hand-typed word is replaced, seen on the simulator).
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "/loop")).firstMatch.tap()
        let completed: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value BEGINSWITH %@", "/loop"), object: field)
        XCTAssertEqual(XCTWaiter.wait(for: [completed], timeout: 3), .completed, "the row puts its token in the text: \(String(describing: field.value))")
        let cleared: XCUIElement = field
        cleared.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 3))
        XCTAssertEqual(app.buttons.matching(identifier: "picker.row").count, 1, "\"/lo\" leaves only /loop")
    }

    func testEditingAQueuedMessageKeepsTheLiveDraftForCancel() {
        let app: XCUIApplication = launch("n2-editing")
        XCTAssertTrue(app.staticTexts["Editing queued message"].waitForExistence(timeout: 10))
        let field: XCUIElement = app.textFields["composer.field"]
        XCTAssertEqual(field.value as? String, "Connect everything to real data, as a Grok-like assistant would, and keep the tool calls visible")
        XCTAssertEqual(app.buttons["composer.send"].label, "Save the queued message", "a ✓ in place of Send")
        app.buttons["composer.cancelEdit"].tap()
        XCTAssertFalse(app.staticTexts["Editing queued message"].waitForExistence(timeout: 2))
        XCTAssertEqual(field.value as? String, "And book the car service too", "Cancel puts the live draft back")
    }

    func testTheQueueSheetOpensAQueuedMessageInTheComposer() {
        let app: XCUIApplication = launch("n2-queue")
        XCTAssertTrue(app.navigationBars["Queued"].waitForExistence(timeout: 10))
        let rows: XCUIElementQuery = app.buttons.matching(identifier: "queue.row")
        XCTAssertEqual(rows.count, 2)
        rows.element(boundBy: 1).tap()
        XCTAssertTrue(app.staticTexts["Editing queued message"].waitForExistence(timeout: 3))
        XCTAssertEqual(app.textFields["composer.field"].value as? String, "Then send me the screenshots of the new designs")
    }
}
