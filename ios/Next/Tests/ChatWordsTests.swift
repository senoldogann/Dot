import DotEngine
import XCTest

@testable import DotNext

/// The chat's words (wave N2): one duration shape everywhere (§19), the fold's tally, the tight blocks of tool rows.
final class ChatWordsTests: XCTestCase {
    private let turkish: Locale = Locale(identifier: "tr")
    private let english: Locale = Locale(identifier: "en")

    func testOneDurationShapeWithoutAbbreviationDots() {
        XCTAssertEqual(durationWords(2475, locale: turkish), "41 dk 15 sn")
        XCTAssertEqual(durationWords(134, locale: turkish), "2 dk 14 sn")
        XCTAssertEqual(durationWords(20, locale: turkish), "20 sn")
        XCTAssertEqual(durationWords(6443, locale: turkish), "1 sa 47 dk 23 sn")
        XCTAssertEqual(durationWords(2475, locale: english), "41m 15s")
        XCTAssertFalse(elapsedWords(from: Date(timeIntervalSince1970: 0), to: Date(timeIntervalSince1970: 20)).contains("."), "no abbreviation dot")
    }

    func testTheFoldSaysHowLongAndWhatInTheOwnersLanguage() {
        let fold: WorkFold = WorkFold(id: "f", seconds: 134, tally: WorkTally(counts: [.read: 3, .command: 2], failed: 0), items: [])
        XCTAssertEqual(foldWords(fold, locale: turkish), "2 dk 14 sn çalıştı · 3 dosya okudu, 2 komut çalıştırdı")
        XCTAssertEqual(foldWords(fold, locale: english), "Worked for 2m 14s · read 3 files, ran 2 commands")
    }

    func testConsecutiveStepsStackAsOneTightBlock() {
        let tool: (String) -> TranscriptItem = { id in
            .tool(ToolEntry(id: id, tool: "shell", kind: .command, target: "ls", args: "", output: "", state: .done, images: [], startedAt: 0, endedAt: 1))
        }
        let thought: TranscriptItem = .thought(id: "th:1", text: "Look at the diff first.", live: false)
        let blocks: [TranscriptBlock] = TranscriptBlock.of([thought, tool("t:1"), tool("t:2"), .answer(id: "a:3", text: "x", writing: false, endedAt: nil), tool("t:4")])
        // Item 1769: a thought is a row of the stretch it opens, with no gap of its own (the owner's shot of the gaps).
        XCTAssertEqual(blocks.map(\.id), ["steps:th:1", "a:3", "steps:t:4"])
    }

    func testEveryToolRowStartsWithACapitalAndDotsOwnToolsSayAPhrase() {
        let entry: (String, String) -> ToolEntry = { tool, target in
            ToolEntry(id: "t", tool: tool, kind: ToolKind.of(tool), target: target, args: "", output: "", state: .done, images: [], startedAt: 0, endedAt: 1)
        }
        // In the simulator's language: the phrases are the web's, English or Turkish.
        XCTAssertEqual(toolLine(entry("finish", "")), dotToolWords("finish"))
        XCTAssertEqual(toolLine(entry("use_tools", "")), dotToolWords("use_tools"))
        XCTAssertEqual(toolLine(entry("schedule_list", "schedules")), (dotToolWords("schedule_list") ?? "") + " · schedules")
        XCTAssertEqual(toolLine(entry("memory_search", "deploy")), (dotToolWords("memory_search") ?? "") + " · deploy", "a note search is Dot's, not a file search")
        for tool in ["finish", "use_tools", "work_orders", "schedule_list", "react", "draft_reply", "mcp__x__y"] {
            let line: String = toolLine(entry(tool, ""))
            XCTAssertEqual(line.first.map { String($0).uppercased() }, line.first.map(String.init), "\(tool) starts with a capital: \(line)")
        }
        XCTAssertEqual(toolLine(entry("draft_reply", "")), "Draft reply", "a name with no phrase reads as a sentence")
        XCTAssertEqual(toolLine(entry("mcp__microsoft-learn__search", "")), "Microsoft Learn · Search")
        XCTAssertTrue(isDotTool(entry("work_orders", "")))
        XCTAssertFalse(isDotTool(entry("delegate", "fix it")), "a delegated task wears the hammer")
        XCTAssertEqual(toolSymbol(.helper), "hammer")
        XCTAssertFalse(isDotTool(entry("shell", "ls")))
    }

    func testWordsArriveAtASteadyPaceAndTheNewestFadeIn() {
        // Item 1769: a bunch of 300 characters from the stream spreads over about a second, never in one frame.
        var shown: Int = 0
        var frames: Int = 0
        while shown < 300 {
            shown = revealStep(shown: shown, target: 300, seconds: 1.0 / 60)
            frames += 1
        }
        XCTAssertGreaterThan(frames, 20, "spread over frames")
        XCTAssertLessThan(frames, 90, "caught up within a second and a half")
        XCTAssertEqual(revealStep(shown: 0, target: 1, seconds: 1.0 / 60), 1, "a short text shows within a frame")
        XCTAssertEqual(revealStep(shown: 5, target: 5, seconds: 1.0 / 60), 5, "nothing waits: nothing moves")
        let faded: AttributedString = fadedTail(AttributedString("The words arrive softly"), characters: 8)
        let colored: Int = faded.runs.filter { $0.foregroundColor != nil }.count
        XCTAssertEqual(colored, 4, "the newest eight characters in four steps")
        XCTAssertEqual(String(faded.characters), "The words arrive softly", "the words are kept")
        XCTAssertEqual(fadedTail(AttributedString("x"), characters: 0), AttributedString("x"))
    }

    func testConsecutiveCommandsThatEndedWellReadAsOneLine() {
        let command: (String, DotEngine.ToolState) -> TranscriptItem = { id, state in
            .tool(ToolEntry(id: id, tool: "shell", kind: .command, target: "ls", args: "", output: "", state: state, images: [], startedAt: 0, endedAt: 1))
        }
        let read: TranscriptItem = .tool(ToolEntry(id: "r", tool: "read_file", kind: .read, target: "/a", args: "", output: "", state: .done, images: [], startedAt: 0, endedAt: 1))
        let groups: [StepGroup] = StepGroup.of([command("c1", .done), command("c2", .done), read, command("c3", .done), command("c4", .failed), command("c5", .done), command("c6", .running)])
        // Item 1769: "Ran 2 commands"; a single command, a failed one and one at work each stand alone.
        XCTAssertEqual(groups.map(\.id), ["commands:c1", "r", "c3", "c4", "c5", "c6"])
    }
}
