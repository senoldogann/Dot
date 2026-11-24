import XCTest
@testable import Dot

/// Helpers that go on after their run ended are shown (item 1466). The owner's bug of 2026-11-24, 10:44: "5 agents work but the app
/// does not show them because the session ended": the chat said "Done · 1 min ago" and the capsule said nothing while five helpers
/// worked, because the line over the composer was idle as soon as the chat's own run had ended.
final class HelperLineTests: XCTestCase {
    /// A chat whose run ended as `status`, and the helpers the daemon lists for it (`live` running, `ended` done), newest first.
    private func chat(runEnded status: String = "done", live: Int, ended: Int = 0) -> ChatState {
        var state: ChatState = ChatState()
        state.addRun(makeRun("run_1", status: status, created: 1000))
        state.setHelpers(helpers(live: live, ended: ended))
        return state
    }

    /// The daemon's list of the chat's helpers, newest first: `ended` of the oldest have ended, `live` of the newest work (a helper
    /// keeps its id and its time as it ends, so the same helpers are listed each time).
    private func helpers(live: Int, ended: Int = 0) -> [RunRecord] {
        let all: [RunRecord] = (0..<(live + ended)).map { index in
            makeRun("h\(index)", status: index < ended ? "done" : "running", parent: "run_1", created: Double(1000 + index))
        }
        return all.sorted { $0.createdAt > $1.createdAt }
    }

    // MARK: The line over the composer

    func testARunThatEndedDoneWithFiveLiveHelpersIsAtWorkInTheBackground() {
        let state: ChatState = chat(live: 5, ended: 60)
        XCTAssertFalse(state.isWorking, "the chat's own run is over")
        XCTAssertEqual(state.line, .helpersInBackground(count: 5), "the helpers it left at work are shown, the finished ones are not counted")
    }

    func testARunThatEndedWithNoLiveHelperIsAtRest() {
        XCTAssertEqual(chat(live: 0, ended: 3).line, .idle)
        XCTAssertEqual(chat(live: 0).line, .idle)
        XCTAssertEqual(ChatState().line, .idle, "a chat that holds nothing yet")
    }

    func testARunThatFailedOrWasStoppedIsAsTheListOfHelpersSays() {
        XCTAssertEqual(chat(runEnded: "failed", live: 2).line, .helpersInBackground(count: 2))
        XCTAssertEqual(chat(runEnded: "cancelled", live: 0, ended: 2).line, .idle, "the daemon stops what a run that did not end done started")
    }

    func testWhileTheRunWorksTheLineIsAsItWas() {
        var state: ChatState = ChatState()
        state.addRun(makeRun("run_1", status: "running", created: 1000))
        XCTAssertEqual(state.line, .working(since: 1000))
        state.apply(event(2, "run_2", "run_queued", ["goal": .string("Read the folder"), "parent_run_id": .string("run_1")]))
        XCTAssertEqual(state.line, .waitingOnSubagents(count: 1, now: nil), "a run that waits for its helper says so")
        state.apply(event(3, "run_1", "helper_released", ["run_id": .string("run_2")]))
        XCTAssertEqual(state.line, .helpersInBackground(count: 1), "and one it stopped waiting for works in the background")
    }

    func testAHelperThatEndsLowersTheCountAndTheLastOneMakesTheChatIdle() {
        var state: ChatState = chat(live: 5)
        XCTAssertEqual(state.line, .helpersInBackground(count: 5))
        // The daemon's list says one of them ended.
        state.setHelpers(helpers(live: 4, ended: 1))
        XCTAssertEqual(state.line, .helpersInBackground(count: 4))
        state.setHelpers(helpers(live: 1, ended: 4))
        XCTAssertEqual(state.line, .helpersInBackground(count: 1))
        state.setHelpers(helpers(live: 0, ended: 5))
        XCTAssertEqual(state.line, .idle)
    }

    func testAHelperThatEndsByItsOwnEventLowersTheCountToo() {
        var state: ChatState = chat(live: 2)
        state.apply(event(9, "h0", "run_finished", ["status": .string("done"), "result": .string("ok")]))
        XCTAssertEqual(state.line, .helpersInBackground(count: 1))
        state.apply(event(10, "h1", "run_finished", ["status": .string("failed"), "result": .string("")]))
        XCTAssertEqual(state.line, .idle)
    }

