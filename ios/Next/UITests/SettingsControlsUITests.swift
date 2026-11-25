import XCTest

/// No dead control in Settings (the lead's order of 2026-11-25): every row of the Settings tab is tapped, and on each
/// native page every button, switch and menu of the page; a tap after which nothing on screen changed (no page, sheet,
/// dialog, menu or value) fails the test with the control's name. Runs on the fixture daemon (`settings-home`).
@MainActor
final class SettingsControlsUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() async throws {
        continueAfterFailure = true
        app = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "settings-home", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        XCTAssertTrue(app.buttons["Emergency stop"].waitForExistence(timeout: 15))
    }

    /// The labels of the buttons and switches of the page's scroll view that stand on screen, top to bottom, the page
    /// scrolled through once and back to its top. One snapshot of the tree per screenful (querying each element is slow).
    private func controls(skipping: Set<String>) -> [String] {
        var seen: [String] = []
        let window: CGRect = app.windows.firstMatch.frame
        for _ in 0..<8 {
            let before: Int = seen.count
            guard let tree = try? app.scrollViews.firstMatch.snapshot() else { break }
            var stack: [any XCUIElementSnapshot] = [tree]
            while let node = stack.popLast() {
                stack.append(contentsOf: node.children.reversed())
                guard node.elementType == .button || node.elementType == .switch, !node.label.isEmpty, node.isEnabled else { continue }
                guard window.contains(CGPoint(x: node.frame.midX, y: node.frame.midY)), node.frame.midY < window.maxY - 90 else { continue }
                if !seen.contains(node.label) && !skipping.contains(node.label) { seen.append(node.label) }
            }
            if seen.count == before { break }
            app.scrollViews.firstMatch.swipeUp()
        }
        for _ in 0..<8 { app.scrollViews.firstMatch.swipeDown() }
        return seen
    }

    private func element(_ label: String) -> XCUIElement {
        let found: XCUIElement = app.scrollViews.firstMatch.descendants(matching: .any).matching(NSPredicate(format: "label == %@", label)).firstMatch
        for _ in 0..<8 where !(found.exists && found.isHittable) { app.scrollViews.firstMatch.swipeUp() }
        return found
    }

    private var title: String { app.navigationBars.firstMatch.identifier }

    /// Taps `label` and says whether anything changed; then puts the screen back as it was (back, cancel, close).
    private func tapChanges(_ label: String, on page: String) -> Bool {
        let target: XCUIElement = element(label)
        guard target.exists else { return true }
        let before: String = app.debugDescription
        target.tap()
        sleep(1)
        let changed: Bool = app.debugDescription != before
        restore(to: page)
        return changed
    }

    private func restore(to page: String) {
        for _ in 0..<3 {
            if app.sheets.firstMatch.exists || app.alerts.firstMatch.exists {
                let cancel: XCUIElement = app.buttons.matching(NSPredicate(format: "label IN %@", ["Cancel", "Close"])).firstMatch
                if cancel.exists { cancel.tap() } else { app.tap() }
            } else if app.navigationBars.buttons["Cancel"].exists {
                app.navigationBars.buttons["Cancel"].tap()
                if app.buttons["Discard"].waitForExistence(timeout: 1) { app.buttons["Discard"].tap() }
            } else if app.navigationBars.buttons["Close"].exists {
                app.navigationBars.buttons["Close"].tap()
            } else if app.menuItems.firstMatch.exists || app.collectionViews["menu"].exists {
                app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.1)).tap()
            } else if title != page, app.navigationBars.buttons.firstMatch.exists {
                app.navigationBars.buttons.firstMatch.tap()
            } else {
                return
            }
            sleep(1)
        }
    }

    func testEveryRowOfSettingsOpensItsPage() {
        let rows: [String] = controls(skipping: ["Emergency stop"])
        XCTAssertGreaterThan(rows.count, 20, "the rows of Settings: \(rows)")
        for row in rows {
            let target: XCUIElement = element(row)
            target.tap()
            sleep(1)
            XCTAssertNotEqual(title, "Settings", "the row \(row) opened nothing")
            restore(to: "Settings")
            XCTAssertEqual(title, "Settings", "back from \(row)")
        }
        XCTAssertTrue(tapChanges("Emergency stop", on: "Settings"), "Emergency stop asks first")
    }

    /// Every control of each native page.
    func testEveryControlOfTheNativePagesDoesSomething() {
        let pages: [String] = [
            "This Mac", "Notifications", "General", "Appearance", "Chat", "Models", "Usage limits and caps", "Scheduled tasks", "Rules", "Memory",
            "Health and version", "Usage",
        ]
        var dead: [String] = []
        for page in pages {
            let row: XCUIElement = app.scrollViews.firstMatch.buttons.matching(NSPredicate(format: "label == %@ OR label BEGINSWITH %@", page, page + ",")).firstMatch
            for _ in 0..<8 where !(row.exists && row.isHittable) { app.scrollViews.firstMatch.swipeUp() }
            row.tap()
            XCTAssertTrue(app.navigationBars[page].waitForExistence(timeout: 5), "page \(page)")
            sleep(2)
            // A field is typed into, not tapped; Copy of the address is a context menu.
            for control in controls(skipping: []) where !tapChanges(control, on: page) {
                dead.append("\(page) › \(control)")
            }
            for item in app.navigationBars[page].buttons.allElementsBoundByIndex.dropFirst() where item.isHittable {
                let label: String = item.label
                let before: String = app.debugDescription
                item.tap()
                sleep(1)
                if app.debugDescription == before { dead.append("\(page) › toolbar \(label)") }
                restore(to: page)
            }
            restore(to: "Settings")
            for _ in 0..<8 { app.scrollViews.firstMatch.swipeDown() }
        }
        XCTAssertEqual(dead, [], "controls that did nothing")
    }
}
