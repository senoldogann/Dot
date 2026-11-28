import Foundation
import XCTest

@testable import DotEngine

/// The engine against the fake daemon of the new contract: one test per gap of R-e (§3: #1, #3, #4, #5, #10) and per path
/// of plan §9, with the supervisor's numbers (R-f §4 #3). The clock is moved by hand; nothing sleeps.
final class SyncTests: XCTestCase {
    private var folder: URL!
    private var daemon: FakeDaemon!
    private var rig: Rig!

    override func setUp() async throws {
        folder = scratchFolder()
        daemon = FakeDaemon(version: "1.0")
        daemon.addChat("thr_1", title: "Book the dentist")
        daemon.addRun("run_1", thread: "thr_1", goal: "Book the dentist", status: "running", parent: nil)
        rig = Rig(daemon: daemon, folder: folder)
    }

    override func tearDown() async throws {
        await rig.supervisor.stop()
        try? FileManager.default.removeItem(at: folder)
    }

    /// Starts the engine and opens `thr_1` until it is live.
    private func openLive() async throws -> EngineSnapshot {
        await rig.supervisor.start()
        await rig.supervisor.openChat("thr_1")
        return try await snapshot(of: rig.store, "the chat is live") { $0.chats["thr_1"]?.freshness == .live && $0.list.freshness == .live }
    }

