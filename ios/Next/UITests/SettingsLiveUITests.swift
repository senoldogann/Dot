import XCTest

/// Settings against a throwaway daemon (wave N3, `scripts/e2e_settings.sh`): every page that writes, tapped as the owner
/// would, on the real engine and a phone's key. The script checks on the daemon's side what each tap wrote. Skipped
/// unless the script names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class SettingsLiveUITests: XCTestCase {
    private func launch() throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_settings.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "settings", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        return app
    }

    private func open(_ app: XCUIApplication, _ row: String) {
        // The row's own label ("Chat", or "This Mac, Connected"), never a tab of the same start ("Chats").
        let button: XCUIElement = app.scrollViews.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", row, row + ",")).firstMatch
        for _ in 0..<6 where !button.isHittable { app.swipeUp() }
        XCTAssertTrue(button.waitForExistence(timeout: 10), "row \(row)")
        button.tap()
    }

    private func back(_ app: XCUIApplication) {
        app.navigationBars.buttons.element(boundBy: 0).tap()
    }

    func testEveryPageThatWritesWritesToTheMac() throws {
        let app: XCUIApplication = try launch()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 15))

        // Dot's computer (the This Mac page): every hop passes and the daemon proved it holds the key.
        open(app, "Dot's computer")
        XCTAssertTrue(app.staticTexts["Your Mac proved it holds this phone's key."].waitForExistence(timeout: 15))
        back(app)

        // Notifications: Done or failed off.
        open(app, "Notifications")
        let done: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Done or failed")).firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 10))
        done.tap()
        sleep(2)
        back(app)

        // General: Dot's name.
        open(app, "General")
        let name: XCUIElement = app.textFields["Dot's name"]
        XCTAssertTrue(name.waitForExistence(timeout: 10))
        name.tap()
        name.press(forDuration: 1.2)
        if app.menuItems["Select All"].waitForExistence(timeout: 2) { app.menuItems["Select All"].tap() }
        name.typeText(XCUIKeyboardKey.delete.rawValue)
        name.typeText("Nokta\n")
        sleep(2)
        back(app)

        // Chat: reactions start no follow-up.
        open(app, "Chat")
        let follow: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Reactions start a follow-up")).firstMatch
        XCTAssertTrue(follow.waitForExistence(timeout: 10))
        follow.tap()
        sleep(2)
        back(app)

        // Scheduled tasks: pause the seeded one with its switch, then rename it.
        open(app, "Scheduled tasks")
        XCTAssertTrue(app.staticTexts["E2E brief"].waitForExistence(timeout: 10))
        app.switches["E2E brief on"].tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Paused")).firstMatch.waitForExistence(timeout: 10))
        app.staticTexts["E2E brief"].tap()
        let title: XCUIElement = app.textFields["Name"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.tap()
        title.typeText(" edited")
        app.buttons["Save"].tap()
        XCTAssertTrue(app.staticTexts["E2E brief edited"].waitForExistence(timeout: 10))
        back(app)

        // Models: the code review's reasoning, from the phone (item 1770).
        open(app, "Models")
        let review: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Code review model")).firstMatch
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        review.tap()
        // The level is a segment of the model catalog's picker (one picker for the app, item 1801).
        let high: XCUIElement = app.buttons["High"]
        for _ in 0..<6 where !high.isHittable { app.swipeUp() }
        high.tap()
        app.buttons["Save"].tap()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "High")).firstMatch.waitForExistence(timeout: 10))
        back(app)

        // Permissions (the approval mode and the rules, items 1881-1885): free mode, switched on on the Mac, switched off
        // from the phone (a phone may).
        open(app, "Permissions")
        let free: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Free mode")).firstMatch
        XCTAssertTrue(free.waitForExistence(timeout: 10))
        free.tap()
        sleep(2)
        back(app)

        // Memory: use the run's proposed words, then edit the note.
        open(app, "Memory")
        XCTAssertTrue(app.staticTexts["From outside content"].waitForExistence(timeout: 10))
        app.buttons["Use"].tap()
        XCTAssertTrue(app.staticTexts["None waits. When a task wants to change a note you wrote, it waits here."].waitForExistence(timeout: 10))
        let note: XCUIElement = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "commands written out")).firstMatch
        XCTAssertTrue(note.waitForExistence(timeout: 10))
        note.tap()
        app.buttons.matching(NSPredicate(format: "label == %@", "Edit")).firstMatch.tap()
        // The note's field holds its words (its placeholder is not its identifier).
        let words: XCUIElement = app.textFields.matching(NSPredicate(format: "value BEGINSWITH %@", "The owner prefers")).firstMatch
        XCTAssertTrue(words.waitForExistence(timeout: 5), "the note's field")
        words.tap()
        words.typeText(" Edited.")
        app.buttons["Save"].tap()
        sleep(2)
        back(app)

        // Agents: a chat with the seeded agent, which greets the owner in its first one.
        open(app, "Agents")
        let scout: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "E2E Scout")).firstMatch
        XCTAssertTrue(scout.waitForExistence(timeout: 10))
        scout.tap()
        let chat: XCUIElement = app.buttons["Chat with E2E Scout"]
        XCTAssertTrue(chat.waitForExistence(timeout: 10))
        chat.tap()
        sleep(3)
        back(app)
        back(app)
        back(app)

        // Campaigns: the seeded campaign is stopped (it asks first), then resumed (item 1895).
        open(app, "Campaigns")
        let campaign: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "E2E campaign")).firstMatch
        XCTAssertTrue(campaign.waitForExistence(timeout: 10))
        campaign.tap()
        let stop: XCUIElement = app.buttons["Stop"]
        XCTAssertTrue(stop.waitForExistence(timeout: 10))
        for _ in 0..<6 where !stop.isHittable { app.swipeUp() }
        stop.tap()
        XCTAssertTrue(app.staticTexts["Stop this campaign?"].waitForExistence(timeout: 5), "Stop asks first")
        // The dialog's own Stop: the page's row lies under the dialog and cannot be tapped.
        let confirm: XCUIElement? = app.buttons.matching(identifier: "Stop").allElementsBoundByIndex.first { $0.isHittable }
        XCTAssertNotNil(confirm, "the dialog's Stop")
        confirm?.tap()
        let resume: XCUIElement = app.buttons["Resume"]
        XCTAssertTrue(resume.waitForExistence(timeout: 10), "stopped")
        XCTAssertTrue(app.staticTexts["Stopped"].exists)
        resume.tap()
        XCTAssertTrue(app.buttons["Stop"].waitForExistence(timeout: 10), "resumed")
        back(app)
        back(app)

        // Usage limits and caps: lower the cap of one task (a daemon with item 1731), or say Dot is too old.
        open(app, "Usage limits and caps")
        let oneTask: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "One task")).firstMatch
        let tooOld: XCUIElement = app.staticTexts["Update Dot on your Mac: this page needs a newer Dot."]
        XCTAssertTrue(oneTask.waitForExistence(timeout: 10) || tooOld.exists)
        if oneTask.exists {
            oneTask.tap()
            let field: XCUIElement = app.textFields["Millions of tokens"]
            XCTAssertTrue(field.waitForExistence(timeout: 5))
            field.tap()
            field.press(forDuration: 1.2)
            if app.menuItems["Select All"].waitForExistence(timeout: 2) { app.menuItems["Select All"].tap() }
            field.typeText(XCUIKeyboardKey.delete.rawValue + "300")
            app.buttons["Save"].tap()
            XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "300M")).firstMatch.waitForExistence(timeout: 10), "the lowered cap")
            // A raise is refused to a phone with the Mac's place.
            app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "One task")).firstMatch.tap()
            XCTAssertTrue(field.waitForExistence(timeout: 5))
            field.tap()
            field.press(forDuration: 1.2)
            if app.menuItems["Select All"].waitForExistence(timeout: 2) { app.menuItems["Select All"].tap() }
            field.typeText(XCUIKeyboardKey.delete.rawValue + "900")
            app.buttons["Save"].tap()
            XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Open on the Mac: Settings › Models › Caps")).firstMatch.waitForExistence(timeout: 10))
            app.buttons["Cancel"].tap()
        }
        back(app)
    }
}