    /// A helper the run left at work may ask the owner something: the chat waits on the owner (the warm line), as it does while the run
    /// works. A card of a helper that has ended, or of the ended run, is not a helper at work asking.
    func testAHelperThatAsksTheOwnerAfterTheRunEndedMakesTheChatWaitOnYou() {
        var state: ChatState = chat(live: 2)
        let question: [String: JSONValue] = [
            "approval_id": .string("apr_1"), "tool": .string("ask_user"), "summary": .string("Asks"), "severity": .string("ask"),
            "args": .object(["question": .string("Which folder?")]),
        ]
        state.apply(event(9, "h0", "approval_requested", question))
        XCTAssertEqual(state.pendingApprovals.count, 1)
        XCTAssertEqual(state.line, .waitingOnYou(count: 1), "not \"2 helpers working\": the owner is needed")
        state.apply(event(10, "h0", "approval_decided", ["approval_id": .string("apr_1"), "status": .string("approved")]))
        XCTAssertEqual(state.line, .helpersInBackground(count: 2), "answered, they work on")
        // The same question from a helper that has since ended asks no one: its card waits for the daemon's list to close it.
        state.apply(event(11, "h1", "approval_requested", question.merging(["approval_id": .string("apr_2")]) { _, new in new }))
        XCTAssertEqual(state.line, .waitingOnYou(count: 1))
        state.apply(event(12, "h1", "run_finished", ["status": .string("failed"), "result": .string("")]))
        XCTAssertEqual(state.line, .helpersInBackground(count: 1), "h0 still works and nothing live asks")
    }

    /// "Queue a note" is for a run that works; with the run over, what is typed starts a new run, and the box says so.
    func testTheBoxDoesNotPromiseAQueueWhenNoRunWorks() {
        XCTAssertEqual(ComposerWords.placeholder(waitingOnYou: true, working: true), "Answer above, or queue a note")
        XCTAssertEqual(ComposerWords.placeholder(waitingOnYou: true, working: false), "Answer above, or send a message")
        XCTAssertEqual(ComposerWords.placeholder(waitingOnYou: false, working: true), "Queue a message")
        XCTAssertEqual(ComposerWords.placeholder(waitingOnYou: false, working: false), "Message Dot")
    }

    // MARK: What it says

    func testTheHeaderSaysHowManyWorkOnceTheRunIsOverAndNotDoneAMinuteAgo() {
        XCTAssertEqual(HelperWords.header(count: 5, runWorking: false), "5 helpers working")
        XCTAssertEqual(HelperWords.header(count: 1, runWorking: false), "1 helper working")
        XCTAssertEqual(HelperWords.header(count: 2, runWorking: true), "2 helpers in the background", "while the run goes on, as before")
    }

