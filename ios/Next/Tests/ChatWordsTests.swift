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

    func testConsecutiveToolRowsStackAsOneTightBlock() {
        let tool: (String) -> TranscriptItem = { id in
            .tool(ToolEntry(id: id, tool: "shell", kind: .command, target: "ls", args: "", output: "", state: .done, images: [], startedAt: 0, endedAt: 1))
        }
        let blocks: [TranscriptBlock] = TranscriptBlock.of([tool("t:1"), tool("t:2"), .answer(id: "a:3", text: "x", writing: false, endedAt: nil), tool("t:4")])
        XCTAssertEqual(blocks.map(\.id), ["tools:t:1", "a:3", "tools:t:4"])
    }
}
