import XCTest
@testable import Dot

final class ChatStateTests: XCTestCase {
    private func started() -> ChatState {
        var state: ChatState = ChatState()
        state.addRun(makeRun())
        return state
    }

    func testARunsGoalIsTheOwnersMessage() {
        let state: ChatState = started()
        XCTAssertEqual(state.items.map(\.id), ["u:run_1"])
        XCTAssertEqual(text(of: state.items[0]), "Do it")
        XCTAssertTrue(state.isWorking)
    }

    func testStreamedWordsGrowOneRowWhoseIdSurvivesTheStepsEnd() {
        var state: ChatState = started()
        state.apply(event(0, "run_1", "delta", ["kind": .string("text"), "text": .string("Hel"), "step": .int(1)]))
        state.apply(event(0, "run_1", "delta", ["kind": .string("text"), "text": .string("lo"), "step": .int(1)]))
        XCTAssertEqual(state.items.map(\.id), ["u:run_1", "a:run_1:1"])
        XCTAssertEqual(state.items[1].kind, .assistant(text: "Hello", streaming: true))
        // The stored `assistant` event of the step replaces the streamed words in place: same id, no second row.
        state.apply(event(7, "run_1", "assistant", ["step": .int(1), "text": .string("Hello there"), "tool_calls": .array([])]))
        XCTAssertEqual(state.items.map(\.id), ["u:run_1", "a:run_1:1"])
        XCTAssertEqual(state.items[1].kind, .assistant(text: "Hello there", streaming: false))
        XCTAssertEqual(state.lastEventId, 7, "a delta has no id: only stored events move the cursor")
    }

    func testAResetDropsTheStreamedWords() {
        var state: ChatState = started()
        state.apply(event(0, "run_1", "delta", ["kind": .string("text"), "text": .string("oops"), "step": .int(2)]))
        state.apply(event(0, "run_1", "delta", ["kind": .string("reset"), "text": .string(""), "step": .int(2)]))
        XCTAssertEqual(state.items.map(\.id), ["u:run_1"])
    }

