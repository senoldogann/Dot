import XCTest

/// Exercises the Live Activity's actual URL against a paired, throwaway daemon. APNs delivery requires a signed phone.
final class PushLinkLiveUITests: XCTestCase {
    @MainActor
    func testTheActivityLinkOpensTheTaskChat() throws {
        let environment: [String: String] = ProcessInfo.processInfo.environment
        guard let host = environment["DOTNEXT_E2E_HOST"], let key = environment["DOTNEXT_E2E_KEY"],
            let run = environment["DOTNEXT_NOTIFICATION_RUN"]
        else { throw XCTSkip("run by scripts/e2e_settings.sh against a throwaway daemon") }
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "home", "-DotNextHost", host, "-DotNextKey", key, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        app.open(try XCTUnwrap(URL(string: "dotnext://run/\(run)")))
        let task: XCUIElement = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Check the notification link.")).firstMatch
        XCTAssertTrue(task.waitForExistence(timeout: 20), app.debugDescription)
    }
}
