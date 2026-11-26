import XCTest

/// Settings › System › Maintenance against a throwaway daemon (item 1790, `scripts/e2e_settings.sh`): the provider
/// programs' versions are read, and Continue after restart is switched off; the script reads `[agent] resume` on the
/// daemon's side. Settings ends with its footnote and no row opens the web app. Skipped unless the script names the
/// daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class SettingsMaintenanceLiveUITests: XCTestCase {
    func testMaintenanceReadsTheProgramsAndSwitchesResumeOnTheMac() throws {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_settings.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "settings", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        let row: XCUIElement = app.scrollViews.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Maintenance")).firstMatch
        for _ in 0..<6 where !row.isHittable { app.swipeUp() }
        XCTAssertTrue(row.waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["More settings are on your Mac."].exists, "the footnote")
        XCTAssertFalse(app.staticTexts["Web"].exists, "no row opens the web app")
        row.tap()
        let resume: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Continue after restart")).firstMatch
        XCTAssertTrue(resume.waitForExistence(timeout: 10))
        XCTAssertEqual(resume.value as? String, "1", "the daemon's default resumes")
        resume.tap()
        sleep(2)
        app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Dot and provider versions")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Claude Code"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["OpenCode"].exists)
    }
}