    func testAToolStepIsOneLineThatCompletes() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object(["command": .string("swift test")])]))
        guard case .tool(let running) = state.items[1].kind else { return XCTFail("a tool row") }
        XCTAssertEqual(running.line, "Shell: swift test")
        XCTAssertEqual(running.state, .running)
        state.apply(event(3, "run_1", "tool_result", ["call_id": .string("c1"), "ok": .bool(true), "summary": .string("Ran swift test")]))
        guard case .tool(let done) = state.items[1].kind else { return XCTFail("a tool row") }
        XCTAssertEqual(done.state, .ok)
        XCTAssertEqual(done.line, "Ran swift test")
        XCTAssertEqual(state.items.count, 2, "the result completes the row, it adds none")
    }

    func testAFailedToolIsMarkedAndFinishIsNoRow() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object([:])]))
        state.apply(event(3, "run_1", "tool_result", ["call_id": .string("c1"), "ok": .bool(false)]))
        state.apply(event(4, "run_1", "tool_call", ["call_id": .string("c2"), "tool": .string("finish"), "args": .object([:])]))
        XCTAssertEqual(state.items.count, 2)
        guard case .tool(let row) = state.items[1].kind else { return XCTFail("a tool row") }
        XCTAssertEqual(row.state, .failed)
    }

    func testAFailedStepKeepsWhatItWasAskedAndSaysWhy() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object(["command": .string("ls -l /nope")])]))
        state.apply(event(3, "run_1", "tool_result", [
            "call_id": .string("c1"), "ok": .bool(false), "summary": .string("Unknown tool 'shell'. Available tools: agents_now, ask_secret, ask_user"),
        ]))
        guard case .tool(let row) = state.items[1].kind else { return XCTFail("a tool row") }
        XCTAssertEqual(row.state, .failed)
        XCTAssertEqual(row.line, "Shell: ls -l /nope", "what it was asked stays on the row")
        XCTAssertEqual(row.failure, "Unknown tool 'shell'")
    }

    func testTheFirstSentenceOfAnErrorIsWhatTheRowSays() {
        XCTAssertEqual(failureSentence("Permission denied. See the log."), "Permission denied")
        XCTAssertEqual(failureSentence("Timed out\nafter 30 s"), "Timed out")
        XCTAssertEqual(failureSentence("x"), "x")
        XCTAssertLessThanOrEqual(failureSentence(String(repeating: "a", count: 300)).count, 90)
    }

    func testAnApprovalCardWaitsThenIsAnswered() {
        var state: ChatState = started()
        state.apply(event(5, "run_1", "approval_requested", [
            "approval_id": .string("apr_1"), "tool": .string("shell"), "summary": .string("Run: rm x"), "severity": .string("risky"),
            "args": .object([:]),
        ]))
        XCTAssertEqual(state.pendingApprovals.map(\.approvalId), ["apr_1"])
        XCTAssertEqual(state.runStatus, "blocked")
        XCTAssertEqual(state.line, .waitingOnYou(count: 1))
        state.apply(event(6, "run_1", "approval_decided", ["approval_id": .string("apr_1"), "status": .string("approved")]))
        XCTAssertTrue(state.pendingApprovals.isEmpty)
        XCTAssertEqual(state.runStatus, "running")
        guard case .approval(let card) = state.items[1].kind else { return XCTFail("a card") }
        XCTAssertEqual(card.status, "approved")
    }

    func testAQuestionCarriesItsWords() {
        var state: ChatState = started()
        state.apply(event(5, "run_1", "approval_requested", [
            "approval_id": .string("apr_2"), "tool": .string("ask_user"), "summary": .string("Asks"), "severity": .string("ask"),
            "args": .object(["question": .string("Which folder?")]),
        ]))
        guard case .approval(let card) = state.items[1].kind else { return XCTFail("a card") }
        XCTAssertEqual(card.question, "Which folder?")
    }

    func testTheLineSaysWaitingOnSubagentsWithWhatTheFirstDoes() {
        var state: ChatState = started()
        state.apply(event(2, "run_2", "run_queued", ["goal": .string("Read the invoices folder"), "parent_run_id": .string("run_1")]))
        state.apply(event(3, "run_3", "run_queued", ["goal": .string("Read the receipts"), "parent_run_id": .string("run_1")]))
        state.apply(event(4, "run_2", "run_started", ["model": .string("m"), "parent_run_id": .string("run_1")]))
        state.apply(event(5, "run_2", "tool_call", ["call_id": .string("h1"), "tool": .string("shell"), "args": .object(["command": .string("ls")])]))
        XCTAssertEqual(state.items.count, 1, "a helper's steps are not rows of the chat")
        XCTAssertEqual(state.line, .waitingOnSubagents(count: 2, now: "Read the invoices folder: Shell: ls"))
        state.apply(event(6, "run_2", "run_finished", ["status": .string("done"), "result": .string("ok")]))
        XCTAssertEqual(state.line, .waitingOnSubagents(count: 1, now: nil))
        state.apply(event(7, "run_3", "run_finished", ["status": .string("done"), "result": .string("ok")]))
        XCTAssertEqual(state.line, .working(since: 1000))
    }

    func testAHelperTheRunStoppedWaitingForWorksOnInTheBackground() {
        var state: ChatState = started()
        state.apply(event(2, "run_2", "run_queued", ["parent_run_id": .string("run_1"), "goal": .string("Read the folder")]))
        state.apply(event(3, "run_2", "run_started", ["model": .string("m"), "parent_run_id": .string("run_1")]))
        XCTAssertEqual(state.line, .waitingOnSubagents(count: 1, now: nil))
        state.apply(event(4, "run_1", "helper_released", ["run_id": .string("run_2")]))
        XCTAssertEqual(state.line, .helpersInBackground(count: 1))
        state.apply(event(4, "run_1", "helper_released", ["run_id": .string("run_2")]))
        XCTAssertEqual(state.line, .helpersInBackground(count: 1), "applied again, the same state")
        state.apply(event(5, "run_2", "run_finished", ["status": .string("done"), "result": .string("ok")]))
        XCTAssertEqual(state.line, .working(since: 1000))
    }

    func testTheAnswerIsShownOnceWhenAReplySaidTheSameWords() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "assistant", ["step": .int(1), "text": .string("All done."), "tool_calls": .array([])]))
        state.apply(event(3, "run_1", "run_finished", ["status": .string("done"), "result": .string("All done.")]))
        XCTAssertEqual(state.items.map(\.id), ["u:run_1", "r:run_1"])
        XCTAssertFalse(state.isWorking)
        XCTAssertEqual(state.line, .idle)
    }

    func testAFinishedRunClosesWhatStillWaitedForTheOwner() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "approval_requested", ["approval_id": .string("a"), "tool": .string("shell"), "summary": .string("x"), "severity": .string("r"), "args": .object([:])]))
        state.apply(event(3, "run_1", "run_finished", ["status": .string("cancelled"), "result": .string("")]))
        XCTAssertTrue(state.pendingApprovals.isEmpty)
        XCTAssertEqual(state.runStatus, "cancelled")
    }

    func testApplyingEventsAgainChangesNothing() {
        let events: [EventRecord] = [
            event(2, "run_1", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object([:])]),
            event(3, "run_1", "tool_result", ["call_id": .string("c1"), "ok": .bool(true)]),
            event(4, "run_1", "assistant", ["step": .int(1), "text": .string("Hi"), "tool_calls": .array([])]),
            event(5, "run_1", "steer", ["text": .string("and also this")]),
        ]
        var once: ChatState = started()
        once.apply(events)
        var twice: ChatState = started()
        twice.apply(events)
        twice.apply(events)
        XCTAssertEqual(once, twice, "a reconnect may hand an event over again")
        XCTAssertEqual(once.items.map(\.id), ["u:run_1", "t:c1", "a:run_1:1", "s:5"])
    }

    func testANewRunOfTheChatFollowsTheOldOneInOrder() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "run_finished", ["status": .string("done"), "result": .string("first")]))
        state.addRun(makeRun("run_2", goal: "More", created: 2000))
        state.apply(event(9, "run_2", "run_started", ["model": .string("m")]))
        XCTAssertEqual(state.items.map(\.id), ["u:run_1", "r:run_1", "u:run_2"])
        XCTAssertEqual(state.newestRunId, "run_2")
        XCTAssertTrue(state.isWorking)
    }

    func testAnOlderRunComesFromItsRecordAlone() {
        var state: ChatState = ChatState()
        state.addSummary(makeRun("run_0", goal: "Old", status: "done", created: 10, result: "Old answer"))
        XCTAssertEqual(state.items.map(\.id), ["u:run_0", "r:run_0"])
    }

    func testTheDaemonsListIsTheTruthBothWaysForACard() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "approval_requested", ["approval_id": .string("a"), "tool": .string("shell"), "summary": .string("x"), "severity": .string("r"), "args": .object([:])]))
        state.apply(event(3, "run_1", "approval_decided", ["approval_id": .string("a"), "status": .string("approved")]))
        XCTAssertTrue(state.pendingApprovals.isEmpty)
        // The daemon still lists the question as open (its answer was refused): the card asks again, and the line says so.
        let open: ApprovalRecord = ApprovalRecord(id: "a", runId: "run_1", status: "pending", tool: "shell", summary: "x", severity: "r", args: .object([:]))
        state.setOpenQuestions([open])
        XCTAssertEqual(state.pendingApprovals.map(\.approvalId), ["a"])
        XCTAssertEqual(state.line, .waitingOnYou(count: 1))
        // And it is closed when the daemon no longer lists it.
        state.setOpenQuestions([])
        XCTAssertTrue(state.pendingApprovals.isEmpty)
        XCTAssertEqual(state.runStatus, "running")
    }

    func testMessagesThatCouldNotBeReadLeaveARowThatStays() {
        var state: ChatState = started()
        state.setUnreadable(2, ofRun: "run_1")
        state.setUnreadable(3, ofRun: "run_1")
        guard case .note(let words, .warn) = state.items.last?.kind else { return XCTFail("a warning row") }
        XCTAssertEqual(words, "3 messages of this run could not be shown here. The web UI shows them.")
        XCTAssertEqual(state.items.filter { $0.id == "n:unreadable:run_1" }.count, 1)
    }

    func testTheDaemonsListOfOpenQuestionsClosesAndOpensCards() {
        var state: ChatState = started()
        state.apply(event(2, "run_1", "approval_requested", ["approval_id": .string("old"), "tool": .string("shell"), "summary": .string("x"), "severity": .string("r"), "args": .object([:])]))
        let open: ApprovalRecord = ApprovalRecord(id: "new", runId: "run_1", status: "pending", tool: "shell", summary: "y", severity: "r", args: .object([:]))
        state.setOpenQuestions([open])
        XCTAssertEqual(state.pendingApprovals.map(\.approvalId), ["new"])
    }

    func testBuildingFromRunsAndStoredEventsMatchesApplyingThemLive() {
        let events: [EventRecord] = [
            event(2, "run_1", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object([:])]),
            event(3, "run_1", "run_finished", ["status": .string("done"), "result": .string("Done")]),
        ]
        let built: ChatState = ChatState.build(runs: [makeRun(status: "done")], helpers: [], events: events.reversed())
        XCTAssertEqual(built.items.map(\.id), ["u:run_1", "t:c1", "r:run_1"])
        XCTAssertEqual(built.lastEventId, 3)
    }

    func testRoutesNameRuns() {
        XCTAssertEqual(runId(inRoute: "#/runs/run_abc-1"), "run_abc-1")
        XCTAssertEqual(runId(inRoute: "/runs/run_abc"), "run_abc")
        XCTAssertNil(runId(inRoute: "#/settings/phone"))
        XCTAssertNil(runId(inRoute: "#/runs/run_abc/extra"))
    }
}
