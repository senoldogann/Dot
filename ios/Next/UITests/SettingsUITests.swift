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

    func testSearchingApprovalsFindsPermissionsAndOpensIt() {
        let app: XCUIApplication = launch("settings-search")
        let rules: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Permissions")).firstMatch
        XCTAssertTrue(rules.waitForExistence(timeout: 10))
        rules.tap()
        XCTAssertTrue(app.staticTexts["Tasks start in"].waitForExistence(timeout: 5), "the approval mode of the fixture daemon")
    }

    func testInstalledPluginRevertAsksFirst() {
        let app: XCUIApplication = launch("settings-home")
        let plugins: XCUIElement = app.buttons["Installed plugins"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 10))
        for _ in 0..<4 where !plugins.isHittable { app.scrollViews.firstMatch.swipeUp() }
        plugins.tap()
        let revert: XCUIElement = app.buttons["Revert"].firstMatch
        XCTAssertTrue(revert.waitForExistence(timeout: 5))
        revert.tap()
        let trash: XCUIElement = app.staticTexts["The updated version goes to the Trash."]
        XCTAssertTrue(trash.waitForExistence(timeout: 5))
    }

    func testDeletingALearnedSkillExplainsThatItsVersionsAreDeleted() {
        let app: XCUIApplication = launch("settings-home")
        let skills: XCUIElement = app.buttons["Skills"]
        XCTAssertTrue(skills.waitForExistence(timeout: 10))
        for _ in 0..<4 where !skills.isHittable { app.scrollViews.firstMatch.swipeUp() }
        skills.tap()
        let skill: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "weekly-review")).firstMatch
        XCTAssertTrue(skill.waitForExistence(timeout: 5))
        skill.tap()
        let delete: XCUIElement = app.buttons["Delete"].firstMatch
        XCTAssertTrue(delete.waitForExistence(timeout: 5))
        delete.tap()
        XCTAssertTrue(app.staticTexts["This permanently deletes the skill and its versions. A later task may learn it again."].waitForExistence(timeout: 5))
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
    /// G1(a): a new schedule is made from the phone (+, its name and goal, Add) and lands in the list; a schedule's menu
    /// opens its history, whose run opens its chat.
    func testAScheduleIsMadeAndItsHistoryOpensARunsChat() {
        let app: XCUIApplication = launch("settings-schedules")
        XCTAssertTrue(app.staticTexts["Morning brief"].waitForExistence(timeout: 10))
        app.buttons["schedules.new"].tap()
        let addButton: XCUIElement = app.buttons["scheduleNew.add"]
        XCTAssertTrue(addButton.waitForExistence(timeout: 5))
        XCTAssertFalse(addButton.isEnabled, "nothing to add before a name and a goal")
        let name: XCUIElement = app.textFields["scheduleNew.name"]
        name.tap()
        name.typeText("Water the plants")
        let goal: XCUIElement = app.descendants(matching: .any)["scheduleNew.goal"].firstMatch
        goal.tap()
        goal.typeText("Remind me to water the plants")
        XCTAssertTrue(app.staticTexts["Every day at 09:00, in your Mac's time zone."].exists, "the rhythm in words")
        XCTAssertTrue(addButton.isEnabled)
        addButton.tap()
        XCTAssertTrue(app.staticTexts["Water the plants"].waitForExistence(timeout: 5), "the new schedule is in the list")

        app.buttons.matching(identifier: "Actions").firstMatch.tap()
        app.buttons["History"].tap()
        let run: XCUIElement = app.buttons["scheduleHistory.run"].firstMatch
        XCTAssertTrue(run.waitForExistence(timeout: 5))
        XCTAssertTrue(run.label.contains("Done · Three mails need you."), "how it went and the first line of its answer: \(run.label)")
        XCTAssertEqual(app.buttons.matching(identifier: "scheduleHistory.run").count, 2)
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "schedule-history"
        shot.lifetime = .keepAlways
        add(shot)
        run.tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: run)
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 5), .completed, "the run's chat is pushed over the history")
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
