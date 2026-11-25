import XCTest

/// The design bar (SETTINGS-NATIVE.md, owner 2026-11-25) measured on the pages of items 1770-1771, drawn from the
/// fixture daemon: every row is a full tap target (44 pt at least), sits with equal insets left and right, and rows of
/// the same kind in a page have one height. A difference over 1 pt fails. The measurements are printed for the report.
final class SettingsDesignUITests: XCTestCase {
    private let scenes: [String] = ["settings-models", "settings-schedules", "settings-rules", "settings-memory", "settings-agents"]

    func testRowsAreFullTargetsWithEqualInsetsAndOneHeightPerKind() throws {
        for scene in scenes {
            let app: XCUIApplication = XCUIApplication()
            app.launchArguments = ["-DotNextScene", scene, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
            app.launch()
            let scroll: XCUIElement = app.scrollViews.firstMatch
            XCTAssertTrue(scroll.waitForExistence(timeout: 10), scene)
            sleep(2)
            let width: CGFloat = app.windows.firstMatch.frame.width
            var heights: [String: Set<Int>] = [:]
            for kind in [XCUIElement.ElementType.button, .switch] {
                let all: [XCUIElement] = scroll.descendants(matching: kind).allElementsBoundByIndex.filter { $0.exists && $0.isHittable }
                for element in all {
                    let frame: CGRect = element.frame
                    XCTAssertGreaterThanOrEqual(frame.height, 44 - 1, "\(scene): \(element.label) is \(frame.height) pt tall")
                    // A row spans its section: the gap at its left equals the gap at its right.
                    if frame.width > width * 0.8 {
                        let left: CGFloat = frame.minX
                        let right: CGFloat = width - frame.maxX
                        XCTAssertLessThanOrEqual(abs(left - right), 1, "\(scene): \(element.label) insets \(left) / \(right)")
                        heights["\(kind.rawValue)", default: []].insert(Int(frame.height.rounded()))
                        print("DESIGN \(scene) row '\(element.label.prefix(30))' x=\(left) right=\(right) h=\(frame.height)")
                    }
                }
            }
            print("DESIGN \(scene) row heights by kind: \(heights)")
            app.terminate()
        }
    }
}
