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
        XCTAssertEqual(foldWords(fold, locale: turkish), "2 dk 14 sn çalıştı")
        XCTAssertEqual(foldWords(fold, locale: english), "Worked for 2m 14s", "how long only, no counts (item 1904)")
    }

    func testFindInChatSearchesTheWordsOfTheChatNotTheToolOutput() {
        let items: [TranscriptItem] = [
            .owner(id: "u:1", text: "Book the garage", delivery: nil),
            .tool(ToolEntry(id: "t:1", tool: "shell", kind: .command, target: "garage", args: "", output: "garage", state: .done, images: [], startedAt: 0, endedAt: 1)),
            .answer(id: "a:1", text: "The GARAGE is booked", writing: false, endedAt: 2),
        ]
        XCTAssertEqual(findMatches(items, words: "garage"), ["u:1", "a:1"], "case does not matter; a tool's output is not searched")
        XCTAssertEqual(findMatches(items, words: "  "), [], "empty words find nothing")
        let fold: WorkFold = WorkFold(
            id: "f:run_a", seconds: 12, tally: WorkTally(counts: [:], failed: 0), items: [.thought(id: "th:1", text: "Which garage is open on Friday", live: false)])
        XCTAssertEqual(findMatches([.work(fold)], words: "friday"), ["f:run_a"], "a finished run's folded thought is found, at its fold")
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
        // Item 1909: consecutive calls that ended well are one line, of any kinds ("Ran 3 commands and read 1 file");
        // a failed one and one at work each stand alone, and so does a single one between them.
        XCTAssertEqual(groups.map(\.id), ["tools:c1", "c4", "c5", "c6"])
        guard case .tools(let entries) = groups[0] else { return XCTFail("a group first") }
        XCTAssertEqual(toolGroupWords(entries, locale: english), "Ran 3 commands and read 1 file")
        XCTAssertEqual(toolGroupWords(entries, locale: turkish), "3 komut çalıştırdı ve 1 dosya okudu")
        let search: ToolEntry = ToolEntry(id: "s", tool: "web_search", kind: .search, target: "x", args: "", output: "", state: .done, images: [], startedAt: 0, endedAt: 1)
        let other: ToolEntry = ToolEntry(id: "o", tool: "notify", kind: .other, target: "", args: "", output: "", state: .done, images: [], startedAt: 0, endedAt: 1)
        XCTAssertEqual(toolGroupWords([other, search], locale: english), "Used 1 tool and searched the web 1 time")
    }

    /// Item 1932: a line that waits on the owner or for a stop is still; only work shimmers (the asks scene held the
    /// phone at 100 % CPU redrawing "Waiting for you" thirty to sixty times a second).
    func testOnlyWorkShimmers() {
        XCTAssertFalse(workLineMoves(.waitingForYou))
        XCTAssertFalse(workLineMoves(.stopping))
        XCTAssertTrue(workLineMoves(.thinking))
        XCTAssertTrue(workLineMoves(.typing))
    }

    /// Item 1957: the helpers group says what works first, then what waits, then what ended, and keeps its clock running
    /// while one helper works; once all ended it stops at the last end.
    func testTheHelpersGroupTalliesAndSpan() {
        XCTAssertEqual(
            helperTallies([.done, .working, .failed, .working]),
            [HelperTally(state: .working, count: 2), HelperTally(state: .failed, count: 1), HelperTally(state: .done, count: 1)])
        XCTAssertEqual(helperTallies([]), [])
        func helper(_ id: String, _ status: String, _ start: Double, _ end: Double?) -> HelperView {
            HelperView(
                runId: id, parentRunId: "r", threadId: "t" + id, agentId: nil, title: id, status: status, startedAt: start,
                finishedAt: end, provider: "claude_code", model: "opus", effort: "high", step: nil, blocking: false, stop: "")
        }
        let live: (start: Date, end: Date?) = helperGroupSpan([helper("a", "done", 100, 160), helper("b", "running", 90, nil)])
        XCTAssertEqual(live.start.timeIntervalSince1970, 90)
        XCTAssertNil(live.end, "a helper works: the clock runs")
        let ended: (start: Date, end: Date?) = helperGroupSpan([helper("a", "done", 100, 160), helper("b", "failed", 90, 200)])
        XCTAssertEqual(ended.end?.timeIntervalSince1970, 200)
        let options: [ProviderModels] = [ProviderModels(provider: "claude_code", models: [], error: nil)]
        XCTAssertEqual(engineLine(helper("a", "done", 0, 1), models: options), "Claude Code · opus · " + effortTitle("high"), "the model's id when the list does not hold it")
    }
}