    func testTheCapsuleShowsTheHelpersOfAChatWhoseRunEnded() {
        let summary: DockSummary = DockSummary.make(
            line: chat(live: 5, ended: 60).line, connection: .ended, queue: QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil),
            automations: [])
        XCTAssertEqual(summary.segments.map(\.kind), [.helpers])
        XCTAssertEqual(summary.segments[0].words, "5 helpers")
        XCTAssertEqual(summary.segments[0].short, "5")
        XCTAssertEqual(summary.segments[0].tone, .accent, "helpers at work are the accent")
        XCTAssertFalse(summary.segments[0].dimmed)
    }

    func testWithNoLinkTheLastKnownCountStaysAndIsDimmed() {
        let queue: QueueView = QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil)
        let line: RunLine = chat(live: 5).line
        let offline: DockSummary = DockSummary.make(line: line, connection: .connecting, queue: queue, automations: [], offline: true)
        XCTAssertEqual(offline.segments.map(\.kind), [.helpers])
        XCTAssertEqual(offline.segments[0].words, "5 helpers", "the count last read stays")
        XCTAssertTrue(offline.segments[0].dimmed)
        XCTAssertEqual(offline.segments[0].tone, .plain)
        // Dot out of reach (the stream is trying again) says so beside it, and the count is dimmed as well.
        let trying: DockSummary = DockSummary.make(line: line, connection: .reconnecting(attempt: 2, unauthorized: false), queue: queue, automations: [])
        XCTAssertEqual(trying.segments.map(\.kind), [.status, .helpers])
        XCTAssertEqual(trying.segments[0].words, "Reconnecting…")
        XCTAssertTrue(trying.segments[1].dimmed)
        // With the link back it is drawn plainly again.
        XCTAssertFalse(DockSummary.make(line: line, connection: .live, queue: queue, automations: []).segments[0].dimmed)
    }

    // MARK: The row of the list

    private func thread(latestRun: String = "run_1", status: String = "done") -> ThreadSummary {
        ThreadSummary(
            id: "thr_1", title: "A chat", source: "ui", updatedAt: 2000, pinnedAt: nil, runCount: 1, latestRunId: latestRun, latestStatus: status,
            latestMode: "task", unread: false, actorId: nil, lastMessage: "Done.", lastRole: "assistant", loopCron: nil, loopNextAt: nil)
    }

    /// The daemon's list of chats says only how the newest run stands, so a chat whose run ended done reads as at rest: the row says
    /// what the phone itself read of the chat's helpers, as long as it was read at the chat's newest run.
    func testARowSaysWhatThePhoneLastReadOfTheChatsHelpers() {
        XCTAssertEqual(thread().state, .idle, "the daemon's own word for the chat")
        let known: HelperCount = HelperCount(live: 5, newestRun: "run_1")
        XCTAssertEqual(ListHelpers.live(known, thread: thread()), 5)
        XCTAssertEqual(thread().state(helpersLive: 5), .working, "a chat whose helpers work is at work, not at rest")
        XCTAssertEqual(thread().state(helpersLive: 0), .idle)
        XCTAssertEqual(thread(status: "blocked").state(helpersLive: 5), .waiting, "a chat that waits on the owner still waits")
        XCTAssertEqual(thread(status: "running").state(helpersLive: 5), .working)
    }

    func testARowTrustsNoCountThatWasReadBeforeTheChatMovedOn() {
        XCTAssertEqual(ListHelpers.live(HelperCount(live: 5, newestRun: "run_1"), thread: thread(latestRun: "run_2")), 0, "a report started a run")
        XCTAssertEqual(ListHelpers.live(HelperCount(live: 0, newestRun: "run_1"), thread: thread()), 0)
        XCTAssertEqual(ListHelpers.live(nil, thread: thread()), 0, "a chat never read in this run of the app")
    }
}

/// When a chat on the screen reads its helpers again (item 1466): every 8 s while one is live, nothing else.
final class HelperWatchRuleTests: XCTestCase {
    func testTheListIsAskedForEveryEightSeconds() {
        XCTAssertEqual(HelperWatch.seconds, 8)
        XCTAssertEqual(HelperWatch.interval, .seconds(8))
    }

    func testItGoesOnWhileAHelperIsLiveAndStopsWhenNoneIs() {
        XCTAssertTrue(HelperWatch.goesOn(live: 5))
        XCTAssertTrue(HelperWatch.goesOn(live: 1))
        XCTAssertFalse(HelperWatch.goesOn(live: 0))
    }

    func testItAsksTheDaemonOnlyWithALink() {
        XCTAssertTrue(HelperWatch.asks(live: 3, online: true))
        XCTAssertFalse(HelperWatch.asks(live: 3, online: false), "offline it asks nothing and keeps the last count")
        XCTAssertFalse(HelperWatch.asks(live: 0, online: true), "nothing live: nothing to ask")
    }

    func testAHelperThatEndedAsksForTheWholeChatToBeRead() {
        XCTAssertTrue(HelperWatch.readsWhole(live: ["a", "b", "c"], then: ["a", "b"]), "its report may have started a run in the chat")
        XCTAssertTrue(HelperWatch.readsWhole(live: ["a"], then: []))
        XCTAssertFalse(HelperWatch.readsWhole(live: ["a", "b"], then: ["a", "b"]), "nothing ended: one small request and no more")
        XCTAssertFalse(HelperWatch.readsWhole(live: ["a"], then: ["a", "b"]), "one more began: nothing ended")
        XCTAssertFalse(HelperWatch.readsWhole(live: [], then: []))
    }

    func testTheCountIsTheLastOneReadWhenThePhoneHasNoLinkOrDotCannotBeReached() {
        XCTAssertTrue(HelperWatch.isLastKnown(online: false, reconnecting: false))
        XCTAssertTrue(HelperWatch.isLastKnown(online: true, reconnecting: true))
        XCTAssertFalse(HelperWatch.isLastKnown(online: true, reconnecting: false))
    }
}
