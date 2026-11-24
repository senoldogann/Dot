import XCTest

@testable import DotEngine

/// The pure rules of the sync (plan §9, R-b §8 #19): scripted frame sequences, no network, no clock.
final class ReducerTests: XCTestCase {
    private func event(_ id: Int, _ run: String, _ kind: String, _ data: [String: JSONValue]) -> EventRecord {
        EventRecord(id: id, runId: run, ts: Double(id), kind: kind, data: data)
    }

    private func record(_ id: String, status: String, at: Double) -> RunRecord {
        RunRecord(
            id: id, threadId: "thr_1", goal: "goal of \(id)", mode: "task", model: "m", status: status, createdAt: at, finishedAt: nil,
            result: "", parentRunId: nil, source: "ui", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
    }

    private func view(revision: Int, runs: [RunRecord], events: [EventRecord]) -> ThreadView {
        ThreadView(
            revision: revision, thread: ThreadRecord(id: "thr_1", title: "Chat", source: "ui", createdAt: 1, updatedAt: 1, agentId: nil),
            summary: nil, runs: runs, older: false,
            newest: runs.last.map { RunDetail(run: $0, title: "Chat", active: $0.isActive, held: false, choices: nil) },
            newestEvents: events, newestEventsCut: false, queue: QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil),
            asks: [], helpers: [], background: [], reactions: [], drafts: [])
    }

    /// A chat whose stream is open on connection 1, with no view yet.
    private func opened() -> (ChatProjection, Int) {
        let (opening, effects): (ChatProjection, [SyncEffect]) = ChatReducer.opening(ChatProjection(threadId: "thr_1"))
        XCTAssertEqual(effects, [.readView(thread: "thr_1")], "the view is read beside the stream, not after it")
        let asked: ChatProjection = ChatReducer.viewAsked(opening, connection: opening.marks.connection)
        return (ChatReducer.opened(asked, connection: asked.marks.connection), asked.marks.connection)
    }

    private func live(revision: Int, runs: [RunRecord]) -> (ChatProjection, Int) {
        let (open, connection): (ChatProjection, Int) = opened()
        let (loaded, _): (ChatProjection, [SyncEffect]) = ChatReducer.viewLoaded(open, view: view(revision: revision, runs: runs, events: []), connection: connection)
        let (synced, _): (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            loaded, .synchronized(Synchronized(revision: revision, runs: [:])), connection: connection)
        XCTAssertEqual(synced.freshness, .live)
        return (synced, connection)
    }

    // MARK: Events

