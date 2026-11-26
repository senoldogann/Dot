import XCTest

/// The jank check of a work-log row (owner's report of 2026-11-26: opening and closing a command row stutters): a working
/// chat whose command rows return long output, a row opened and closed six times. The app's frame meter writes a line per
/// toggle (`scripts/jank.sh` reads them); this test holds what must not break: the row's top stays where it was.
final class WorkLogJankUITests: XCTestCase {
    func testAnOpenedRowKeepsItsTopWhereItWasAndClosesBackToIt() {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "n2-jank", "-toolCallMode", "showAll", "-DotNextToggleMeter", "1", "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
        ]
        app.launch()
        let row: XCUIElement = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "vitest")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 15), "the command row with the long output")
        usleep(1_500_000)
        var report: [String] = []
        for round in 1...6 {
            let before: CGRect = row.frame
            row.tap()
            usleep(1_200_000)
            let opened: CGRect = row.frame
            row.tap()
            usleep(1_200_000)
            let closed: CGRect = row.frame
            report.append("round \(round): top before \(before.minY) opened \(opened.minY) closed \(closed.minY)")
            XCTAssertEqual(opened.minY, before.minY, accuracy: 1, "round \(round): the row's top stayed where it was when it opened")
            XCTAssertEqual(closed.minY, before.minY, accuracy: 1, "round \(round): and when it closed again")
        }
        // The control: a row with one line of output, on the same screen, measured the same way.
        let short: XCUIElement = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "git status")).firstMatch
        XCTAssertTrue(short.exists, "the short row")
        for _ in 1...4 {
            short.tap()
            usleep(1_200_000)
            short.tap()
            usleep(1_200_000)
        }
        let attachment: XCTAttachment = XCTAttachment(string: report.joined(separator: "\n"))
        attachment.name = "row-tops"
        attachment.lifetime = .keepAlways
        add(attachment)
        print(report.joined(separator: "\n"))
    }
}
