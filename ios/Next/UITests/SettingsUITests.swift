import XCTest

/// Settings on the fixture daemon (wave N3): the search with synonyms, the hop diagnosis, a write the Mac refuses to a
/// phone, a schedule paused from its menu.
final class SettingsUITests: XCTestCase {
    private func launch(_ scene: String) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", scene, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        return app
    }

    func testSearchingApprovalsFindsRulesAndOpensIt() {
        let app: XCUIApplication = launch("settings-search")
        let rules: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Rules")).firstMatch
        XCTAssertTrue(rules.waitForExistence(timeout: 10))
        rules.tap()
        XCTAssertTrue(app.staticTexts["Ask before sending any mail"].waitForExistence(timeout: 5), "the rules of the fixture daemon")
    }

    /// The phone does not engage the emergency stop (item 1766), but while the Mac's holds it says so and releases it.
    func testTheMacsEmergencyStopIsShownAndReleased() {
        let app: XCUIApplication = launch("settings-stopped")
        let notice: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Everything is stopped")).firstMatch
        XCTAssertTrue(notice.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["Emergency stop"].exists, "the phone has no button that engages it")
        app.buttons["Release the stop"].tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: notice)
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 5), .completed, "released, the notice goes")
    }

    func testTheDiagnosisNamesTheFailingHopWhenTheMacIsUnreachable() {
        let app: XCUIApplication = launch("settings-offline")
        XCTAssertTrue(app.staticTexts["The Mac's tailnet name does not resolve. Open Tailscale on this iPhone and connect."].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["Copy diagnostics"].isEnabled)
    }

    func testARaiseOfACapIsRefusedWithTheMacsPlace() {
        let app: XCUIApplication = launch("settings-caps-refused")
        let refusal: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Open on the Mac: Settings › Models › Caps")).firstMatch
        XCTAssertTrue(refusal.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["Copy instructions"].exists)
    }

    func testAScheduleIsPausedFromItsMenu() {
        let app: XCUIApplication = launch("settings-schedules")
        XCTAssertTrue(app.staticTexts["Morning brief"].waitForExistence(timeout: 10))
        app.buttons.matching(identifier: "Actions").firstMatch.tap()
        XCTAssertTrue(app.buttons["Pause"].waitForExistence(timeout: 3))
    }
}
