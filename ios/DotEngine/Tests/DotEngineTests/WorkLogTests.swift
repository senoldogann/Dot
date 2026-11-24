import XCTest

@testable import DotEngine

/// The work log of a chat (wave N2): the four tool modes as the table on `ToolCallMode` says, the fold of a finished
/// run, the asks left in place with who answered, the plan, the helpers and how a run ended.
final class WorkLogTests: XCTestCase {
    private func event(_ id: Int, _ kind: String, _ data: [String: JSONValue], run: String = "run_a") -> EventRecord {
        EventRecord(id: id, runId: run, ts: Double(id), kind: kind, data: data)
    }

    private func run(_ id: String, status: String, at: Double = 1, source: String = "ui") -> RunRecord {
        RunRecord(
            id: id, threadId: "thr_1", goal: "goal of \(id)", mode: "task", model: "m", status: status, createdAt: at, finishedAt: nil,
            result: "", parentRunId: nil, source: source, effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
    }

    /// A chat holding `runs` and their `events`, with `helpers`.
    private func chat(_ runs: [RunRecord], _ events: [EventRecord], helpers: [HelperView] = []) -> ChatProjection {
        var chat: ChatProjection = ChatProjection(threadId: "thr_1")
        chat.view = ThreadView(
            revision: 1, thread: ThreadRecord(id: "thr_1", title: "Chat", source: "ui", createdAt: 1, updatedAt: 1, agentId: nil),
            summary: nil, runs: runs, older: false, newest: nil, newestEvents: [], newestEventsCut: false,
            queue: QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil), asks: [], helpers: helpers, background: [],
            reactions: [], drafts: [])
        for event in events {
            chat.runs[event.runId] = (chat.runs[event.runId] ?? RunTimeline()).adding(event) ?? chat.runs[event.runId]
        }
        chat.freshness = .live
        return chat
    }

    private func call(_ id: Int, _ call: String, _ tool: String) -> EventRecord {
        event(id, "tool_call", ["call_id": .string(call), "tool": .string(tool), "args": .object(["path": .string("/tmp/\(call)")])])
    }

    private func result(_ id: Int, _ call: String, ok: Bool) -> EventRecord {
        event(id, "tool_result", ["call_id": .string(call), "ok": .bool(ok), "text": .string("out \(call)")])
    }

    /// A working run with one of each kind of row the table names.
    private var working: ChatProjection {
        chat(
            [run("run_a", status: "running")],
            [
                call(10, "ok", "read_file"), result(11, "ok", ok: true),
                event(12, "assistant", ["text": .string("Looking"), "step": .int(1)]),
                call(13, "bad", "shell"), result(14, "bad", ok: false),
                call(15, "open", "wait_until"),
                event(16, "plan", ["steps": .array([.object(["text": .string("Read"), "status": .string("done")])])]),
                event(17, "approval_requested", ["approval_id": .string("apr_1"), "severity": .string("medium"), "summary": .string("Run rm -rf build")]),
            ])
    }

    func testEveryCellOfTheModeTable() {
        let ids: (ToolCallMode) -> [String] = { transcript(of: self.working, pending: [], mode: $0, streaming: true, context: .none).map(\.id) }
        XCTAssertEqual(ids(.showAll), ["u:run_a", "t:ok", "a:12", "t:bad", "t:open", "plan:run_a", "ask:apr_1"])
        XCTAssertEqual(ids(.hideSuccessful), ["u:run_a", "a:12", "t:bad", "t:open", "plan:run_a", "ask:apr_1"], "running and failed stay")
        XCTAssertEqual(ids(.hideAll), ["u:run_a", "a:12", "t:bad", "plan:run_a", "ask:apr_1"], "a failed tool still shows")
        XCTAssertEqual(ids(.zen), ["u:run_a", "ask:apr_1"], "Zen: the owner's words and the asks; the reply may not be the last")
    }

