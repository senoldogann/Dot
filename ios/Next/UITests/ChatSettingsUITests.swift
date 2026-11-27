import XCTest

/// The chat's settings sheet (PLAN §24) and Settings › Voice (PLAN §25), drawn from the debug build's fixtures: no daemon.
final class ChatSettingsUITests: XCTestCase {
    private func launch(_ scene: String) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", scene, "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-models.favourites", "()", "-models.hidden", "()"]
        app.launch()
        return app
    }

    private func any(_ app: XCUIApplication, _ identifier: String) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(identifier: identifier)
    }

    func testTheChipOpensTheSheetWithProvidersBadgesStarsAndOptions() {
        let app: XCUIApplication = launch("n2-chat")
        let field: XCUIElement = any(app, "composer.field").firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        let chip: XCUIElement = app.buttons["composer.model"]
        XCTAssertTrue(chip.waitForExistence(timeout: 5), "the chip shows with the composer")
        chip.tap()
        XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Claude Code"].exists, "providers by the web's names")
        let models: XCUIElementQuery = any(app, "modelCatalog.model")
        XCTAssertTrue(
            models.matching(NSPredicate(format: "label CONTAINS %@", ", Default")).firstMatch.exists,
            "the daemon's main model says Default under its name")
        XCTAssertTrue(app.buttons["Approvals"].exists || any(app, "chatSettings.approvals").firstMatch.exists)
        XCTAssertFalse(app.buttons["Add Sonnet 5.5 to favourites"].exists, "no column of empty stars")

        // The chat's model (Opus) is on top among the recent ones, and its provider's group is open; a long press stars a
        // model, and the star is kept.
        XCTAssertTrue(models.element(boundBy: 0).label.hasPrefix("Opus 5.5"), "the current model first: \(models.element(boundBy: 0).label)")
        let sonnet: XCUIElement = models.matching(NSPredicate(format: "label BEGINSWITH %@", "Sonnet 5.5")).firstMatch
        sonnet.press(forDuration: 1.0)
        let star: XCUIElement = app.buttons["Add Sonnet 5.5 to favourites"].firstMatch
        XCTAssertTrue(star.waitForExistence(timeout: 3), "the long press offers the star")
        star.tap()
        XCTAssertTrue(
            models.matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS %@", "Sonnet 5.5", "Favourite")).firstMatch.waitForExistence(timeout: 3))

        // The other providers start folded with their count, and open with a tap.
        XCTAssertTrue(app.staticTexts["2"].exists, "folded, Ollama says how many models it holds")
        // Counted by the catalog's rows, whatever the fixture names the models.
        let folded: Int = models.count
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Ollama")).firstMatch.tap()
        let opened: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "count == %d", folded + 2), object: models)
        XCTAssertEqual(XCTWaiter.wait(for: [opened], timeout: 3), .completed, "opened, Ollama's two models show")

        // The search narrows the list.
        let search: XCUIElement = app.textFields["modelCatalog.search"]
        search.tap()
        search.typeText("flash")
        XCTAssertEqual(models.count, 1, "the recent ones and the folds give way to the search")
        XCTAssertTrue(models.element(boundBy: 0).label.hasPrefix("DeepSeek V4.1 Flash"))
        app.buttons["Clear the search"].tap()

        // Cancel keeps nothing of the picks.
        app.buttons["chatSettings.cancel"].tap()
        XCTAssertFalse(app.navigationBars["Chat settings"].waitForExistence(timeout: 2))
    }

    /// The composer is one capsule (item 1924): + | field | the model chip | microphone | send, on one centre line, each target
    /// 44 pt or more. The chip is the provider's mark and a chevron; it opens the chat's grouped picker, and a pick
    /// changes the chip (its VoiceOver label names the model; see ChatListLiveUITests).
    func testTheChipSitsInTheCapsuleOpensThePickerAndAPickChangesIt() {
        let app: XCUIApplication = launch("n2-chat")
        let chip: XCUIElement = app.buttons["composer.model"]
        XCTAssertTrue(chip.waitForExistence(timeout: 10), "the chip is in the capsule at rest")
        XCTAssertTrue(chip.label.hasPrefix("Model: Opus 5.5"), chip.label)
        let mic: XCUIElement = app.buttons["composer.mic"]
        let send: XCUIElement = app.buttons["composer.send"]
        let plus: XCUIElement = app.buttons["Attach"]
        for part in [chip, mic, send, plus] {
            XCTAssertGreaterThanOrEqual(part.frame.width, 44, "\(part.identifier) is a 44 pt target")
            XCTAssertGreaterThanOrEqual(part.frame.height, 44)
        }
        print("composer capsule: plus \(plus.frame) chip \(chip.frame) mic \(mic.frame) send \(send.frame)")
        for part in [chip, mic, plus] {
            XCTAssertLessThanOrEqual(abs(part.frame.midY - send.frame.midY), 1, "one centre line: \(part.frame) \(send.frame)")
        }
        XCTAssertLessThan(plus.frame.maxX, chip.frame.minX)
        XCTAssertLessThanOrEqual(chip.frame.maxX, mic.frame.minX + 1)
        XCTAssertLessThanOrEqual(mic.frame.maxX, send.frame.minX + 1)
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "composer-capsule"
        shot.lifetime = .keepAlways
        add(shot)

        chip.tap()
        let picker: XCUIElement = app.navigationBars["Chat settings"]
        if !picker.waitForExistence(timeout: 5) {
            print("first tap opened nothing; hittable \(chip.isHittable) frame \(chip.frame)")
            chip.tap()
        }
        XCTAssertTrue(picker.waitForExistence(timeout: 5), "the existing grouped picker")
        // A pick changes the chip of a new chat (no fixture keeps a chat's choice): ChatListLiveUITests picks a model there
        // and reads the chip's label.
        app.buttons["chatSettings.cancel"].tap()
        XCTAssertTrue(chip.waitForExistence(timeout: 5))
    }

    /// Dark and light, and the largest accessibility size: the chip stays a 44 pt target (the mark alone at AX3) and the
    /// capsule keeps its parts on one line each. The shots go to the result bundle for the report.
    func testTheCapsuleHoldsInDarkAndAtTheLargestAccessibilitySize() {
        for (name, extra) in [("dark", ["-AppleInterfaceStyle", "Dark"]), ("ax3", ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL"])] {
            let app: XCUIApplication = XCUIApplication()
            app.launchArguments = ["-DotNextScene", "n2-chat", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"] + extra
            app.launch()
            let chip: XCUIElement = app.buttons["composer.model"]
            XCTAssertTrue(chip.waitForExistence(timeout: 10), "\(name): the chip")
            XCTAssertGreaterThanOrEqual(chip.frame.width, 44, name)
            XCTAssertGreaterThanOrEqual(chip.frame.height, 44, name)
            XCTAssertLessThanOrEqual(chip.frame.maxX, app.windows.firstMatch.frame.maxX, "\(name): nothing pushed off the edge")
            XCTAssertLessThanOrEqual(app.buttons["composer.send"].frame.maxX, app.windows.firstMatch.frame.maxX, name)
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "composer-\(name)"
            shot.lifetime = .keepAlways
            add(shot)
            app.terminate()
        }
    }

    /// The one-line capsule's chip is the mark and a chevron; once the composer expands (more than a line) the chip names the
    /// model, its level in grey after it, on the same centre line as + the microphone and send (item 1991). At the largest
    /// accessibility size the mark stays, the name may truncate and nothing is pushed off the edge.
    func testTheExpandedComposerNamesTheModelAndTheOneLineCapsuleKeepsTheMark() {
        let app: XCUIApplication = launch("n2-chat")
        let chip: XCUIElement = app.buttons["composer.model"]
        XCTAssertTrue(chip.waitForExistence(timeout: 10))
        let oneLine: CGFloat = chip.frame.width
        XCTAssertLessThan(oneLine, 70, "one line: the mark and a chevron alone, \(oneLine) pt")
        let field: XCUIElement = any(app, "composer.field").firstMatch
        // The screen may still be settling: tap, and again if the keyboard did not come.
        field.tap()
        if !app.keyboards.firstMatch.waitForExistence(timeout: 5) { field.tap() }
        field.typeText("one two three four five six seven eight nine ten eleven")
        // The layout follows the typing a moment later: wait for the chip to widen.
        for _ in 0..<50 where chip.frame.width <= oneLine + 60 { Thread.sleep(forTimeInterval: 0.1) }
        XCTAssertGreaterThan(chip.frame.width, oneLine + 60, "the expanded chip names the model: \(chip.frame)")
        XCTAssertTrue(chip.label.hasPrefix("Model: Opus 5.5"), chip.label)
        XCTAssertFalse(chip.label.contains("Claude Code"), "the provider's name is not in front of the model's: \(chip.label)")
        let mic: XCUIElement = app.buttons["composer.mic"]
        let send: XCUIElement = app.buttons["composer.send"]
        let plus: XCUIElement = app.buttons["Attach"]
        for part in [chip, mic, plus] {
            XCTAssertLessThanOrEqual(abs(part.frame.midY - send.frame.midY), 1, "one centre line: \(part.frame) \(send.frame)")
            XCTAssertGreaterThanOrEqual(part.frame.height, 44)
        }
        XCTAssertLessThanOrEqual(chip.frame.maxX, mic.frame.minX + 1)
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "composer-expanded-chip"
        shot.lifetime = .keepAlways
        add(shot)
        app.terminate()

        let large: XCUIApplication = XCUIApplication()
        large.launchArguments = ["-DotNextScene", "n2-chat", "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXLarge"]
        large.launch()
        let bigChip: XCUIElement = large.buttons["composer.model"]
        XCTAssertTrue(bigChip.waitForExistence(timeout: 10))
        XCTAssertGreaterThanOrEqual(bigChip.frame.height, 44)
        XCTAssertLessThanOrEqual(bigChip.frame.maxX, large.windows.firstMatch.frame.maxX, "nothing pushed off the edge")
        XCTAssertLessThanOrEqual(large.buttons["composer.send"].frame.maxX, large.windows.firstMatch.frame.maxX)
        let bigShot: XCTAttachment = XCTAttachment(screenshot: large.screenshot())
        bigShot.name = "composer-expanded-chip-ax3"
        bigShot.lifetime = .keepAlways
        add(bigShot)
    }

    /// The owner's report of 2026-11-27: fast mode shows only for a model that has it (no greyed switch), the level is a
    /// menu of the chosen model's own levels in full words, the approvals open their page.
    func testFastModeShowsOnlyForAModelWithItAndTheLevelAndApprovalsArePicked() {
        let app: XCUIApplication = launch("n2-model")
        XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 10))
        let fast: XCUIElement = any(app, "chatSettings.fast").firstMatch
        let sonnet: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier == 'modelCatalog.model' AND label BEGINSWITH %@", "Sonnet 5.5")).firstMatch
        for _ in 0..<4 where !sonnet.isHittable { app.swipeUp() }
        sonnet.tap()
        XCTAssertFalse(fast.waitForExistence(timeout: 2), "Sonnet has no fast mode: the row is not there")
        XCTAssertFalse(app.staticTexts["This model has no fast mode."].exists)
        app.buttons.matching(NSPredicate(format: "identifier == 'modelCatalog.model' AND label BEGINSWITH %@", "Opus 5.5")).firstMatch.tap()
        for _ in 0..<4 where !fast.exists { app.swipeDown() }
        XCTAssertTrue(fast.waitForExistence(timeout: 3), "Opus has it")
        XCTAssertTrue(fast.isEnabled)

        // The level is a menu of the chosen model's own levels, each in full words.
        let reasoning: XCUIElement = any(app, "chatSettings.reasoning").firstMatch
        for _ in 0..<4 where !reasoning.isHittable { app.swipeDown() }
        reasoning.tap()
        let high: XCUIElement = app.buttons["High"].firstMatch
        XCTAssertTrue(high.waitForExistence(timeout: 3), "the menu of levels opened")
        high.tap()
        XCTAssertEqual(reasoning.value as? String, "High", "the row says the level picked")

        let approvals: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Approvals")).firstMatch
        for _ in 0..<3 where !approvals.isHittable { app.swipeDown() }
        approvals.tap()
        any(app, "chatSettings.approval.yolo").firstMatch.tap()
        app.navigationBars["Approvals"].buttons.element(boundBy: 0).tap()
        XCTAssertTrue(approvals.waitForExistence(timeout: 3))
        XCTAssertTrue(approvals.label.contains("Ask only to pay or send"), approvals.label)
        XCTAssertTrue(app.buttons["chatSettings.done"].isEnabled)
    }

    /// No word of the level is cut, at the default size and at the largest accessibility size: the row and every choice of
    /// its menu lie whole inside the window, one menu item per level with the full word. Shots of each, light and dark,
    /// go to the result bundle for the report.
    func testNoLevelIsCutAtTheDefaultSizeOrAtAX3() {
        for (name, extra) in [
            ("default", [String]()), ("dark", ["-AppleInterfaceStyle", "Dark"]),
            ("ax3", ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]),
        ] {
            let app: XCUIApplication = XCUIApplication()
            app.launchArguments =
                ["-DotNextScene", "n2-model", "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-models.favourites", "()", "-models.hidden", "()"] + extra
            app.launch()
            XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 10), name)
            let window: CGRect = app.windows.firstMatch.frame
            let reasoning: XCUIElement = any(app, "chatSettings.reasoning").firstMatch
            for _ in 0..<6 where !reasoning.isHittable { app.swipeUp() }
            XCTAssertTrue(reasoning.isHittable, "\(name): the level row")
            XCTAssertGreaterThanOrEqual(reasoning.frame.height, 44, name)
            XCTAssertTrue(window.contains(reasoning.frame), "\(name): the row lies inside the window: \(reasoning.frame)")
            let page: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            page.name = "chat-settings-\(name)"
            page.lifetime = .keepAlways
            add(page)
            reasoning.tap()
            for level in ["Low", "Medium", "High", "Extra high", "Max"] {
                let item: XCUIElement = app.buttons[level].firstMatch
                XCTAssertTrue(item.waitForExistence(timeout: 3), "\(name): \(level) in the menu")
                XCTAssertEqual(item.label, level, "\(name): the full word")
                XCTAssertTrue(window.contains(item.frame), "\(name): \(level) lies whole inside the window: \(item.frame)")
            }
            let menu: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            menu.name = "chat-settings-levels-\(name)"
            menu.lifetime = .keepAlways
            add(menu)
            app.terminate()
        }
    }

    /// The design bar (SETTINGS-NATIVE.md): rows of one kind are identical, the sheet's cards keep equal left and right
    /// insets, every tap target is 44 pt or more. Measured from the elements' frames; a difference over 1 pt fails. The
    /// numbers go to the log for the report.
    func testTheSheetMeasuresToTheDesignBar() {
        let app: XCUIApplication = launch("n2-model")
        XCTAssertTrue(app.navigationBars["Chat settings"].waitForExistence(timeout: 10))
        let window: CGRect = app.windows.firstMatch.frame
        let rows: [XCUIElement] = any(app, "modelCatalog.model").allElementsBoundByIndex.filter { $0.isHittable && !$0.label.contains(", Default") }
        XCTAssertGreaterThanOrEqual(rows.count, 2)
        let heights: [CGFloat] = rows.map { $0.frame.height }
        print("design-bar model rows: heights \(heights) minX \(rows.map { $0.frame.minX }) maxX \(rows.map { $0.frame.maxX })")
        XCTAssertLessThanOrEqual((heights.max() ?? 0) - (heights.min() ?? 0), 1, "rows of one kind are identical")
        XCTAssertGreaterThanOrEqual(heights.min() ?? 0, 44)
        let left: CGFloat = (rows.map { $0.frame.minX }.min() ?? 0) - window.minX
        let right: CGFloat = window.maxX - (rows.map { $0.frame.maxX }.max() ?? 0)
        print("design-bar sheet insets: left \(left) right \(right)")
        XCTAssertLessThanOrEqual(abs(left - right), 1, "equal insets left and right")
        let search: XCUIElement = app.textFields["modelCatalog.search"]
        let filter: XCUIElement = app.buttons["modelCatalog.filter"]
        print("design-bar search: \(search.frame) filter \(filter.frame)")
        XCTAssertLessThanOrEqual(abs(search.frame.midY - filter.frame.midY), 1, "the filter and the search field share a centre line")
        XCTAssertGreaterThanOrEqual(filter.frame.height, 44)
    }

    func testTheVoicePageOffersWhereToTranscribeAndTheMicrophonesInOrder() {
        let app: XCUIApplication = launch("settings-voice")
        XCTAssertTrue(app.navigationBars["Voice"].waitForExistence(timeout: 10))
        let device: XCUIElement = any(app, "voice.device").firstMatch
        let mac: XCUIElement = any(app, "voice.mac").firstMatch
        XCTAssertTrue(device.exists)
        if !device.isSelected { device.tap() }
        XCTAssertTrue(device.isSelected, "this phone is a choice")
        mac.tap()
        XCTAssertTrue(mac.isSelected)
        device.tap()
        // The list adds a "Drag" handle of its own to each row: only the rows' own labels are compared.
        let mics: [String] = any(app, "voice.mic").allElementsBoundByIndex.map { $0.label }.filter { $0 != "Drag" }
        XCTAssertEqual(mics.map { $0.components(separatedBy: ",").first ?? "" }, ["Other wired", "Other Bluetooth", "Built-in microphone", "CarPlay"])
    }
}
