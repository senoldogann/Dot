import XCTest

/// Connect apps against a throwaway daemon (item 1862, `scripts/e2e_settings.sh`): the sheet reads the daemon's real
/// catalogue with a phone's key, a chip filters a section, the search finds a service, and a row opens its detail.
/// Nothing is connected: a step would ask macOS on the Mac that runs the test. Skipped unless the script names the
/// daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class ConnectAppsLiveUITests: XCTestCase {
    func testTheSheetFiltersSearchesAndOpensADetail() throws {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_settings.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "settings", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()

        let row: XCUIElement = app.scrollViews.buttons.matching(NSPredicate(format: "label == %@", "Connect apps")).firstMatch
        for _ in 0..<6 where !row.isHittable { app.swipeUp() }
        XCTAssertTrue(row.waitForExistence(timeout: 15))
        row.tap()

        // The catalogue is read: Featured holds the web's Popular services, and the pill counts the connected ones.
        XCTAssertTrue(app.buttons["Featured"].firstMatch.waitForExistence(timeout: 20))
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "GitHub")).firstMatch.exists)
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label ENDSWITH %@", " connected")).firstMatch.exists)

        // The Code chip lists that section alone.
        app.buttons["Code"].firstMatch.tap()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Sentry")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Slack")).firstMatch.exists)
        app.buttons["All"].firstMatch.tap()

        // The search finds a service by a word of its name.
        let search: XCUIElement = app.textFields["Search apps and skills"]
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap()
        search.typeText("notion")
        let notion: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Notion")).firstMatch
        XCTAssertTrue(notion.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "GitHub")).firstMatch.exists)

        // A row opens the detail: what it can do, and where it is finished (a connected app is added on the Mac).
        notion.tap()
        XCTAssertTrue(app.staticTexts["What it can do"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Finish on your Mac")).firstMatch.exists)
    }
}