    func testDuplicatesAndALateLowerIdAcrossTwoRunsAreEachKeptOnceInIdOrder() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 5, runs: [record("run_a", status: "running", at: 1)])
        let frames: [StreamFrame] = [
            .event(event(10, "run_a", "tool_call", ["call_id": .string("c1"), "tool": .string("shell")])),
            .event(event(12, "run_b", "tool_call", ["call_id": .string("c2"), "tool": .string("shell")])),
            .event(event(13, "run_a", "assistant", ["text": .string("a"), "step": .int(1)])),
            // The stream reads each run a little behind its cursor: the same event again.
            .event(event(10, "run_a", "tool_call", ["call_id": .string("c1"), "tool": .string("shell")])),
            // A `dotx` run relayed late: an id lower than one already sent, still new.
            .event(event(11, "run_a", "tool_result", ["call_id": .string("c1"), "ok": .bool(true)])),
            .event(event(12, "run_b", "tool_call", ["call_id": .string("c2"), "tool": .string("shell")])),
        ]
        for frame in frames { chat = ChatReducer.frame(chat, frame, connection: connection).0 }
        XCTAssertEqual(chat.runs["run_a"]?.events.map(\.id), [10, 11, 13], "11 came after 13 and is still taken, in id order")
        XCTAssertEqual(chat.runs["run_b"]?.events.map(\.id), [12])
        XCTAssertEqual(chat.cursors, ["run_a": 13, "run_b": 12])
        XCTAssertEqual(chat.cursorQuery, "run_a:13,run_b:12")
    }

    func testWordsBeingWrittenGoOnlyOnTheOpenStepAndEndWithItsStoredReply() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 5, runs: [record("run_a", status: "running", at: 1)])
        let delta: (String, Int) -> StreamFrame = { text, step in
            .delta(EventRecord(id: 0, runId: "run_a", ts: 0, kind: "delta", data: ["kind": .string("text"), "text": .string(text), "step": .int(step)]))
        }
        chat = ChatReducer.frame(chat, delta("Hel", 1), connection: connection).0
        chat = ChatReducer.frame(chat, delta("lo", 1), connection: connection).0
        XCTAssertEqual(chat.writing["run_a"], "Hello")
        chat = ChatReducer.frame(chat, .event(event(20, "run_a", "assistant", ["text": .string("Hello"), "step": .int(1)])), connection: connection).0
        XCTAssertNil(chat.writing["run_a"], "the stored reply ends the words being written")
        chat = ChatReducer.frame(chat, delta("late", 1), connection: connection).0
        XCTAssertNil(chat.writing["run_a"], "words of a step already stored are not drawn twice")
        chat = ChatReducer.frame(chat, .event(event(21, "run_a", "run_finished", ["status": .string("done")])), connection: connection).0
        chat = ChatReducer.frame(chat, delta("after", 2), connection: connection).0
        XCTAssertNil(chat.writing["run_a"], "an ended run takes no words")
        let broken: ChatProjection = ChatReducer.broke(ChatReducer.frame(chat, delta("x", 3), connection: connection).0, connection: connection)
        XCTAssertTrue(broken.writing.isEmpty, "a break drops the live words: the stored event brings them back")
    }

    func testThinkingGoesOnlyOnTheOpenStepAndAResetDropsIt() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 5, runs: [record("run_a", status: "running", at: 1)])
        let delta: (String, String, Int) -> StreamFrame = { kind, text, step in
            .delta(EventRecord(id: 0, runId: "run_a", ts: 0, kind: "delta", data: ["kind": .string(kind), "text": .string(text), "step": .int(step)]))
        }
        chat = ChatReducer.frame(chat, delta("thinking", "Which ", 1), connection: connection).0
        chat = ChatReducer.frame(chat, delta("thinking", "day?", 1), connection: connection).0
        XCTAssertEqual(chat.thinking["run_a"], LiveThought(step: 1, text: "Which day?"))
        chat = ChatReducer.frame(chat, delta("reset", "", 1), connection: connection).0
        XCTAssertNil(chat.thinking["run_a"], "a step asked again starts its thinking again")
        chat = ChatReducer.frame(chat, delta("thinking", "Monday", 1), connection: connection).0
        chat = ChatReducer.frame(chat, .event(event(20, "run_a", "assistant", ["text": .string(""), "step": .int(1)])), connection: connection).0
        XCTAssertNil(chat.thinking["run_a"], "the stored reply ends the live thinking")
        chat = ChatReducer.frame(chat, delta("thinking", "late", 1), connection: connection).0
        XCTAssertNil(chat.thinking["run_a"], "thinking of a stored step is not drawn again")
        chat = ChatReducer.frame(chat, delta("thinking", "next", 2), connection: connection).0
        XCTAssertEqual(chat.thinking["run_a"]?.step, 2)
        XCTAssertTrue(ChatReducer.broke(chat, connection: connection).thinking.isEmpty, "a break drops the live thinking")
    }

    /// The pre-push review of item 1756: a view read that brings the step's stored reply before the stream does ends the
    /// live thinking and words, so the stored thought is not drawn beside a live one under the same id.
    func testAViewThatBringsTheStoredStepFirstEndsTheLiveThinking() {
        let running: RunRecord = record("run_a", status: "running", at: 1)
        var (chat, connection): (ChatProjection, Int) = live(revision: 5, runs: [running])
        let delta: (String) -> StreamFrame = { kind in
            .delta(EventRecord(id: 0, runId: "run_a", ts: 0, kind: "delta", data: ["kind": .string(kind), "text": .string("Which day"), "step": .int(1)]))
        }
        chat = ChatReducer.frame(chat, delta("thinking"), connection: connection).0
        chat = ChatReducer.frame(chat, delta("text"), connection: connection).0
        let stored: EventRecord = event(20, "run_a", "assistant", ["text": .string("Which day"), "thinking": .string("Which day"), "step": .int(1)])
        chat = ChatReducer.viewLoaded(chat, view: view(revision: 6, runs: [running], events: [stored]), connection: connection).0
        chat = ChatReducer.frame(chat, .event(stored), connection: connection).0
        XCTAssertNil(chat.thinking["run_a"])
        XCTAssertNil(chat.writing["run_a"])
        let ids: [String] = transcript(of: chat, pending: [], mode: .showAll, streaming: true, context: .none).map(\.id)
        XCTAssertEqual(ids.filter { $0 == "th:run_a:1" }.count, 1, "one thought row: \(ids)")
    }

    func testAnUnknownKindMovesTheCursorAndDrawsNothing() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 5, runs: [record("run_a", status: "running", at: 1)])
        let message: SSEMessage = SSEMessage(
            event: "brand_new_kind", data: #"{"id":30,"run_id":"run_a","ts":1,"kind":"brand_new_kind","data":{"x":1}}"#, id: "30")
        let frame: StreamFrame = readFrame(message)
        guard case .event = frame else { return XCTFail("an unknown stored kind is an event, kept for its cursor") }
        let before: [TranscriptItem] = transcript(of: chat, pending: [], mode: .showAll, streaming: true, context: .none)
        chat = ChatReducer.frame(chat, frame, connection: connection).0
        XCTAssertEqual(chat.runs["run_a"]?.cursor, 30)
        XCTAssertEqual(transcript(of: chat, pending: [], mode: .showAll, streaming: true, context: .none), before, "nothing is drawn for it")
        let liveOnly: StreamFrame = readFrame(SSEMessage(event: "mystery", data: #"{"id":0,"run_id":"run_a","ts":1,"kind":"mystery","data":{}}"#, id: nil))
        XCTAssertEqual(ChatReducer.frame(chat, liveOnly, connection: connection).0, chat, "a live-only unknown kind changes nothing")
    }

    func testADecodeDefectReadsTheViewAgainAndCountsWhatCouldNotBeShown() {
        let (chat, connection): (ChatProjection, Int) = live(revision: 5, runs: [record("run_a", status: "running", at: 1)])
        let frame: StreamFrame = readFrame(SSEMessage(event: "tool_call", data: "{not json", id: "40"))
        XCTAssertEqual(frame, .defect(id: 40, kind: "tool_call"))
        let (next, effects): (ChatProjection, [SyncEffect]) = ChatReducer.frame(chat, frame, connection: connection)
        XCTAssertEqual(effects, [.readView(thread: "thr_1")])
        XCTAssertEqual(next.unreadable, 1)
        XCTAssertEqual(next.freshness, .syncing, "not live while it catches up")
        XCTAssertEqual(next.runs["run_a"]?.ids.contains(40), true, "the cursor goes past it, so it is not asked for forever")
    }

    // MARK: Freshness

    func testLiveOnlyOnceTheViewOfThisConnectionAndSynchronizedAgree() {
        let (open, connection): (ChatProjection, Int) = opened()
        XCTAssertEqual(open.freshness, .empty)
        // The view first: not live until the stream says it caught up.
        let (viewFirst, _): (ChatProjection, [SyncEffect]) = ChatReducer.viewLoaded(open, view: view(revision: 7, runs: [], events: []), connection: connection)
        XCTAssertNotEqual(viewFirst.freshness, .live, "a view alone is not live: the stream has not said synchronized")
        XCTAssertEqual(
            ChatReducer.frame(viewFirst, .synchronized(Synchronized(revision: 7, runs: [:])), connection: connection).0.freshness, .live)
        let (synced, early): (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            open, .synchronized(Synchronized(revision: 7, runs: [:])), connection: connection)
        XCTAssertNotEqual(synced.freshness, .live, "synchronized alone is not live: the view has not come")
        XCTAssertEqual(early, [], "the read under way is not asked twice")
        // The view was read before a change the stream then told: its revision is older than synchronized's.
        let (stale, effects): (ChatProjection, [SyncEffect]) = ChatReducer.viewLoaded(
            synced, view: view(revision: 6, runs: [], events: []), connection: connection)
        XCTAssertEqual(stale.freshness, .syncing)
        XCTAssertEqual(effects, [.readView(thread: "thr_1")], "a view older than synchronized is read again")
        let again: ChatProjection = ChatReducer.viewAsked(stale, connection: connection)
        let (fresh, none): (ChatProjection, [SyncEffect]) = ChatReducer.viewLoaded(
            again, view: view(revision: 7, runs: [], events: []), connection: connection)
        XCTAssertEqual(fresh.freshness, .live)
        XCTAssertEqual(none, [])
    }

    func testAViewOfAnEarlierConnectionDoesNotMakeANewOneLive() {
        let (chat, first): (ChatProjection, Int) = live(revision: 3, runs: [])
        let broken: ChatProjection = ChatReducer.broke(chat, connection: first)
        XCTAssertEqual(broken.freshness, .cached, "what is held stays, marked as not live")
        let (reopened, effects): (ChatProjection, [SyncEffect]) = ChatReducer.opening(broken)
        XCTAssertEqual(effects, [.readView(thread: "thr_1")], "every break reads the view again")
        let open: ChatProjection = ChatReducer.opened(ChatReducer.viewAsked(reopened, connection: reopened.marks.connection), connection: reopened.marks.connection)
        let (synced, _): (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            open, .synchronized(Synchronized(revision: 3, runs: [:])), connection: open.marks.connection)
        XCTAssertNotEqual(synced.freshness, .live, "the held view was read on the connection before")
        let late: (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            synced, .synchronized(Synchronized(revision: 3, runs: [:])), connection: first)
        XCTAssertEqual(late.0, synced, "a frame of the old connection is not taken")
    }

    func testARevisionGapReadsTheViewOnceAndTheSameRevisionNothing() {
        let (chat, connection): (ChatProjection, Int) = live(revision: 9, runs: [])
        let (same, none): (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            chat, .threadChanged(ThreadChanged(threadId: "thr_1", revision: 9, deleted: false)), connection: connection)
        XCTAssertEqual(none, [])
        XCTAssertEqual(same.freshness, .live)
        let (moved, effects): (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            chat, .threadChanged(ThreadChanged(threadId: "thr_1", revision: 14, deleted: false)), connection: connection)
        XCTAssertEqual(effects, [.readView(thread: "thr_1")])
        XCTAssertEqual(moved.freshness, .syncing)
        let asked: ChatProjection = ChatReducer.viewAsked(moved, connection: connection)
        let (twice, owed): (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            asked, .threadChanged(ThreadChanged(threadId: "thr_1", revision: 15, deleted: false)), connection: connection)
        XCTAssertEqual(owed, [], "one read at a time")
        let (after, next): (ChatProjection, [SyncEffect]) = ChatReducer.viewLoaded(
            twice, view: view(revision: 14, runs: [], events: []), connection: connection)
        XCTAssertEqual(next, [.readView(thread: "thr_1")], "the change told while it was out is read once it ended")
        XCTAssertEqual(after.freshness, .syncing)
    }

    func testADeletedChatClosesItsStreamAndTakesNothingMore() {
        let (chat, connection): (ChatProjection, Int) = live(revision: 2, runs: [])
        let (gone, effects): (ChatProjection, [SyncEffect]) = ChatReducer.frame(
            chat, .threadChanged(ThreadChanged(threadId: "thr_1", revision: 3, deleted: true)), connection: connection)
        XCTAssertEqual(gone.freshness, .deleted)
        XCTAssertEqual(effects, [.closeStream(thread: "thr_1")])
        XCTAssertEqual(ChatReducer.frame(gone, .resync, connection: connection).0, gone)
        XCTAssertEqual(ChatReducer.opening(gone).1, [], "a deleted chat is not opened again")
        let (missing, closed): (ChatProjection, [SyncEffect]) = ChatReducer.viewFailed(chat, missing: true)
        XCTAssertEqual(missing.freshness, .deleted, "a 404 on the view says deleted, not unreachable")
        XCTAssertEqual(closed, [.closeStream(thread: "thr_1")])
    }

    func testARewoundRunLeavesThePictureWhenTheViewNoLongerHasIt() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 2, runs: [record("run_a", status: "done", at: 100), record("run_b", status: "done", at: 200)])
        chat = ChatReducer.frame(chat, .event(event(300, "run_b", "assistant", ["text": .string("b")])), connection: connection).0
        chat = ChatReducer.frame(chat, .event(event(299, "run_a", "assistant", ["text": .string("a")])), connection: connection).0
        let asked: ChatProjection = ChatReducer.viewAsked(chat, connection: connection)
        let (rewound, _): (ChatProjection, [SyncEffect]) = ChatReducer.viewLoaded(
            asked, view: view(revision: 3, runs: [record("run_a", status: "done", at: 100)], events: []), connection: connection)
        XCTAssertNil(rewound.runs["run_b"], "the rewound run's steps are gone")
        XCTAssertNotNil(rewound.runs["run_a"])
    }

    // MARK: The list

    func testTheListDropsADeletedChatAndAnOlderViewDoesNotBringItBack() {
        let row: (String, Int) -> ThreadRow = { id, revision in
            ThreadRow(
                summary: ThreadSummary(
                    id: id, title: id, source: "ui", createdAt: 1, updatedAt: 1, pinnedAt: nil, runCount: 1, latestRunId: "r",
                    latestStatus: "done", latestCreatedAt: 1, unread: false, actorId: nil, lastMessage: "", lastRole: "user"),
                revision: revision, asks: 0)
        }
        var (list, _): (ListProjection, [ListEffect]) = ListReducer.opening(ListProjection())
        let connection: Int = list.marks.connection
        list = ListReducer.opened(ListReducer.asked(list), connection: connection)
        list = ListReducer.loaded(list, view: ThreadsView(revision: 10, threads: [row("thr_a", 4), row("thr_b", 10)]), connection: connection).0
        XCTAssertEqual(list.freshness, .live)
        let (gone, none): (ListProjection, [ListEffect]) = ListReducer.frame(
            list, .threadChanged(ThreadChanged(threadId: "thr_a", revision: 11, deleted: true)), connection: connection)
        XCTAssertEqual(none, [])
        XCTAssertEqual(gone.rows.map(\.summary.id), ["thr_b"])
        let crossed: ListProjection = ListReducer.loaded(
            ListReducer.asked(gone), view: ThreadsView(revision: 11, threads: [row("thr_a", 4), row("thr_b", 10)]), connection: connection).0
        XCTAssertEqual(crossed.rows.map(\.summary.id), ["thr_b"], "a read that crossed the deletion does not bring the chat back")
        let (_, read): (ListProjection, [ListEffect]) = ListReducer.frame(
            gone, .threadChanged(ThreadChanged(threadId: "thr_b", revision: 12, deleted: false)), connection: connection)
        XCTAssertEqual(read, [.readList], "a renamed or moved chat reads the list again")
        XCTAssertEqual(ListReducer.frame(gone, .resync, connection: connection).1, [.readList])
    }

    // MARK: What the screens draw

    func testASteerWhileAToolIsOpenSaysDotReadsItAfterThisStep() {
        var timeline: RunTimeline = RunTimeline()
        let steer: EventRecord = event(52, "run_a", "steer", ["text": .string("Also check the logs")])
        for each in [event(50, "run_a", "tool_call", ["call_id": .string("w1"), "tool": .string("wait_until")]), steer] {
            timeline = timeline.adding(each) ?? timeline
        }
        XCTAssertTrue(readsAfterStep(steer: steer, in: timeline), "delivered, not read: the wait is still open (R-e #1)")
        timeline = timeline.adding(event(60, "run_a", "tool_result", ["call_id": .string("w1"), "ok": .bool(true)])) ?? timeline
        XCTAssertFalse(readsAfterStep(steer: steer, in: timeline), "the step ended: the message was read with it")
        var idle: RunTimeline = RunTimeline()
        idle = idle.adding(event(70, "run_a", "steer", ["text": .string("now")])) ?? idle
        XCTAssertFalse(readsAfterStep(steer: event(70, "run_a", "steer", ["text": .string("now")]), in: idle))
    }

    func testTheToolCallModesAndResponseStreamingFilterWithoutChangingThePicture() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 2, runs: [record("run_a", status: "running", at: 1)])
        let frames: [StreamFrame] = [
            .event(event(10, "run_a", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object(["command": .string("ls")])])),
            .event(event(11, "run_a", "tool_result", ["call_id": .string("c1"), "ok": .bool(true)])),
            .event(event(12, "run_a", "assistant", ["text": .string("Looking"), "step": .int(1)])),
            .event(event(13, "run_a", "tool_call", ["call_id": .string("c2"), "tool": .string("shell")])),
            .event(event(14, "run_a", "tool_result", ["call_id": .string("c2"), "ok": .bool(false)])),
            .delta(EventRecord(id: 0, runId: "run_a", ts: 0, kind: "delta", data: ["kind": .string("text"), "text": .string("Almost"), "step": .int(3)])),
        ]
        for frame in frames { chat = ChatReducer.frame(chat, frame, connection: connection).0 }
        let ids: (ToolCallMode, Bool) -> [String] = { mode, streaming in
            transcript(of: chat, pending: [], mode: mode, streaming: streaming, context: .none).map(\.id)
        }
        XCTAssertEqual(ids(.showAll, true), ["u:run_a", "t:c1", "a:12", "t:c2", "w:run_a"])
        XCTAssertEqual(ids(.hideSuccessful, true), ["u:run_a", "a:12", "t:c2", "w:run_a"], "a failure always shows")
        XCTAssertEqual(ids(.hideAll, true), ["u:run_a", "a:12", "t:c2", "w:run_a"], "a failed tool still shows (the mode table)")
        XCTAssertEqual(ids(.zen, true), ["u:run_a", "w:run_a"], "Zen keeps the last answer only")
        XCTAssertEqual(ids(.showAll, false), ["u:run_a", "t:c1", "a:12", "t:c2"], "streaming off: an answer shows once it is whole")
    }

    // MARK: Numbers

    func testTheReconnectWaitGrowsFromHalfASecondToFifteenWithAFifthAtRandom() {
        XCTAssertEqual(reconnectWait(attempt: 1, jitter: 0), 0.5)
        XCTAssertEqual(reconnectWait(attempt: 2, jitter: 0), 1)
        XCTAssertEqual(reconnectWait(attempt: 6, jitter: 0), 15)
        XCTAssertEqual(reconnectWait(attempt: 40, jitter: 1), 18, accuracy: 0.0001)
    }

    func testTheCacheIsShownUnderTwelveHoursAndResumedOnlyFromTheSameDaemon() {
        XCTAssertTrue(CacheRules.showable(schema: CacheRules.schema, savedAt: 1000, now: 1000 + 11 * 3600))
        XCTAssertFalse(CacheRules.showable(schema: CacheRules.schema, savedAt: 1000, now: 1000 + 12 * 3600))
        XCTAssertFalse(CacheRules.showable(schema: CacheRules.schema + 1, savedAt: 1000, now: 1001))
        XCTAssertTrue(CacheRules.resumable(schema: CacheRules.schema, savedAt: 1000, version: "1.2", daemonVersion: "1.2", now: 1001))
        XCTAssertFalse(CacheRules.resumable(schema: CacheRules.schema, savedAt: 1000, version: "1.2", daemonVersion: "1.3", now: 1001))
    }

    func testAPairingCodeIsEightLettersAndDigitsWhateverWasTypedAround() {
        XCTAssertEqual(normalizedPairingCode("ab12-cd34"), "AB12CD34")
        XCTAssertEqual(normalizedPairingCode(" AB12 CD34 "), "AB12CD34")
        XCTAssertNil(normalizedPairingCode("AB12CD3"))
        XCTAssertNil(normalizedPairingCode("AB12CD345"))
    }

    func testARunAHelpersReportStartedOpensWithDotsNoteNotTheOwnersWords() {
        let report: RunRecord = RunRecord(
            id: "run_r", threadId: "thr_1", goal: "A helper that worked in the background ended", mode: "task", model: "m", status: "done",
            createdAt: 2, finishedAt: 3, result: "", parentRunId: nil, source: "helper_report", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        let (chat, _): (ChatProjection, Int) = live(revision: 2, runs: [record("run_a", status: "done", at: 1), report])
        let items: [TranscriptItem] = transcript(of: chat, pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(items.first { $0.id == "u:run_r" }, .note(id: "u:run_r", text: "A helper that worked in the background ended"))
        XCTAssertEqual(items.first { $0.id == "u:run_a" }, .owner(id: "u:run_a", text: "goal of run_a", delivery: nil))
    }

    func testRepliesThatRestateTheAnswerAreShownOnceAsTheAnswer() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 2, runs: [record("run_a", status: "running", at: 1)])
        for frame: StreamFrame in [
            .event(event(10, "run_a", "assistant", ["text": .string("Booked"), "step": .int(1)])),
            .event(event(11, "run_a", "assistant", ["text": .string("Booked"), "step": .int(2)])),
            .event(event(12, "run_a", "run_finished", ["status": .string("done"), "result": .string("Booked")])),
        ] {
            chat = ChatReducer.frame(chat, frame, connection: connection).0
        }
        let answers: [TranscriptItem] = transcript(of: chat, pending: [], mode: .showAll, streaming: true, context: .none).filter {
            if case .answer = $0 { true } else { false }
        }
        XCTAssertEqual(answers, [.answer(id: "r:run_a", text: "Booked", writing: false, endedAt: 12)])
    }

    func testAHelpersEndOnTheStreamEndsItInTheViewSoTheCapsuleStopsCountingIt() {
        var (chat, connection): (ChatProjection, Int) = live(revision: 2, runs: [record("run_a", status: "running", at: 1)])
        let helper: HelperView = HelperView(
            runId: "run_h", parentRunId: "run_a", threadId: "thr_h", agentId: nil, title: "t", status: "running", startedAt: 1, finishedAt: nil,
            provider: "p", model: "m", effort: nil, step: nil, blocking: false, stop: "/api/runs/run_h/cancel")
        chat.view = chat.view?.with(helpers: [helper])
        chat = ChatReducer.frame(chat, .event(event(30, "run_h", "run_finished", ["status": .string("done")])), connection: connection).0
        XCTAssertEqual(chat.view?.helpers.first?.status, "done")
        XCTAssertEqual(chat.view?.helpers.first?.finishedAt, 30)
    }
}
