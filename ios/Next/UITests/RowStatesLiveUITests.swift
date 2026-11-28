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

    func testTheStatesAreTurkishInTurkish() throws {
        let app: XCUIApplication = try launch("tr")
        let asker: XCUIElement = row(app, "Asker")
        XCTAssertTrue(asker.waitForExistence(timeout: 60))
        expectation(for: NSPredicate(format: "label CONTAINS %@", "Seni bekliyor"), evaluatedWith: asker)
        waitForExpectations(timeout: 30)
        keepShot(app, "row-states-tr")
    }
}
