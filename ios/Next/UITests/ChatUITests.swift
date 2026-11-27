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

    /// Swipe to quote (item 1972), on the fixtures: the owner's message pulled to the right puts its first line in the
    /// composer as a chip with a cross; a short pull, or one that starts at the screen's edge, quotes nothing; the cross drops
    /// the chip. At the largest accessibility size too.
    @MainActor
    func testASwipeOnAMessageQuotesItAndTheCrossDropsTheQuote() {
        for extra in [[String](), ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXLarge"]] {
            // A scene with nothing writing: a chat whose rows move under the finger is no place to measure a swipe.
            let app: XCUIApplication = launch("n2-failed", defaults: extra)
            let message: XCUIElement = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "You: Book me the dentist")).firstMatch
            XCTAssertTrue(message.waitForExistence(timeout: 10))
            let origin: XCUICoordinate = app.coordinate(withNormalizedOffset: .zero)
            func drag(from x: CGFloat, by dx: CGFloat) {
                let start: CGVector = CGVector(dx: x, dy: message.frame.midY)
                origin.withOffset(start).press(forDuration: 0.1, thenDragTo: origin.withOffset(CGVector(dx: x + dx, dy: start.dy)))
            }
            let chip: XCUIElement = app.descendants(matching: .any)["composer.quote"]
            drag(from: message.frame.midX, by: 30)
            XCTAssertFalse(chip.exists, "a short pull is no quote")
            drag(from: 10, by: 160)
            XCTAssertFalse(chip.exists, "a pull from the screen's edge is the back gesture's")
            drag(from: message.frame.midX - 40, by: 160)
            XCTAssertTrue(chip.waitForExistence(timeout: 10), "the swipe put the message in the composer as a quote")
            XCTAssertTrue(chip.label.contains("Book me the dentist"), chip.label)
            let remove: XCUIElement = app.buttons["composer.quote.remove"]
            XCTAssertGreaterThanOrEqual(remove.frame.height, 44)
            XCTAssertGreaterThanOrEqual(remove.frame.width, 44)
            let attachment: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = extra.isEmpty ? "quote-chip" : "quote-chip-ax3"
            attachment.lifetime = .keepAlways
            add(attachment)
            remove.tap()
            XCTAssertFalse(chip.exists, "the cross drops the quote")
            app.terminate()
        }
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
        // The video stands as a tile (its first frame, the length at its foot, a × at its corner), item 1992.
        XCTAssertTrue(app.otherElements["composer.videoTile"].waitForExistence(timeout: 15), app.debugDescription)
        let duration: XCUIElement = app.staticTexts["tile.duration"]
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

    /// The owner's report of 2026-11-27: the capsule says "Waiting on N helpers" only while the main run has stopped for
    /// them (`n2-helpers`: its step's one open call is a delegate); while it works beside its helper (`n2-chat`) it says
    /// "Working". No spinner on either, and the status takes only its words' width, so that Stop, the plan and the helpers
    /// keep their room.
    func testTheCapsuleSaysWaitingOnlyWhileTheRunWaitsAndSpinsNothing() {
        for (scene, words) in [("n2-helpers", "Waiting on 1 helper"), ("n2-chat", "Working, ")] {
            let app: XCUIApplication = launch(scene)
            let status: XCUIElement = app.buttons["capsule.status"]
            XCTAssertTrue(status.waitForExistence(timeout: 10), scene)
            XCTAssertTrue(status.label.hasPrefix(words), "\(scene): \(status.label)")
            let stop: XCUIElement = app.buttons["capsule.stop"]
            let people: XCUIElement = app.buttons["capsule.helpers"]
            XCTAssertTrue(stop.exists && people.exists, scene)
            let spinners: [XCUIElement] = app.activityIndicators.allElementsBoundByIndex.filter { $0.frame.intersects(status.frame) }
            XCTAssertTrue(spinners.isEmpty, "\(scene): no spinner in the capsule: \(spinners.map { $0.frame })")
            print("capsule \(scene): status \(status.frame) stop \(stop.frame) helpers \(people.frame)")
            XCTAssertLessThanOrEqual(status.frame.width, 180, "\(scene): the status keeps to its words: \(status.frame)")
            XCTAssertLessThanOrEqual(stop.frame.minX - status.frame.maxX, 2, "\(scene): Stop follows at once: \(status.frame) \(stop.frame)")
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "capsule-\(scene)"
            shot.lifetime = .keepAlways
            add(shot)
            app.terminate()
        }
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
        let row: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS[c] %@", "Check the dentist", "Opus")).firstMatch
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

    /// Owner 2026-11-27: an empty state is in the app's own type, so Settings › Appearance's text size reaches it (the
    /// system's empty-state view kept its own larger title), and its glyph is still (no dotted circle like a spinner).
    func testAnEmptyStateFollowsTheChosenTextSize() {
        var heights: [CGFloat] = []
        for size in ["xSmall", "xxxLarge"] {
            let app: XCUIApplication = launch("n2-offline", defaults: ["-textSize", size])
            let title: XCUIElement = app.staticTexts["Can't reach your Mac"]
            XCTAssertTrue(title.waitForExistence(timeout: 10))
            heights.append(title.frame.height)
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "empty-state-\(size)"
            shot.lifetime = .keepAlways
            add(shot)
            app.terminate()
        }
        XCTAssertGreaterThan(heights[1], heights[0] * 1.3, "the title grows with the chosen text size: \(heights)")
    }

    /// bubble; a tap unfolds the whole message. At AX3 too, where the title may wrap and nothing is cut off the screen.
    func testARoutinesMessageIsOneCompactRowThatUnfoldsItsWords() {
        for extra in [[String](), ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXLarge"]] {
            let app: XCUIApplication = launch("n2-routine", defaults: ["-showSteps", "NO", "-stepsOverrides", "{}"] + extra)
            let row: XCUIElement = app.buttons["routine.row"]
            XCTAssertTrue(row.waitForExistence(timeout: 10))
            XCTAssertEqual(row.label, "Routine: Site build check")
            XCTAssertFalse(app.staticTexts["You: Find out why the site's build fails on CI and fix it"].exists, "no owner's bubble")
            XCTAssertGreaterThanOrEqual(row.frame.height, 44 - 0.01)
            XCTAssertLessThanOrEqual(row.frame.maxX, app.windows.firstMatch.frame.maxX, "\(row.frame)")
            row.tap()
            let unfolded: XCTNSPredicateExpectation = XCTNSPredicateExpectation(
                predicate: NSPredicate(format: "value == %@", "Find out why the site's build fails on CI and fix it"), object: row)
            XCTAssertEqual(XCTWaiter.wait(for: [unfolded], timeout: 3), .completed, "a tap shows the whole message: \(String(describing: row.value))")
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "routine-row-\(extra.isEmpty ? "L" : "AX3")"
            shot.lifetime = .keepAlways
            add(shot)
            app.terminate()
        }
    }

    /// sheet), with ✕; the helper's chat shows its steps as the chat that started it does unless it chose itself.
    func testAHelperOfTheStreamOpensAsASheetAndShowsItsStepsAsItsParent() {
        for parentShows in [false, true] {
            let overrides: String = "<dict><key>thr_fixture</key><\(parentShows)/></dict>"
            let app: XCUIApplication = launch("n2-chat", defaults: ["-toolCallMode", "showAll", "-showSteps", "YES", "-stepsOverrides", overrides])
            let group: XCUIElement = app.buttons["helpers.group"]
            XCTAssertTrue(group.waitForExistence(timeout: 10))
            group.tap()
            let row: XCUIElement = app.buttons.matching(identifier: "helper.row").matching(NSPredicate(format: "label BEGINSWITH %@", "Check the dentist")).firstMatch
            XCTAssertTrue(row.waitForExistence(timeout: 5))
            row.tap()
            let close: XCUIElement = app.buttons["helperChat.close"]
            XCTAssertTrue(close.waitForExistence(timeout: 5), "the helper's chat is a sheet with ✕, not a pushed chat")
            XCTAssertTrue(app.staticTexts["The booking page lists two free afternoons next week."].waitForExistence(timeout: 5))
            let step: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "dentist.example")).firstMatch
            XCTAssertEqual(step.waitForExistence(timeout: 2), parentShows, "the helper's steps follow its parent's Show steps (\(parentShows))")
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "helper-sheet-steps-\(parentShows)"
            shot.lifetime = .keepAlways
            add(shot)
            close.tap()
            let gone: XCTNSPredicateExpectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: close)
            XCTAssertEqual(XCTWaiter.wait(for: [gone], timeout: 3), .completed, "✕ closes the sheet")
            app.terminate()
        }
    }

    /// Item 1769: with the steps hidden (Hide all, the default), one live line under the last message says what the run
    /// Item 1769: with the steps hidden (Hide all, the default), one live line under the last message says what the run
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
        // The helpers are one row (item 1957): its card lists each.
        let helpers: XCUIElement = app.buttons["helpers.group"]
        XCTAssertTrue(helpers.waitForExistence(timeout: 10), "the helpers are one row")
        helpers.tap()
        let helper: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier == %@ AND label BEGINSWITH %@", "helper.row", "Check the dentist")).firstMatch
        XCTAssertTrue(helper.waitForExistence(timeout: 5), "each helper a row of the card")
        XCTAssertGreaterThanOrEqual(helper.frame.height.rounded(), 44)
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

    /// Item 1957: the run's helpers are one row, "2 helpers" over "1 working · 1 done", with their time and a chevron; a
    /// tap opens a card with a row per helper (its state and its engine in words), whose tap opens the helper's chat.
    func testTheHelpersAreOneRowThatOpensACardOfEach() {
        let app: XCUIApplication = launch("n2-chat")
        let group: XCUIElement = app.buttons["helpers.group"]
        for _ in 0..<4 where !group.exists { app.swipeUp() }
        XCTAssertTrue(group.waitForExistence(timeout: 10))
        XCTAssertEqual(group.label, "2 helpers, 1 working, 1 done")
        XCTAssertEqual(group.value as? String, "Collapsed")
        XCTAssertGreaterThanOrEqual(group.frame.height.rounded(), 44)
        XCTAssertFalse(app.buttons["helper.row"].exists, "no row per helper while folded")
        let folded: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        folded.name = "helpers-collapsed"
        folded.lifetime = .keepAlways
        add(folded)
        group.tap()
        let rows: XCUIElementQuery = app.buttons.matching(identifier: "helper.row")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 3))
        XCTAssertEqual(rows.count, 2)
        let working: XCUIElement = rows.matching(NSPredicate(format: "label BEGINSWITH %@", "Check the dentist")).firstMatch
        XCTAssertTrue(working.label.contains("Working"), working.label)
        XCTAssertTrue(working.label.contains("Claude Code"), "the engine in words: \(working.label)")
        let open: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        open.name = "helpers-expanded"
        open.lifetime = .keepAlways
        add(open)
        working.tap()
        XCTAssertTrue(app.staticTexts["The booking page lists two free afternoons next week."].waitForExistence(timeout: 5), "the helper's chat")
    }

    /// Item 1957: the open group at AX3 in Turkish and dark: the words wrap, nothing is cut but a title, every row keeps
    /// its 44 pt.
    func testTheHelpersGroupHoldsAtAX3InTurkishAndDark() {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "n2-chat", "-AppleLanguages", "(tr)", "-AppleLocale", "tr_TR", "-colorScheme", "dark",
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL",
        ]
        app.launch()
        let group: XCUIElement = app.buttons["helpers.group"]
        // Brought to the middle of the screen by its frame, in short drags: `isHittable` of a row out of sight fails the
        // test on GitHub's runner ("Activation point invalid"), and at AX sizes a row near an edge sits under the
        // navigation bar or the composer, where a tap does not reach it.
        let window: CGRect = app.windows.firstMatch.frame
        let middle: ClosedRange<CGFloat> = (window.minY + window.height * 0.25)...(window.minY + window.height * 0.6)
        let centre: XCUICoordinate = app.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        for _ in 0..<16 where !(group.exists && middle.contains(group.frame.midY)) {
            let down: Bool = group.exists && group.frame.midY < middle.lowerBound
            centre.press(forDuration: 0.05, thenDragTo: centre.withOffset(CGVector(dx: 0, dy: down ? 220 : -220)))
        }
        XCTAssertTrue(group.waitForExistence(timeout: 10))
        XCTAssertTrue(group.label.hasPrefix("2 yardımcı"), group.label)
        group.tap()
        let rows: XCUIElementQuery = app.buttons.matching(identifier: "helper.row")
        XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: 3))
        for index in 0..<rows.count { XCTAssertGreaterThanOrEqual(rows.element(boundBy: index).frame.height.rounded(), 44) }
        app.swipeUp()
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "helpers-ax3-tr-dark"
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
        // The sections of This chat start folded (item 2017).
        let messages: XCUIElement = app.buttons.matching(NSPredicate(format: "identifier == 'inspector.section' AND label BEGINSWITH 'Messages,'")).firstMatch
        XCTAssertTrue(messages.waitForExistence(timeout: 5))
        messages.tap()
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

    /// Item 1996, the owner's shot of 2026-11-27: with Show steps off, no step row shows, a failed one neither, while a
    /// run works and once it ended, and after the chat is opened again: a finished run with a failed step stays folded
    /// in its one "Worked for" line, and the failure that ended it is its end line.
    func testShowStepsOffKeepsEveryStepFoldedAFailedOneToo() {
        let off: [String] = ["-showSteps", "NO", "-stepsOverrides", "{}", "-toolCallMode", "showAll"]
        let working: XCUIApplication = launch("n2-chat", defaults: off)
        XCTAssertTrue(
            working.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "Tuesday and Thursday afternoons")).firstMatch
                .waitForExistence(timeout: 15))
        for _ in 0..<3 { working.swipeUp() }
        XCTAssertFalse(working.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "icalbuddy")).firstMatch.exists, "a failed step of a working run is no row")
        working.terminate()
        for pass in ["ended", "reopened"] {
            let app: XCUIApplication = launch("n2-failed", defaults: off)
            let end: XCUIElement = app.descendants(matching: .any).matching(identifier: "end.line").firstMatch
            for _ in 0..<4 where !end.exists { app.swipeUp() }
            XCTAssertTrue(end.waitForExistence(timeout: 15), "\(pass): the failure that ended the run is its end line")
            let folds: XCUIElementQuery = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Worked for"))
            XCTAssertGreaterThanOrEqual(folds.count, 1, "\(pass): the steps are behind Worked for")
            for index in 0..<folds.count {
                XCTAssertEqual(folds.element(boundBy: index).value as? String, "Collapsed", "\(pass): every fold starts folded")
            }
            XCTAssertFalse(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "icalbuddy")).firstMatch.exists, "\(pass): no failed step row")
            XCTAssertFalse(app.buttons["thought.row"].exists, "\(pass): no thought row")
            let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
            shot.name = "messages-only-\(pass)"
            shot.lifetime = .keepAlways
            add(shot)
            app.terminate()
        }
    }

    /// Item 1996, the owner's shot of 2026-11-27: an answer the finish said again stands once, with no "Finish" row, in
    /// every tool mode and with Show steps off.
    func testAnAnswerTheFinishSaysAgainStandsOnce() {
        for defaults in [["-toolCallMode", "showAll", "-showSteps", "YES", "-stepsOverrides", "{}"], ["-showSteps", "NO", "-stepsOverrides", "{}"]] {
            let app: XCUIApplication = launch("n2-restated", defaults: defaults)
            let first: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "Tuesday at 14:00 with Dr. Lind")).firstMatch
            for _ in 0..<4 where !first.exists { app.swipeUp() }
            XCTAssertTrue(first.waitForExistence(timeout: 15), "\(defaults): the answer")
            for _ in 0..<3 { app.swipeUp() }
            XCTAssertEqual(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Tuesday at 14:00 with Dr. Lind")).count, 1, "\(defaults): the answer once")
            XCTAssertFalse(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Finish")).firstMatch.exists, "\(defaults): no Finish row")
            app.terminate()
        }
    }

    /// Item 2018: Dot's first words stand under the owner's message as one of its short messages, with the steps shown and
    /// with Show steps off.
    func testTheFirstWordsStandUnderTheOwnersMessageWithAndWithoutTheSteps() {
        for defaults in [["-toolCallMode", "showAll", "-showSteps", "YES", "-stepsOverrides", "{}"], ["-showSteps", "NO", "-stepsOverrides", "{}"]] {
            let app: XCUIApplication = launch("n2-chat", defaults: defaults)
            let words: XCUIElement = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "Looking at next week's calendar first.")).firstMatch
            // The chat opens at its newest words; the first words stand near the run's start, above them.
            for _ in 0..<6 where !words.waitForExistence(timeout: 2) { app.swipeDown() }
            for _ in 0..<6 where !words.exists { app.swipeUp() }
            XCTAssertTrue(words.waitForExistence(timeout: 15), "\(defaults): the first words")
            app.terminate()
        }
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

    /// Item 1931 and the owner's report of 2026-11-27: with no link, the capsule says Offline in the status's place once
    /// it lasted a few seconds, and offers no Stop; the header keeps the bot's face and name, no link words.
    func testThePillSaysOfflineWithoutALink() {
        let app: XCUIApplication = launch("n2-offline-saved")
        let link: XCUIElement = app.buttons["capsule.link"]
        XCTAssertTrue(link.waitForExistence(timeout: 15))
        XCTAssertEqual(link.label, "Offline")
        for words in ["Syncing", "Reconnecting", "Offline ·"] {
            XCTAssertFalse(
                app.navigationBars.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", words)).firstMatch.exists, "no link words in the header: \(words)")
        }
        XCTAssertFalse(app.buttons["capsule.stop"].exists, "nothing to stop over no link")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "pill-offline"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testTheDockShowsEachAskInTurnAndFoldsBackToTheComposer() {
        let app: XCUIApplication = launch("n2-asks")
        XCTAssertTrue(app.staticTexts["User input needed"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["1 / 2"].exists, "a count for several asks")
        XCTAssertTrue(app.buttons["Allow once"].exists)
        XCTAssertTrue(app.buttons["Always allow in this chat"].exists)
        XCTAssertFalse(app.textFields["composer.field"].exists, "the dock stands in the composer's place")
        app.buttons["Next ask"].tap()
        XCTAssertTrue(app.staticTexts["2 / 2"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Which day suits you for the dentist?"].exists)
        XCTAssertTrue(app.buttons["Stop"].exists, "Stop stays reachable")
        app.buttons["Fold what Dot asked"].tap()
        XCTAssertTrue(app.textFields["composer.field"].waitForExistence(timeout: 3), "folded, the composer is back")
    }

    /// Item 2001, after another coding agent's question card: three questions page in one card ("1 / 3", never wrapped);
    /// a single-select pick rings its card, a multi-select one takes several and lets one go, an own answer is written;
    /// one "Submit answers" waits until every question has an answer and keeps the picks while paging.
    func testThreeQuestionsPageInOneCardWithSingleMultiAndWrittenAnswers() {
        let app: XCUIApplication = launch("n2-question")
        XCTAssertTrue(app.staticTexts["Fill in the pending answers"].waitForExistence(timeout: 10))
        // The fixture's live picture floats over the card's arrows: folded first, so the taps reach them.
        let hidePicture: XCUIElement = app.buttons["Hide the live picture"]
        if hidePicture.waitForExistence(timeout: 3) { hidePicture.tap() }
        let counter: XCUIElement = app.staticTexts["1 / 3"]
        XCTAssertTrue(counter.exists, "the count")
        XCTAssertLessThan(counter.frame.height, 30, "the count stays on one line: \(counter.frame)")
        let submit: XCUIElement = app.buttons["ask.submit"]
        XCTAssertEqual(submit.label, "Submit answers")
        XCTAssertFalse(submit.isEnabled, "nothing answered yet")
        // Single select: the pick is the selected card, a second pick moves it.
        let close: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Close both (Recommended)")).firstMatch
        let keep: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Keep them on")).firstMatch
        keep.tap()
        close.tap()
        XCTAssertTrue(close.isSelected)
        XCTAssertFalse(keep.isSelected, "one answer for a single-select question")
        XCTAssertFalse(submit.isEnabled, "two questions still wait")
        // Multi select (the third question): two picks, then one let go; both stay selected until then.
        app.buttons["Next ask"].tap()
        XCTAssertTrue(app.staticTexts["2 / 3"].waitForExistence(timeout: 3))
        app.buttons["Next ask"].tap()
        XCTAssertTrue(app.staticTexts["3 / 3"].waitForExistence(timeout: 3))
        let memory: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "#1199")).firstMatch
        let webhook: XCUIElement = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "#1188")).firstMatch
        XCTAssertTrue(memory.waitForExistence(timeout: 3))
        memory.tap()
        webhook.tap()
        XCTAssertTrue(memory.isSelected && webhook.isSelected, "several options at once")
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "question-card-multi"
        shot.lifetime = .keepAlways
        add(shot)
        webhook.tap()
        XCTAssertFalse(webhook.isSelected, "a second tap lets an option go")
        XCTAssertFalse(submit.isEnabled, "the second question still waits")
        // Paging back keeps what was picked.
        app.buttons["Previous ask"].tap()
        app.buttons["Previous ask"].tap()
        XCTAssertTrue(app.staticTexts["1 / 3"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Close both (Recommended)")).firstMatch.isSelected)
        // Free text: the second question is answered in words, the last answer Submit waited for.
        app.buttons["Next ask"].tap()
        XCTAssertTrue(app.staticTexts["2 / 3"].waitForExistence(timeout: 3))
        let written: XCUIElement = app.descendants(matching: .any).matching(identifier: "ask.written").firstMatch
        XCTAssertTrue(written.waitForExistence(timeout: 3))
        written.tap()
        written.typeText("Friday at noon")
        XCTAssertTrue(submit.isEnabled, "every question has an answer")
    }

    /// Item 2001: at AX3, in Turkish and dark, the card's options grow with their words (nothing cut) and the count
    /// stays on one line.
    func testTheQuestionCardGrowsAtAX3InTurkishAndDark() {
        let app: XCUIApplication = XCUIApplication()
        app.launchArguments = [
            "-DotNextScene", "n2-question", "-AppleLanguages", "(tr)", "-AppleLocale", "tr_TR", "-colorScheme", "dark",
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityL",
        ]
        app.launch()
        let counter: XCUIElement = app.staticTexts["1 / 3"]
        XCTAssertTrue(counter.waitForExistence(timeout: 10))
        XCTAssertLessThan(counter.frame.height, 60, "the count stays on one line at AX3: \(counter.frame)")
        let options: XCUIElementQuery = app.buttons.matching(identifier: "ask.option")
        XCTAssertTrue(options.firstMatch.waitForExistence(timeout: 5))
        for index in 0..<options.count {
            let option: XCUIElement = options.element(boundBy: index)
            XCTAssertGreaterThanOrEqual(option.frame.height.rounded(), 44)
            XCTAssertLessThanOrEqual(option.frame.maxX, app.windows.firstMatch.frame.maxX, "nothing pushed off the edge")
        }
        let shot: XCTAttachment = XCTAttachment(screenshot: app.screenshot())
        shot.name = "question-card-ax3-tr-dark"
        shot.lifetime = .keepAlways
        add(shot)
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
