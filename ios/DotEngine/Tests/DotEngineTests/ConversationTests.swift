import XCTest

@testable import DotEngine

/// The chat told as a conversation, "Show steps" off (items 1915 and 1976): Dot's words and the lines of the calls its
/// words did not tell, no steps while the run works, and one fold of the steps before a finished run's answer.
final class ConversationTests: XCTestCase {
    private func event(_ id: Int, _ kind: String, _ data: [String: JSONValue]) -> EventRecord {
        EventRecord(id: id, runId: "run_a", ts: Double(id), kind: kind, data: data)
    }

    private func chat(status: String, _ events: [EventRecord]) -> ChatProjection {
        var chat: ChatProjection = ChatProjection(threadId: "thr_1")
        let run: RunRecord = RunRecord(
            id: "run_a", threadId: "thr_1", goal: "Fill the form", mode: "chat", model: "m", status: status, createdAt: 1, finishedAt: nil,
            result: "", parentRunId: nil, source: "ui", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        chat.view = ThreadView(
            revision: 1, thread: ThreadRecord(id: "thr_1", title: "Chat", source: "ui", createdAt: 1, updatedAt: 1, agentId: nil),
            summary: nil, runs: [run], older: false, newest: nil, newestEvents: [], newestEventsCut: false,
            queue: QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil), asks: [], helpers: [], background: [],
            reactions: [], drafts: [])
        for event in events {
            chat.runs[event.runId] = (chat.runs[event.runId] ?? RunTimeline()).adding(event) ?? chat.runs[event.runId]
        }
        chat.freshness = .live
        return chat
    }

    private func step(_ id: Int, _ text: String, _ tools: [String]) -> EventRecord {
        event(id, "assistant", ["step": .int(id), "text": .string(text), "tool_calls": .array(tools.map(JSONValue.string))])
    }

    private func call(_ id: Int, _ tool: String, _ args: [String: JSONValue]) -> [EventRecord] {
        [
            event(id, "tool_call", ["call_id": .string("c\(id)"), "tool": .string(tool), "args": .object(args)]),
            event(id + 1, "narration", ["call_id": .string("c\(id)"), "tool": .string(tool), "args": .object(args)]),
        ]
    }

    private func result(_ id: Int, _ call: Int) -> EventRecord {
        event(id, "tool_result", ["call_id": .string("c\(call)"), "ok": .bool(true), "text": .string("out")])
    }

    /// A run that said what it does first, then searched and opened a page without a word.
    private var events: [EventRecord] {
        [step(10, "First I look at the site.", ["browser_open"])] + call(11, "browser_open", ["url": .string("https://gistsmith.com")])
            + [result(13, 11), step(20, "", ["web_search"])] + call(21, "web_search", ["query": .string("gistsmith contact")]) + [result(23, 21)]
    }

    func testAWorkingRunShowsItsWordsAndTheLinesItsWordsDidNotTellAndNoSteps() {
        let items: [TranscriptItem] = conversation(of: chat(status: "running", events), pending: [], streaming: true, context: .none)
        XCTAssertEqual(items.map(\.id), ["u:run_a", "a:10", "n:22"], "the page's line stands beside the words that told it: it is not drawn")
        guard case .narration(_, let told) = items[2] else { return XCTFail("the search is told") }
        XCTAssertEqual(told.tool, "web_search")
        XCTAssertEqual(told.target, "gistsmith contact")
    }

    func testAFinishedRunKeepsTheConversationAndFoldsItsStepsBeforeTheAnswer() {
        let finished: [EventRecord] = events + [step(30, "", ["finish"]), event(31, "run_finished", ["status": .string("done"), "result": .string("Two fields.")])]
        let items: [TranscriptItem] = conversation(of: chat(status: "done", finished), pending: [], streaming: true, context: .none)
        XCTAssertEqual(items.map(\.id), ["u:run_a", "a:10", "n:22", "f:t:c11", "r:run_a"])
        guard case .work(let fold) = items[3] else { return XCTFail("the steps fold before the answer") }
        XCTAssertEqual(fold.items.map(\.id), ["t:c11", "t:c21"])
    }

