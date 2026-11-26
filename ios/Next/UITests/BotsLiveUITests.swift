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

    /// Starts a chat with Dot on the throwaway daemon, as the Mac or a code review would (`POST /api/runs`).
    private func startDotChat(_ goal: String) throws {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        let host: String = try XCTUnwrap(environment["DOTNEXT_E2E_HOST"])
        let key: String = try XCTUnwrap(environment["DOTNEXT_E2E_KEY"])
        var request: URLRequest = URLRequest(url: try XCTUnwrap(URL(string: host + "/api/runs")))
        request.httpMethod = "POST"
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "goal": goal, "key": "k-\(UUID().uuidString)", "attachments": [String](), "mode": "task", "standing": false, "speed": "standard",
            "plan_first": false,
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let done: XCTestExpectation = expectation(description: "the daemon took the chat")
        nonisolated(unsafe) var status: Int = 0
        URLSession.shared.dataTask(with: request) { _, response, _ in
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 15)
        XCTAssertTrue((200..<300).contains(status), "POST /api/runs: \(status)")
    }

    /// The owner's build 6 (item 1923): while they read one of Dot's chats, a code review started a new chat under Dot
    /// and the open screen jumped to it. A newer chat of the bot only shows in the list; the open one stays.
    func testANewChatOfDotNeverReplacesTheOneOpen() throws {
        try startDotChat("hello: the chat the owner reads")
        let app: XCUIApplication = try launch()
        let dot: XCUIElement = app.buttons["home.bot.dot"]
        XCTAssertTrue(dot.waitForExistence(timeout: 60))
        dot.tap()
        let reading: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'the chat the owner reads'")).firstMatch
        XCTAssertTrue(reading.waitForExistence(timeout: 30), "Dot's chat opens")
        try startDotChat("hello: a newer chat that starts meanwhile")
        let newer: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'a newer chat that starts meanwhile'")).firstMatch
        XCTAssertFalse(newer.waitForExistence(timeout: 12), "the newer chat does not take the screen")
        XCTAssertTrue(reading.exists, "the chat the owner reads stays open")
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

        // New chat in the menu opens like a chat (item 1916): pushed under the same header with a back button and no ✕, the
        // bot's face and name centred above the composer; its first message goes to the bot, in the same screen.
        app.buttons["More"].tap()
        app.buttons["New chat"].tap()
        let greeting: XCUIElement = app.descendants(matching: .any)["newChat.greeting"]
        XCTAssertTrue(greeting.waitForExistence(timeout: 10), "the bot's face and name in its empty chat")
        XCTAssertFalse(app.buttons["Close"].exists, "a pushed chat, not a sheet with a ✕")
        let bar: XCUIElement = app.navigationBars.firstMatch
        XCTAssertTrue(app.buttons["Scout"].exists, "the bot's pill stays in the header")
        XCTAssertTrue(app.buttons["More"].isHittable, "and its ⋯ menu")
        let newField: XCUIElement = app.descendants(matching: .any)["composer.field"]
        XCTAssertTrue(newField.waitForExistence(timeout: 10))
        XCTAssertEqual(greeting.frame.midX, app.windows.firstMatch.frame.midX, accuracy: 1, "centred across")
        let above: CGFloat = greeting.frame.minY - bar.frame.maxY
        let below: CGFloat = newField.frame.minY - greeting.frame.maxY
        XCTAssertGreaterThan(above, 0)
        XCTAssertGreaterThan(below, 0)
        // The composer's own padding lies between the field and the space the greeting is centred in.
        XCTAssertEqual(above, below, accuracy: 60, "centred down: \(above) above, \(below) below")
        shot(app, "bot-new-chat")
        // The push may still be settling: tap once it can take the tap, again if the keyboard did not come.
        XCTAssertTrue(NSPredicate(format: "isHittable == true").evaluate(with: newField) || newField.waitForExistence(timeout: 5))
        newField.tap()
        if !app.keyboards.firstMatch.waitForExistence(timeout: 5) { newField.tap() }
        newField.typeText("Hello from a new chat")
        app.buttons["composer.send"].tap()
        XCTAssertTrue(app.staticTexts["Hello from a new chat"].waitForExistence(timeout: 20), "the first message opens the chat in place")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(pill.waitForExistence(timeout: 10), "back to the bot's earlier chat")

        app.buttons["More"].tap()
        XCTAssertTrue(app.buttons["Find in chat"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Share as template…"].exists)
        shot(app, "bot-menu")
        app.buttons["View profile"].tap()
        XCTAssertTrue(app.staticTexts["Routines"].waitForExistence(timeout: 10), "the bot's profile")
        XCTAssertTrue(app.staticTexts["Watch the market and tell me each morning"].exists, "its role")
        shot(app, "profile")

        // The profile as Grok's (item 1963): Library and Computer, Configure the bot with Routines "N on", a Notifications
        // switch, and Edit under the avatar and the name.
        let library: XCUIElement = app.buttons["profile.library"]
        let computer: XCUIElement = app.buttons["profile.computer"]
        let edit: XCUIElement = app.buttons["profile.edit"]
        XCTAssertTrue(library.exists && computer.exists && edit.exists)
        XCTAssertTrue(computer.label.contains("Idle") || computer.label.contains("Working"), "the bot's computer says what it does: \(computer.label)")
        XCTAssertTrue(app.buttons["profile.routines"].label.contains("0 on"), app.buttons["profile.routines"].label)
        XCTAssertTrue(app.descendants(matching: .any)["profile.notifications"].exists)
        XCTAssertLessThanOrEqual(abs(library.frame.height - computer.frame.height), 1, "rows of one kind are identical")
        XCTAssertLessThanOrEqual(abs(edit.frame.midX - app.windows.firstMatch.frame.midX), 1, "Edit sits under the avatar, centred")
        XCTAssertGreaterThanOrEqual(edit.frame.height, 44)

        edit.tap()
        let nameField: XCUIElement = app.textFields["botEdit.name"]
        XCTAssertTrue(nameField.waitForExistence(timeout: 10), "the edit page")
        XCTAssertEqual(nameField.value as? String, "Scout")
        XCTAssertFalse(app.buttons["botEdit.done"].isEnabled, "nothing changed yet")
        app.buttons["botEdit.avatar"].tap()
        let cloud: XCUIElement = app.buttons["avatar.shape.cloud"]
        XCTAssertTrue(cloud.waitForExistence(timeout: 10))
        XCTAssertGreaterThanOrEqual(cloud.frame.height, 44)
        cloud.tap()
        app.buttons["avatar.colour.teal"].tap()
        shot(app, "bot-avatar")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.buttons["botEdit.done"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["botEdit.done"].isEnabled, "a new look is a change")
        shot(app, "bot-edit")
        app.buttons["botEdit.done"].tap()
        XCTAssertTrue(app.buttons["profile.edit"].waitForExistence(timeout: 10), "Done keeps it and goes back to the profile")
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
