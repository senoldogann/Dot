import XCTest

/// The Activity tab against a throwaway daemon (item 1826, `scripts/e2e_activity.sh`, seeded by `e2e_daemon.py
/// --seed-activity`): every part read from the daemon and every control that writes tapped as the owner would, on the
/// real engine and a phone's key; the script checks on the daemon's side what each tap wrote. A draft is never sent
/// here: a send hands it to the Mac's own Mail or Messages. Each look is kept as a screenshot (light and dark), which
/// the script exports. Skipped unless the script names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class ActivityLiveUITests: XCTestCase {
    private func launch(look: String) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_activity.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "activity", "-DotNextHost", host, "-DotNextKey", key, "-colorScheme", look, "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
        ]
        app.launch()
        return app
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func reveal(_ app: XCUIApplication, _ element: XCUIElement) {
        for _ in 0..<8 where !element.isHittable { app.swipeUp() }
    }

    /// A row by the start of its title (its label is "title, line").
    private func row(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", title)).firstMatch
    }

    /// The looks, without a write: the home at its top and its foot, a draft, a proposal, the two pages, in `look`.
    private func shoot(look: String) throws {
        let app: XCUIApplication = try launch(look: look)
        XCTAssertTrue(app.staticTexts["While you were away"].waitForExistence(timeout: 20))
        shot(app, "\(look)-home")
        row(app, "Re: The March invoice").tap()
        XCTAssertTrue(app.buttons["Send with Mail"].waitForExistence(timeout: 10))
        shot(app, "\(look)-draft")
        app.buttons["Close"].tap()
        row(app, "Read the README of a repository").tap()
        XCTAssertTrue(app.buttons["Set aside"].waitForExistence(timeout: 10))
        shot(app, "\(look)-proposal")
        app.buttons["Close"].tap()
        let own: XCUIElement = row(app, "On its own")
        reveal(app, own)
        shot(app, "\(look)-home-foot")
        own.tap()
        XCTAssertTrue(app.staticTexts["Tidy the Downloads folder"].waitForExistence(timeout: 10))
        shot(app, "\(look)-initiative")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let watchers: XCUIElement = row(app, "Watchers")
        reveal(app, watchers)
        watchers.tap()
        XCTAssertTrue(app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Landlord")).firstMatch.waitForExistence(timeout: 10))
        shot(app, "\(look)-watchers")
        app.terminate()
    }

    func testAShotOfEachLook() throws {
        try shoot(look: "light")
        try shoot(look: "dark")
    }

    func testEveryControlThatWritesWritesToTheMac() throws {
        let app: XCUIApplication = try launch(look: "light")
        XCTAssertTrue(app.staticTexts["While you were away"].waitForExistence(timeout: 20))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "2 tasks: 1 done, 1 failed")).firstMatch.exists)

        // A run of the list opens its chat.
        let backup: XCUIElement = row(app, "Back up the projects folder.")
        reveal(app, backup)
        backup.tap()
        XCTAssertTrue(app.navigationBars.buttons.element(boundBy: 0).waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts["While you were away"].isHittable, "the chat is pushed over the tab")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Ready: the mail draft is discarded (never sent: Send would hand it to the Mac's own Mail).
        app.swipeDown()
        row(app, "Re: The March invoice").tap()
        XCTAssertTrue(app.buttons["Discard"].waitForExistence(timeout: 10))
        app.buttons["Discard"].tap()
        app.buttons.matching(NSPredicate(format: "label == %@", "Discard")).element(boundBy: 1).tap()
        XCTAssertTrue(row(app, "Running ten minutes late").waitForExistence(timeout: 10))
        XCTAssertFalse(row(app, "Re: The March invoice").exists)

        // Dot's proposals: one put in use, the one from outside content set aside.
        row(app, "Run the tests of what changed").tap()
        XCTAssertTrue(app.buttons["Use"].waitForExistence(timeout: 10))
        app.buttons["Use"].tap()
        XCTAssertTrue(row(app, "Read the README of a repository").waitForExistence(timeout: 10))
        row(app, "Read the README of a repository").tap()
        XCTAssertTrue(app.buttons["Set aside"].waitForExistence(timeout: 10))
        app.buttons["Set aside"].tap()
        app.buttons.matching(NSPredicate(format: "label == %@", "Set aside")).element(boundBy: 1).tap()
        sleep(2)

        // Dot comes to you: the brief off, the check-in on.
        let brief: XCUIElement = app.switches["Morning brief"]
        reveal(app, brief)
        brief.tap()
        sleep(2)
        let checkin: XCUIElement = app.switches["Evening check-in"]
        reveal(app, checkin)
        checkin.tap()
        sleep(2)

        // On its own: switched on, a thing removed, one added.
        let own: XCUIElement = row(app, "On its own")
        reveal(app, own)
        own.tap()
        let initiative: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Work on its own")).firstMatch
        XCTAssertTrue(initiative.waitForExistence(timeout: 10))
        initiative.tap()
        sleep(2)
        app.buttons["Take Tidy the Downloads folder off the list"].tap()
        sleep(2)
        let field: XCUIElement = app.textFields["Something Dot can do for you on its own"]
        reveal(app, field)
        field.tap()
        field.typeText("Renew the passport")
        app.buttons["Add"].tap()
        XCTAssertTrue(app.staticTexts["Renew the passport"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Watchers: Landlord off.
        let watchers: XCUIElement = row(app, "Watchers")
        reveal(app, watchers)
        watchers.tap()
        let landlord: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Landlord")).firstMatch
        XCTAssertTrue(landlord.waitForExistence(timeout: 10))
        landlord.tap()
        sleep(2)
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // While you were away: marked read for every device.
        app.swipeDown()
        app.swipeDown()
        let read: XCUIElement = app.buttons["Mark as read"]
        XCTAssertTrue(read.waitForExistence(timeout: 10))
        read.tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: app.staticTexts["While you were away"])
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 10), .completed)
    }
}
