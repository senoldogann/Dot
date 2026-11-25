import XCTest

/// The chat list against a throwaway daemon (`scripts/e2e_chats.sh`, PLAN §23): the owner's taps on the real engine and
/// a phone's key; the script reads on the daemon's side what they did (the pins, the deleted chats). Skipped unless the
/// script names the daemon.
final class ChatListLiveUITests: XCTestCase {
    private func launch() throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_chats.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "chats", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
        ]
        app.launch()
        return app
    }

    private func any(_ app: XCUIApplication, _ identifier: String) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(identifier: identifier)
    }

    private func row(_ app: XCUIApplication, _ title: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "identifier == 'chats.row' AND label BEGINSWITH %@", title)).firstMatch
    }

    /// Select mode: two chats pinned at once go to Pinned on top; then all of them deleted after one confirmation.
    func testSeveralChatsArePinnedAndDeletedTogether() throws {
        let app: XCUIApplication = try launch()
        XCTAssertTrue(row(app, "[test] list one").waitForExistence(timeout: 20))
        app.buttons["chats.select"].tap()
        row(app, "[test] list one").tap()
        row(app, "[test] list two").tap()
        XCTAssertEqual(app.staticTexts["chats.pickedCount"].label, "2 selected")
        app.buttons["chats.pinPicked"].tap()
        XCTAssertTrue(app.staticTexts["Pinned"].waitForExistence(timeout: 15), "the pinned chats have their section")

        app.buttons["chats.select"].tap()
        app.buttons["chats.selectAll"].tap()
        app.buttons["chats.deletePicked"].tap()
        let confirm: XCUIElement = app.buttons["chats.confirmDelete"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: row(app, "[test] list one"))
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 20), .completed, "the chats left the list")
    }

}