    /// Item 1996: a failed step is no row while the run works, and a finished run's fold holding it is drawn folded (the
    /// owner's shot: every step of an ended run showed with Show steps off, its fold opened by one failed page).
    func testAFailedStepIsNoRowAndItsFoldStartsFolded() {
        let failing: [EventRecord] =
            events + [event(30, "tool_call", ["call_id": .string("c30"), "tool": .string("shell"), "args": .object([:])]),
                event(31, "tool_result", ["call_id": .string("c30"), "ok": .bool(false), "text": .string("no such command")])]
        let working: [TranscriptItem] = conversation(of: chat(status: "running", failing), pending: [], streaming: true, context: .none)
        XCTAssertFalse(working.contains { if case .tool = $0 { true } else { false } }, "no step row, a failed one neither")
        let ended: [EventRecord] = failing + [event(40, "run_finished", ["status": .string("done"), "result": .string("Two fields.")])]
        let items: [TranscriptItem] = conversation(of: chat(status: "done", ended), pending: [], streaming: true, context: .none)
        guard let fold: WorkFold = items.compactMap({ if case .work(let fold) = $0 { fold } else { nil } }).first else { return XCTFail("one fold") }
        XCTAssertTrue(fold.hasFailure)
        XCTAssertFalse(fold.startsOpen, "folded with Show steps off, its failure inside")
        XCTAssertEqual(items.filter { if case .work = $0 { true } else { false } }.count, 1)
    }

    /// Item 1996, the owner's shot of 2026-11-27: the model wrote its answer, then called finish with it in slightly
    /// other words; the daemon named the step restated (`restated: [3]`), and the phone, reading that list as words,
    /// drew both and a "Finish" row between them. The answer stands once, and a finish that was taken has no row.
    func testAnAnswerSaidAgainByTheFinishStandsOnce() {
        let said: [EventRecord] = [
            step(3, "Two parts:\n\n1) the park.\n2) the screen. ", []),
            step(4, "", ["finish"]),
            event(5, "tool_call", ["call_id": .string("f1"), "tool": .string("finish"), "args": .object(["answer": .string("Two parts:\n\n1) the park.\n2) the screen.")])]),
            event(6, "tool_result", ["call_id": .string("f1"), "ok": .bool(true), "text": .string("done")]),
            event(7, "run_finished", ["status": .string("done"), "result": .string("Two parts:\n\n1) the park.\n2) the screen."), "restated": .array([.int(3)])]),
        ]
        for mode in ToolCallMode.allCases {
            let items: [TranscriptItem] = transcript(of: chat(status: "done", said), pending: [], mode: mode, streaming: true, context: .none)
            XCTAssertEqual(items.map(\.id), ["u:run_a", "r:run_a"], "\(mode): the answer once, no Finish row")
        }
        XCTAssertEqual(conversation(of: chat(status: "done", said), pending: [], streaming: true, context: .none).map(\.id), ["u:run_a", "r:run_a"])
    }

    func testNoToolModeDrawsALineOfACall() {
        for mode in ToolCallMode.allCases {
            let items: [TranscriptItem] = transcript(of: chat(status: "running", events), pending: [], mode: mode, streaming: true, context: .none)
            XCTAssertFalse(items.contains { if case .narration = $0 { true } else { false } }, "\(mode)")
        }
    }

    /// Dot's first words (item 2018): the words said at once, then the fast model's line in their place, one row before
    /// the run's own words, in the conversation and in every tool mode, outside a finished run's fold.
    func testTheFirstWordsAreOneRowFirstInEveryModeAndTheLineTakesThePlaceOfTheWordsSaidAtOnce() {
        let said: [EventRecord] = [event(2, "first_words", ["text": .string(""), "model": .null])]
        let atOnce: [TranscriptItem] = conversation(of: chat(status: "running", said + events), pending: [], streaming: true, context: .none)
        XCTAssertEqual(atOnce.map(\.id), ["u:run_a", "fw:run_a", "a:10", "n:22"])
        guard case .firstWords(_, let empty) = atOnce[1] else { return XCTFail("the words said at once are a row") }
        XCTAssertEqual(empty, "", "the app says them in its language")
        let lined: [EventRecord] = said + [event(3, "first_words", ["text": .string(" Looking at the site first. "), "model": .string("fast")])] + events
        let finished: [EventRecord] =
            lined + [step(30, "", ["finish"]), event(31, "run_finished", ["status": .string("done"), "result": .string("Two fields.")])]
        for mode in ToolCallMode.allCases {
            for (status, timeline) in [("running", lined), ("done", finished)] {
                let items: [TranscriptItem] = transcript(of: chat(status: status, timeline), pending: [], mode: mode, streaming: true, context: .none)
                let rows: [String] = items.compactMap { if case .firstWords(_, let text) = $0 { text } else { nil } }
                XCTAssertEqual(rows, ["Looking at the site first."], "\(mode) \(status)")
                XCTAssertEqual(items[1].id, "fw:run_a", "\(mode) \(status): right under the owner's words")
            }
        }
    }
}
