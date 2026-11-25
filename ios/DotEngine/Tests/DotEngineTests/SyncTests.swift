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
        let item: OutboxItem = OutboxItem(id: "key_new", action: .newChat(text: "Book the dentist", images: [], uploaded: []), createdAt: 1)
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

    /// R-e #5: the app went to the background for 15 s (the streams may have died unseen); back in front every stream is
    /// opened again from its cursors and the chat's view is read, so what changed meanwhile shows.
    func testBackFromTheBackgroundAfterTenSecondsEveryStreamIsOpenedAgainFromItsCursors() async throws {
        _ = try await openLive()
        await rig.supervisor.enterBackground()
        daemon.setQueueSilently("thr_1", texts: ["sent from the Mac meanwhile"])
        let opens: Int = daemon.count("GET", "/api/threads/thr_1/stream")
        rig.clock.advance(9.9)
        // A frame just before the return: the stream looks alive by its heartbeat, yet after 10 s away it is opened again.
        daemon.say("run_1", "tool_call", ["call_id": .string("c9"), "tool": .string("shell")])
        _ = try await snapshot(of: rig.store, "the frame was read") { $0.chats["thr_1"]?.runs["run_1"]?.events.contains { $0.kind == "tool_call" } == true }
        rig.clock.advance(0.2)
        await rig.supervisor.enterForeground()
        let back: EngineSnapshot = try await snapshot(of: rig.store, "caught up") {
            $0.chats["thr_1"]?.view?.queue.messages.first?.text == "sent from the Mac meanwhile" && $0.chats["thr_1"]?.freshness == .live
        }
        XCTAssertGreaterThan(daemon.count("GET", "/api/threads/thr_1/stream"), opens, "the stream was opened again")
        let reopened: FakeDaemon.Seen? = daemon.requests.last { $0.path == "/api/threads/thr_1/stream" }
        XCTAssertTrue(reopened?.query["cursors"]?.hasPrefix("run_1:") == true, "from the cursors held")
        XCTAssertEqual(back.chats["thr_1"]?.runs["run_1"]?.events.filter { $0.kind == "tool_call" }.count, 1)
    }

    /// Back in front after 3 s with a stream that spoke a moment ago: the stream is kept (a probe by heartbeat age), not
    /// replaced.
    func testBackFromAShortBackgroundAStreamThatSpokeLatelyIsKept() async throws {
        _ = try await openLive()
        await rig.supervisor.enterBackground()
        rig.clock.advance(3)
        let opens: Int = daemon.count("GET", "/api/threads/thr_1/stream")
        await rig.supervisor.enterForeground()
        daemon.say("run_1", "assistant", ["text": .string("still here"), "step": .int(4)])
        _ = try await snapshot(of: rig.store, "the event came on the same stream") {
            $0.chats["thr_1"]?.runs["run_1"]?.events.contains { $0.kind == "assistant" } == true
        }
        XCTAssertEqual(daemon.count("GET", "/api/threads/thr_1/stream"), opens, "a live stream is not replaced")
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
        XCTAssertTrue(items.contains { if case .owner(_, "Also check the logs", .readsAfterStep) = $0 { true } else { false } })
        daemon.say("run_1", "tool_result", ["call_id": .string("w1"), "ok": .bool(true)])
        let read: EngineSnapshot = try await snapshot(of: rig.store, "the step ended") {
            $0.chats["thr_1"]?.runs["run_1"]?.events.contains { $0.kind == "tool_result" } == true
        }
        let after: [TranscriptItem] = transcript(of: read.chats["thr_1"]!, pending: [], mode: .showAll, streaming: true, context: .none)
        XCTAssertTrue(after.contains { if case .owner(_, "Also check the logs", nil) = $0 { true } else { false } })
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
        XCTAssertEqual(items.filter { if case .owner(_, "And add it to my calendar", _) = $0 { true } else { false } }.count, 1, "shown once")
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
}
