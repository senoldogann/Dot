import XCTest

/// The ask dock and the queue's edit on the real chat screen, drawn from the debug build's fixtures (wave N2).
final class ChatUITests: XCTestCase {
    private func launch(_ scene: String) -> XCUIApplication {
        launch(scene, defaults: [])
    }

    /// `defaults` are `UserDefaults` given as arguments (`-key value`), over what an earlier test left on the simulator.
    private func launch(_ scene: String, defaults: [String]) -> XCUIApplication {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", scene, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"] + defaults
        app.launch()
        return app
    }

    @MainActor
    func testThePlusMenuAttachesAVideoWithItsDurationAndCanRemoveIt() throws {
        let clip: URL = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "attachment-sample", withExtension: "mp4"))
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "n2-failed", "-AppleLanguages", "(en)", "-AppleLocale", "en_US",
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXLarge",
        ]
        app.launchEnvironment["DotNextTestVideo"] = try Data(contentsOf: clip).base64EncodedString()
        app.launch()

        let attach: XCUIElement = app.buttons["composer.attach"]
        XCTAssertTrue(attach.waitForExistence(timeout: 10))
        attach.tap()
        let library: XCUIElement = app.descendants(matching: .any).matching(
            NSPredicate(format: "label == %@", "Photos and videos")
        ).firstMatch
        XCTAssertTrue(library.waitForExistence(timeout: 10), app.debugDescription)
        library.tap()
        let duration: XCUIElement = app.staticTexts["composer.videoDuration"]
        XCTAssertTrue(duration.waitForExistence(timeout: 15))
        XCTAssertTrue(duration.label.hasPrefix("0:01"), duration.label)
        app.buttons["Remove Sample.mp4"].tap()
        XCTAssertFalse(duration.exists)
    }

    /// The owner's report of 2026-11-25: the capsule's clock froze between events. The fixture's run gets no event at
    /// all, yet the clock goes on every second; and the capsule says what the run does (here: writing its words).
    func testTheCapsuleClockTicksWithNoEventsAndSaysWhatTheRunDoes() {
        let app: XCUIApplication = launch("n2-chat")
        // Found by its kind (a button): a search of the whole tree is slow to read on a loaded runner.
        let status: XCUIElement = app.buttons["capsule.status"]
        XCTAssertTrue(status.waitForExistence(timeout: 10))
        let first: String = status.label
        // Items 1769 and 1880: the capsule says Working and its clock; what the run does is the live line's.
        XCTAssertTrue(first.hasPrefix("Working, "), "the capsule says the main run works, with its clock: \(first)")
        let second: String = nextLabel(of: status, after: first, within: 2.5)
        XCTAssertNotEqual(second, first, "the clock moved on its own: \(first) -> \(second)")
        let third: String = nextLabel(of: status, after: second, within: 2.5)
        XCTAssertNotEqual(third, second, "and again a second later: \(second) -> \(third)")
    }

    /// The element's label once it differs from `label`, read every 100 ms (an `XCTNSPredicateExpectation` reads it about
    /// once a second, so a tick could be seen a second late, past the wait on a slow runner); the last label read when
    /// nothing changed within `seconds`.
    private func nextLabel(of element: XCUIElement, after label: String, within seconds: TimeInterval) -> String {
        var now: String = element.label
        _ = holds(within: seconds) {
            now = element.label
            return now != label
        }
        return now
    }

    /// The owner's order of 2026-11-25: a long press on the composer's button while a run works opens Steer now and
    /// Queue, the follow-up setting's way (Queue) checked; each pick sends the draft. No Stop in the chat's header.
    func testALongPressOnSendOffersSteerNowAndQueue() {
        let app: XCUIApplication = launch("n2-chat", defaults: ["-followUp", "queue"])
        let field: XCUIElement = app.descendants(matching: .any).matching(identifier: "composer.field").firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        XCTAssertFalse(app.navigationBars.buttons["Stop"].exists, "no Stop in the chat's header")
        for (way, words) in [("Steer now", "Look at the logs first"), ("Queue", "Then book the car service")] {
            field.tap()
            field.typeText(words)
            let send: XCUIElement = app.buttons["composer.send"]
            XCTAssertEqual(send.label, "Queue: starts after this run", "a tap queues, as the setting says")
            send.press(forDuration: 1.2)
            // The menu's subtitles join the title in the label on some iOS versions; the shots show them.
            let steer: XCUIElement = app.buttons["composer.followUp.steer"]
            let queue: XCUIElement = app.buttons["composer.followUp.queue"]
            XCTAssertTrue(steer.waitForExistence(timeout: 3), "the menu opened")
            XCTAssertTrue(steer.label.hasPrefix("Steer now"), "the title, with its subtitle where the tree has it: \(steer.label)")
            XCTAssertTrue(queue.label.hasPrefix("Queue"), "the title, with its subtitle where the tree has it: \(queue.label)")
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "composer-follow-up-menu"
            shot.lifetime = .keepAlways
            add(shot)
            XCTAssertTrue(queue.isSelected, "the setting's way is checked")
            XCTAssertFalse(steer.isSelected)
            (way == "Steer now" ? steer : queue).tap()
            // Polled, the field read afresh each time: on GitHub's runner a predicate expectation on the field kept
            // seeing the draft long after the field was empty (item 1903).
            let deadline: Date = Date().addingTimeInterval(15)
            while ((field.value as? String) ?? "").contains(words), Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.25)) }
            XCTAssertFalse(((field.value as? String) ?? "").contains(words), "\(way) sent the draft: \(String(describing: field.value))")
        }
    }

    /// Item 1880: the plan is a segment of the one working pill, never a card in the stream; a tap opens its checklist.
    /// The design bar: the pill's segments stand on one line, as tall as each other, centred on the same y, 44 pt tall.
    func testThePlanIsAChipBesideTheCapsuleThatOpensItsChecklist() {
        let app: XCUIApplication = launch("n2-chat")
        let chip: XCUIElement = app.buttons["capsule.plan"]
        XCTAssertTrue(chip.waitForExistence(timeout: 10))
        XCTAssertEqual(chip.label, "Plan, 1 of 3 steps done")
        XCTAssertFalse(app.staticTexts["Plan · 1 of 3 done"].exists, "no plan card in the stream")
        let status: XCUIElement = app.descendants(matching: .any).matching(identifier: "capsule.status").firstMatch
        let stop: XCUIElement = app.buttons["capsule.stop"]
        let people: XCUIElement = app.buttons["capsule.helpers"]
        XCTAssertTrue(status.exists && stop.exists && people.exists)
        let segments: [XCUIElement] = [status, stop, chip, people]
        let measured: String = segments.map { "\($0.identifier) \($0.frame)" }.joined(separator: "; ") + "; window \(app.windows.firstMatch.frame)"
        add(XCTAttachment(string: measured))
        for segment in segments {
            XCTAssertEqual(segment.frame.midY, status.frame.midY, accuracy: 1, measured)
            XCTAssertEqual(segment.frame.height, status.frame.height, accuracy: 1, measured)
            XCTAssertGreaterThanOrEqual(segment.frame.height, 44, measured)
        }
        XCTAssertLessThan(status.frame.maxX, stop.frame.minX + 1, "one line, in order: \(measured)")
        XCTAssertLessThan(chip.frame.maxX, people.frame.minX + 1, "one line, in order: \(measured)")
        XCTAssertLessThanOrEqual(people.frame.maxX, app.windows.firstMatch.frame.maxX, "inside the screen: \(measured)")
        let bar: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        bar.name = "plan-chip"
        bar.lifetime = .keepAlways
        add(bar)
        chip.tap()
        XCTAssertTrue(app.staticTexts["Plan · 1 of 3 done"].waitForExistence(timeout: 5), "the checklist in a sheet")
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "Find free afternoon slots at the dentist")).firstMatch.exists)
        let sheet: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        sheet.name = "plan-sheet"
        sheet.lifetime = .keepAlways
        add(sheet)
    }

    /// The owner, 2026-11-26: an answer's links and files show in its words, not listed under it. A GitHub pull
    /// request reads `#1012` after the GitHub mark, a link keeps the words it was written with, a file is its name
    /// after its kind's symbol; a tap on the file opens its preview sheet with Open on Mac.
    func testAnAnswersLinksAndFilesShowInItsWordsAndOpenInPlace() {
        let app: XCUIApplication = launch("n2-chat")
        let file: XCUIElement = app.links["ci-notes.md"]
        for _ in 0..<8 where !(file.exists && file.isHittable) {
            app.swipeDown()
        }
        XCTAssertTrue(file.waitForExistence(timeout: 5))
        XCTAssertTrue(app.links["#1012"].exists, "the pull request by its number")
        XCTAssertTrue(app.links["Node's release notes"].exists, "a written link keeps its words")
        XCTAssertFalse(
            app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "https://github.com/example")).firstMatch.exists, "no bare address in the words")
        XCTAssertFalse(app.buttons["answer.sources"].exists || app.buttons["answer.file"].exists, "nothing listed under the answer")
        let words: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        words.name = "answer-inline-references"
        words.lifetime = .keepAlways
        add(words)
        file.tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Node 22 is pinned on CI.")).firstMatch.waitForExistence(timeout: 5))
        let sheet: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        sheet.name = "answer-file-sheet"
        sheet.lifetime = .keepAlways
        add(sheet)
        app.buttons["file.openOnMac"].tap()
        XCTAssertTrue(app.staticTexts["Opened on your Mac."].waitForExistence(timeout: 5))
    }

    /// The owner, 2026-11-26: a table's wrapped cells drew over the next row. Each row is as tall as its tallest
    /// cell, the long column wraps so the table fits the reading column, and it does so at the largest text size too.
    func testATablesLongCellsWrapInsideTheirRows() {
        for size in ["UICTContentSizeCategoryL", "UICTContentSizeCategoryAccessibilityXXXL"] {
            let app: XCUIApplication = launch("n2-chat", defaults: ["-UIPreferredContentSizeCategoryName", size])
            let table: XCUIElement = app.descendants(matching: .any).matching(identifier: "answer.table").firstMatch
            for _ in 0..<12 where !(table.exists && table.isHittable) {
                app.swipeDown()
            }
            XCTAssertTrue(table.waitForExistence(timeout: 5), size)
            let long: XCUIElement = table.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Checks a lost invention")).firstMatch
            let next: XCUIElement = table.staticTexts["Story Scripter"]
            let first: XCUIElement = table.staticTexts["Invention Scout"]
            XCTAssertTrue(long.exists && next.exists && first.exists, size)
            let measured: String = "\(size): table \(table.frame) long \(long.frame) first \(first.frame) next \(next.frame) window \(app.windows.firstMatch.frame)"
            add(XCTAttachment(string: measured))
            XCTAssertGreaterThan(long.frame.height, first.frame.height * 1.5, "the long cell wraps: \(measured)")
            XCTAssertLessThanOrEqual(long.frame.maxY, next.frame.minY + 1, "no cell draws over the next row: \(measured)")
            XCTAssertEqual(long.frame.minY, first.frame.minY, accuracy: 1, "a row's cells share its top: \(measured)")
            if size == "UICTContentSizeCategoryL" {
                XCTAssertLessThanOrEqual(table.frame.maxX, app.windows.firstMatch.frame.maxX - 15, "the table fits the column: \(measured)")
            }
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "answer-table-\(size)"
            shot.lifetime = .keepAlways
            add(shot)
            app.terminate()
        }
    }

    /// Item 1880: the helpers are no card in the stream; the capsule's people segment opens the Agents sheet, where a
    /// working helper's row says what it does in plain words beside its state, clock and model.
    func testTheHelpersLiveBehindTheCapsuleNotInTheStream() {
        let app: XCUIApplication = launch("n2-chat")
        XCTAssertTrue(app.buttons["capsule.plan"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "helpers ·")).firstMatch.exists, "no helper card in the stream")
        let people: XCUIElement = app.buttons["capsule.helpers"]
        XCTAssertEqual(people.label, "Helpers, 1 of 2 done")
        people.tap()
        XCTAssertTrue(app.navigationBars["Agents"].waitForExistence(timeout: 5))
        // The sheet's row (it names the helper's model), not the helper's row in the work log under the sheet (item
        // 1909), which no tap reaches.
        let row: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS %@", "Check the dentist", "Opus")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        XCTAssertTrue(row.label.contains("Working"), row.label)
        XCTAssertFalse(row.label.contains("browser_open"), "no raw tool name: \(row.label)")
        // A tap opens the helper's own chat, which shows the helper's run (its parent run lives in another chat).
        row.tap()
        XCTAssertTrue(app.staticTexts["The booking page lists two free afternoons next week."].waitForExistence(timeout: 5), "the helper's chat shows its words")
        let sheet: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        sheet.name = "helper-chat"
        sheet.lifetime = .keepAlways
        add(sheet)
    }

    /// Item 1769: with the steps hidden (Hide all, the default), one live line under the last message says what the run
    /// does; a tap shows the steps and a second folds them back.
    func testTheLiveLineSaysTheWorkAndATapShowsTheSteps() {
        let app: XCUIApplication = launch("n2-asks", defaults: ["-toolCallMode", "hideAll"])
        let line: XCUIElement = app.buttons["work.line"]
        XCTAssertTrue(line.waitForExistence(timeout: 10))
        XCTAssertEqual(line.label, "Waiting for you")
        XCTAssertGreaterThanOrEqual(line.frame.height, 44 - 0.01, "the line is a button: a 44 pt tap target (the design bar; a frame comes out as 43.99999 on this simulator)")
        // The run's calls, one line in plain words since item 1909 ("Started 1 helper and opened 1 page").
        let step: XCUIElement = app.buttons["tools.group"].firstMatch
        XCTAssertFalse(step.exists, "the steps are hidden")
        line.tap()
        XCTAssertTrue(step.waitForExistence(timeout: 3), "a tap shows the steps")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "work-line-steps"
        shot.lifetime = .keepAlways
        add(shot)
        line.tap()
        let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: step)
        XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 3), .completed, "a second tap folds them back")
    }

    /// Item 1904: a failed run ends in one plain row, "The run failed · <reason>" (with Continue and Retry as text
    /// buttons on it when offered); the finished run's fold says how long it worked, no counts.
    func testAFailedRunIsOnePlainRowAndTheFoldSaysOnlyHowLong() {
        let app: XCUIApplication = launch("n2-failed")
        let failed: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "The run failed · The model answered HTTP 529")).firstMatch
        XCTAssertTrue(failed.waitForExistence(timeout: 10))
        // The fixture's run offers no Continue or Retry (its chat has no newest detail); the row is its line alone.
        XCTAssertFalse(app.buttons["end.retry"].exists)
        let fold: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "Worked for")).firstMatch
        XCTAssertTrue(fold.waitForExistence(timeout: 5))
        XCTAssertFalse(fold.label.contains("·"), "no counts: \(fold.label)")
        // A stored step's thinking is one folded row, "Thought" and the chevron; its words show once opened. The failed
        // run's steps are folded: open its fold first.
        let folds: [XCUIElement] = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "Worked for")).allElementsBoundByIndex
        folds.last?.tap()
        let thought: XCUIElement = app.buttons["thought.row"]
        XCTAssertTrue(thought.waitForExistence(timeout: 5))
        XCTAssertEqual(thought.label, "Thought")
        let words: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Compare the calendar")).firstMatch
        XCTAssertFalse(words.exists, "no sentence of the thinking while folded")
        thought.tap()
        XCTAssertTrue(words.waitForExistence(timeout: 3), "the thinking opened")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "failed-row"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Item 1909, after another coding agent's work log: consecutive tool calls are one line in plain words, each helper
    /// is its own row (title, what it does, its time, a chevron), and while the run waits one quiet line says on what,
    /// with Details and no Stop of its own (the pill has it).
    func testTheWorkLogGroupsToolsShowsHelpersAndSaysWhatItWaitsOn() {
        // The queue's fixture: its run waits in `wait_until` and writes nothing meanwhile. It opens its queue's sheet:
        // closed first, so the taps reach the chat.
        let app: XCUIApplication = launch("n2-queue")
        if app.navigationBars["Queued"].waitForExistence(timeout: 5) { app.buttons["Close"].firstMatch.tap() }
        let helper: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier == %@ AND label BEGINSWITH %@", "helper.row", "Check the dentist")).firstMatch
        XCTAssertTrue(helper.waitForExistence(timeout: 10), "a helper is its own row")
        XCTAssertGreaterThanOrEqual(helper.frame.height, 44)
        // The line stands at the end of the stream: scroll to it (the stream draws only what is near the screen).
        let waiting: XCUIElement = app.staticTexts["waiting.line"]
        for _ in 0..<4 where !waiting.exists { app.swipeUp() }
        XCTAssertTrue(waiting.waitForExistence(timeout: 5))
        XCTAssertTrue(waiting.label.hasPrefix("Waiting · "), waiting.label)
        XCTAssertTrue(app.buttons["waiting.details"].exists)
        let shotWaiting: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shotWaiting.name = "work-log-waiting"
        shotWaiting.lifetime = .keepAlways
        add(shotWaiting)
        for _ in 0..<4 { app.swipeDown() }
        // The finished run is folded: open its fold.
        let fold: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Worked for")).firstMatch
        XCTAssertTrue(fold.waitForExistence(timeout: 5))
        fold.tap()
        let group: XCUIElement = app.buttons["tools.group"].firstMatch
        XCTAssertTrue(group.waitForExistence(timeout: 5), "the finished run's calls as one line")
        XCTAssertEqual(group.label, "Read 2 files and ran 2 commands")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "work-log"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Item 1915: "Show steps" off in the chat's This chat sheet leaves only the messages: the step rows go, and one live
    /// line under the last text says what the run does.
    func testShowStepsOffLeavesTheMessagesAndOneLiveLine() {
        let app: XCUIApplication = XCUIApplication()
        // The queue's fixture (its run waits in `wait_until`); its queue's sheet is closed first.
        app.launchArguments = ["-DotNextScene", "n2-queue", "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-toolCallMode", "showAll", "-showSteps", "YES", "-stepsOverrides", "{}"]
        app.launch()
        if app.navigationBars["Queued"].waitForExistence(timeout: 5) { app.buttons["Close"].firstMatch.tap() }
        let thought: XCUIElement = app.buttons["thought.row"]
        for _ in 0..<4 where !thought.exists { app.swipeUp() }
        XCTAssertTrue(thought.waitForExistence(timeout: 10), "the steps show first")
        app.buttons["Changes and files"].tap()
        let toggle: XCUIElement = app.switches["inspector.showSteps"]
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        XCTAssertEqual(toggle.value as? String, "1")
        toggle.switches.firstMatch.exists ? toggle.switches.firstMatch.tap() : toggle.tap()
        XCTAssertEqual(toggle.value as? String, "0")
        app.buttons["Close"].firstMatch.tap()
        // The run waits in `wait_until`: its one line is the waiting line (item 1909), the live line's place.
        let line: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "identifier IN %@", ["work.line", "waiting.line"])).firstMatch
        for _ in 0..<4 where !line.exists { app.swipeUp() }
        XCTAssertTrue(line.waitForExistence(timeout: 5), "one live line says the work")
        XCTAssertFalse(app.buttons["thought.row"].exists, "no step rows")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "messages-only"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Item 1932: the asks scene settles: once it stood still for a few seconds, the screen is not drawn again (its
    /// scroll followed the lazy stack's own height estimates in a loop, at 100 % CPU).
    func testTheAsksSceneSettles() {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = ["-DotNextScene", "n2-asks", "-AppleLanguages", "(en)", "-AppleLocale", "en_US", "-DotNextRenderMeter", "1"]
        app.launch()
        let meter: XCUIElement = app.staticTexts["debug.renders"]
        XCTAssertTrue(meter.waitForExistence(timeout: 15))
        sleep(4)
        let settled: String = meter.label
        sleep(4)
        XCTAssertEqual(meter.label, settled, "drawn again with nothing changing: \(settled) -> \(meter.label)")
    }

    func testTheDockShowsEachAskInTurnAndFoldsBackToTheComposer() {
        let app: XCUIApplication = launch("n2-asks")
        XCTAssertTrue(app.staticTexts["Your answer is needed"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["1 of 2"].exists, "a count for several asks")
        XCTAssertTrue(app.buttons["Allow once"].exists)
        XCTAssertTrue(app.buttons["Always allow in this chat"].exists)
        XCTAssertFalse(app.textFields["composer.field"].exists, "the dock stands in the composer's place")
        app.buttons["Next ask"].tap()
        XCTAssertTrue(app.staticTexts["2 of 2"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Which day suits you for the dentist?"].exists)
        XCTAssertTrue(app.buttons["Stop"].exists, "Stop stays reachable")
        app.buttons["Fold what Dot asked"].tap()
        XCTAssertTrue(app.textFields["composer.field"].waitForExistence(timeout: 3), "folded, the composer is back")
    }

    func testQuestionsArePickedAndSentWithOneSubmit() {
        let app: XCUIApplication = launch("n2-question")
        XCTAssertTrue(app.staticTexts["Fill in the pending answers"].waitForExistence(timeout: 10))
        let submit: XCUIElement = app.buttons["ask.submit"]
        XCTAssertFalse(submit.isEnabled, "nothing picked yet")
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Close both (Recommended)")).firstMatch.tap()
        XCTAssertTrue(submit.isEnabled)
        XCTAssertEqual(submit.label, "Submit answer")
        app.buttons["Next ask"].tap()
        let tuesday: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Tuesday afternoon (Recommended)")).firstMatch
        XCTAssertTrue(tuesday.waitForExistence(timeout: 3))
        tuesday.tap()
        XCTAssertEqual(submit.label, "Submit 2 answers", "the pick of the first question is kept while paging")
    }

    func testSlashOpensTheCommandsPanelFilteredAsTheOwnerTypes() {
        let app: XCUIApplication = launch("n2-chat")
        let atRest: XCUIElement = app.textFields["composer.field"]
        XCTAssertTrue(atRest.waitForExistence(timeout: 10))
        atRest.tap()
        // Focused, the composer grows and its field may become a text view: found by its kind, never by a search of the
        // whole tree (`descendants(matching: .any)`), whose every read took seconds on a loaded CI runner (#1105).
        composerField(app).typeText("/")
        XCTAssertTrue(app.staticTexts["COMMANDS"].waitForExistence(timeout: 3) || app.staticTexts["Commands"].exists)
        let rows: XCUIElementQuery = app.buttons.matching(identifier: "picker.row")
        XCTAssertTrue(holds(within: 5) { rows.count == 6 }, "six commands: \(rows.count)")
        // A row puts its token in the text (tapped on the bare "/": XCUITest's typed letters stay as the keyboard's
        // candidate, which a tap does not replace; a hand-typed word is replaced, seen on the simulator).
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "/loop")).firstMatch.tap()
        // Read every 100 ms: the predicate expectation read the value about once a second, and on the runner of #1105
        // the token was in the text ("/loop ") when the 3 s wait had already given up.
        XCTAssertTrue(holds(within: 5) { (composerField(app).value as? String)?.hasPrefix("/loop") == true }, "the row puts its token in the text: \(String(describing: composerField(app).value))")
        composerField(app).typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 3))
        // The panel filters once the deletions are in the text: waited for, not read at once.
        XCTAssertTrue(holds(within: 5) { rows.count == 1 }, "\"/lo\" leaves only /loop: \(rows.count)")
    }

    /// The composer's field, a text view while the composer is grown and a text field at rest.
    private func composerField(_ app: XCUIApplication) -> XCUIElement {
        let grown: XCUIElement = app.textViews["composer.field"]
        return grown.exists ? grown : app.textFields["composer.field"]
    }

    /// Whether `condition` holds within `seconds`, read every 100 ms: as soon as it holds, not on a fixed pause.
    private func holds(within seconds: TimeInterval, _ condition: () -> Bool) -> Bool {
        let deadline: Date = Date().addingTimeInterval(seconds)
        while !condition() {
            if Date() >= deadline { return false }
            usleep(100_000)
        }
        return true
    }

    func testEditingAQueuedMessageKeepsTheLiveDraftForCancel() {
        let app: XCUIApplication = launch("n2-editing")
        XCTAssertTrue(app.staticTexts["Editing queued message"].waitForExistence(timeout: 10))
        let field: XCUIElement = app.textFields["composer.field"]
        XCTAssertEqual(field.value as? String, "Connect everything to real data, as a Grok-like assistant would, and keep the tool calls visible")
        XCTAssertEqual(app.buttons["composer.send"].label, "Save the queued message", "a ✓ in place of Send")
        app.buttons["composer.cancelEdit"].tap()
        XCTAssertFalse(app.staticTexts["Editing queued message"].waitForExistence(timeout: 2))
        XCTAssertEqual(field.value as? String, "And book the car service too", "Cancel puts the live draft back")
    }

    func testTheQueueSheetOpensAQueuedMessageInTheComposer() {
        let app: XCUIApplication = launch("n2-queue")
        XCTAssertTrue(app.navigationBars["Queued"].waitForExistence(timeout: 10))
        let rows: XCUIElementQuery = app.buttons.matching(identifier: "queue.row")
        XCTAssertEqual(rows.count, 2)
        rows.element(boundBy: 1).tap()
        XCTAssertTrue(app.staticTexts["Editing queued message"].waitForExistence(timeout: 3))
        XCTAssertEqual(app.textFields["composer.field"].value as? String, "Then send me the screenshots of the new designs")
    }
}
