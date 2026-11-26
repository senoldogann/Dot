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

    /// Item 1904: a page's loading state stands centred both ways in the visible area, between the navigation bar and
    /// the bottom's safe area, within the design bar's 1 pt.
    func testALoadingPageIsCentredInTheVisibleArea() {
        let app: XCUIApplication = launch("settings-loading")
        let words: XCUIElement = app.staticTexts["Reading your Mac…"]
        XCTAssertTrue(words.waitForExistence(timeout: 10))
        let spinner: XCUIElement = app.activityIndicators.firstMatch
        XCTAssertTrue(spinner.exists)
        let bar: XCUIElement = app.navigationBars["Usage"]
        XCTAssertTrue(bar.waitForExistence(timeout: 5))
        let window: CGRect = app.windows.firstMatch.frame
        // The visible area ends at the tab bar where one shows, else at the bottom's safe area (no home button).
        let tabs: XCUIElement = app.tabBars.firstMatch
        let bottom: CGFloat = tabs.exists && tabs.frame.minY > bar.frame.maxY ? tabs.frame.minY : window.maxY - 34
        // What is drawn: the spinner over its line.
        let frame: CGRect = spinner.frame.union(words.frame)
        let measured: String = "drawn \(frame) (spinner \(spinner.frame), words \(words.frame)), bar \(bar.frame), bottom \(bottom), window \(window)"
        add(XCTAttachment(string: measured))
        XCTAssertEqual(frame.midX, window.midX, accuracy: 1, measured)
        XCTAssertEqual(frame.midY, (bar.frame.maxY + bottom) / 2, accuracy: 1, measured)
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "loading-centred"
        shot.lifetime = .keepAlways
        add(shot)
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
