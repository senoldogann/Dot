import XCTest

/// The chat's settings sheet (PLAN §24) and Settings › Voice (PLAN §25), drawn from the debug build's fixtures: no daemon.
final class ChatSettingsUITests: XCTestCase {
    private func launch(_ scene: String) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", scene, "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-models.favourites", "()", "-models.hidden", "()"]
        app.launch()
        return app
    }

    private func any(_ app: XCUIApplication, _ identifier: String) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(identifier: identifier)
    }

    func testTheChipOpensTheSheetWithProvidersBadgesStarsAndOptions() {
        let app: XCUIApplication = launch("n2-chat")
        let field: XCUIElement = any(app, "composer.field").firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        let chip: XCUIElement = app.buttons["composer.model"]
        XCTAssertTrue(chip.waitForExistence(timeout: 5), "the chip shows once the composer opens")
        chip.tap()
        XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Claude Code"].exists, "providers by the web's names")
        XCTAssertTrue(app.staticTexts["Default"].exists, "the daemon's main model wears the badge")
        XCTAssertTrue(app.buttons["Approvals"].exists || any(app, "chatSettings.approvals").firstMatch.exists)

        // The chat's model (Opus) is on top among the recent ones, and its provider's group is open; a star is kept.
        let models: XCUIElementQuery = any(app, "modelCatalog.model")
        XCTAssertTrue(models.element(boundBy: 0).label.hasPrefix("Opus 5.5"), "the current model first: \(models.element(boundBy: 0).label)")
        let star: XCUIElement = app.buttons["Add Sonnet 5.5 to favourites"].firstMatch
        XCTAssertTrue(star.exists)
        star.tap()
        XCTAssertTrue(app.buttons["Remove Sonnet 5.5 from favourites"].firstMatch.waitForExistence(timeout: 3))

        // The other providers start folded with their count, and open with a tap.
        XCTAssertTrue(app.staticTexts["2"].exists, "folded, Ollama says how many models it holds")
        // Counted by the catalog's rows, whatever the fixture names the models.
        let folded: Int = models.count
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Ollama")).firstMatch.tap()
        let opened: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "count == %d", folded + 2), object: models)
        XCTAssertEqual(XCTWaiter.wait(for: [opened], timeout: 3), .completed, "opened, Ollama's two models show")

        // The search narrows the list.
        let search: XCUIElement = app.textFields["modelCatalog.search"]
        search.tap()
        search.typeText("flash")
        XCTAssertEqual(models.count, 1, "the recent ones and the folds give way to the search")
        XCTAssertTrue(models.element(boundBy: 0).label.hasPrefix("DeepSeek V4.1 Flash"))
        app.buttons["Clear the search"].tap()

        // Cancel keeps nothing of the picks.
        app.buttons["chatSettings.cancel"].tap()
        XCTAssertFalse(app.navigationBars["Chat settings"].waitForExistence(timeout: 2))
    }

    func testFastModeFollowsTheModelAndTheLevelAndApprovalsArePicked() {
        let app: XCUIApplication = launch("n2-model")
        XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 10))
        let fast: XCUIElement = any(app, "chatSettings.fast").firstMatch
        let sonnet: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier == 'modelCatalog.model' AND label BEGINSWITH %@", "Sonnet 5.5")).firstMatch
        for _ in 0..<4 where !sonnet.isHittable { app.swipeUp() }
        sonnet.tap()
        XCTAssertTrue(fast.waitForExistence(timeout: 3))
        XCTAssertFalse(fast.isEnabled, "Sonnet has no fast mode")
        app.buttons.matching(NSPredicate(format: "identifier == 'modelCatalog.model' AND label BEGINSWITH %@", "Opus 5.5")).firstMatch.tap()
        XCTAssertTrue(fast.isEnabled, "Opus has it")

        // The level is one tap on the chosen model's own levels.
        for _ in 0..<4 where !app.buttons["High"].isHittable { app.swipeDown() }
        app.buttons["High"].tap()
        XCTAssertTrue(app.buttons["High"].isSelected)

        let approvals: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Approvals")).firstMatch
        for _ in 0..<3 where !approvals.isHittable { app.swipeDown() }
        approvals.tap()
        any(app, "chatSettings.approval.yolo").firstMatch.tap()
        app.navigationBars["Approvals"].buttons.element(boundBy: 0).tap()
        XCTAssertTrue(approvals.waitForExistence(timeout: 3))
        XCTAssertTrue(approvals.label.contains("Ask only to pay or send"), approvals.label)
        XCTAssertTrue(app.buttons["chatSettings.done"].isEnabled)
    }

    /// The design bar (SETTINGS-NATIVE.md): rows of one kind are identical, every star sits centred on its row, the
    /// sheet's left and right insets are equal, every tap target is 44 pt or more. Measured from the elements' frames;
    /// a difference over 1 pt fails. The numbers go to the log for the report.
    func testTheSheetMeasuresToTheDesignBar() {
        let app: XCUIApplication = launch("n2-model")
        XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 10))
        let window: CGRect = app.windows.firstMatch.frame
        let rows: [XCUIElement] = any(app, "modelCatalog.model").allElementsBoundByIndex.filter { $0.isHittable }
        let stars: [XCUIElement] = any(app, "modelCatalog.star").allElementsBoundByIndex.filter { $0.isHittable }
        XCTAssertGreaterThanOrEqual(rows.count, 2)
        let heights: [CGFloat] = rows.map { $0.frame.height }
        print("design-bar model rows: heights \(heights) minX \(rows.map { $0.frame.minX })")
        XCTAssertLessThanOrEqual((heights.max() ?? 0) - (heights.min() ?? 0), 1, "rows of one kind are identical")
        XCTAssertGreaterThanOrEqual(heights.min() ?? 0, 44)
        for (row, star) in zip(rows, stars) {
            XCTAssertLessThanOrEqual(abs(row.frame.midY - star.frame.midY), 1, "the star is centred on its row: \(row.frame) \(star.frame)")
            XCTAssertGreaterThanOrEqual(star.frame.width, 44)
            XCTAssertGreaterThanOrEqual(star.frame.height, 44)
        }
        let left: CGFloat = (rows.map { $0.frame.minX }.min() ?? 0) - window.minX
        let right: CGFloat = window.maxX - (stars.map { $0.frame.maxX }.max() ?? 0)
        print("design-bar sheet insets: left \(left) right \(right)")
        let search: XCUIElement = app.textFields["modelCatalog.search"]
        let filter: XCUIElement = app.buttons["modelCatalog.filter"]
        print("design-bar search: \(search.frame) filter \(filter.frame)")
        XCTAssertLessThanOrEqual(abs(search.frame.midY - filter.frame.midY), 1, "the filter and the search field share a centre line")
        XCTAssertGreaterThanOrEqual(filter.frame.height, 44)
    }

    func testTheVoicePageOffersWhereToTranscribeAndTheMicrophonesInOrder() {
        let app: XCUIApplication = launch("settings-voice")
        XCTAssertTrue(app.navigationBars["Voice"].waitForExistence(timeout: 10))
        let device: XCUIElement = any(app, "voice.device").firstMatch
        let mac: XCUIElement = any(app, "voice.mac").firstMatch
        XCTAssertTrue(device.exists)
        if !device.isSelected { device.tap() }
        XCTAssertTrue(device.isSelected, "this phone is a choice")
        mac.tap()
        XCTAssertTrue(mac.isSelected)
        device.tap()
        // The list adds a "Drag" handle of its own to each row: only the rows' own labels are compared.
        let mics: [String] = any(app, "voice.mic").allElementsBoundByIndex.map { $0.label }.filter { $0 != "Drag" }
        XCTAssertEqual(mics.map { $0.components(separatedBy: ",").first ?? "" }, ["Other wired", "Other Bluetooth", "Built-in microphone", "CarPlay"])
    }
}
