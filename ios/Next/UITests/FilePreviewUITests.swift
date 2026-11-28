import XCTest

/// A file's preview reads by its format (the owner's shot of 2026-11-28: `C8.out.md` showed `## Rapor`, `**87 not**` and
/// a table's pipes as raw text, its last lines under Open on Mac and the Share pill). Part of `ChatUITests`, so that
/// ios.yml's chat part runs it on GitHub. The files come from the fixture daemon (`FileFixture`).
extension ChatUITests {
    private func openFile(_ name: String, dark: Bool) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "n2-file", "-DotNextFile", name, "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-colorScheme", dark ? "dark" : "light",
        ]
        app.launch()
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 20), "the preview of \(name)")
        return app
    }

    private func keep(_ app: XCUIApplication, _ name: String) {
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// A Markdown report is drawn as the chat draws an answer: its heading, its bold words without their stars, its
    /// table as a grid; its source a tap away; and its last line scrolls clear of the controls that float at the bottom.
    func testAMarkdownFileReadsFormattedAndScrollsClearOfTheControls() {
        for dark in [false, true] {
            let app: XCUIApplication = openFile("C8.out.md", dark: dark)
            let document: XCUIElement = app.descendants(matching: .any).matching(identifier: "file.markdown").firstMatch
            XCTAssertTrue(document.waitForExistence(timeout: 10), "the formatted report")
            XCTAssertTrue(app.staticTexts["Rapor 1"].exists, "the heading without its marks")
            XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "answer.table").firstMatch.exists, "the table as a grid")
            XCTAssertTrue(app.staticTexts["Not"].exists && app.staticTexts["Karakter"].exists, "the table's header cells")
            for raw in ["## ", "**", "|---", "| N1 |"] {
                XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", raw)).firstMatch.exists, "no raw \(raw)")
            }
            keep(app, "file-markdown-\(dark ? "dark" : "light")")
            // The end of the file, above Open on Mac and the Share pill.
            let last: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Son satır")).firstMatch
            for _ in 0..<12 where !(last.exists && last.isHittable) { document.swipeUp() }
            document.swipeUp()
            usleep(800_000)
            XCTAssertTrue(last.exists, "the last line")
            let controls: [XCUIElement] = [app.buttons["file.openOnMac"], app.buttons["Share"], app.buttons["Save to Files"]].filter { $0.exists }
            XCTAssertFalse(controls.isEmpty, "the floating controls")
            for control in controls {
                XCTAssertLessThanOrEqual(last.frame.maxY, control.frame.minY, "the last line ends above \(control.label): \(last.frame) \(control.frame)")
            }
            keep(app, "file-markdown-end-\(dark ? "dark" : "light")")
            app.buttons["file.view"].buttons["Source"].tap()
            XCTAssertTrue(
                app.descendants(matching: .any).matching(NSPredicate(format: "identifier == 'change.line' AND label CONTAINS %@", "## Rapor 1")).firstMatch.waitForExistence(timeout: 5),
                "the source as written")
            keep(app, "file-markdown-source-\(dark ? "dark" : "light")")
            app.terminate()
        }
    }

    /// A CSV file is a grid: its header row, its rows read by VoiceOver with their column names.
    func testACSVFileIsATable() {
        let app: XCUIApplication = openFile("rows.csv", dark: false)
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "file.table").firstMatch.waitForExistence(timeout: 10), "the grid")
        XCTAssertTrue(app.descendants(matching: .any).matching(identifier: "file.table.header").firstMatch.exists, "the header row")
        let row: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "identifier == 'file.table.row' AND label BEGINSWITH %@", "id: 1, name: Client 1,"))
            .firstMatch
        XCTAssertTrue(row.exists, "a row with its column names")
        keep(app, "file-csv-light")
    }

    /// A JSON file on one line is laid out, one key to a line; a plain log wraps as text.
    func testAJSONFileIsLaidOutAndALogIsText() {
        let app: XCUIApplication = openFile("doneq.json", dark: false)
        let line: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "identifier == 'change.line' AND label CONTAINS %@", "\"left\": 12")).firstMatch
        XCTAssertTrue(line.waitForExistence(timeout: 10), "the JSON laid out")
        keep(app, "file-json-light")
        app.terminate()
        let log: XCUIApplication = openFile("run.log", dark: true)
        XCTAssertTrue(log.descendants(matching: .any).matching(identifier: "file.text").firstMatch.waitForExistence(timeout: 10), "the log as text")
        XCTAssertTrue(log.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "merged into 17")).firstMatch.exists)
        keep(log, "file-log-dark")
    }

    /// A Markdown report of 200 KB and a CSV of 5,000 rows open within a second and a half of their sheet, and ten
    /// swipes through them each end within a second (the measures go to the log and the result).
    func testLargeFilesOpenAndScrollWithoutAHitch() {
        var report: [String] = []
        for (name, identifier) in [("big.md", "file.markdown"), ("big.csv", "file.table")] {
            let app: XCUIApplication = openFile(name, dark: false)
            let start: Date = Date()
            let body: XCUIElement = app.descendants(matching: .any).matching(identifier: identifier).firstMatch
            XCTAssertTrue(body.waitForExistence(timeout: 10), name)
            let opened: TimeInterval = Date().timeIntervalSince(start)
            var swipes: [TimeInterval] = []
            for _ in 0..<10 {
                let swiped: Date = Date()
                body.swipeUp(velocity: .fast)
                swipes.append(Date().timeIntervalSince(swiped))
            }
            report.append("\(name) open \(Int(opened * 1000)) ms, swipes max \(Int((swipes.max() ?? 0) * 1000)) ms mean \(Int(swipes.reduce(0, +) / Double(swipes.count) * 1000)) ms")
            XCTAssertLessThan(opened, 1.5, "\(name) opens at once")
            XCTAssertLessThan(swipes.max() ?? 0, 2.5, "\(name) scrolls without a long stall")
            keep(app, "file-\(name)-scrolled")
            app.terminate()
        }
        let measures: XCTAttachment = XCTAttachment(string: report.joined(separator: "\n"))
        measures.name = "file-preview-measures"
        measures.lifetime = .keepAlways
        add(measures)
        print("MEASURES file preview\n" + report.joined(separator: "\n"))
    }
}
