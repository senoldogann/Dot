import XCTest

/// Deleting every chat of Dot at once against a throwaway daemon (`scripts/e2e_bulk_delete.sh`, items 1964 and 1965):
/// the 20 chats leave the list within seconds of Delete (one request, hidden at once), and Dot's profile, which drew
/// three of them, empties too. Skipped unless the script names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
@MainActor
final class BulkDeleteLiveUITests: XCTestCase {
    /// The longest the list may take to empty after Delete is tapped (the owner waited far longer for 20 requests).
    static let allowed: TimeInterval = 3

    private func launch() throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_bulk_delete.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "home", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        return app
    }

    func testTwentyChatsDeletedAtOnceLeaveTheListAndTheProfileQuickly() throws {
        let app: XCUIApplication = try launch()
        let dot: XCUIElement = app.buttons["home.bot.dot"]
        XCTAssertTrue(dot.waitForExistence(timeout: 60))
        dot.tap()
        let pill: XCUIElement = app.buttons["botChat.pill"]
        XCTAssertTrue(pill.waitForExistence(timeout: 20))
        let profileChats: XCUIElementQuery = app.buttons.matching(identifier: "profile.chat")
        // A tap while the chat is still being pushed is lost: tapped again until the profile shows.
        for _ in 0..<4 where !profileChats.firstMatch.exists {
            pill.tap()
            _ = profileChats.firstMatch.waitForExistence(timeout: 8)
        }
        XCTAssertTrue(profileChats.firstMatch.exists, "the profile draws Dot's newest chats")
        let all: XCUIElement = app.descendants(matching: .any)["profile.allChats"]
        for _ in 0..<5 where !all.isHittable { app.swipeUp() }
        all.tap()
        let rows: XCUIElementQuery = app.descendants(matching: .any).matching(identifier: "chats.row")
        // The list draws the rows on screen only: the newest is there once all twenty were read.
        XCTAssertTrue(rows.matching(NSPredicate(format: "label CONTAINS 'chat number 20'")).firstMatch.waitForExistence(timeout: 30))
        app.buttons["chats.select"].tap()
        app.buttons["chats.selectAll"].tap()
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "chats.pickedCount").firstMatch.label.contains("20"), "all twenty checked")
        app.buttons["chats.deletePicked"].tap()
        let confirm: XCUIElement = app.buttons.matching(identifier: "chats.confirmDelete").firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        let started: Date = Date()
        confirm.tap()
        let empty: NSPredicate = NSPredicate(format: "count == 0")
        expectation(for: empty, evaluatedWith: rows)
        waitForExpectations(timeout: 30)
        let took: TimeInterval = Date().timeIntervalSince(started)
        print("took \(String(format: "%.2f", took)) s to empty the list of 20 chats")
        XCTAssertLessThan(took, Self.allowed, "20 chats leave the list at once")

        // Back on the profile, none of them is drawn any more (it read a list of its own before item 1965).
        app.navigationBars.buttons.element(boundBy: 0).tap()
        expectation(for: empty, evaluatedWith: profileChats)
        waitForExpectations(timeout: 10)

        // Item 1985: the Mac is still deleting (it takes 8 s here); back on Dot's own chat, its new-chat page shows at
        // once, never a "Syncing…" of the chat it held, which is being deleted.
        // The first look after the tap (XCUITest waits for the app to settle, which is its own time, as the 2 s the
        // list took to "empty" though it empties in the same frame): the page is there already, nothing syncs.
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.descendants(matching: .any)["botNewChat"].firstMatch.exists, "Dot's new-chat page at the first look")
        XCTAssertFalse(app.staticTexts["Syncing…"].exists, "no chat being deleted is synced")
        XCTAssertTrue(app.descendants(matching: .any)["botNewChat"].firstMatch.exists, "and it stays")
        // The app stays up while the Mac answers (8 s here), so the script finds every chat deleted on the Mac.
        _ = XCTWaiter.wait(for: [XCTestExpectation(description: "the Mac answers the delete")], timeout: 12)
        XCTAssertFalse(app.staticTexts["Syncing…"].exists, "nothing synced once the Mac answered either")
    }
}
