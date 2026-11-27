import XCTest

/// The bots of the home against a throwaway daemon (PLAN §27, items 1881-1885; `scripts/e2e_bots.sh`): a bot made from
/// the home greets in its chat, its header's menu opens its profile, and the avatar button opens the one settings sheet. The
/// script checks on the daemon's side that the agent was made and its first meeting ran. Skipped unless the script
/// names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`). With `DOTNEXT_SHOTS` it keeps a shot of each screen.
@MainActor
final class BotsLiveUITests: XCTestCase {
    private func launch() throws -> XCUIApplication {
        try launch(extra: ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"])
    }

    private func launch(extra: [String]) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_bots.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "home", "-DotNextHost", host, "-DotNextKey", key] + extra
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

        // Swipe to quote (item 1972): the message pulled to the right becomes a chip above the text; sent, the message
        // carries it as a "> quote" and the chat shows it as one. The swipe starts away from the screen's edge.
        let chatField: XCUIElement = app.descendants(matching: .any)["composer.field"]
        XCTAssertTrue(chatField.waitForExistence(timeout: 30), "the chat itself, with its composer")
        let said: XCUIElement = app.scrollViews.staticTexts["You: Hello from a new chat"]
        XCTAssertTrue(said.waitForExistence(timeout: 10))
        let origin: XCUICoordinate = app.coordinate(withNormalizedOffset: .zero)
        let from: CGVector = CGVector(dx: said.frame.midX - 40, dy: said.frame.midY)
        origin.withOffset(from).press(forDuration: 0.1, thenDragTo: origin.withOffset(CGVector(dx: from.dx + 160, dy: from.dy)))
        let chip: XCUIElement = app.descendants(matching: .any)["composer.quote"]
        XCTAssertTrue(chip.waitForExistence(timeout: 10), "the swipe put the message in the composer as a quote")
        XCTAssertTrue(chip.label.contains("Hello from a new chat"), chip.label)
        XCTAssertGreaterThanOrEqual(app.buttons["composer.quote.remove"].frame.height, 44)
        shot(app, "bot-quote")
        chatField.tap()
        chatField.typeText("Say it again")
        app.buttons["composer.send"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["message.quote"].waitForExistence(timeout: 20), "the sent message shows its quote")
        XCTAssertFalse(chip.exists, "the chip went with the message")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(pill.waitForExistence(timeout: 10), "back to the bot's earlier chat")

        app.buttons["More"].tap()
        XCTAssertTrue(app.buttons["Find in chat"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Share as template…"].exists)
        shot(app, "bot-menu")
        app.buttons["View profile"].tap()
        XCTAssertTrue(app.buttons["profile.routines"].waitForExistence(timeout: 10), "the bot's profile")
        XCTAssertTrue(app.staticTexts["Watch the market and tell me each morning"].exists, "its role")
        shot(app, "profile")

        // The profile as Grok's (item 1963): Library and Computer, Configure the bot with Routines "N on", a Notifications
        // switch, and Edit under the avatar and the name.
        let library: XCUIElement = app.buttons["profile.library"]
        let computer: XCUIElement = app.buttons["profile.computer"]
        let edit: XCUIElement = app.buttons["profile.edit"]
        XCTAssertTrue(library.exists && computer.exists && edit.exists)
        XCTAssertTrue(computer.label.contains("Idle") || computer.label.contains("Working"), "the bot's computer says what it does: \(computer.label)")
        // The count comes with the schedules' read, a moment after the page.
        let routinesOn: XCTNSPredicateExpectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label CONTAINS %@", "0 on"), object: app.buttons["profile.routines"])
        XCTAssertEqual(XCTWaiter.wait(for: [routinesOn], timeout: 10), .completed, app.buttons["profile.routines"].label)
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

    // MARK: A bot's work (items 87, 907, 908 on the phone; the daemon seeds Scribe with `--seed-work`)

    /// Opens Scribe's profile from the home: its chat, then the pill in the header.
    private func openScribeProfile(_ app: XCUIApplication) {
        let scribe: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@ AND label BEGINSWITH %@", "home.bot.", "Scribe")).firstMatch
        XCTAssertTrue(scribe.waitForExistence(timeout: 60), "Scribe, the seeded bot, on the home")
        scribe.tap()
        let pill: XCUIElement = app.buttons["botChat.pill"]
        XCTAssertTrue(pill.waitForExistence(timeout: 30), "Scribe's chat header")
        pill.tap()
        XCTAssertTrue(app.buttons["profile.workOrders"].waitForExistence(timeout: 20), "the profile's Work group")
    }

    private func waitForLabel(_ element: XCUIElement, containing text: String, timeout: TimeInterval) {
        let expectation: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", text), object: element)
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: timeout), .completed, "\(element.identifier): \(element.label)")
    }

