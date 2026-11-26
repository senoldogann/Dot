import XCTest

/// The bots of the home against a throwaway daemon (PLAN §27, items 1881-1885; `scripts/e2e_bots.sh`): a bot made from
/// the home greets in its chat, its header's menu opens its profile, and the avatar button opens the one settings sheet. The
/// script checks on the daemon's side that the agent was made and its first meeting ran. Skipped unless the script
/// names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`). With `DOTNEXT_SHOTS` it keeps a shot of each screen.
@MainActor
final class BotsLiveUITests: XCTestCase {
    private func launch() throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_bots.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "home", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        return app
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// The settings button is the owner's avatar icon in a glass button like the "+" at the other end: as large as the
    /// "+" button (the system's glass, 44 pt across on the screen; the accessibility frame of both is 36 pt), on the same
    /// line, with its symbol centred in it within 0.5 pt (the shot's pixels, measured by hand: glass 44 x 44 pt, offset 0).
    func testTheSettingsButtonIsAnAvatarIconCentredLikeThePlus() throws {
        let app: XCUIApplication = try launch()
        XCTAssertTrue(app.buttons["home.bot.dot"].waitForExistence(timeout: 60), "Dot, featured on the home")
        let settings: XCUIElement = app.buttons["home.settings"]
        let plus: XCUIElement = app.buttons["home.newBot"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10) && plus.waitForExistence(timeout: 10))
        XCTAssertEqual(settings.label, "Settings", "the avatar button keeps its accessibility label")
        XCTAssertEqual(settings.frame.width, plus.frame.width, accuracy: 0.5, "as wide as the + button")
        XCTAssertEqual(settings.frame.height, plus.frame.height, accuracy: 0.5, "as tall as the + button")
        XCTAssertEqual(settings.frame.midY, plus.frame.midY, accuracy: 0.5, "on the + button's line")
        let symbol: XCUIElement = settings.images.firstMatch
        if symbol.exists {
            XCTAssertEqual(symbol.frame.midX, settings.frame.midX, accuracy: 0.5, "the symbol is centred across")
            XCTAssertEqual(symbol.frame.midY, settings.frame.midY, accuracy: 0.5, "the symbol is centred down")
            print("AVATAR button \(settings.frame) symbol \(symbol.frame) plus \(plus.frame)")
        } else {
            print("AVATAR button \(settings.frame) (no symbol element) plus \(plus.frame)")
        }
        shot(app, "home-avatar")
        app.buttons["home.settings"].tap()
        XCTAssertTrue(app.buttons["settings.close"].waitForExistence(timeout: 30), "the avatar button opens the settings sheet")
    }

    func testABotMadeFromTheHomeGreetsAndItsMenuProfileAndTheSettingsSheetOpen() throws {
        let app: XCUIApplication = try launch()
        XCTAssertTrue(app.buttons["home.bot.dot"].waitForExistence(timeout: 20), "Dot, featured on the home")
        XCTAssertFalse(app.tabBars.firstMatch.exists, "no tab bar")
        shot(app, "home")

        app.buttons["home.newBot"].tap()
        let name: XCUIElement = app.textFields["newBot.name"]
        XCTAssertTrue(name.waitForExistence(timeout: 10))
        name.typeText("Scout")
        let does: XCUIElement = app.textFields["newBot.does"].exists ? app.textFields["newBot.does"] : app.textViews["newBot.does"]
        does.tap()
        does.typeText("Watch the market and tell me each morning")
        shot(app, "new-bot")
        app.buttons["newBot.create"].tap()

        // Its chat opens under its header: the pill with its name, and it greets first.
        let pill: XCUIElement = app.buttons["Scout"]
        XCTAssertTrue(pill.waitForExistence(timeout: 30), "the bot's pill in its chat header")
        // The composer is a text field or a text view by its size; either counts.
        let field: XCUIElement = app.descendants(matching: .any)["composer.field"]
        XCTAssertTrue(field.waitForExistence(timeout: 60), "the composer of the bot's chat")
        sleep(3)
        shot(app, "bot-chat")

        app.buttons["More"].tap()
        XCTAssertTrue(app.buttons["Find in chat"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Share as template…"].exists)
        shot(app, "bot-menu")
        app.buttons["View profile"].tap()
        XCTAssertTrue(app.staticTexts["Routines"].waitForExistence(timeout: 10), "the bot's profile")
        XCTAssertTrue(app.staticTexts["Watch the market and tell me each morning"].exists, "its role")
        shot(app, "profile")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(pill.waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // The home lists the bot; the avatar button opens the settings sheet.
        let scout: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@ AND label BEGINSWITH %@", "home.bot.", "Scout")).firstMatch
        XCTAssertTrue(scout.waitForExistence(timeout: 10), "the new bot on the home")
        shot(app, "home-with-bot")
        app.buttons["home.settings"].tap()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Permissions")).firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["More settings are on your Mac."].exists || app.scrollViews.staticTexts["More settings are on your Mac."].exists)
        shot(app, "settings-sheet")
        app.buttons["settings.close"].tap()
        XCTAssertTrue(app.buttons["home.bot.dot"].waitForExistence(timeout: 10))
    }
}
