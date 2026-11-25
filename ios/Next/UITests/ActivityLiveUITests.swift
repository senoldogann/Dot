import XCTest

/// The Activity tab against a throwaway daemon (item 1826, `scripts/e2e_activity.sh`, seeded by `e2e_daemon.py
/// --seed-activity`): every part read from the daemon and every control that writes tapped as the owner would, on the
/// real engine and a phone's key; the script checks on the daemon's side what each tap wrote. A draft is never sent
/// here: a send hands it to the Mac's own Mail or Messages. Each look is kept as a screenshot (light and dark), which
/// the script exports. Skipped unless the script names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class ActivityLiveUITests: XCTestCase {
    /// The app's language for this run (`DOTNEXT_E2E_LANG`, `en` or `tr`): the shots and the measures run in either;
    /// the writes are checked in English.
    private var language: String { ProcessInfo.processInfo.environment["DOTNEXT_E2E_LANG"] ?? "en" }

    /// The words the tests look for, in the run's language (the catalog's Turkish).
    private func word(_ english: String) -> String {
        guard language == "tr" else { return english }
        let turkish: [String: String] = [
            "While you were away": "Sen yokken", "Send with Mail": "Mail ile gönder", "Close": "Kapat", "Set aside": "Kenara koy",
            "On its own": "Kendi başına", "Watchers": "İzleyiciler",
        ]
        return turkish[english] ?? english
    }

    /// The device and the language, which name the shots.
    private var device: String { "\(UIDevice.current.name.replacingOccurrences(of: " ", with: "-"))-\(language)" }

    private func launch(look: String) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_activity.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "activity", "-DotNextHost", host, "-DotNextKey", key, "-colorScheme", look, "-AppleLanguages", "(\(language))", "-AppleLocale",
            language == "tr" ? "tr_TR" : "en_US",
        ]
        app.launch()
        return app
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "\(device)-\(name)"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// Closes the sheet on screen, and waits until it is gone (a tap while it still settles is lost).
    private func close(_ app: XCUIApplication) {
        let button: XCUIElement = app.buttons[word("Close")]
        for _ in 0..<3 where button.exists {
            button.tap()
            _ = button.waitForNonExistence(timeout: 3)
        }
        XCTAssertFalse(button.exists, "the sheet closed")
    }

    private func reveal(_ app: XCUIApplication, _ element: XCUIElement) {
        for _ in 0..<8 where !element.isHittable { app.swipeUp() }
    }

    /// A row by the start of its title (its label is "title, line").
    private func row(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", title)).firstMatch
    }

    /// The looks, without a write: the home at its top and its foot, a draft, a proposal, the two pages, in `look`.
    private func shoot(look: String) throws {
        let app: XCUIApplication = try launch(look: look)
        XCTAssertTrue(app.staticTexts[word("While you were away")].waitForExistence(timeout: 20))
        // Every part of the page is read at once; a moment for the first drawing to settle before a row is tapped.
        XCTAssertTrue(row(app, "Re: The March invoice").waitForExistence(timeout: 10))
        sleep(2)
        shot(app, "\(look)-home")
        row(app, "Re: The March invoice").tap()
        XCTAssertTrue(app.buttons[word("Send with Mail")].waitForExistence(timeout: 10))
        shot(app, "\(look)-draft")
        close(app)
        row(app, "Read the README of a repository").tap()
        XCTAssertTrue(app.buttons[word("Set aside")].waitForExistence(timeout: 10))
        shot(app, "\(look)-proposal")
        close(app)
        let own: XCUIElement = row(app, word("On its own"))
        reveal(app, own)
        shot(app, "\(look)-home-foot")
        own.tap()
        XCTAssertTrue(app.staticTexts["Tidy the Downloads folder"].waitForExistence(timeout: 10))
        shot(app, "\(look)-initiative")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let watchers: XCUIElement = row(app, word("Watchers"))
        reveal(app, watchers)
        watchers.tap()
        XCTAssertTrue(app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Landlord")).firstMatch.waitForExistence(timeout: 10))
        shot(app, "\(look)-watchers")
        app.terminate()
    }

    /// The design bar's measures (SETTINGS-NATIVE.md), from the pixels: on the home at its top and its foot, on the
    /// initiative's page and on the watchers', every row's mark and chevron are centred, its insets equal, its height at
    /// least 44 pt, a switch's inset the rows'. The numbers are kept as an attachment.
    func testTheRowsAreEvenlyLaidOut() throws {
        let app: XCUIApplication = try launch(look: "light")
        // A row every run has (the card of "while you were away" is gone once the writes' test marked it read).
        XCTAssertTrue(row(app, word("On its own")).waitForExistence(timeout: 20))
        sleep(2)
        var lines: [String] = RowMeasure.check(app, screen: "home")
        let own: XCUIElement = row(app, word("On its own"))
        reveal(app, own)
        sleep(1)
        lines += RowMeasure.check(app, screen: "home foot")
        own.tap()
        XCTAssertTrue(app.staticTexts["Tidy the Downloads folder"].waitForExistence(timeout: 10))
        sleep(1)
        lines += RowMeasure.check(app, screen: "initiative")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let watchers: XCUIElement = row(app, word("Watchers"))
        reveal(app, watchers)
        watchers.tap()
        XCTAssertTrue(app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Landlord")).firstMatch.waitForExistence(timeout: 10))
        sleep(1)
        lines += RowMeasure.check(app, screen: "watchers")
        XCTAssertFalse(lines.isEmpty, "no row was measured")
        let attachment: XCTAttachment = XCTAttachment(string: lines.joined(separator: "\n"))
        attachment.name = "\(device)-measures"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testAShotOfEachLook() throws {
        try shoot(look: "light")
        try shoot(look: "dark")
    }

    func testEveryControlThatWritesWritesToTheMac() throws {
        guard language == "en" else { throw XCTSkip("the writes are checked in the English run") }
        let app: XCUIApplication = try launch(look: "light")
        XCTAssertTrue(app.staticTexts["While you were away"].waitForExistence(timeout: 20))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "2 tasks: 1 done, 1 failed")).firstMatch.exists)

        // A run of the list opens its chat.
        let backup: XCUIElement = row(app, "Back up the projects folder.")
        reveal(app, backup)
        backup.tap()
        XCTAssertTrue(app.navigationBars.buttons.element(boundBy: 0).waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts["While you were away"].isHittable, "the chat is pushed over the tab")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Ready: the mail draft is discarded (never sent: Send would hand it to the Mac's own Mail).
        app.swipeDown()
        row(app, "Re: The March invoice").tap()
        let discard: XCUIElement = app.buttons["Discard"]
        XCTAssertTrue(discard.waitForExistence(timeout: 10))
        // On a small phone the sheet opens at its middle height with Discard under the fold: drawn up first.
        reveal(app, discard)
        discard.tap()
        app.sheets["Discard this draft?"].buttons["Discard"].tap()
        XCTAssertTrue(row(app, "Running ten minutes late").waitForExistence(timeout: 10))
        XCTAssertFalse(row(app, "Re: The March invoice").exists)

        // Dot's proposals: one put in use, the one from outside content set aside.
        row(app, "Run the tests of what changed").tap()
        XCTAssertTrue(app.buttons["Use"].waitForExistence(timeout: 10))
        app.buttons["Use"].tap()
        XCTAssertTrue(row(app, "Read the README of a repository").waitForExistence(timeout: 10))
        row(app, "Read the README of a repository").tap()
        XCTAssertTrue(app.buttons["Set aside"].waitForExistence(timeout: 10))
        app.buttons["Set aside"].tap()
        app.sheets["Set this version aside?"].buttons["Set aside"].tap()
        sleep(2)

        // Dot comes to you: the brief off, the check-in on.
        let brief: XCUIElement = app.switches["Morning brief"]
        reveal(app, brief)
        brief.tap()
        sleep(2)
        let checkin: XCUIElement = app.switches["Evening check-in"]
        reveal(app, checkin)
        checkin.tap()
        sleep(2)

        // On its own: switched on, a thing removed, one added.
        let own: XCUIElement = row(app, "On its own")
        reveal(app, own)
        own.tap()
        let initiative: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Work on its own")).firstMatch
        XCTAssertTrue(initiative.waitForExistence(timeout: 10))
        initiative.tap()
        sleep(2)
        app.buttons["Take Tidy the Downloads folder off the list"].tap()
        sleep(2)
        let field: XCUIElement = app.textFields["Something Dot can do for you on its own"]
        reveal(app, field)
        field.tap()
        field.typeText("Renew the passport")
        app.buttons["Add"].tap()
        XCTAssertTrue(app.staticTexts["Renew the passport"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Watchers: Landlord off.
        let watchers: XCUIElement = row(app, "Watchers")
        reveal(app, watchers)
        watchers.tap()
        let landlord: XCUIElement = app.switches.matching(NSPredicate(format: "label BEGINSWITH %@", "Landlord")).firstMatch
        XCTAssertTrue(landlord.waitForExistence(timeout: 10))
        landlord.tap()
        sleep(2)
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // While you were away: marked read for every device.
        app.swipeDown()
        app.swipeDown()
        let read: XCUIElement = app.buttons["Mark as read"]
        XCTAssertTrue(read.waitForExistence(timeout: 10))
        read.tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: app.staticTexts["While you were away"])
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 10), .completed)
    }
}
