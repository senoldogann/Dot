import XCTest

/// A walk of the app against a real daemon over the tailnet (the owner allowed it, 2026-11-25): every native Settings
/// page read (nothing of the owner's settings is changed), and one short "[test]" chat on the daemon's
/// default model, which the calling script deletes afterwards. Skipped unless the script names the daemon
/// (`DOTNEXT_REAL_HOST`, `DOTNEXT_REAL_KEY`).
@MainActor
final class RealDaemonWalkUITests: XCTestCase {
    private func launch(scene: String) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_REAL_HOST"], let key = environment["DOTNEXT_REAL_KEY"] else {
            throw XCTSkip("run by the real-daemon walk script")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", scene, "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        return app
    }

    private func open(_ app: XCUIApplication, _ row: String) {
        let button: XCUIElement = app.scrollViews.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", row, row + ",")).firstMatch
        for _ in 0..<6 where !(button.exists && button.isHittable) { app.swipeUp() }
        XCTAssertTrue(button.waitForExistence(timeout: 10), "row \(row)")
        button.tap()
    }

    func testEverySettingsPageReadsTheRealMac() throws {
        let app: XCUIApplication = try launch(scene: "settings")
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 20), "the settings sheet")
        open(app, "Dot's computer")
        XCTAssertTrue(app.staticTexts["Your Mac proved it holds this phone's key."].waitForExistence(timeout: 30))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        // Each row of the settings sheet (PLAN §27) and the title of the page it opens.
        let pages: [(String, String)] = [
            ("Notifications", "Notifications"), ("General", "General"), ("Appearance", "Appearance"), ("Chat", "Chat"), ("Models", "Models"),
            ("Usage limits and caps", "Usage limits and caps"), ("Scheduled tasks", "Scheduled tasks"), ("Permissions", "Rules"), ("Memory", "Memory"),
            ("Health and version", "Health and version"), ("Usage", "Usage"),
        ]
        for (row, page) in pages {
            open(app, row)
            XCTAssertTrue(app.navigationBars[page].waitForExistence(timeout: 10), page)
            let loading: XCUIElement = app.staticTexts["Reading your Mac…"]
            let deadline: Date = Date().addingTimeInterval(20)
            while loading.exists && Date() < deadline { usleep(300_000) }
            XCTAssertFalse(loading.exists, "\(page) still reading")
            XCTAssertFalse(app.staticTexts["Your Mac could not be reached."].exists, "\(page) could not reach the Mac")
            XCTAssertFalse(app.staticTexts["Your Mac refused this phone's key. Pair again."].exists, "\(page): key refused")
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "real-\(page)"
            shot.lifetime = .keepAlways
            add(shot)
            app.navigationBars.buttons.element(boundBy: 0).tap()
        }
    }

    func testAShortTestChatIsAnswered() throws {
        let app: XCUIApplication = try launch(scene: "home")
        let dot: XCUIElement = app.buttons["home.bot.dot"]
        XCTAssertTrue(dot.waitForExistence(timeout: 20))
        dot.tap()
        let more: XCUIElement = app.buttons["More"]
        XCTAssertTrue(more.waitForExistence(timeout: 20))
        more.tap()
        app.buttons["New chat"].tap()
        let field: XCUIElement = app.textFields["composer.field"].exists ? app.textFields["composer.field"] : app.textViews["composer.field"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        field.typeText("[test] Reply with the single word: ready")
        app.buttons["composer.send"].tap()
        // Dot's chat opens the new chat at once and it turns into the real chat in place (item 1849).
        let answer: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] %@ AND NOT (label CONTAINS %@)", "ready", "[test]")).firstMatch
        XCTAssertTrue(answer.waitForExistence(timeout: 180), "Dot's answer")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "real-chat"
        shot.lifetime = .keepAlways
        add(shot)
    }
}
