import XCTest

/// A chat's streaming against a throwaway daemon (`scripts/e2e_streaming.sh`): a new chat stays open when its first
/// message is sent and turns into the real chat, and a run that thinks, reads and answers for about half a minute is
/// streamed while the app's frame meter counts the frames (the script reads its record). Skipped unless the script
/// names the daemon (`DOTNEXT_E2E_HOST`, `DOTNEXT_E2E_KEY`).
final class StreamingLiveUITests: XCTestCase {
    private func launch(scene: String) throws -> XCUIApplication {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"] else {
            throw XCTSkip("run by scripts/e2e_streaming.sh against a throwaway daemon")
        }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", scene, "-DotNextHost", host, "-DotNextKey", key, "-DotNextFrameMeter", "1", "-AppleLanguages", "(en)", "-AppleLocale",
            "en_US",
        ]
        app.launch()
        return app
    }

    private func type(_ app: XCUIApplication, _ words: String) {
        let field: XCUIElement = app.textFields["composer.field"].exists ? app.textFields["composer.field"] : app.textViews["composer.field"]
        XCTAssertTrue(field.waitForExistence(timeout: 15))
        field.tap()
        field.typeText(words)
        app.buttons["composer.send"].tap()
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testAStreamingRunIsMeasured() throws {
        let app: XCUIApplication = try launch(scene: "live-chat")
        type(app, "stream: where do we stand")
        let end: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "the queue is empty")).firstMatch
        XCTAssertTrue(end.waitForExistence(timeout: 120), "the long answer streamed to its end")
        let done: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "placeholderValue == %@ OR value == %@", "Message Dot", "Message Dot")).firstMatch
        _ = done.waitForExistence(timeout: 30)
        sleep(2)
        shot(app, "streamed")
    }
}