    /// Item 1849: the chat a new chat's first message started is kept by its outbox key for the session, and its words
    /// stay the chat's overlay even when the list already holds its row as the daemon's answer comes (that dropped them,
    /// and the chat opened on "Syncing…" until its picture was read).
    func testANewChatsThreadAndWordsAreKeptWhenTheListHasItsRowFirst() async throws {
        await rig.supervisor.start()
        _ = try await snapshot(of: rig.store, "the list is live") { $0.list.freshness == .live && $0.list.rows.contains { $0.summary.id == "thr_1" } }
        let item: OutboxItem = OutboxItem(id: "key_new", action: .newChat(text: "Book the dentist", images: [], uploaded: [], start: nil, agent: nil), createdAt: 1)
        let run: RunRecord = RunRecord(
            id: "run_1", threadId: "thr_1", goal: "Book the dentist", mode: "task", model: "m", status: "running", createdAt: 1, finishedAt: nil,
            result: "", parentRunId: nil, source: "phone", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        await rig.store.outcome(.started(item, run: run))
        let kept: EngineSnapshot = try await snapshot(of: rig.store, "the started chat is named") { $0.startedChats["key_new"] != nil }
        XCTAssertEqual(kept.startedChats["key_new"], "thr_1")
        XCTAssertEqual(kept.pending(in: "thr_1").map(\.text), ["Book the dentist"], "the owner's words stay until the chat shows them")
    }

    // MARK: Live words

    /// The owner's report of 2026-11-25: a step's thinking and its words stream as they are written, each delta drawn on
    /// its own (no burst at the end), and the capsule's word follows what the run does. 25 thinking deltas, then 25 of
    /// words, each awaited in the transcript before the next is sent.
    func testFiftyDeltasRenderOneByOneThinkingThenWords() async throws {
        _ = try await openLive()
        let draw: @Sendable (EngineSnapshot) -> [TranscriptItem] = { snap in
            transcript(of: snap.chats["thr_1"]!, pending: [], mode: .showAll, streaming: true, context: .none)
        }
        for index in 1...25 {
            daemon.thinking("run_1", "t\(index) ", step: 1)
            let snap: EngineSnapshot = try await snapshot(of: rig.store, "thinking delta \(index) drawn") { snap in
                draw(snap).contains { if case .thought(_, let text, true) = $0 { text.hasSuffix("t\(index) ") } else { false } }
            }
            XCTAssertEqual(activity(of: snap.chats["thr_1"]!, run: "run_1"), .thinking)
            XCTAssertTrue(transcript(of: snap.chats["thr_1"]!, pending: [], mode: .zen, streaming: true, context: .none).allSatisfy {
                if case .thought = $0 { false } else { true }
            }, "Zen draws no thinking")
        }
        for index in 1...25 {
            daemon.delta("run_1", "w\(index) ", step: 1)
            let snap: EngineSnapshot = try await snapshot(of: rig.store, "word delta \(index) drawn") { snap in
                draw(snap).contains { if case .answer(_, let text, true, nil) = $0 { text.hasSuffix("w\(index) ") } else { false } }
            }
            XCTAssertEqual(activity(of: snap.chats["thr_1"]!, run: "run_1"), .writing)
            XCTAssertTrue(transcript(of: snap.chats["thr_1"]!, pending: [], mode: .zen, streaming: true, context: .none).contains {
                if case .answer(_, _, true, _) = $0 { true } else { false }
            }, "Zen streams the words too")
        }
        // The step is stored: the live rows give way to the stored thought and words, under the same thought id.
        daemon.say("run_1", "assistant", ["step": .int(1), "text": .string("Booked."), "thinking": .string("stored thought"), "tool_calls": .array([])])
        let stored: EngineSnapshot = try await snapshot(of: rig.store, "the step stored") { $0.chats["thr_1"]?.writing.isEmpty == true && $0.chats["thr_1"]?.thinking.isEmpty == true }
        XCTAssertEqual(draw(stored).compactMap { if case .thought(let id, let text, let live) = $0 { "\(id) \(text) \(live)" } else { nil } }, ["th:run_1:1 stored thought false"])
        XCTAssertNil(activity(of: stored.chats["thr_1"]!, run: "run_1"))
    }

    // MARK: R-e gaps

    /// R-e #3: the stream breaks, the queue changes meanwhile with no word on any stream; the stream comes back and the
    /// queue is read again, not left stale.
    func testAQueueChangedWhileTheStreamWasBrokenIsReadAgainWhenItComesBack() async throws {
        daemon.setQueueSilently("thr_1", texts: ["m1"])
        let first: EngineSnapshot = try await openLive()
        XCTAssertEqual(first.chats["thr_1"]?.view?.queue.messages.map(\.text), ["m1"])
        let views: Int = daemon.count("GET", "/api/threads/thr_1/view")
        let sleeps: Int = rig.clock.askedCount
        daemon.dropStreams()
        _ = try await snapshot(of: rig.store, "not live after the break") { $0.chats["thr_1"]?.freshness == .cached }
        daemon.setQueueSilently("thr_1", texts: [])
        try await rig.clock.advancePastSleep(of: 0.5, count: 2, since: sleeps)
        let back: EngineSnapshot = try await snapshot(of: rig.store, "live again with the queue read") {
            $0.chats["thr_1"]?.freshness == .live && $0.chats["thr_1"]?.view?.queue.messages.isEmpty == true
        }
        XCTAssertEqual(back.chats["thr_1"]?.view?.queue.messages, [])
        XCTAssertGreaterThan(daemon.count("GET", "/api/threads/thr_1/view"), views, "the view was read again")
    }

    /// R-e #4: the list's stream fell behind (`resync`): the list is read again; and a chat's stream that lost events
    /// tells the chat's revision (`thread_changed`), which reads the chat's view again.
    func testAResyncReadsTheListAgainAndALostChangeOfAChatReadsItsView() async throws {
        _ = try await openLive()
        let lists: Int = daemon.count("GET", "/api/threads/view")
        daemon.pushRaw("event: resync\ndata: {}\n\n", thread: nil)
        try await daemonState(daemon, "the list read again") { $0.count("GET", "/api/threads/view") > lists }
        daemon.setQueueSilently("thr_1", texts: ["queued while the bus dropped events"])
        daemon.bump("thr_1", tell: true)
        let after: EngineSnapshot = try await snapshot(of: rig.store, "the queue read again") {
            $0.chats["thr_1"]?.view?.queue.messages.first?.text == "queued while the bus dropped events" && $0.chats["thr_1"]?.freshness == .live
        }
        XCTAssertEqual(after.chats["thr_1"]?.view?.queue.messages.count, 1)
    }

    /// R-e #5 and item 1926: in the background nothing streams. Every stream ends at once (the daemon sees its connection
    /// close), what the Mac does meanwhile is not read, and back in front each stream is opened again from its cursors and
    /// the chat's view is read once, so what changed meanwhile shows.
    func testInTheBackgroundEveryStreamEndsAndBackInFrontEachIsOpenedAgainFromItsCursors() async throws {
        _ = try await openLive()
        let opens: Int = daemon.count("GET", "/api/threads/thr_1/stream")
        let views: Int = daemon.count("GET", "/api/threads/thr_1/view")
        await rig.supervisor.enterBackground()
        try await daemonState(daemon, "every stream closed") { $0.openStreams == 0 }
        daemon.setQueueSilently("thr_1", texts: ["sent from the Mac meanwhile"])
        daemon.say("run_1", "tool_call", ["call_id": .string("c9"), "tool": .string("shell")])
        rig.clock.advance(3)
        await rig.supervisor.enterForeground()
        let back: EngineSnapshot = try await snapshot(of: rig.store, "caught up") {
            $0.chats["thr_1"]?.view?.queue.messages.first?.text == "sent from the Mac meanwhile" && $0.chats["thr_1"]?.freshness == .live
                && $0.chats["thr_1"]?.runs["run_1"]?.events.contains { $0.kind == "tool_call" } == true
        }
        XCTAssertEqual(daemon.count("GET", "/api/threads/thr_1/stream"), opens + 1, "the stream was opened again, once")
        XCTAssertEqual(daemon.count("GET", "/api/threads/thr_1/view"), views + 1, "the view was read once")
        let reopened: FakeDaemon.Seen? = daemon.requests.last { $0.path == "/api/threads/thr_1/stream" }
        XCTAssertTrue(reopened?.query["cursors"]?.hasPrefix("run_1:") == true, "from the cursors held")
        XCTAssertEqual(back.chats["thr_1"]?.runs["run_1"]?.events.filter { $0.kind == "tool_call" }.count, 1)
        try await daemonState(daemon, "the list's stream and the chat's open again") { $0.openStreams == 2 }
    }

    /// A chat left while the app was in the background is not opened again when it comes back.
    func testAChatClosedInTheBackgroundStaysClosed() async throws {
        _ = try await openLive()
        await rig.supervisor.enterBackground()
        await rig.supervisor.closeChat("thr_1")
        try await daemonState(daemon, "every stream closed") { $0.openStreams == 0 }
        let opens: Int = daemon.count("GET", "/api/threads/thr_1/stream")
        await rig.supervisor.enterForeground()
        try await daemonState(daemon, "the list's stream open again") { $0.openStreams(of: "/api/threads/stream") == 1 }
        XCTAssertEqual(daemon.count("GET", "/api/threads/thr_1/stream"), opens, "the closed chat's stream stays closed")
    }

    /// R-e #10: a chat renamed on the Mac shows its new name in the list and in the open chat; a chat deleted on the Mac
    /// leaves the list, says "deleted" (not "unreachable") and its stream is not opened again.
    func testARenameAndADeleteOnTheMacReachTheListAndTheOpenChat() async throws {
        _ = try await openLive()
        daemon.rename("thr_1", to: "Dentist, Thursday", tell: true)
        let renamed: EngineSnapshot = try await snapshot(of: rig.store, "renamed") {
            $0.list.rows.first { $0.summary.id == "thr_1" }?.summary.title == "Dentist, Thursday"
                && $0.chats["thr_1"]?.view?.thread.title == "Dentist, Thursday"
        }
        XCTAssertEqual(renamed.chats["thr_1"]?.freshness, .live)
        let opens: Int = daemon.count("GET", "/api/threads/thr_1/stream")
        daemon.deleteChat("thr_1")
        let gone: EngineSnapshot = try await snapshot(of: rig.store, "deleted") {
            $0.chats["thr_1"]?.freshness == .deleted && !$0.list.rows.contains { $0.summary.id == "thr_1" }
        }
        XCTAssertEqual(gone.transport, .up, "a deleted chat is not a broken link")
        rig.clock.advance(30)
        await settle()
        XCTAssertEqual(daemon.count("GET", "/api/threads/thr_1/stream"), opens, "a deleted chat's stream is not opened again")
    }

    /// Item 1964: chats deleted together go in one request and leave the list at once, before the Mac answers.
    func testChatsDeletedTogetherGoInOneRequestAndLeaveTheListAtOnce() async throws {
        for number in 2...20 { daemon.addChat("thr_\(number)", title: "Chat \(number)") }
        _ = try await openLive()
        let all: [String] = (1...20).map { "thr_\($0)" }
        rig.link.report(.down)
        try await rig.engine.perform(.deleteChats(threads: all))
        let hidden: EngineSnapshot = try await snapshot(of: rig.store, "hidden while the link is down") { $0.shownList.rows.isEmpty }
        XCTAssertEqual(hidden.list.rows.count, 20, "nothing reached the Mac yet")
        XCTAssertEqual(hidden.goneChats, Set(all))
        rig.link.report(.up)
        let daemon: FakeDaemon = daemon
        _ = try await snapshot(of: rig.store, "the Mac deleted them") { $0.list.rows.isEmpty && $0.outbox.isEmpty && daemon.count("DELETE", "/api/threads") == 1 }
        XCTAssertEqual(daemon.count("DELETE", "/api/threads"), 1, "one request for twenty chats")
    }

    /// Item 1964: a delete the Mac refuses shows every chat again, each with the Mac's words.
    func testChatsWhoseDeleteTheMacRefusesShowAgain() async throws {
        daemon.addChat("thr_2", title: "Chat 2")
        _ = try await openLive()
        daemon.failNext("DELETE", path: "/api/threads", status: 409, body: #"{"message":"a run of the chat is at work"}"#)
        try await rig.engine.perform(.deleteChats(threads: ["thr_1", "thr_2"]))
        let back: EngineSnapshot = try await snapshot(of: rig.store, "shown again") {
            $0.refusedDeletes.count == 2 && $0.shownList.rows.count == 2
        }
        XCTAssertTrue(back.goneChats.isEmpty)
    }

    /// R-e #1: the owner's message steered into a run whose tool is open shows as delivered, read after this step, until
    /// the step ends.
    func testASteerIntoARunWhoseToolIsOpenShowsDeliveredAndReadAfterThisStep() async throws {
        _ = try await openLive()
        daemon.say("run_1", "tool_call", ["call_id": .string("w1"), "tool": .string("wait_until")])
        _ = try await rig.engine.steer("Also check the logs", run: "run_1", thread: "thr_1", photos: [])
        let delivered: EngineSnapshot = try await snapshot(of: rig.store, "the steer came back on the stream") {
            $0.chats["thr_1"]?.runs["run_1"]?.events.contains { $0.kind == "steer" } == true && $0.accepted.isEmpty
        }
        let items: [TranscriptItem] = transcript(
            of: delivered.chats["thr_1"]!, pending: delivered.pending(in: "thr_1"), mode: .showAll, streaming: true, context: .none)
        XCTAssertTrue(items.contains { if case .owner(_, "Also check the logs", .readsAfterStep, _) = $0 { true } else { false } })
        daemon.say("run_1", "tool_result", ["call_id": .string("w1"), "ok": .bool(true)])
        let read: EngineSnapshot = try await snapshot(of: rig.store, "the step ended") {
            $0.chats["thr_1"]?.runs["run_1"]?.events.contains { $0.kind == "tool_result" } == true
        }
        let after: [TranscriptItem] = transcript(of: read.chats["thr_1"]!, pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertTrue(after.contains { if case .owner(_, "Also check the logs", nil, _) = $0 { true } else { false } })
    }

    // MARK: Plan §9 paths

    func testAStreamSilentForTwelveSecondsIsTakenForDeadAndOpenedAgain() async throws {
        _ = try await openLive()
        let opens: Int = daemon.count("GET", "/api/threads/thr_1/stream")
        rig.clock.advance(11)
        await settle()
        XCTAssertEqual(daemon.count("GET", "/api/threads/thr_1/stream"), opens, "11 s of silence is still alive")
        let sleeps: Int = rig.clock.askedCount
        rig.clock.advance(2)
        _ = try await snapshot(of: rig.store, "not live once it is taken for dead") { $0.chats["thr_1"]?.freshness != .live }
        try await rig.clock.advancePastSleep(of: 0.5, count: 2, since: sleeps)
        try await daemonState(daemon, "opened again") { $0.count("GET", "/api/threads/thr_1/stream") > opens }
        _ = try await snapshot(of: rig.store, "live again") { $0.chats["thr_1"]?.freshness == .live }
    }

    func testTheBackoffStartsAgainOnlyAfterAConnectionStayedUpThirtySeconds() async throws {
        _ = try await openLive()
        // Two quick breaks: the second wait is longer than the first.
        var sleeps: Int = rig.clock.askedCount
        daemon.dropStreams()
        _ = try await snapshot(of: rig.store, "broken") { $0.transport == .reconnecting(attempt: 1) }
        try await rig.clock.advancePastSleep(of: 0.5, count: 2, since: sleeps)
        _ = try await snapshot(of: rig.store, "up") { $0.transport == .up }
        sleeps = rig.clock.askedCount
        daemon.dropStreams()
        _ = try await snapshot(of: rig.store, "broken again soon after") { $0.transport == .reconnecting(attempt: 2) }
        try await rig.clock.advancePastSleep(of: 1, count: 2, since: sleeps)
        _ = try await snapshot(of: rig.store, "up again") { $0.transport == .up }
        // Up for 30 s (keepalives keep it alive), then a break: the count starts again.
        for _ in 0..<6 {
            daemon.pushRaw(": keepalive\n\n", thread: nil)
            daemon.pushRaw(": keepalive\n\n", thread: "thr_1")
            await settle()
            rig.clock.advance(5.5)
        }
        daemon.dropStreams()
        _ = try await snapshot(of: rig.store, "the count started again") { $0.transport == .reconnecting(attempt: 1) }
    }

    func testAChangeOfInterfaceOpensEveryStreamAgainAndALostLinkLeavesThePictureNotLive() async throws {
        _ = try await openLive()
        let opens: Int = daemon.count("GET", "/api/threads/thr_1/stream")
        rig.link.report(LinkState(online: true, interface: "cellular"))
        try await daemonState(daemon, "opened again on the new interface") { $0.count("GET", "/api/threads/thr_1/stream") > opens }
        _ = try await snapshot(of: rig.store, "live") { $0.chats["thr_1"]?.freshness == .live }
        rig.link.report(.down)
        let offline: EngineSnapshot = try await snapshot(of: rig.store, "offline") { $0.transport == .offline && $0.chats["thr_1"]?.freshness == .cached }
        XCTAssertNotNil(offline.chats["thr_1"]?.view, "the content is never cleared")
        rig.link.report(.up)
        _ = try await snapshot(of: rig.store, "back") { $0.transport == .up && $0.chats["thr_1"]?.freshness == .live }
    }

    /// R-f §4 #4: the view is read beside the stream's opening, not after it caught up (one round trip on a cold open).
    func testTheViewIsReadBesideTheStreamNotAfterItCaughtUp() async throws {
        daemon.holdStreamStarts = true
        await rig.supervisor.start()
        await rig.supervisor.openChat("thr_1")
        try await daemonState(daemon, "the view asked while the stream has said nothing") { $0.count("GET", "/api/threads/thr_1/view") >= 1 }
        let held: EngineSnapshot = await rig.store.snapshot
        XCTAssertNotEqual(held.chats["thr_1"]?.freshness, .live, "not live before synchronized")
        daemon.releaseStreamStarts()
        _ = try await snapshot(of: rig.store, "live") { $0.chats["thr_1"]?.freshness == .live }
    }

    func testAnUndecodableFrameReadsTheViewAgain() async throws {
        _ = try await openLive()
        let views: Int = daemon.count("GET", "/api/threads/thr_1/view")
        daemon.pushRaw("id: 99999\nevent: tool_call\ndata: {broken\n\n", thread: "thr_1")
        _ = try await snapshot(of: rig.store, "counted and live again") { $0.chats["thr_1"]?.unreadable == 1 && $0.chats["thr_1"]?.freshness == .live }
        XCTAssertGreaterThan(daemon.count("GET", "/api/threads/thr_1/view"), views, "the view was read again")
    }

    func testHelpersOfTheChatAreInItsViewAndTheirStepsComeLiveOnItsStream() async throws {
        daemon.addHelper("run_h1", parentRun: "run_1", parentThread: "thr_1", title: "Find a free afternoon slot")
        let first: EngineSnapshot = try await openLive()
        XCTAssertEqual(first.chats["thr_1"]?.view?.helpers.map(\.title), ["Find a free afternoon slot"])
        daemon.say("run_h1", "tool_call", ["call_id": .string("h1"), "tool": .string("browser_open"), "args": .object(["url": .string("https://clinic.example")])])
        let stepped: EngineSnapshot = try await snapshot(of: rig.store, "the helper's step") {
            $0.chats["thr_1"]?.view?.helpers.first?.step?.tool == "browser_open"
        }
        XCTAssertEqual(stepped.chats["thr_1"]?.view?.helpers.first?.step?.target, "https://clinic.example")
    }

    func testADaemonFromBeforeTheSyncCallsSaysUpdateDotNotUnreachable() async throws {
        daemon.old = true
        await rig.supervisor.start()
        _ = try await snapshot(of: rig.store, "too old") { $0.transport == .daemonTooOld }
    }

    func testARefusedKeyStopsTryingAndSaysSo() async throws {
        daemon.failNext("/api/status", status: 401, body: #"{"detail":"unknown token"}"#)
        await rig.supervisor.start()
        _ = try await snapshot(of: rig.store, "key refused") { $0.transport == .keyRefused }
        rig.clock.advance(60)
        await settle()
        XCTAssertEqual(daemon.count("GET", "/api/status"), 1, "a refused key is not asked again by itself")
    }

    // MARK: The outbox in the picture

    func testAMessageSentOfflineWaitsShowsOfflineAndGoesOnceTheLinkIsBack() async throws {
        _ = try await openLive()
        rig.link.report(.down)
        daemon.offline = true
        _ = try await snapshot(of: rig.store, "offline") { $0.transport == .offline }
        // The daemon could still be reached (a link the system calls down may yet carry a request): nothing goes until the
        // link says up.
        daemon.offline = false
        let sleeps: Int = rig.clock.askedCount
        let key: String = try await rig.engine.send("Thursday afternoon please", thread: "thr_1", photos: [], picks: .none)
        let waiting: EngineSnapshot = try await snapshot(of: rig.store, "in the outbox") { !$0.outbox.isEmpty }
        XCTAssertEqual(waiting.pending(in: "thr_1").map(\.delivery), [.offline])
        // The outbox waits for the link (30 s at most, or until it comes back) instead of sending.
        try await rig.clock.waitForSleep(of: 30, count: 1, since: sleeps)
        XCTAssertEqual(daemon.count("POST", "/queue"), 0, "nothing was asked of a network that is not there")
        XCTAssertEqual(daemon.acceptedTexts, [])
        rig.link.report(.up)
        let queued: EngineSnapshot = try await snapshot(of: rig.store, "queued behind the run") {
            $0.outbox.isEmpty && $0.pending(in: "thr_1").first?.delivery == .queued(position: 1)
        }
        XCTAssertEqual(queued.pending(in: "thr_1").map(\.key), [key], "the overlay is keyed by the client's id")
        XCTAssertEqual(daemon.acceptedTexts, ["Thursday afternoon please"])
    }

    /// Plan §11: a steer keeps its kind. Made while the link was down for a run that then ended, it is refused as a
    /// steer and never goes to the queue under the same key; the owner sends it as a new message, with a new key.
    func testASteerKeepsItsKindAcrossTheEndOfItsRun() async throws {
        _ = try await openLive()
        rig.link.report(.down)
        let key: String = try await rig.engine.steer("Use the second clinic", run: "run_1", thread: "thr_1", photos: [])
        daemon.say("run_1", "run_finished", ["status": .string("done"), "result": .string("Booked")])
        rig.link.report(.up)
        let refused: EngineSnapshot = try await snapshot(of: rig.store, "refused as a steer") { $0.outbox.first?.phase == .failed }
        XCTAssertEqual(refused.outbox.first?.failure, .runEnded(detail: "the run is not active"))
        XCTAssertEqual(refused.pending(in: "thr_1").first?.delivery, .rejected(.runEnded(detail: "the run is not active")))
        XCTAssertEqual(daemon.count("POST", "/api/threads/thr_1/queue"), 0, "never sent to the queue by itself")
        let fresh: String? = await rig.engine.outbox.sendAsMessage(key)
        XCTAssertNotNil(fresh)
        XCTAssertNotEqual(fresh, key, "a new item with a new key")
        try await daemonState(daemon, "sent as a message") { $0.acceptedTexts == ["Use the second clinic"] }
        let body: Data? = daemon.requests.last { $0.path == "/api/threads/thr_1/queue" }?.body
        let sent: QueueMessage? = body.flatMap { try? JSONDecoder().decode(QueueMessage.self, from: $0) }
        XCTAssertEqual(sent?.key, fresh)
    }

    func testAMessageThatStartedARunLeavesTheOverlayOnceTheRunIsInThePicture() async throws {
        daemon.say("run_1", "run_finished", ["status": .string("done"), "result": .string("Booked")])
        _ = try await openLive()
        _ = try await rig.engine.send("And add it to my calendar", thread: "thr_1", photos: [], picks: .none)
        let shown: EngineSnapshot = try await snapshot(of: rig.store, "the new run is in the view and the overlay gone") {
            $0.outbox.isEmpty && $0.accepted.isEmpty && $0.chats["thr_1"]?.view?.runs.contains { $0.goal == "And add it to my calendar" } == true
        }
        let items: [TranscriptItem] = transcript(of: shown.chats["thr_1"]!, pending: shown.pending(in: "thr_1"), mode: .showAll, streaming: true, context: .none)
        XCTAssertEqual(items.filter { if case .owner(_, "And add it to my calendar", _, _) = $0 { true } else { false } }.count, 1, "shown once")
    }

    /// Item 2106: Send now on a queued message stops the run at work and starts the message as a run of its own; the overlay
    /// says Sent until that run is in the picture, then goes.
    func testAQueuedMessageSentNowStartsItsOwnRunAndTheOverlayGoesOnceItShows() async throws {
        _ = try await openLive()
        _ = try await rig.engine.send("Use the short form", thread: "thr_1", photos: [], picks: .none)
        let queued: EngineSnapshot = try await snapshot(of: rig.store, "the message waits in the chat's queue") {
            $0.outbox.isEmpty && $0.pending(in: "thr_1").map(\.delivery) == [.queued(position: 1)]
        }
        let id: String = try XCTUnwrap(queued.chats["thr_1"]?.view?.queue.messages.first?.id)
        _ = try await rig.engine.perform(.sendQueuedNow(thread: "thr_1", message: id))
        let shown: EngineSnapshot = try await snapshot(of: rig.store, "the new run is in the picture and the overlay gone") {
            $0.outbox.isEmpty && $0.accepted.isEmpty && $0.chats["thr_1"]?.view?.runs.contains { $0.goal == "Use the short form" } == true
        }
        XCTAssertEqual(shown.chats["thr_1"]?.view?.runs.first { $0.id == "run_1" }?.status, "cancelled", "the run at work was stopped")
        XCTAssertEqual(shown.chats["thr_1"]?.view?.queue.messages, [])
        XCTAssertEqual(daemon.steers("run_1"), [], "sent now, not steered")
    }

    // MARK: The cache

    func testTheCacheShowsTheChatAtOnceAfterAKillAndAnotherDaemonVersionDropsIt() async throws {
        _ = try await openLive()
        await rig.supervisor.enterBackground()
        await rig.supervisor.stop()
        let second: Rig = Rig(daemon: daemon, folder: folder)
        await second.supervisor.openChat("thr_1")
        let cached: EngineSnapshot = await second.store.snapshot
        XCTAssertEqual(cached.chats["thr_1"]?.freshness, .cached, "drawn from the cache before any network")
        XCTAssertNotNil(cached.workingAt["thr_1"], "a run was at work: the app says when it was")
        XCTAssertEqual(cached.chats["thr_1"]?.view?.thread.title, "Book the dentist")
        await second.supervisor.stop()
        let newer: FakeDaemon = FakeDaemon(version: "2.0")
        newer.addChat("thr_1", title: "Book the dentist")
        let third: Rig = Rig(daemon: newer, folder: folder)
        await third.supervisor.openChat("thr_1")
        await third.supervisor.start()
        _ = try await snapshot(of: third.store, "the copy of another version is dropped") { $0.chats["thr_1"]?.runs.isEmpty == true }
        await third.supervisor.stop()
    }

    /// L1's report of 2026-11-25: the Mac's Dot was reset (a new, empty home) at the same address and version. Its
    /// revisions start again below the copies', so the kept list never went live ("Syncing…" for ever) and a new chat,
    /// sent only on a live list, stayed "Sending…". The copies of the old daemon are dropped, its outbox doings too, the
    /// words of an unsent message kept for the owner, a new chat goes to the new daemon, and the owner is told once.
    func testAResetDotAtTheSameVersionDropsTheOldCopiesAndOutboxAndKeepsTheUnsentWords() async throws {
        for number in 2...12 { daemon.addChat("thr_\(number)", title: "Chat \(number)") }
        _ = try await openLive()
        await rig.supervisor.closeChat("thr_1")
        rig.link.report(.down)
        _ = try await rig.engine.send("Thursday afternoon please", thread: "thr_1", photos: [Data("a photo".utf8)], picks: .none)
        _ = try await rig.engine.perform(.pin(thread: "thr_2", pinned: true))
        _ = try await rig.engine.startChat("Plan the trip to Oulu", photos: [], start: .daemons, agent: nil)
        await rig.supervisor.enterBackground()
        await rig.supervisor.stop()

        let reset: FakeDaemon = FakeDaemon(version: daemon.version)
        reset.addChat("thr_new", title: "Hello")
        let after: Rig = Rig(daemon: reset, folder: folder)
        let before: EngineSnapshot = await after.store.snapshot
        XCTAssertEqual(before.list.freshness, .cached, "the old list is drawn until the Mac is heard")
        // The Mac is slow to answer at first: a doing the owner makes on the old picture meanwhile is not sent either.
        reset.failNext("/api/status", status: 503, body: "busy")
        let sleeps: Int = after.clock.askedCount
        await after.supervisor.start()
        try await after.engine.perform(.stop(run: "run_1", thread: "thr_1"))
        // Both waits are asked before anything is read: the outbox's hold (its own task) and the supervisor's reconnect
        // wait, which comes only after the refused status was asked. Waiting on the hold alone raced the status read.
        let retry: Double = reconnectWait(attempt: 1, jitter: 0)
        try await after.clock.waitForSleep(of: 5, count: 1, since: sleeps)
        try await after.clock.waitForSleep(of: retry, count: 1, since: sleeps)
        XCTAssertEqual(reset.requests.map(\.path), ["/api/status"], "nothing is sent before the daemon is heard")
        after.clock.advance(retry)
        let live: EngineSnapshot = try await snapshot(of: after.store, "the reset daemon's list is live and the new chat sent") {
            $0.list.freshness == .live && $0.outbox.isEmpty && $0.reset != nil
        }
        XCTAssertTrue(live.list.rows.contains { $0.summary.id == "thr_new" })
        XCTAssertFalse(live.list.rows.contains { $0.summary.id == "thr_1" || $0.summary.id == "thr_2" }, "no old chat is left")
        XCTAssertEqual(live.reset?.words, ["Thursday afternoon please"], "the unsent words are kept for the owner")
        XCTAssertEqual(live.reset?.photosLeft, true, "the notice can say its photo was not kept")
        XCTAssertEqual(reset.acceptedTexts, ["Plan the trip to Oulu"], "the new chat went to the new daemon")
        XCTAssertEqual(reset.count("POST", "/api/threads/thr_1/queue"), 0, "nothing of the old daemon's chats is sent")
        XCTAssertEqual(reset.count("PATCH", "/api/threads/thr_2"), 0)
        XCTAssertEqual(reset.count("POST", "/api/runs/run_1/cancel"), 0)
        XCTAssertEqual(reset.count("POST", "/api/attachments"), 0, "the old message's photo is not sent")

        await after.store.resetTaken()
        await after.supervisor.stop()
        let again: Rig = Rig(daemon: reset, folder: folder)
        let reopened: EngineSnapshot = await again.store.snapshot
        XCTAssertEqual(reopened.reset?.taken, true, "the words are handed once, the notice kept")
        XCTAssertEqual(reopened.reset?.words, [])
        XCTAssertEqual(reopened.reset?.photosLeft, true)
        await again.store.resetSeen()
        await again.supervisor.start()
        _ = try await snapshot(of: again.store, "live again") { $0.list.freshness == .live }
        let told: EngineSnapshot = await again.store.snapshot
        XCTAssertNil(told.reset, "told once: the same daemon heard again is no reset")
        await again.supervisor.stop()
    }

    /// A restart or an update of the same database is no reset: the outbox is kept and nobody is told.
    func testTheSameDatabaseHeardAgainUnderAnotherVersionIsNoReset() {
        let old: DaemonIdentity = DaemonIdentity(version: "1.0", store: "store_a")
        XCTAssertEqual(DaemonRules.change(from: nil, to: old), .same)
        XCTAssertEqual(DaemonRules.change(from: old, to: old), .same)
        XCTAssertEqual(DaemonRules.change(from: old, to: DaemonIdentity(version: "1.1", store: "store_a")), .updated)
        XCTAssertEqual(DaemonRules.change(from: DaemonIdentity(version: "1.0", store: nil), to: old), .same, "an id first heard")
        XCTAssertEqual(DaemonRules.change(from: old, to: DaemonIdentity(version: "1.0", store: "store_b")), .reset)
        XCTAssertEqual(DaemonRules.draft(keeping: "Half a thought", adding: ["Thursday please", "  "]), "Half a thought\n\nThursday please")
        XCTAssertEqual(DaemonRules.draft(keeping: "", adding: ["Thursday please"]), "Thursday please")
        let first: UnsentWords = UnsentWords(key: "k1", text: "Thursday please", photos: 0)
        let second: UnsentWords = UnsentWords(key: "k2", text: "", photos: 1)
        XCTAssertEqual(DaemonRules.merged([first], [first, second]), [first, second], "a reset found again tells each message once")
    }
}

/// Item 1905: a chat the owner opens is marked seen on the Mac, as the web's sidebar does (`PATCH {seen: true}`), so its
/// row loses "unread" in the list; and one whose run ends while it is open is marked again. Before, the phone never sent
/// it and the chat stayed unread for good.
final class SeenTests: XCTestCase {
    private var folder: URL!
    private var daemon: FakeDaemon!
    private var rig: Rig!

    override func setUp() async throws {
        folder = scratchFolder()
        daemon = FakeDaemon(version: "1.0")
        daemon.addChat("thr_1", title: "Book the dentist")
        daemon.addRun("run_1", thread: "thr_1", goal: "Book the dentist", status: "done", parent: nil)
        daemon.markUnread("thr_1")
        rig = Rig(daemon: daemon, folder: folder)
    }

    override func tearDown() async throws {
        await rig.supervisor.stop()
        try? FileManager.default.removeItem(at: folder)
    }

    func testAnOpenedChatIsMarkedSeenAndItsRowSaysSo() async throws {
        await rig.supervisor.start()
        _ = try await snapshot(of: rig.store, "the list shows the chat unread") { seenRow($0)?.summary.unread == true }
        XCTAssertEqual(daemon.count("PATCH", "/api/threads/thr_1"), 0, "nothing is marked before the chat is opened")
        await rig.supervisor.openChat("thr_1")
        _ = try await snapshot(of: rig.store, "the row is read again") { seenRow($0)?.summary.unread == false }
        XCTAssertFalse(daemon.isUnread("thr_1"))
        let seen: [String: JSONValue] = try JSONDecoder().decode([String: JSONValue].self, from: try XCTUnwrap(daemon.bodies("PATCH", "/api/threads/thr_1").first))
        XCTAssertEqual(seen["seen"]?.bool, true)
    }

    func testARunThatEndsWhileTheChatIsOpenIsSeenToo() async throws {
        await rig.supervisor.start()
        await rig.supervisor.openChat("thr_1")
        _ = try await snapshot(of: rig.store, "the row is read") { seenRow($0)?.summary.unread == false }
        let before: Int = daemon.count("PATCH", "/api/threads/thr_1")
        daemon.markUnread("thr_1")
        let daemon: FakeDaemon = daemon
        _ = try await snapshot(of: rig.store, "read again after the run ended") { _ in daemon.count("PATCH", "/api/threads/thr_1") > before }
        _ = try await snapshot(of: rig.store, "the row is read") { seenRow($0)?.summary.unread == false }
    }

    /// A switch back to a chat before its old screen went (item 1913): the old screen's close, arriving after the new
    /// one's open, must not close the chat the owner is reading; it is still marked seen when its run ends.
    func testAChatShownTwiceStaysOpenUntilBothLeave() async throws {
        await rig.supervisor.start()
        await rig.supervisor.openChat("thr_1")
        await rig.supervisor.openChat("thr_1")
        _ = try await snapshot(of: rig.store, "the row is read") { seenRow($0)?.summary.unread == false }
        await rig.supervisor.closeChat("thr_1")
        let before: Int = daemon.count("PATCH", "/api/threads/thr_1")
        daemon.markUnread("thr_1")
        let daemon: FakeDaemon = daemon
        _ = try await snapshot(of: rig.store, "the chat still on screen is read again") { _ in daemon.count("PATCH", "/api/threads/thr_1") > before }
        await rig.supervisor.closeChat("thr_1")
        let after: Int = daemon.count("PATCH", "/api/threads/thr_1")
        daemon.markUnread("thr_1")
        _ = try await snapshot(of: rig.store, "once both left it keeps its unread") { seenRow($0)?.summary.unread == true }
        XCTAssertEqual(daemon.count("PATCH", "/api/threads/thr_1"), after)
    }

    func testAClosedChatIsNotMarkedSeen() async throws {
        await rig.supervisor.start()
        await rig.supervisor.openChat("thr_1")
        _ = try await snapshot(of: rig.store, "the row is read") { seenRow($0)?.summary.unread == false }
        await rig.supervisor.closeChat("thr_1")
        let before: Int = daemon.count("PATCH", "/api/threads/thr_1")
        daemon.markUnread("thr_1")
        _ = try await snapshot(of: rig.store, "the list shows it unread") { seenRow($0)?.summary.unread == true }
        XCTAssertEqual(daemon.count("PATCH", "/api/threads/thr_1"), before, "a chat not on screen keeps its unread")
    }
}

private func seenRow(_ snapshot: EngineSnapshot) -> ThreadRow? { snapshot.list.rows.first { $0.summary.id == "thr_1" } }
