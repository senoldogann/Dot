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
        // Found by its kind (a button): a search of the whole tree is slow to read on a loaded runner.
        let status: XCUIElement = app.buttons["capsule.status"]
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
        var now: String = element.label
        _ = holds(within: seconds) {
            now = element.label
            return now != label
        }
        return now
    }

    /// The owner's order of 2026-11-25: a long press on the composer's button while a run works opens Steer now and
    /// Queue, the follow-up setting's way (Queue) checked; each pick sends the draft. No Stop in the chat's header.
    func testALongPressOnSendOffersSteerNowAndQueue() {
        let app: XCUIApplication = launch("n2-chat", defaults: ["-followUp", "queue"])
        let field: XCUIElement = app.descendants(matching: .any).matching(identifier: "composer.field").firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        XCTAssertFalse(app.navigationBars.buttons["Stop"].exists, "no Stop in the chat's header")
        for (way, words) in [("Steer now", "Look at the logs first"), ("Queue", "Then book the car service")] {
            field.tap()
            field.typeText(words)
            let send: XCUIElement = app.buttons["composer.send"]
            XCTAssertEqual(send.label, "Queue: starts after this run", "a tap queues, as the setting says")
            send.press(forDuration: 1.2)
            // The menu's subtitles are not in the tree XCUITest reads; the shots show them.
            let steer: XCUIElement = app.buttons["composer.followUp.steer"]
            let queue: XCUIElement = app.buttons["composer.followUp.queue"]
            XCTAssertTrue(steer.waitForExistence(timeout: 3), "the menu opened")
            XCTAssertEqual(steer.label, "Steer now")
            XCTAssertEqual(queue.label, "Queue")
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "composer-follow-up-menu"
            shot.lifetime = .keepAlways
            add(shot)
            XCTAssertTrue(queue.isSelected, "the setting's way is checked")
            XCTAssertFalse(steer.isSelected)
            (way == "Steer now" ? steer : queue).tap()
            let sent: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "NOT (value CONTAINS %@)", words), object: field)
            XCTAssertEqual(XCTWaiter.wait(for: [sent], timeout: 3), .completed, "\(way) sent the draft: \(String(describing: field.value))")
        }
    }

    /// Item 1769: with the steps hidden (Hide all, the default), one live line under the last message says what the run
    /// does; a tap shows the steps and a second folds them back.
    func testTheLiveLineSaysTheWorkAndATapShowsTheSteps() {
        let app: XCUIApplication = launch("n2-asks", defaults: ["-toolCallMode", "hideAll"])
        let line: XCUIElement = app.buttons["work.line"]
        XCTAssertTrue(line.waitForExistence(timeout: 10))
        XCTAssertEqual(line.label, "Waiting for you")
        XCTAssertGreaterThanOrEqual(line.frame.height, 44, "the line is a button: a 44 pt tap target (the design bar)")
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
        let atRest: XCUIElement = app.textFields["composer.field"]
        XCTAssertTrue(atRest.waitForExistence(timeout: 10))
        atRest.tap()
        // Focused, the composer grows and its field may become a text view: found by its kind, never by a search of the
        // whole tree (`descendants(matching: .any)`), whose every read took seconds on a loaded CI runner (#1105).
        composerField(app).typeText("/")
        XCTAssertTrue(app.staticTexts["COMMANDS"].waitForExistence(timeout: 3) || app.staticTexts["Commands"].exists)
        let rows: XCUIElementQuery = app.buttons.matching(identifier: "picker.row")
        XCTAssertTrue(holds(within: 5) { rows.count == 6 }, "six commands: \(rows.count)")
        // A row puts its token in the text (tapped on the bare "/": XCUITest's typed letters stay as the keyboard's
        // candidate, which a tap does not replace; a hand-typed word is replaced, seen on the simulator).
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "/loop")).firstMatch.tap()
        // Read every 100 ms: the predicate expectation read the value about once a second, and on the runner of #1105
        // the token was in the text ("/loop ") when the 3 s wait had already given up.
        XCTAssertTrue(holds(within: 5) { (composerField(app).value as? String)?.hasPrefix("/loop") == true }, "the row puts its token in the text: \(String(describing: composerField(app).value))")
        composerField(app).typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 3))
        // The panel filters once the deletions are in the text: waited for, not read at once.
        XCTAssertTrue(holds(within: 5) { rows.count == 1 }, "\"/lo\" leaves only /loop: \(rows.count)")
    }

    /// The composer's field, a text view while the composer is grown and a text field at rest.
    private func composerField(_ app: XCUIApplication) -> XCUIElement {
        let grown: XCUIElement = app.textViews["composer.field"]
        return grown.exists ? grown : app.textFields["composer.field"]
    }

    /// Whether `condition` holds within `seconds`, read every 100 ms: as soon as it holds, not on a fixed pause.
    private func holds(within seconds: TimeInterval, _ condition: () -> Bool) -> Bool {
        let deadline: Date = Date().addingTimeInterval(seconds)
        while !condition() {
            if Date() >= deadline { return false }
            usleep(100_000)
        }
        return true
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
