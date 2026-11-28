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
                event(12, "assistant", ["text": .string("Looking"), "thinking": .string("Which file first?"), "step": .int(1)]),
                call(13, "bad", "shell"), result(14, "bad", ok: false),
                call(15, "open", "wait_until"),
                event(16, "plan", ["steps": .array([.object(["text": .string("Read"), "status": .string("done")])])]),
                event(17, "approval_requested", ["approval_id": .string("apr_1"), "severity": .string("medium"), "summary": .string("Run rm -rf build")]),
            ])
    }

    func testEveryCellOfTheModeTable() {
        let ids: (ToolCallMode) -> [String] = { transcript(of: self.working, pending: [], mode: $0, streaming: true, context: .none).map(\.id) }
        XCTAssertEqual(ids(.showAll), ["u:run_a", "t:ok", "th:run_a:1", "a:12", "t:bad", "t:open", "plan:run_a", "ask:apr_1"])
        XCTAssertEqual(ids(.hideSuccessful), ["u:run_a", "th:run_a:1", "a:12", "t:bad", "t:open", "plan:run_a", "ask:apr_1"], "running and failed stay")
        XCTAssertEqual(ids(.hideAll), ["u:run_a", "a:12", "t:bad", "plan:run_a", "ask:apr_1"], "a failed tool still shows; the live line says the thinking")
        XCTAssertEqual(ids(.zen), ["u:run_a", "ask:apr_1"], "Zen: the owner's words and the asks; the reply may not be the last, no thinking")
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
        XCTAssertTrue(fold.hasFailure)
        XCTAssertTrue(fold.startsOpen, "a failed fold is drawn open beside the steps")
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
            event(15, "approval_auto", ["approval_id": .string("apr_4"), "severity": .string("high"), "summary": .string("Delete the backups"),
                "status": .string("denied"), "decided_by": .string("review")]),
        ]
        let items: [TranscriptItem] = transcript(
            of: chat([run("run_a", status: "running")], asked), pending: [], mode: .zen, streaming: true,
            context: TranscriptContext(answeredHere: ["apr_1"]))
        let anchors: [AskAnchor] = items.compactMap { if case .decision(let anchor) = $0 { anchor } else { nil } }
        XCTAssertEqual(anchors.map(\.status), ["approved", "expired", "denied"], "what the policy allowed with nobody asked is not drawn")
        XCTAssertEqual(anchors.map(\.decider), [.thisPhone, .nobody, .automatic("review")])
        for mode in ToolCallMode.allCases {
            let ids: [String] = transcript(of: chat([run("run_a", status: "running")], asked), pending: [], mode: mode, streaming: true, context: .none).map(\.id)
            XCTAssertFalse(ids.contains("ask:apr_3"), "an allowed ask nobody answered is in no mode (\(mode))")
            XCTAssertTrue(ids.contains("ask:apr_4"), "a refusal stays in every mode (\(mode))")
        }
        XCTAssertEqual(anchors[1].summary, "Which day?", "a question's own words")
        let asking: [TranscriptItem] = transcript(
            of: chat([run("run_a", status: "running")], [call(9, "q1", "ask_user")] + asked), pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertFalse(asking.contains { $0.id == "t:q1" }, "the ask's own tool call is not drawn beside its anchor")
        let elsewhere: [TranscriptItem] = transcript(of: chat([run("run_a", status: "running")], asked), pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertTrue(elsewhere.contains { if case .decision(let anchor) = $0 { anchor.decider == .elsewhere } else { false } })
    }

    /// The footer (copy, reaction, time) is on the final answer alone, once the run ended: none on the passing words
    /// while it works, none on the last words before it ended.
    func testOnlyTheFinalAnswerOfAnEndedRunHasItsFooter() {
        let steps: [EventRecord] = [
            event(10, "assistant", ["text": .string("Checking the disk"), "step": .int(1)]), call(11, "c1", "shell"), result(12, "c1", ok: true),
            event(13, "assistant", ["text": .string("All clear"), "step": .int(2)]),
        ]
        let ended: (TranscriptItem) -> Double?? = { if case .answer(_, _, _, let at) = $0 { at } else { nil } }
        let working: [TranscriptItem] = transcript(of: chat([run("run_a", status: "running")], steps), pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(working.compactMap(ended), [nil, nil], "no footer while the run works")
        let done: [TranscriptItem] = transcript(
            of: chat([run("run_a", status: "done")], steps + [event(14, "run_finished", ["status": .string("done"), "result": .string("")])]),
            pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(done.last.flatMap(ended), .some(14), "the final answer has the footer with the time the run ended")
        XCTAssertEqual(done.filter { if case .answer(_, _, _, .some) = $0 { true } else { false } }.count, 1)
    }

    /// Item 2070: a run done as far as it can go and waiting on the owner (`waiting`), and a failed run that said what it
    /// needs (item 1061), end "Waiting for you" with what it needs, their answer above; never "The run failed".
    func testARunThatWaitsOnTheOwnerEndsWaitingWithWhatItNeeds() {
        let waiting: ChatProjection = chat(
            [run("run_a", status: "done")],
            [event(10, "run_finished", ["status": .string("done"), "result": .string("Four forms are filled."),
                "needs": .string("Press Submit in the four tabs.\nAnswer the Tampere question."), "waiting": .bool(true)])])
        let ended: [TranscriptItem] = transcript(of: waiting, pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(ended.last, .ended(id: "e:run_a", status: waitingEnd, detail: "Press Submit in the four tabs."))
        XCTAssertTrue(ended.contains { if case .answer(_, "Four forms are filled.", _, _) = $0 { true } else { false } })
        let said: ChatProjection = chat(
            [run("run_a", status: "failed")],
            [event(10, "run_finished", ["status": .string("failed"), "result": .string("The bank asks for a code."),
                "needs": .string("The six-digit code.")])])
        let failedWaiting: [TranscriptItem] = transcript(of: said, pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(failedWaiting.last, .ended(id: "e:run_a", status: waitingEnd, detail: "The six-digit code."))
        XCTAssertTrue(failedWaiting.contains { if case .answer(_, "The bank asks for a code.", _, _) = $0 { true } else { false } })
        let helper: RunRecord = RunRecord(
            id: "run_a", threadId: "thr_1", goal: "goal of run_a", mode: "task", model: "m", status: "failed", createdAt: 1, finishedAt: nil,
            result: "", parentRunId: "run_p", source: "ui", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        let helped: ChatProjection = chat(
            [helper],
            [event(10, "run_finished", ["status": .string("failed"), "result": .string("No code."), "needs": .string("The code.")])])
        XCTAssertEqual(
            transcript(of: helped, pending: [], mode: .showAll, streaming: true, context: .none).last,
            .ended(id: "e:run_a", status: "failed", detail: "No code."), "a helper's needs are the run's that asked it")
        let done: ChatProjection = chat(
            [run("run_a", status: "done")],
            [event(10, "run_finished", ["status": .string("done"), "result": .string("Done."), "needs": .string("")])])
        XCTAssertFalse(
            transcript(of: done, pending: [], mode: .showAll, streaming: true, context: .none).contains { if case .ended = $0 { true } else { false } })
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
        let welcome: RunRecord = RunRecord(
            id: "run_w", threadId: "thr_1", goal: "The owner has just made you (Scout)", mode: "chat", model: "m", status: "done", createdAt: 5, finishedAt: 6,
            result: "Hi, I am Scout", parentRunId: nil, source: "welcome", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        XCTAssertEqual(
            transcript(of: chat([welcome], []), pending: [], mode: .showAll, streaming: true, context: .none).map(\.id), ["r:run_w"],
            "a new agent speaks first: Dot's words to it are not the owner's message")
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

    // MARK: The live line (item 1769)

    private func now(_ events: [EventRecord], stopping: Bool = false) -> WorkNow? {
        workNow(of: chat([run("run_a", status: "running")], events), run: "run_a", stopping: stopping)
    }

    func testTheLiveLineSaysWhatTheRunDoesFromItsEvents() {
        let started: EventRecord = event(1, "run_started", [:])
        XCTAssertEqual(now([event(1, "run_queued", ["goal": .string("x")])]), .queued, "no run_started yet")
        XCTAssertEqual(now([started]), .typing, "asked its model: the typing dots")
        let shell: EventRecord = event(2, "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object(["command": .string("swift test")])])
        guard case .tool(let step)? = now([started, shell]) else { return XCTFail("a call at work") }
        XCTAssertEqual(step.tool, "shell")
        XCTAssertEqual(step.target, "swift test")
        XCTAssertEqual(now([started, shell, result(3, "c1", ok: true)]), .typing, "its result back: the model is asked again")
        let handoff: EventRecord = event(4, "tool_call", ["call_id": .string("c2"), "tool": .string("delegate"), "args": .object(["agent": .string("Ada"), "goal": .string("g")])])
        XCTAssertEqual(now([started, handoff]), .handoff(agent: "Ada"))
        let finish: EventRecord = event(5, "tool_call", ["call_id": .string("c3"), "tool": .string("finish"), "args": .object([:])])
        XCTAssertEqual(now([started, finish]), .typing, "finish is said by the answer, not the line")
        let ask: EventRecord = event(6, "approval_requested", ["approval_id": .string("apr_1")])
        XCTAssertEqual(now([started, ask]), .waitingForYou)
        XCTAssertEqual(now([started, ask, event(7, "approval_decided", ["approval_id": .string("apr_1"), "status": .string("approved")])]), .typing)
        XCTAssertEqual(now([started, shell], stopping: true), .stopping)
        XCTAssertNil(now([started, event(8, "run_finished", ["status": .string("done")])]), "an ended run has no line")
    }

    func testTheLiveLineSaysWhatTheMessageToldWhileItsCallsRun() {
        let step: EventRecord = event(
            2, "assistant", ["step": .int(1), "text": .string("**Kanıt netleşti.** Son bir kontrol yapıyorum."), "tool_calls": .array([.string("shell")])])
        XCTAssertEqual(now([event(1, "run_started", [:]), step]), .told("Kanıt netleşti. Son bir kontrol yapıyorum"))
        let quiet: EventRecord = event(3, "assistant", ["step": .int(2), "text": .string("Done."), "tool_calls": .array([.string("finish")])])
        XCTAssertEqual(now([event(1, "run_started", [:]), step, quiet]), .typing, "a message that only finishes tells nothing next")
    }

    func testTheLiveThinkingAndWordsComeBeforeTheSteps() {
        var live: ChatProjection = chat([run("run_a", status: "running")], [event(1, "run_started", [:])])
        live.thinking["run_a"] = LiveThought(step: 1, text: "Which test fails?")
        XCTAssertEqual(workNow(of: live, run: "run_a", stopping: false), .thinking)
        live.writing["run_a"] = "The test"
        XCTAssertEqual(workNow(of: live, run: "run_a", stopping: false), .writing, "words in sight: the line steps aside")
    }

    func testHideAllFoldsAllOfAnEndedRunsStepsSoTheyStayReachable() {
        let finished: ChatProjection = chat(
            [run("run_a", status: "done")],
            [
                event(9, "assistant", ["text": .string(""), "thinking": .string("Read the hosts file first."), "step": .int(1)]),
                call(10, "r1", "read_file"), result(20, "r1", ok: true),
                event(150, "run_finished", ["status": .string("done"), "result": .string("All done")]),
            ])
        let items: [TranscriptItem] = transcript(of: finished, pending: [], mode: .hideAll, streaming: true, context: .none)
        XCTAssertEqual(items.map(\.id), ["u:run_a", "f:th:run_a:1", "r:run_a"], "the fold, then the answer")
        guard case .work(let fold) = items[1] else { return XCTFail("the steps fold") }
        XCTAssertEqual(fold.items.map(\.id), ["th:run_a:1", "t:r1"], "the thought and the successful read are in it")
        XCTAssertEqual(transcript(of: finished, pending: [], mode: .zen, streaming: true, context: .none).map(\.id), ["u:run_a", "r:run_a"], "Zen: the answer alone")
    }

    // MARK: Working or waiting on helpers (the owner's report of 2026-11-27)

    private func delegate(_ id: Int, _ call: String, wait: Bool?) -> EventRecord {
        var args: [String: JSONValue] = ["goal": .string("part \(call)")]
        if let wait { args["wait"] = .bool(wait) }
        return event(id, "tool_call", ["call_id": .string(call), "tool": .string("delegate"), "args": .object(args)])
    }

    /// The capsule says "Waiting for N helpers" only while the main run has stopped for them: every open call of its newest
    /// step waits on a helper. While another call of that step runs, while it thinks or writes, or once the waits were
    /// answered, the run works.
    func testTheRunWaitsOnHelpersOnlyWhileEveryOpenCallIsAWaitOnOne() {
        let step: EventRecord = event(10, "assistant", ["text": .string(""), "step": .int(1)])
        let waits: ChatProjection = chat([run("run_a", status: "running")], [step, delegate(11, "d1", wait: nil), delegate(12, "d2", wait: true)])
        XCTAssertTrue(waitsOnHelpers(of: waits, run: "run_a"), "two delegates open, nothing else: it waits")

        let beside: ChatProjection = chat([run("run_a", status: "running")], [step, delegate(11, "d1", wait: nil), call(12, "sh", "shell")])
        XCTAssertFalse(waitsOnHelpers(of: beside, run: "run_a"), "a command of the same step still runs: it works")

        let shellDone: ChatProjection = chat(
            [run("run_a", status: "running")], [step, delegate(11, "d1", wait: nil), call(12, "sh", "shell"), result(13, "sh", ok: true)])
        XCTAssertTrue(waitsOnHelpers(of: shellDone, run: "run_a"), "the command ended, the delegate is left: it waits")

        let noWait: ChatProjection = chat([run("run_a", status: "running")], [step, delegate(11, "d1", wait: false)])
        XCTAssertFalse(waitsOnHelpers(of: noWait, run: "run_a"), "a helper it asked not to wait for holds nothing")

        let answered: ChatProjection = chat(
            [run("run_a", status: "running")],
            [step, delegate(11, "d1", wait: nil), result(12, "d1", ok: true), event(13, "assistant", ["text": .string("Next"), "step": .int(2)])])
        XCTAssertFalse(waitsOnHelpers(of: answered, run: "run_a"), "the report came and a new step began: it works")

        var thinking: ChatProjection = waits
        thinking.writing["run_a"] = "Reading the reports"
        XCTAssertFalse(waitsOnHelpers(of: thinking, run: "run_a"), "words streaming: it works")

        let idle: ChatProjection = chat([run("run_a", status: "running")], [step])
        XCTAssertFalse(waitsOnHelpers(of: idle, run: "run_a"), "no open call: the model works on its next step")

        let ended: ChatProjection = chat([run("run_a", status: "done")], [step, delegate(11, "d1", wait: nil), event(12, "run_finished", [:])])
        XCTAssertFalse(waitsOnHelpers(of: ended, run: "run_a"))
    }

    // MARK: The plan once its run ended (the owner's report of 2026-11-28)

    func testAFailedOrStoppedRunsPlanShowsItsStepFailedOrStopped() {
        let steps: [PlanStep] = [
            PlanStep(text: "Read the CV", status: "done"), PlanStep(text: "Search LinkedIn", status: "in_progress"),
            PlanStep(text: "Write the letters", status: "pending"),
        ]
        XCTAssertEqual(PlanEnd(runStatus: "failed"), .failed)
        XCTAssertEqual(PlanEnd(runStatus: "cancelled"), .stopped)
        XCTAssertEqual(PlanEnd(runStatus: "running"), .working)
        XCTAssertEqual(PlanEnd(runStatus: nil), .working)
        XCTAssertEqual(planAtEnd(steps, end: .failed).map(\.status), ["done", "failed", "pending"])
        XCTAssertEqual(planAtEnd(steps, end: .stopped).map(\.status), ["done", "stopped", "pending"])
        XCTAssertEqual(planAtEnd(steps, end: .working), steps, "at work the step stays in progress")
        XCTAssertEqual(planCurrentStep(planAtEnd(steps, end: .failed)), 2)
        XCTAssertEqual(planCurrentStep([PlanStep(text: "a", status: "done"), PlanStep(text: "b", status: "pending")]), 2)
        XCTAssertNil(planCurrentStep([PlanStep(text: "a", status: "done"), PlanStep(text: "b", status: "skipped")]))
    }
}
