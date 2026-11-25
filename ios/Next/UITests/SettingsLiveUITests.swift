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

        // This Mac: every hop passes and the daemon proved it holds the key.
        open(app, "This Mac")
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

        // Scheduled tasks: pause the seeded one, then rename it.
        open(app, "Scheduled tasks")
        XCTAssertTrue(app.staticTexts["E2E brief"].waitForExistence(timeout: 10))
        app.buttons.matching(identifier: "Actions").firstMatch.tap()
        app.buttons["Pause"].tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Paused")).firstMatch.waitForExistence(timeout: 10))
        app.staticTexts["E2E brief"].tap()
        let title: XCUIElement = app.textFields["Name"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.tap()
        title.typeText(" edited")
        app.buttons["Save"].tap()
        XCTAssertTrue(app.staticTexts["E2E brief edited"].waitForExistence(timeout: 10))
        back(app)

        // Memory: use the run's proposed words.
        open(app, "Memory")
        XCTAssertTrue(app.staticTexts["From outside content"].waitForExistence(timeout: 10))
        app.buttons["Use"].tap()
        XCTAssertTrue(app.staticTexts["None waits. When a task wants to change a note you wrote, it waits here."].waitForExistence(timeout: 10))
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

        // A web row opens the Mac's own page in the app.
        open(app, "Vault")
        XCTAssertTrue(app.webViews.firstMatch.waitForExistence(timeout: 15))
        // Signed in with the phone's key: the page of the vault, never the web's sign-in screen.
        XCTAssertTrue(app.webViews.staticTexts["Vault"].waitForExistence(timeout: 20) || app.webViews.staticTexts["Kasa"].exists, "the web page is signed in")
        XCTAssertFalse(app.webViews.buttons["Sign in"].exists, "the web's sign-in screen")
        let web: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        web.name = "e2e-web-vault"
        web.lifetime = .keepAlways
        add(web)
        back(app)
    }
}
