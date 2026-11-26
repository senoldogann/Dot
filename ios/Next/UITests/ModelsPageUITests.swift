import XCTest

/// Settings › Models with every model setting of the Mac's page (item 1907), on the debug build's Settings fixture.
final class ModelsPageUITests: XCTestCase {
    private func launch() -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "settings-models", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        return app
    }

    private func row(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", title)).firstMatch
    }

    func testEveryRoleOfTheMacsPageIsHereAndOneIsPickedOnTheOneCatalog() {
        let app: XCUIApplication = launch()
        XCTAssertTrue(row(app, "Main model").waitForExistence(timeout: 15))
        for title in ["Fast model", "Vision model", "Review model", "Security research model"] {
            XCTAssertTrue(row(app, title).exists, title)
        }
        XCTAssertTrue(app.staticTexts["Default level"].exists, "the default level of reasoning, read only")
        for title in ["Own work model", "Proposer", "Composer", "Experience writer", "Self-repair model", "Code review model"] {
            let element: XCUIElement = row(app, title)
            for _ in 0..<6 where !element.isHittable { app.swipeUp() }
            XCTAssertTrue(element.exists, title)
        }

        // The vision model is picked on the model catalog, as the chat's settings pick theirs.
        let vision: XCUIElement = row(app, "Vision model")
        for _ in 0..<6 where !vision.isHittable { app.swipeDown() }
        vision.tap()
        XCTAssertTrue(app.navigationBars["Vision model"].waitForExistence(timeout: 5))
        let search: XCUIElement = app.textFields["modelCatalog.search"]
        XCTAssertTrue(search.exists, "the one catalog's search")
        XCTAssertFalse(
            app.buttons.matching(NSPredicate(format: "identifier == 'modelCatalog.model' AND label BEGINSWITH %@", "Opus 5.5")).firstMatch.exists,
            "the Claude plan cannot read pictures for another model")
        search.tap()
        search.typeText("DeepSeek")
        let deepseek: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier == 'modelCatalog.model' AND label BEGINSWITH %@", "DeepSeek")).firstMatch
        XCTAssertTrue(deepseek.waitForExistence(timeout: 3))
        deepseek.tap()
        app.buttons["Save"].tap()
        XCTAssertFalse(app.navigationBars["Vision model"].waitForExistence(timeout: 3), "saved, the sheet closes")
    }

    func testARoleLeftToAnotherModelOffersThatChoice() {
        let app: XCUIApplication = launch()
        let proposer: XCUIElement = row(app, "Proposer")
        XCTAssertTrue(row(app, "Main model").waitForExistence(timeout: 15))
        for _ in 0..<6 where !proposer.isHittable { app.swipeUp() }
        XCTAssertTrue(proposer.label.contains("Review model"), "a role played on the review model says so: \(proposer.label)")
        proposer.tap()
        let none: XCUIElement = app.buttons["modelPicker.none"]
        XCTAssertTrue(none.waitForExistence(timeout: 5))
        XCTAssertTrue(none.isSelected, "the review model is the choice that stands")
        app.buttons["Cancel"].tap()
    }
}
