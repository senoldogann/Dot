import XCTest

@testable import DotEngine

/// A message a routine sent into a chat (owner 2026-11-27): a schedule's, a watcher's or a hook's run opens with the
/// routine's compact row, named by the chat's schedule, never an owner's bubble.
final class RoutineMessageTests: XCTestCase {
    private func chat(source: String, goal: String, schedules: [ChatSchedule]) -> ChatProjection {
        var chat: ChatProjection = ChatProjection(threadId: "thr_1")
        let run: RunRecord = RunRecord(
            id: "run_a", threadId: "thr_1", goal: goal, mode: "task", model: "m", status: "done", createdAt: 1, finishedAt: 2,
            result: "Done.", parentRunId: nil, source: source, effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        var view: ThreadView = ThreadView(
            revision: 1, thread: ThreadRecord(id: "thr_1", title: "Chat", source: "ui", createdAt: 1, updatedAt: 1, agentId: nil),
            summary: nil, runs: [run], older: false, newest: nil, newestEvents: [], newestEventsCut: false,
            queue: QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil), asks: [], helpers: [], background: [],
            reactions: [], drafts: [])
        view.schedules = schedules
        chat.view = view
        chat.freshness = .live
        return chat
    }

    func testAScheduledRunOpensWithTheRoutinesRowNamedByItsSchedule() {
        let brief: ChatSchedule = ChatSchedule(id: "sch_1", name: "Morning brief", goal: "Read my mail and tell me what matters today.")
        let other: ChatSchedule = ChatSchedule(id: "sch_2", name: "Invoices", goal: "Check the invoices.")
        for mode in ToolCallMode.allCases {
            let items: [TranscriptItem] = transcript(
                of: chat(source: "schedule", goal: brief.goal, schedules: [other, brief]), pending: [], mode: mode, streaming: true, context: .none)
            guard case .automation(let id, let title, let prompt) = items.first else { return XCTFail("\(mode): the routine's row first") }
            XCTAssertEqual([id, title, prompt], ["u:run_a", "Morning brief", brief.goal], "\(mode)")
        }
        let told: [TranscriptItem] = conversation(of: chat(source: "watcher", goal: "A new mail from the bank. Read it.", schedules: []), pending: [], streaming: true, context: .none)
        guard case .automation(_, let title, _) = told.first else { return XCTFail("a watcher's run too, in the conversation") }
        XCTAssertEqual(title, "A new mail from the bank")
        let owners: [TranscriptItem] = transcript(of: chat(source: "ui", goal: "Hi", schedules: [brief]), pending: [], mode: .showAll, streaming: true, context: .none)
        guard case .owner = owners.first else { return XCTFail("the owner's own message stays a bubble") }
    }

    func testARoutinesTitleIsItsSchedulesNameElseTheOneScheduleElseItsWords() {
        let one: ChatSchedule = ChatSchedule(id: "sch_1", name: "Daily check", goal: "Old words")
        XCTAssertEqual(routineTitle(goal: "New words. More.", schedules: [one]), "Daily check")
        XCTAssertEqual(routineTitle(goal: "New words. More.", schedules: [one, ChatSchedule(id: "sch_2", name: "B", goal: "x")]), "New words")
        XCTAssertEqual(routineTitle(goal: "New words. More.", schedules: []), "New words")
    }

    func testTheViewReadsTheChatsSchedulesFromTheDaemonsShape() throws {
        let json: String = #"{"schedule":{"record":{"id":"sch_1","name":"Morning brief","goal":"Read my mail","cron":"0 8 * * *"},"when":"Every day"},"loop":false}"#
        let decoded: ChatSchedule = try JSONDecoder().decode(ChatSchedule.self, from: Data(json.utf8))
        XCTAssertEqual(decoded, ChatSchedule(id: "sch_1", name: "Morning brief", goal: "Read my mail"))
    }
}