    /// Rows of one kind sit at the same left edge and width, and none is under the 44 pt hit floor or off the screen.
    private func assertAligned(_ rows: [XCUIElement], in app: XCUIApplication, _ name: String) {
        let window: CGRect = app.windows.firstMatch.frame
        guard let first = rows.first else { return XCTFail("\(name): no rows") }
        for row in rows {
            XCTAssertEqual(row.frame.minX, first.frame.minX, accuracy: 1, "\(name): one left edge")
            XCTAssertEqual(row.frame.width, first.frame.width, accuracy: 1, "\(name): one width")
            XCTAssertGreaterThanOrEqual(row.frame.height, 44, "\(name): a hit target")
            XCTAssertLessThanOrEqual(row.frame.maxX, window.maxX, "\(name): inside the screen")
        }
        XCTAssertEqual(first.frame.minX + first.frame.width / 2, window.midX, accuracy: 1, "\(name): equal insets left and right")
    }

    func testABotsWorkIsReadInItsProfileAndTheMenuPausesAndResumesIt() throws {
        let app: XCUIApplication = try launch()
        openScribeProfile(app)
        let orders: XCUIElement = app.buttons["profile.workOrders"]
        let objectives: XCUIElement = app.buttons["profile.objectives"]
        let handoffs: XCUIElement = app.buttons["profile.handoffs"]
        waitForLabel(orders, containing: "1 open", timeout: 15)
        waitForLabel(objectives, containing: "1 active", timeout: 15)
        waitForLabel(handoffs, containing: "2 today", timeout: 15)
        assertAligned([orders, objectives, handoffs], in: app, "the Work rows")
        XCTAssertLessThanOrEqual(abs(orders.frame.height - objectives.frame.height), 1, "rows of one kind are identical")
        XCTAssertLessThanOrEqual(abs(objectives.frame.height - handoffs.frame.height), 1, "rows of one kind are identical")
        shot(app, "work-profile")

        // Work orders: the open one first, then the accepted one; a row opens to the brief and what done means.
        orders.tap()
        let rows: XCUIElementQuery = app.buttons.matching(identifier: "work.order")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 20))
        XCTAssertEqual(rows.count, 2)
        XCTAssertTrue(rows.element(boundBy: 0).label.contains("Draft the weekly note") && rows.element(boundBy: 0).label.contains("Handed out"), rows.element(boundBy: 0).label)
        XCTAssertTrue(rows.element(boundBy: 0).label.contains("handed out"), "when it was handed out, as the web's row says: \(rows.element(boundBy: 0).label)")
        XCTAssertTrue(rows.element(boundBy: 1).label.contains("Accepted"), rows.element(boundBy: 1).label)
        XCTAssertTrue(app.staticTexts["Open"].exists && app.staticTexts["Finished"].exists, "the two sections")
        assertAligned([rows.element(boundBy: 0), rows.element(boundBy: 1)], in: app, "work orders")
        shot(app, "work-orders")
        rows.element(boundBy: 0).tap()
        XCTAssertTrue(app.staticTexts["Collect the week's highlights and write the note."].waitForExistence(timeout: 10), "the brief")
        XCTAssertTrue(app.staticTexts["A note under 200 words, ready to send."].exists, "what done means")
        shot(app, "work-order")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Objectives.
        XCTAssertTrue(objectives.waitForExistence(timeout: 10))
        objectives.tap()
        let objectiveRows: XCUIElementQuery = app.buttons.matching(identifier: "work.objective")
        XCTAssertTrue(objectiveRows.firstMatch.waitForExistence(timeout: 20))
        XCTAssertEqual(objectiveRows.count, 2, "the active objective and the finished one the web hides")
        let objective: XCUIElement = objectiveRows.element(boundBy: 0)
        XCTAssertTrue(objective.label.contains("Keep the weekly note short") && objective.label.contains("Active"), objective.label)
        XCTAssertTrue(objective.label.contains("Judged by: Under 200 words"), "how it is judged, as the web's row says: \(objective.label)")
        XCTAssertTrue(objective.label.contains("Next: Draft this Friday's note"), "the next step of one with no progress yet: \(objective.label)")
        XCTAssertTrue(objectiveRows.element(boundBy: 1).label.contains("Done"), objectiveRows.element(boundBy: 1).label)
        assertAligned([objective, objectiveRows.element(boundBy: 1)], in: app, "objectives")
        shot(app, "work-objectives")
        objective.tap()
        XCTAssertTrue(app.staticTexts["Under 200 words, every Friday."].waitForExistence(timeout: 10), "how progress is judged")
        XCTAssertTrue(app.staticTexts["Draft this Friday's note"].exists, "the next step")
        shot(app, "work-objective")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Hand-offs: what Dot asked of Scribe (received, answered) and what Scribe asked of Dot (sent, waiting).
        XCTAssertTrue(handoffs.waitForExistence(timeout: 10))
        handoffs.tap()
        let hands: XCUIElementQuery = app.buttons.matching(identifier: "work.handoff")
        XCTAssertTrue(hands.firstMatch.waitForExistence(timeout: 20))
        XCTAssertEqual(hands.count, 2)
        XCTAssertTrue(hands.element(boundBy: 0).label.contains("Dot → Scribe") && hands.element(boundBy: 0).label.contains("Which topics fit this week?") && hands.element(boundBy: 0).label.contains("Reply"), hands.element(boundBy: 0).label)
        XCTAssertTrue(hands.element(boundBy: 1).label.contains("Is the offsite date final?") && hands.element(boundBy: 1).label.contains("Dot is working on it"), hands.element(boundBy: 1).label)
        assertAligned([hands.element(boundBy: 0), hands.element(boundBy: 1)], in: app, "hand-offs")
        shot(app, "work-handoffs")
        hands.element(boundBy: 0).tap()
        XCTAssertTrue(app.staticTexts["Launch, the offsite and the new price list."].waitForExistence(timeout: 10), "the reply")
        shot(app, "work-handoff")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Pause and Resume in the profile's ⋯ menu; it ends paused, which the script reads on the daemon's side.
        let menu: XCUIElement = app.buttons["profile.menu"]
        XCTAssertTrue(menu.waitForExistence(timeout: 10), "the ⋯ menu of an agent's profile")
        // A toolbar button's accessibility frame is 36 pt across (the glass around it is 44).
        XCTAssertGreaterThanOrEqual(menu.frame.height, 36)
        XCTAssertFalse(app.descendants(matching: .any)["profile.paused"].exists, "running")
        menu.tap()
        let pause: XCUIElement = app.buttons["profile.pause"]
        XCTAssertTrue(pause.waitForExistence(timeout: 10), "the menu opens with Pause bot")
        pause.tap()
        XCTAssertTrue(app.descendants(matching: .any)["profile.paused"].waitForExistence(timeout: 20), "the paused notice")
        shot(app, "work-paused")
        menu.tap()
        let resume: XCUIElement = app.buttons["profile.resume"]
        XCTAssertTrue(resume.waitForExistence(timeout: 10), "the menu opens with Resume bot")
        XCTAssertFalse(app.buttons["profile.pause"].exists, "Pause becomes Resume")
        resume.tap()
        let gone: NSPredicate = NSPredicate(format: "exists == false")
        XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: gone, object: app.descendants(matching: .any)["profile.paused"])], timeout: 20), .completed, "resumed")
        menu.tap()
        XCTAssertTrue(pause.waitForExistence(timeout: 10), "the menu opens with Pause bot again")
        pause.tap()
        XCTAssertTrue(app.descendants(matching: .any)["profile.paused"].waitForExistence(timeout: 20), "paused again: the script reads it on the daemon")
    }

    /// Dark with Turkish words, and the largest accessibility size: the three pages keep every row inside the screen and
    /// a hit target; the shots go to the result bundle for the report.
    func testTheWorkPagesHoldInDarkTurkishAndAtTheLargestAccessibilitySize() throws {
        let variants: [(String, [String])] = [
            ("dark-tr", ["-colorScheme", "dark", "-AppleLanguages", "(tr)", "-AppleLocale", "tr_TR"]),
            ("ax3", ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXL", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]),
        ]
        for (name, extra) in variants {
            let app: XCUIApplication = try launch(extra: extra)
            openScribeProfile(app)
            let window: CGRect = app.windows.firstMatch.frame
            for id in ["profile.workOrders", "profile.objectives", "profile.handoffs"] {
                let row: XCUIElement = app.buttons[id]
                for _ in 0..<6 where !row.isHittable { app.swipeUp() }
                XCTAssertGreaterThanOrEqual(row.frame.height, 44, "\(name) \(id)")
                XCTAssertLessThanOrEqual(row.frame.maxX, window.maxX, "\(name) \(id): inside the screen")
            }
            shot(app, "work-profile-\(name)")
            for (id, rowId) in [("profile.workOrders", "work.order"), ("profile.objectives", "work.objective"), ("profile.handoffs", "work.handoff")] {
                let entry: XCUIElement = app.buttons[id]
                for _ in 0..<6 where !entry.isHittable { app.swipeUp() }
                entry.tap()
                let rows: XCUIElementQuery = app.buttons.matching(identifier: rowId)
                XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 20), "\(name) \(rowId)")
                for index in 0..<rows.count {
                    XCTAssertGreaterThanOrEqual(rows.element(boundBy: index).frame.height, 44, "\(name) \(rowId)")
                    XCTAssertLessThanOrEqual(rows.element(boundBy: index).frame.maxX, window.maxX, "\(name) \(rowId): inside the screen")
                }
                shot(app, "\(rowId)-\(name)")
                app.navigationBars.buttons.element(boundBy: 0).tap()
                XCTAssertTrue(app.buttons[id].waitForExistence(timeout: 10))
            }
            app.terminate()
        }
    }
}
