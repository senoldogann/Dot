import XCTest

/// Each row says where its chat stands (item 1920), against a throwaway daemon (`scripts/e2e_row_states.sh`): one agent
/// per state, its row on the home read by VoiceOver's words (the label says the state the row draws), Dot's face, and a
/// bot pinned from its menu going to the top with its pin. Skipped unless the script names the daemon
/// (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`). With `DOTNEXT_SHOTS` it keeps a shot of the home.
@MainActor
final class RowStatesLiveUITests: XCTestCase {
    private func launch(_ language: String) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_row_states.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "home", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(\(language))", "-AppleLocale",
            language == "tr" ? "tr_TR" : "en_US", "-pinnedBots", "()",
        ]
        app.launch()
        return app
    }

    private func row(_ app: XCUIApplication, _ name: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'home.bot.' AND label BEGINSWITH %@", name + ",")).firstMatch
    }

    private func keepShot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testEachRowSaysItsStateAndAPinnedBotGoesToTheTop() throws {
        let app: XCUIApplication = try launch("en")
        let states: [(String, String)] = [
            ("Asker", "Needs you"), ("Worker", "Working"), ("Delegator", "Waiting for 1 helper"), ("Runner", "Waiting · sleep 600"),
        ]
        for (name, words) in states {
            let element: XCUIElement = row(app, name)
            XCTAssertTrue(element.waitForExistence(timeout: 60), name)
            let deadline: Date = Date().addingTimeInterval(30)
            while !element.label.contains(words), Date() < deadline { _ = element.waitForExistence(timeout: 1) }
            XCTAssertTrue(element.label.contains(words), "\(name) says \(words): \(element.label)")
        }
        XCTAssertTrue(app.descendants(matching: .any)["row.live"].firstMatch.exists, "a row at work shows the live mark for its time")
        let reader: XCUIElement = row(app, "Reader")
        XCTAssertTrue(reader.label.contains("Unread"), "an answer not yet seen: \(reader.label)")
        XCTAssertFalse(reader.label.contains("Needs you") || reader.label.contains("Working"), reader.label)
        keepShot(app, "row-states-en")

        // Pinned from its menu, a bot goes above the others with its pin.
        reader.press(forDuration: 1.0)
        app.buttons["Pin"].firstMatch.tap()
        let first: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'home.bot.' AND NOT (identifier == 'home.bot.dot')")).firstMatch
        expectation(for: NSPredicate(format: "label BEGINSWITH 'Reader,'"), evaluatedWith: first)
        waitForExpectations(timeout: 10)
    }

    /// The home's search opens with its chips (the owner's ask of 2026-11-27): All shows the chat whose title holds the
    /// word and the file a run wrote, each chip only its kind, Bots none here.
    func testTheSearchChipsShowEachKindAlone() throws {
        let app: XCUIApplication = try launch("en")
        XCTAssertTrue(row(app, "Writer").waitForExistence(timeout: 60))
        let field: XCUIElement = app.textFields["home.search"]
        field.tap()
        for chip in ["all", "messages", "bots", "files"] {
            XCTAssertTrue(app.buttons["search.chip.\(chip)"].waitForExistence(timeout: 5), chip)
        }
        XCTAssertTrue(app.buttons["search.chip.all"].isSelected, "All is chosen when the search opens")
        field.typeText("note")
        let chat: XCUIElement = app.buttons["search.chat"].firstMatch
        let file: XCUIElement = app.buttons["search.file"].firstMatch
        XCTAssertTrue(chat.waitForExistence(timeout: 30), "the chat whose title holds the word")
        XCTAssertTrue(file.waitForExistence(timeout: 30), "the file a run wrote")
        XCTAssertTrue(file.label.contains("dotnext-e2e-note.txt"), file.label)
        keepShot(app, "search-all-en")
        app.buttons["search.chip.files"].tap()
        XCTAssertTrue(file.waitForExistence(timeout: 5))
        XCTAssertFalse(chat.exists, "Files shows files alone")
        XCTAssertTrue(app.buttons["search.chip.files"].isSelected)
        keepShot(app, "search-files-en")
        app.buttons["search.chip.bots"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["search.nothing"].waitForExistence(timeout: 5), "no bot holds the word")
        XCTAssertFalse(file.exists)
        app.buttons["search.chip.messages"].tap()
        XCTAssertTrue(chat.waitForExistence(timeout: 5))
        XCTAssertFalse(file.exists, "Messages shows the chats and what they say alone")
        chat.tap()
        XCTAssertTrue(app.buttons["botChat.pill"].waitForExistence(timeout: 10), "a find opens its chat")
    }

    /// A bot's face lives while its chat works (the owner's ask of 2026-11-27): on the home and in its chat's header, and
    /// it rests once the run is stopped. Last of the class (by name): it stops the Worker's run.
    func testWhileABotWorksItsFaceLivesAndRestsOnceStopped() throws {
        let app: XCUIApplication = try launch("en")
        let worker: XCUIElement = row(app, "Worker")
        XCTAssertTrue(worker.waitForExistence(timeout: 60))
        let living: XCUIElementQuery = app.descendants(matching: .any).matching(identifier: "face.living")
        expectation(for: NSPredicate(format: "count >= 2"), evaluatedWith: living)
        waitForExpectations(timeout: 30)
        XCTAssertTrue(app.descendants(matching: .any)["face.rest"].firstMatch.exists, "a bot at rest keeps a still face")
        keepShot(app, "faces-living-en")
        worker.tap()
        let header: XCUIElement = app.navigationBars.descendants(matching: .any)["face.living"].firstMatch
        XCTAssertTrue(header.waitForExistence(timeout: 20), "the chat's header face lives while its run works")
        let stop: XCUIElement = app.buttons["composer.send"]
        XCTAssertTrue(stop.waitForExistence(timeout: 10))
        stop.tap()
        let confirm: XCUIElement = app.buttons["Stop"].firstMatch
        if confirm.waitForExistence(timeout: 2) { confirm.tap() }
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: header)
        waitForExpectations(timeout: 30)
        XCTAssertTrue(app.navigationBars.descendants(matching: .any)["face.rest"].firstMatch.exists, "stopped, it rests")
    }

    func testTheStatesAreTurkishInTurkish() throws {
        let app: XCUIApplication = try launch("tr")
        let asker: XCUIElement = row(app, "Asker")
        XCTAssertTrue(asker.waitForExistence(timeout: 60))
        expectation(for: NSPredicate(format: "label CONTAINS %@", "Seni bekliyor"), evaluatedWith: asker)
        waitForExpectations(timeout: 30)
        keepShot(app, "row-states-tr")
    }
}
