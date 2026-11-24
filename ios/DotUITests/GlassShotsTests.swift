import XCTest

/// Screenshots of the screens that need a tap to reach (item 1380), for looking at the design in the simulator: the new
/// chat sheet, the model pickers, the run inspector, a step's detail. They read the scratch daemon of
/// `/tmp/dot-glass-daemon.json` (the daemon of `ios/README.md`'s screenshot section) and are skipped without it. Each
/// file is named by what it shows and by the suffix in `/tmp/dot-glass-suffix.txt` (for example `dark-large`), and goes to
/// `/tmp/dot-ui-shots/glass/`. They assert only that the screen came up.
final class GlassShotsTests: XCTestCase {
    struct Daemon: Decodable {
        let origin: String
        let key: String
    }

    var app: XCUIApplication!
    var suffix: String = "shot"

    override func setUpWithError() throws {
        continueAfterFailure = false
        guard let data: Data = try? Data(contentsOf: URL(fileURLWithPath: "/tmp/dot-glass-daemon.json")) else {
            throw XCTSkip("no scratch daemon at /tmp/dot-glass-daemon.json: the shots are skipped")
        }
        let daemon: Daemon = try JSONDecoder().decode(Daemon.self, from: data)
        try? FileManager.default.createDirectory(atPath: "/tmp/dot-ui-shots/glass", withIntermediateDirectories: true)
        suffix = (try? String(contentsOfFile: "/tmp/dot-glass-suffix.txt", encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "shot"
        app = XCUIApplication()
        app.launchArguments = ["-DotDevOrigin", daemon.origin, "-DotDevKey", daemon.key, "-DotClearDrafts", "1"]
    }

    func shot(_ name: String) {
        let png: Data = XCUIScreen.main.screenshot().pngRepresentation
        try? png.write(to: URL(fileURLWithPath: "/tmp/dot-ui-shots/glass/\(name)-\(suffix).png"))
    }

    func testNewChatSheetAndModelPicker() {
        app.launch()
        let compose: XCUIElement = app.buttons["new-chat"]
        XCTAssertTrue(compose.waitForExistence(timeout: 20))
        compose.tap()
        let field: XCUIElement = app.descendants(matching: .any)["composer-field"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        sleep(1)
        shot("new-chat-1-empty")
        field.tap()
        field.typeText("Plan the week and tell me what needs me")
        shot("new-chat-2-typed")
        app.buttons["model-pill"].tap()
        XCTAssertTrue(app.buttons["model-ollama:deep"].waitForExistence(timeout: 10))
        sleep(1)
        shot("new-chat-3-models")
    }

    func testAgentWithNoChat() {
        app.launch()
        let chip: XCUIElement = app.buttons["Research"]
        XCTAssertTrue(chip.waitForExistence(timeout: 20))
        chip.tap()
        XCTAssertTrue(app.descendants(matching: .any)["composer-field"].waitForExistence(timeout: 10))
        sleep(1)
        shot("agent-new-chat")
    }

    func testInspectorModelsAndStepDetail() {
        app.launchArguments += ["-DotOpenChat", "demo:tools"]
        app.launch()
        XCTAssertTrue(app.buttons["Run details"].waitForExistence(timeout: 20))
        sleep(2)
        let step: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'tool-'")).firstMatch
        if step.waitForExistence(timeout: 5) {
            step.tap()
            sleep(2)
            shot("step-detail")
            app.buttons["Done"].tap()
        }
        app.buttons["Run details"].tap()
        XCTAssertTrue(app.buttons["chat-model"].waitForExistence(timeout: 10))
        sleep(1)
        shot("inspector-medium")
        app.buttons["chat-model"].tap()
        XCTAssertTrue(app.buttons["model-ollama:deep"].waitForExistence(timeout: 10))
        sleep(1)
        shot("inspector-model-picker")
    }

    func testQuestionAnswerAndKeyboard() throws {
        app.launchArguments += ["-DotOpenChat", "demo:question"]
        app.launch()
        // The question may already be answered (a test or a tap before this one used it): then there is nothing to show.
        guard app.buttons["Skip"].waitForExistence(timeout: 20) else { throw XCTSkip("the scratch daemon's question is no longer pending") }
        sleep(1)
        let answer: XCUIElement = app.textFields["Your answer"]
        if answer.waitForExistence(timeout: 5) {
            answer.tap()
            answer.typeText("Archive 2019")
            sleep(1)
            shot("question-typing")
        }
    }
}
