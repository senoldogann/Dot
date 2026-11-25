import XCTest

/// Settings on the fixture daemon (wave N3): the search with synonyms, the hop diagnosis, a write the Mac refuses to a
/// phone, a schedule paused with its switch.
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
        XCTAssertTrue(app.staticTexts["Tasks start in"].waitForExistence(timeout: 5), "the approval mode of the fixture daemon")
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

    func testAScheduleIsPausedWithItsSwitch() {
        let app: XCUIApplication = launch("settings-schedules")
        XCTAssertTrue(app.staticTexts["Morning brief"].waitForExistence(timeout: 10))
        let toggle: XCUIElement = app.switches["Morning brief on"]
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        // XCUITest reads a toggle's value as "1"/"0" whatever its `accessibilityValue` says (measured on iOS 26.5: a
        // check against "On" failed with `Optional("1")`, this one passes; the ink switch keeps the switch trait).
        XCTAssertEqual(toggle.value as? String, "1", "switched on")
        app.buttons.matching(identifier: "Actions").firstMatch.tap()
        XCTAssertTrue(app.buttons["Run now"].waitForExistence(timeout: 3), "the row's menu runs it now")
    }
}