    func testAFinishedRunFoldsItsStepsAndTalliesThemWithItsDuration() {
        let finished: ChatProjection = chat(
            [run("run_a", status: "done")],
            [
                call(10, "r1", "read_file"), result(20, "r1", ok: true),
                call(30, "r2", "Read"), result(40, "r2", ok: true),
                call(50, "c1", "shell"), result(144, "c1", ok: true),
                event(150, "run_finished", ["status": .string("done"), "result": .string("All done")]),
            ])
        let items: [TranscriptItem] = transcript(of: finished, pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(items.map(\.id), ["u:run_a", "f:t:r1", "r:run_a"])
        guard case .work(let fold) = items[1] else { return XCTFail("the steps fold") }
        XCTAssertEqual(fold.seconds, 134, "from the first call to the last result")
        XCTAssertEqual(fold.tally.counts, [.read: 2, .command: 1])
        XCTAssertFalse(fold.hasFailure)
        XCTAssertEqual(fold.items.map(\.id), ["t:r1", "t:r2", "t:c1"])
    }

    func testAFoldWithAFailureSaysSoAndZenDropsTheFoldButKeepsTheAnswer() {
        let finished: ChatProjection = chat(
            [run("run_a", status: "done")],
            [
                call(10, "c1", "shell"), result(11, "c1", ok: false),
                event(12, "assistant", ["text": .string("Trying again"), "step": .int(2)]),
                event(13, "run_finished", ["status": .string("done"), "result": .string("Fixed")]),
            ])
        let items: [TranscriptItem] = transcript(of: finished, pending: [], mode: .showAll, streaming: true, context: .none)
        guard case .work(let fold) = items[1] else { return XCTFail("the steps fold") }
        XCTAssertTrue(fold.hasFailure, "a failed fold is drawn open")
        XCTAssertEqual(fold.items.map(\.id), ["t:c1", "a:12"], "the passing words fold with the steps")
        XCTAssertEqual(transcript(of: finished, pending: [], mode: .zen, streaming: true, context: .none).map(\.id), ["u:run_a", "r:run_a"])
    }

    func testTheOwnersSteerSplitsTheFoldAndStaysVisible() {
        let finished: ChatProjection = chat(
            [run("run_a", status: "done")],
            [
                call(10, "c1", "shell"), result(11, "c1", ok: true),
                event(12, "steer", ["text": .string("Also the logs")]),
                call(13, "c2", "grep"), result(14, "c2", ok: true),
                event(15, "run_finished", ["status": .string("done"), "result": .string("Done")]),
            ])
        XCTAssertEqual(
            transcript(of: finished, pending: [], mode: .showAll, streaming: true, context: .none).map(\.id), ["u:run_a", "f:t:c1", "s:12", "f:t:c2", "r:run_a"])
    }

    func testAnAskStaysInPlaceWithWhoAnswered() {
        let asked: [EventRecord] = [
            event(10, "approval_requested", ["approval_id": .string("apr_1"), "severity": .string("medium"), "summary": .string("Send the mail")]),
            event(11, "approval_decided", ["approval_id": .string("apr_1"), "status": .string("approved"), "note": .string("")]),
            event(12, "approval_requested", ["approval_id": .string("apr_2"), "severity": .string("question"), "summary": .string("q"),
                "args": .object(["question": .string("Which day?")])]),
            event(13, "approval_decided", ["approval_id": .string("apr_2"), "status": .string("expired"), "note": .string("")]),
            event(14, "approval_auto", ["approval_id": .string("apr_3"), "severity": .string("low"), "summary": .string("Read a file"),
                "status": .string("approved"), "decided_by": .string("review")]),
        ]
        let items: [TranscriptItem] = transcript(
            of: chat([run("run_a", status: "running")], asked), pending: [], mode: .zen, streaming: true,
            context: TranscriptContext(answeredHere: ["apr_1"]))
        let anchors: [AskAnchor] = items.compactMap { if case .decision(let anchor) = $0 { anchor } else { nil } }
        XCTAssertEqual(anchors.map(\.status), ["approved", "expired", "approved"])
        XCTAssertEqual(anchors.map(\.decider), [.thisPhone, .nobody, .automatic("review")])
        XCTAssertEqual(anchors[1].summary, "Which day?", "a question's own words")
        let asking: [TranscriptItem] = transcript(
            of: chat([run("run_a", status: "running")], [call(9, "q1", "ask_user")] + asked), pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertFalse(asking.contains { $0.id == "t:q1" }, "the ask's own tool call is not drawn beside its anchor")
        let elsewhere: [TranscriptItem] = transcript(of: chat([run("run_a", status: "running")], asked), pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertTrue(elsewhere.contains { if case .decision(let anchor) = $0 { anchor.decider == .elsewhere } else { false } })
    }

    func testAUsageLimitIsItsOwnCardAndAFailureSaysItsCauseInOneLine() {
        let limited: ChatProjection = chat(
            [run("run_a", status: "failed")],
            [event(10, "run_finished", ["status": .string("failed"), "result": .string(""),
                "error": .object(["kind": .string("plan_limit"), "message": .string("limit"), "context": .object(["resets_at": .double(1_800_000_000)])])])])
        XCTAssertEqual(transcript(of: limited, pending: [], mode: .showAll, streaming: true, context: .none).last, .limit(id: "e:run_a", resetsAt: 1_800_000_000))
        let failed: ChatProjection = chat(
            [run("run_a", status: "failed")],
            [event(10, "run_finished", ["status": .string("failed"), "result": .string(""),
                "error": .object(["kind": .string("model"), "message": .string("The model answered 500\ntrace…")])])])
        XCTAssertEqual(
            transcript(of: failed, pending: [], mode: .zen, streaming: true, context: .none).last, .ended(id: "e:run_a", status: "failed", detail: "The model answered 500"))
        let worded: ChatProjection = chat(
            [run("run_a", status: "failed")],
            [event(10, "run_finished", ["status": .string("failed"), "result": .string("Internal error: boom")])])
        XCTAssertEqual(
            transcript(of: worded, pending: [], mode: .showAll, streaming: true, context: .none).map(\.id), ["u:run_a", "e:run_a"], "the error's words stand once, in the card")
    }

    func testTheHelpersOfARunShowAsOneGroupAndAReactionFollowUpDrawsNoOwnersMessage() {
        let helper: HelperView = HelperView(
            runId: "run_h", parentRunId: "run_a", threadId: "thr_h", agentId: nil, title: "Check the tests", status: "running", startedAt: 2,
            finishedAt: nil, provider: "claude", model: "opus", effort: "medium", step: nil, blocking: false, stop: "/api/runs/run_h/cancel")
        let items: [TranscriptItem] = transcript(
            of: chat([run("run_a", status: "running")], [call(10, "d1", "delegate")], helpers: [helper]), pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(items.map(\.id), ["u:run_a", "t:d1", "h:run_a"])
        let follow: RunRecord = RunRecord(
            id: "run_f", threadId: "thr_1", goal: "The owner reacted 👎", mode: "task", model: "m", status: "done", createdAt: 5, finishedAt: 6,
            result: "Sorry, fixed", parentRunId: nil, source: "ui", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: "wrong")
        XCTAssertEqual(transcript(of: chat([follow], []), pending: [], mode: .showAll, streaming: true, context: .none).map(\.id), ["r:run_f"])
    }

    func testAToolRowKeepsItsArgumentsOutputAndScreenshots() {
        let shot: ChatProjection = chat(
            [run("run_a", status: "running")],
            [
                event(10, "tool_call", ["call_id": .string("s1"), "tool": .string("browser_click"), "args": .object(["url": .string("https://x.test")])]),
                event(11, "tool_result", ["call_id": .string("s1"), "ok": .bool(true), "text": .string("clicked"), "images": .array([.string("shot_1.png")])]),
            ])
        guard case .tool(let entry) = transcript(of: shot, pending: [], mode: .showAll, streaming: true, context: .none)[1] else { return XCTFail("a tool row") }
        XCTAssertEqual(entry.kind, .web)
        XCTAssertEqual(entry.args, "url: https://x.test")
        XCTAssertEqual(entry.output, "clicked")
        XCTAssertEqual(entry.images, ["shot_1.png"])
        XCTAssertEqual(entry.state, .done)
    }

    func testReactionTargetsNameTheAnswerAndTheStep() {
        let chat: ChatProjection = chat([run("run_a", status: "running")], [event(10, "assistant", ["text": .string("x"), "step": .int(3)])])
        XCTAssertEqual(reactionTarget(of: "a:10", in: chat)?.message, "step-3")
        XCTAssertEqual(reactionTarget(of: "r:run_a", in: chat)?.message, "answer")
        XCTAssertNil(reactionTarget(of: "w:run_a", in: chat))
    }

    func testToolKindsReadDotsAndTheClaudePlansNames() {
        XCTAssertEqual(["read_file", "Read", "shell", "Bash", "edit_file", "Write", "grep", "Glob", "wait_until", "browser_open", "WebFetch",
            "screen_click", "delegate", "Task", "remember"].map { ToolKind.of($0) },
            [.read, .read, .command, .command, .edit, .edit, .search, .search, .wait, .web, .web, .screen, .helper, .helper, .other])
    }
}
