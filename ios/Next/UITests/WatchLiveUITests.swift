import XCTest

/// Watching from the phone against a throwaway daemon (items 1838-1840, `scripts/e2e_watch.sh`): the Mac's screen from
/// Activity, a run that drives its browser followed live in its chat, taken over, tapped and typed on, and handed back.
/// The script reads on the daemon's side what the hold did. Skipped unless the script names the daemon.
final class WatchLiveUITests: XCTestCase {
    private func launch(_ scene: String) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_watch.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", scene, "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        return app
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// The Mac's screen opens full screen from Activity and says whether it is live or why not, then closes.
    func testWatchTheMacFromActivity() throws {
        let app: XCUIApplication = try launch("activity")
        let row: XCUIElement = app.buttons["activity.watchMac"]
        XCTAssertTrue(row.waitForExistence(timeout: 15))
        row.tap()
        XCTAssertTrue(app.buttons["Close"].waitForExistence(timeout: 10))
        // Live, or the daemon's reason (a Mac whose capture failed) with Try again: never a blank screen.
        let live: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Live")).firstMatch
        // The Mac's capture can fail to start on a loaded Mac (ScreenCaptureKit's 5 s): the viewer then says it is
        // reconnecting, or why it stopped.
        let refused: XCUIElement = app.buttons["Try again"]
        let retrying: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Reconnecting")).firstMatch
        let shown: Bool = live.waitForExistence(timeout: 25) || refused.exists || retrying.exists
        shot(app, "watch-mac")
        XCTAssertTrue(shown, "the viewer says Live or why it could not show the screen")
        // The top bar hides once the picture is live: a tap brings it back.
        if !app.buttons["Close"].exists { app.scrollViews.firstMatch.tap() }
        app.buttons["Close"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Close"].waitForNonExistence(timeout: 5), "Close closes the viewer")
        XCTAssertTrue(row.waitForExistence(timeout: 5))
    }

    /// A run that drives its browser: the mini player floats in its chat, opens full screen, is taken over, takes a tap
    /// and typed text, and is handed back.
    func testFollowARunLiveAndTakeItsPageOver() throws {
        let app: XCUIApplication = try launch("live-chat")
        let mini: XCUIElement = app.descendants(matching: .any).matching(identifier: "live.mini").firstMatch
        XCTAssertTrue(mini.waitForExistence(timeout: 40), "the mini player shows while the run drives its browser")
        sleep(3)
        shot(app, "chat-mini-player")
        mini.tap()
        let takeOver: XCUIElement = app.buttons["live.takeOver"]
        XCTAssertTrue(takeOver.waitForExistence(timeout: 10))
        sleep(2)
        shot(app, "run-live-full")
        takeOver.tap()
        let handBack: XCUIElement = app.buttons["live.handBack"]
        XCTAssertTrue(handBack.waitForExistence(timeout: 15), "the run is held for this phone")
        // On the picture of the play page (its image, fitted in the canvas), measured on its shot: the "Press me" button
        // at 12 % across and 29 % down, the field at 32 % across and 40 % down. Two taps a second apart stay two single
        // clicks (a double click is two within 0.35 s); a tap on the field gives it the focus the typing goes to.
        let picture: XCUIElement = app.scrollViews["Live picture"].images.firstMatch
        XCTAssertTrue(picture.waitForExistence(timeout: 5))
        let button: XCUICoordinate = picture.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: 0.29))
        button.tap()
        sleep(1)
        button.tap()
        sleep(1)
        picture.coordinate(withNormalizedOffset: CGVector(dx: 0.32, dy: 0.40)).tap()
        sleep(1)
        app.buttons["live.keyboard"].tap()
        sleep(1)
        app.typeText("hi")
        sleep(2)
        shot(app, "run-live-held")
        handBack.tap()
        XCTAssertTrue(takeOver.waitForExistence(timeout: 15), "handed back, the run can be taken over again")
        if !app.buttons["Close"].exists { app.scrollViews.firstMatch.tap() }
        app.buttons["Close"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Close"].waitForNonExistence(timeout: 5), "Close closes the viewer")
        XCTAssertTrue(mini.waitForExistence(timeout: 10))
    }
}
