import XCTest

/// A chat's message actions and the run inspector against a throwaway daemon (package W4, `scripts/e2e_chat_actions.sh`):
/// each action tapped as the owner would, on the real engine and a phone's key; the script checks on the daemon's side
/// what each tap did. The chats are made by the script ("hello e2e", "fail: e2e", "draft: e2e", "write: e2e", "bg: e2e",
/// "helper: e2e"). With `DOTNEXT_W4_WRITES=0` the walk only opens each screen for its shots (the dark run); with `1` it
/// also taps every write. Skipped unless the script names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class ChatActionsLiveUITests: XCTestCase {
    private var writes: Bool = false
    private var look: String = "light"

    private func launch() throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_chat_actions.sh against a throwaway daemon")
        }
        writes = environment["DOTNEXT_W4_WRITES"] == "1"
        look = environment["DOTNEXT_W4_LOOK"] ?? "light"
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "chats", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
        ]
        app.launch()
        return app
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "w4-\(name)-\(look)"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func openChat(_ app: XCUIApplication, _ words: String) {
        let row: XCUIElement = app.collectionViews.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", words)).firstMatch
        for _ in 0..<4 where !(row.exists && row.isHittable) { app.swipeUp() }
        XCTAssertTrue(row.waitForExistence(timeout: 20), "the chat \(words) in the list")
        row.tap()
    }

    private func back(_ app: XCUIApplication) {
        app.navigationBars.buttons.element(boundBy: 0).tap()
    }

    private func closeSheet(_ app: XCUIApplication) {
        app.buttons["Close"].firstMatch.tap()
    }

    func testEveryActionReachesTheMac() throws {
        let app: XCUIApplication = try launch()
        XCTAssertTrue(app.buttons["New chat"].waitForExistence(timeout: 20))

        // Edit from here: the menu of the owner's message, the question about the files, the words back in the box.
        openChat(app, "hello e2e")
        let bubble: XCUIElement = app.scrollViews.staticTexts["You: hello e2e"].firstMatch
        XCTAssertTrue(bubble.waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Echo:")).firstMatch.waitForExistence(timeout: 15))
        bubble.press(forDuration: 1.2)
        XCTAssertTrue(app.buttons["Copy the text"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Select text"].exists)
        XCTAssertTrue(app.buttons["Read aloud"].exists)
        let edit: XCUIElement = app.buttons["Edit from here"]
        XCTAssertTrue(edit.exists)
        shot(app, "message-menu")
        if writes {
            edit.tap()
            let keep: XCUIElement = app.buttons["Keep the file changes"]
            XCTAssertTrue(keep.waitForExistence(timeout: 5))
            shot(app, "edit-from-here-asks")
            keep.tap()
            let field: XCUIElement = app.descendants(matching: .any).matching(identifier: "composer.field").firstMatch
            let filled: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value CONTAINS %@", "hello e2e"), object: field)
            XCTAssertEqual(XCTWaiter.wait(for: [filled], timeout: 15), .completed, "the words came back to the composer")
            shot(app, "edit-from-here-done")
        } else {
            app.buttons["Select text"].tap()
            XCTAssertTrue(app.navigationBars["Select text"].waitForExistence(timeout: 5))
            closeSheet(app)
        }
        back(app)

        // A failed run: Retry (asked first) sends the message again; Continue resumes the newest run.
        openChat(app, "fail: e2e")
        let retry: XCUIElement = app.buttons["end.retry"]
        XCTAssertTrue(retry.waitForExistence(timeout: 20), "Retry on a failed run")
        XCTAssertTrue(app.buttons["end.resume"].exists, "Continue on the newest failed run")
        shot(app, "failed-card")
        if writes {
            retry.tap()
            let again: XCUIElement = app.buttons.matching(NSPredicate(format: "label == %@ AND identifier != %@", "Retry", "end.retry")).firstMatch
            XCTAssertTrue(again.waitForExistence(timeout: 5))
            again.tap()
            sleep(6)
            let resume: XCUIElement = app.buttons["end.resume"]
            XCTAssertTrue(resume.waitForExistence(timeout: 20), "the retried run failed again and offers Continue")
            resume.tap()
            sleep(6)
        }
        back(app)

        // A draft: its card, the editor, Discard (asked first). Send is not tapped: it would hand the mail to Mail.app.
        openChat(app, "draft: e2e")
        let discard: XCUIElement = app.buttons["draft.discard"]
        XCTAssertTrue(discard.waitForExistence(timeout: 20), "the draft's card")
        XCTAssertTrue(app.staticTexts["Hello from the e2e check."].waitForExistence(timeout: 10))
        shot(app, "draft-card")
        app.buttons["draft.edit"].tap()
        let body: XCUIElement = app.textViews["draft.body"]
        XCTAssertTrue(body.waitForExistence(timeout: 5))
        shot(app, "draft-editor")
        if writes {
            body.tap()
            body.typeText(" Edited.")
            app.buttons["Save"].tap()
            XCTAssertTrue(app.staticTexts["Edited on this phone: Send sends these words."].waitForExistence(timeout: 5))
            discard.tap()
            let confirm: XCUIElement = app.buttons.matching(NSPredicate(format: "label == %@ AND identifier != %@", "Discard", "draft.discard")).firstMatch
            XCTAssertTrue(confirm.waitForExistence(timeout: 5))
            confirm.tap()
            // The view lists only the drafts that still take an answer: a discarded one leaves the chat.
            let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: app.buttons["draft.send"])
            XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 10), .completed, "the discarded draft's card left")
        } else {
            app.buttons["Cancel"].tap()
        }
        back(app)

        // The inspector: the run, the model, the recap (written now), the cost, the change and its Undo.
        openChat(app, "write: e2e")
        app.buttons["Changes and files"].tap()
        XCTAssertTrue(app.navigationBars["This chat"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Tokens in"].waitForExistence(timeout: 15), "the cost section")
        XCTAssertTrue(app.staticTexts["dotnext-e2e-note.txt"].exists, "the changed file")
        shot(app, "inspector-top")
        app.swipeUp()
        shot(app, "inspector-bottom")
        if writes {
            let undo: XCUIElement = app.buttons["inspector.undo"]
            XCTAssertTrue(undo.waitForExistence(timeout: 5))
            undo.tap()
            let confirm: XCUIElement = app.buttons["Undo the change"]
            XCTAssertTrue(confirm.waitForExistence(timeout: 5))
            confirm.tap()
            XCTAssertTrue(app.staticTexts["Undone"].waitForExistence(timeout: 10))
            app.swipeDown()
            let recap: XCUIElement = app.buttons["inspector.recap"]
            XCTAssertTrue(recap.waitForExistence(timeout: 5))
            recap.tap()
            XCTAssertTrue(app.buttons["Write a new recap"].waitForExistence(timeout: 30), "a recap was written")
            shot(app, "inspector-after")
        }
        closeSheet(app)
        back(app)

        // A command in the background: Stop in the inspector.
        openChat(app, "bg: e2e")
        app.buttons["Changes and files"].tap()
        let stop: XCUIElement = app.buttons["inspector.stopCommand"]
        for _ in 0..<3 where !(stop.exists && stop.isHittable) { app.swipeUp() }
        XCTAssertTrue(stop.waitForExistence(timeout: 15), "the background command with Stop")
        shot(app, "inspector-background")
        if writes {
            stop.tap()
            sleep(3)
        }
        closeSheet(app)
        back(app)

        // A helper: the Agents sheet opens its own chat, which names the chat it works for.
        openChat(app, "helper: e2e")
        app.buttons["Agents"].tap()
        let helper: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH[c] %@", "wait for the test")).firstMatch
        XCTAssertTrue(helper.waitForExistence(timeout: 15))
        helper.tap()
        // The helper's own chat, pushed in the sheet (item 1880): its title is the helper's goal.
        XCTAssertTrue(app.navigationBars.staticTexts.matching(NSPredicate(format: "label BEGINSWITH[c] %@", "wait for the test")).firstMatch.waitForExistence(timeout: 15))
        shot(app, "helper-chat")
    }
}
