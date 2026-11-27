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

    func testNoToolModeDrawsALineOfACall() {
        for mode in ToolCallMode.allCases {
            let items: [TranscriptItem] = transcript(of: chat(status: "running", events), pending: [], mode: mode, streaming: true, context: .none)
            XCTAssertFalse(items.contains { if case .narration = $0 { true } else { false } }, "\(mode)")
        }
    }
}
