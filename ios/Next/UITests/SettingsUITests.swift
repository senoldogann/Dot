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

    // MARK: Campaigns (item 1895)

    func testACampaignIsStoppedAfterItAsksAndThenResumed() {
        let app: XCUIApplication = launch("settings-campaigns")
        let row: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Keep the test suite green")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        XCTAssertTrue(row.label.contains("Active"), "the state in the row: \(row.label)")
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Ship the new landing page")).firstMatch.label.contains("Achieved"))
        row.tap()
        let stop: XCUIElement = app.buttons["Stop"]
        XCTAssertTrue(stop.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["May I skip the live mail test?"].exists, "the question that waits")
        for _ in 0..<6 where !stop.isHittable { app.swipeUp() }
        stop.tap()
        XCTAssertTrue(app.staticTexts["Stop this campaign?"].waitForExistence(timeout: 5), "Stop asks first")
        // The dialog's own Stop: the page's row lies under the dialog and cannot be tapped.
        let confirm: XCUIElement? = app.buttons.matching(identifier: "Stop").allElementsBoundByIndex.first { $0.isHittable }
        XCTAssertNotNil(confirm, "the dialog's Stop")
        confirm?.tap()
        XCTAssertTrue(app.buttons["Resume"].waitForExistence(timeout: 10), "stopped: Resume is offered")
        XCTAssertFalse(app.buttons["Stop"].exists)
    }

    func testACampaignsPageKeepsItsActionsReachableAtAX3() {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "settings-campaign", "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXLarge",
        ]
        app.launch()
        let achieved: XCUIElement = app.buttons["Mark achieved"]
        XCTAssertTrue(achieved.waitForExistence(timeout: 10))
        for _ in 0..<20 where !achieved.isHittable { app.swipeUp() }
        XCTAssertTrue(achieved.isHittable)
        XCTAssertGreaterThanOrEqual(achieved.frame.height, 44, "a tap target of 44 pt at least")
        XCTAssertLessThanOrEqual(achieved.frame.maxX, app.windows.firstMatch.frame.maxX, "nothing runs off the screen")
    }
}
