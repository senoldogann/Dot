import XCTest

/// The scroll check of a long chat (owner's report of 2026-11-27: scrolling up and down a chat freezes and stutters): the
/// fixture chat `n2-long` (24 turns of tool rows and answers with tables, code and links) flicked up to its top and back
/// down to its end. XCTest's scroll metric gives the hitch time ratio of the flicks (ms of hitches per second of scroll);
/// the app's frame meter writes a line per flick (`-DotNextScrollMeter 1`, `scripts/scroll.sh` prints them) and the
/// render meter says how many times the chat screen's body ran while it scrolled, which must stay small: a scroll moves
/// the rows, it does not build the chat again.
@MainActor
final class ChatScrollUITests: XCTestCase {
    private func launched(mode: String) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "n2-long", "-toolCallMode", mode, "-DotNextScrollMeter", "1", "-DotNextRenderMeter", "1",
            "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
        ]
        app.launch()
        XCTAssertTrue(app.scrollViews.firstMatch.waitForExistence(timeout: 20), "the chat's transcript")
        usleep(2_000_000)
        return app
    }

    private func renders(_ app: XCUIApplication) -> Int {
        let meter: XCUIElement = app.staticTexts["debug.renders"]
        XCTAssertTrue(meter.waitForExistence(timeout: 15), "the render meter shows")
        guard let count = Int(meter.label) else {
            XCTFail("the render meter shows a count: \(meter.label)")
            return 0
        }
        return count
    }

    /// Flicks the transcript up `count` times, then down as many.
    private func flick(_ transcript: XCUIElement, count: Int) {
        for _ in 0..<count { transcript.swipeDown(velocity: .fast) }
        for _ in 0..<count { transcript.swipeUp(velocity: .fast) }
    }

    func testFlickingALongChatHitchesLittleAndDrawsTheScreenRarely() {
        for mode in ["hideAll", "showAll"] {
            let app: XCUIApplication = launched(mode: mode)
            let transcript: XCUIElement = app.scrollViews.firstMatch
            let before: Int = renders(app)
            flick(transcript, count: 8)
            usleep(1_500_000)
            let after: Int = renders(app)
            print("scroll-check mode=\(mode) screen_renders_during_16_flicks=\(after - before)")
            // Measured 1 in both modes on 2026-11-27: leaving the end once moves the jump button; nothing else redraws the screen.
            XCTAssertLessThanOrEqual(after - before, 2, "\(mode): a scroll moves the rows; it does not draw the chat screen again")
            app.terminate()
        }
    }

    /// XCTest's own measure of the flicks' hitches (five rounds), for the record of a change (`scripts/scroll.sh`).
    func testScrollHitchesOfALongChat() {
        let app: XCUIApplication = launched(mode: "showAll")
        let transcript: XCUIElement = app.scrollViews.firstMatch
        let options: XCTMeasureOptions = XCTMeasureOptions()
        options.iterationCount = 5
        measure(metrics: [XCTOSSignpostMetric.scrollingAndDecelerationMetric], options: options) {
            flick(transcript, count: 4)
        }
    }
}
