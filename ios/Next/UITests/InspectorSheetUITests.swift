import XCTest

/// The "This chat" sheet (owner's report of 2026-11-26: rows far too tall, raised cards, too much space): plain 44 pt rows
/// with hairlines on the sheet's own ground, the model said readably with its provider apart. The measures are of the
/// element frames, in points; `DOTNEXT_SHOTS` (a folder, given to the runner as `TEST_RUNNER_DOTNEXT_SHOTS`) also gets a
/// PNG of each look.
final class InspectorSheetUITests: XCTestCase {
    private func launch(dark: Bool, language: String, locale: String) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "n2-inspector", "-AppleLanguages", "(\(language))", "-AppleLocale", locale, "-colorScheme", dark ? "dark" : "light"]
        app.launch()
        return app
    }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let screenshot: XCUIScreenshot = app.screenshot()
        let attachment: XCTAttachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let folder = ProcessInfo.processInfo.environment["DOTNEXT_SHOTS"] {
            try? FileManager.default.createDirectory(atPath: folder, withIntermediateDirectories: true)
            try? screenshot.pngRepresentation.write(to: URL(fileURLWithPath: folder).appendingPathComponent(name + ".png"))
        }
    }

    /// A row is one combined element, "State, Working": its frame is the row's.
    private func row(_ app: XCUIApplication, _ prefix: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", prefix)).firstMatch
    }

    private func measure(_ app: XCUIApplication, detent: String, into report: inout [String]) {
        let window: CGFloat = app.windows.firstMatch.frame.width
        let sheet: CGRect = app.navigationBars["This chat"].frame
        // The sheet is drawn at its own width, the window's less 16 pt at the medium detent, with its content scaled with it
        // (96 %): the rows are judged against 44 pt and 16 pt at that scale.
        let scale: CGFloat = sheet.width / window
        let keys: [String] = ["State,", "Started,", "Took,", "Approval mode,"]
        var tops: [CGFloat] = []
        for key in keys {
            let element: XCUIElement = row(app, key)
            guard element.exists else { continue }
            tops.append(element.frame.minY)
            let left: CGFloat = element.frame.minX - sheet.minX
            let right: CGFloat = sheet.maxX - element.frame.maxX
            report.append("\(detent) \(key) minY \(element.frame.minY) insets left \(left) right \(right) (scale \(scale))")
            XCTAssertEqual(left, right, accuracy: 1, "\(detent) \(key): equal left and right insets")
            XCTAssertEqual(left, 16 * scale, accuracy: 1, "\(detent) \(key): the page's 16 pt margin")
        }
        // The pitch of consecutive rows is a 44 pt row and its hairline.
        for (upper, lower) in zip(tops, tops.dropFirst()) {
            report.append("\(detent) row pitch \(lower - upper)")
            XCTAssertEqual(lower - upper, 44 * scale, accuracy: 1, "\(detent): a 44 pt row, no taller")
        }
    }

    private func check(dark: Bool) {
        let app: XCUIApplication = launch(dark: dark, language: "en", locale: "en_US")
        XCTAssertTrue(app.navigationBars["This chat"].waitForExistence(timeout: 20))
        let model: XCUIElement = row(app, "Model,")
        XCTAssertTrue(model.waitForExistence(timeout: 10), "the model row")
        XCTAssertFalse(model.label.contains("claude_code:"), "no raw reference: \(model.label)")
        XCTAssertTrue(model.label.contains("Opus 5.5 · Medium"), "the model and its level: \(model.label)")
        XCTAssertTrue(row(app, "Provider, Claude Code").exists, "the provider apart")
        // The sheet settles first (the measures are of the settled sheet), and its look is kept before anything is asserted.
        usleep(2_500_000)
        let name: String = dark ? "inspector-dark" : "inspector-light"
        shot(app, name)
        var report: [String] = []
        measure(app, detent: "medium", into: &report)
        // The same sheet at the large detent: the grabber's tap.
        let grabber: XCUIElement = app.buttons["Sheet Grabber"]
        if grabber.exists {
            grabber.tap()
            usleep(2_500_000)
            shot(app, name + "-large")
            measure(app, detent: "large", into: &report)
        }
        // No plan in the sheet (item 1996): the plan is the pill's Plan n/m and its sheet. The rest stays.
        app.swipeUp()
        XCTAssertFalse(app.staticTexts["Read next week's calendar"].exists, "no plan section")
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label ==[c] %@", "Plan")).firstMatch.exists, "no plan header")
        XCTAssertTrue(row(app, "State,").exists || app.switches["inspector.showSteps"].exists, "the other sections stay")
        let attachment: XCTAttachment = XCTAttachment(string: report.joined(separator: "\n"))
        attachment.name = name + "-measures"
        attachment.lifetime = .keepAlways
        add(attachment)
        print("MEASURES \(name)\n" + report.joined(separator: "\n"))
    }

    func testTheSheetIsPlainAndCompactInLight() { check(dark: false) }

    func testTheSheetIsPlainAndCompactInDark() { check(dark: true) }

    /// The Turkish look, for the owner's eyes: nothing clipped.
    func testTheSheetInTurkish() {
        let app: XCUIApplication = launch(dark: true, language: "tr", locale: "tr_TR")
        _ = app.buttons["Sheet Grabber"].waitForExistence(timeout: 20)
        usleep(2_500_000)
        shot(app, "inspector-tr-dark")
    }
}
