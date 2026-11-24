import XCTest
@testable import Dot

/// The capsule over the composer (item 1390): which segments it has, in what order, and what each says.
final class DockTests: XCTestCase {
    private func queue(_ count: Int, held: Bool = false) -> QueueView {
        QueueView(
            held: held,
            messages: (0..<count).map { QueuedMessage(id: "q_\($0)", position: $0, text: "message \($0)", createdAt: 1) },
            besideRunId: nil, besideThreadId: nil)
    }

    private func automation(loop: Bool, when: String, enabled: Bool = true, id: String = "sch_1") throws -> ThreadAutomation {
        let json: String = """
            {"schedule": {"record": {"id": "\(id)", "name": "n", "cron": "*/10 * * * *", "run_at": null, "goal": "g",
              "enabled": \(enabled), "next_run_at": null, "last_run_at": null, "thread_id": "thr_1", "last_status": ""},
              "when": "\(when)", "next_local": "03:50", "agent": null, "brief": false}, "loop": \(loop)}
            """
        return try JSONDecoder().decode(ThreadAutomation.self, from: Data(json.utf8))
    }

    private func make(
        _ line: RunLine = .idle, connection: FeedState = .live, queue: QueueView? = nil, automations: [ThreadAutomation] = []
    ) -> DockSummary {
        DockSummary.make(line: line, connection: connection, queue: queue ?? self.queue(0), automations: automations)
    }

    func testAChatAtRestHasNoCapsule() {
        XCTAssertTrue(make().isEmpty)
        XCTAssertTrue(make(.idle, connection: .connecting).isEmpty, "a stream still connecting is not a thing to say")
    }

    func testAWorkingRunHasOneStatusSegmentThatCountsItsTime() {
        let summary: DockSummary = make(.working(since: 1_000))
        XCTAssertEqual(summary.segments.map(\.kind), [.status])
        XCTAssertEqual(summary.segments[0].words, "Working")
        XCTAssertEqual(summary.segments[0].since, 1_000)
        XCTAssertFalse(summary.asksTheOwner)
    }

    func testARunThatWaitsOnTheOwnerIsTheWarmOne() {
        let one: DockSummary = make(.waitingOnYou(count: 1))
        XCTAssertEqual(one.segments[0].words, "Waiting on you")
        XCTAssertEqual(one.segments[0].tone, .warn)
        XCTAssertTrue(one.asksTheOwner)
        XCTAssertEqual(make(.waitingOnYou(count: 3)).segments[0].words, "Waiting on you · 3")
        XCTAssertEqual(make(.waitingOnYou(count: 3)).segments[0].short, "You · 3")
    }

    func testWaitingOnHelpersSaysWhatTheFirstDoes() {
        let summary: DockSummary = make(.waitingOnSubagents(count: 2, now: "Reads the page"))
        XCTAssertEqual(summary.segments.map(\.kind), [.status])
        XCTAssertEqual(summary.segments[0].words, "Waiting on 2 helpers")
        XCTAssertEqual(summary.segments[0].detail, "Reads the page")
        XCTAssertEqual(summary.segments[0].short, "2 helpers")
        XCTAssertEqual(make(.waitingOnSubagents(count: 1, now: nil)).segments[0].words, "Waiting on 1 helper")
    }

    func testHelpersThatWorkOnInTheBackgroundAreTheirOwnSegment() {
        let summary: DockSummary = make(.helpersInBackground(count: 1))
        XCTAssertEqual(summary.segments.map(\.kind), [.helpers])
        XCTAssertEqual(summary.segments[0].words, "1 helper")
    }

    func testTheQueueSaysHowManyAndWhenItIsHeld() {
        XCTAssertEqual(make(queue: queue(3)).segment(.queue)?.words, "3 queued")
        XCTAssertEqual(make(queue: queue(3)).segment(.queue)?.short, "3")
        let held: DockSummary.Segment? = make(queue: queue(2, held: true)).segment(.queue)
        XCTAssertEqual(held?.words, "Queued · paused")
        XCTAssertEqual(held?.tone, .warn)
        XCTAssertNil(make(queue: queue(0, held: true)).segment(.queue), "a held queue with nothing in it says nothing")
    }

    func testALoopIsNamedByItsRhythmAndSeveralByTheirNumber() throws {
        let one: DockSummary = make(automations: [try automation(loop: true, when: "every 10 minutes")])
        XCTAssertEqual(one.segment(.loops)?.words, "every 10 min")
        let two: DockSummary = make(
            automations: [try automation(loop: true, when: "every 10 minutes"), try automation(loop: true, when: "every hour", id: "sch_2")])
        XCTAssertEqual(two.segment(.loops)?.words, "2 loops")
        XCTAssertEqual(two.segment(.loops)?.short, "2")
    }

    func testOnlyALoopThatIsOnCounts() throws {
        XCTAssertNil(make(automations: [try automation(loop: false, when: "every day at 10:00")]).segment(.loops), "a schedule is not a loop")
        XCTAssertNil(make(automations: [try automation(loop: true, when: "every 10 minutes", enabled: false)]).segment(.loops))
    }

    func testARhythmTooLongForTheCapsuleIsJustALoop() {
        XCTAssertEqual(DockSummary.rhythm("Every 30 minutes"), "every 30 min")
        XCTAssertEqual(DockSummary.rhythm("every 2 hours"), "every 2 h")
        XCTAssertEqual(DockSummary.rhythm("every weekday at 10:00 and 15:00"), "1 loop")
        XCTAssertEqual(DockSummary.rhythm("  "), "1 loop")
        XCTAssertEqual(DockSummary.rhythm("cron */10 * * * *"), "every 10 min")
        XCTAssertEqual(DockSummary.rhythm("cron 0 */2 * * *"), "every 2 h")
        XCTAssertEqual(DockSummary.rhythm("cron 30 9 * * 1-5"), "1 loop", "a cron the capsule cannot say in a word is just a loop")
    }

    func testTheSegmentsStandInOneOrder() throws {
        let summary: DockSummary = make(
            .waitingOnSubagents(count: 2, now: nil), queue: queue(1),
            automations: [try automation(loop: true, when: "every 10 minutes")])
        XCTAssertEqual(summary.segments.map(\.kind), [.status, .loops, .queue], "status, helpers, loops, queue")
        XCTAssertEqual(make(.helpersInBackground(count: 2), queue: queue(1)).segments.map(\.kind), [.helpers, .queue])
    }

    func testAStreamThatCannotConnectIsSaidInTheStatusSegment() {
        let down: DockSummary = make(.idle, connection: .reconnecting(attempt: 2, unauthorized: false))
        XCTAssertEqual(down.segments.map(\.words), ["Reconnecting…"])
        let refused: DockSummary = make(.idle, connection: .reconnecting(attempt: 1, unauthorized: true))
        XCTAssertEqual(refused.segments[0].symbol, "key")
        XCTAssertEqual(refused.segments[0].tone, .warn)
        let working: DockSummary = make(.working(since: nil), connection: .reconnecting(attempt: 1, unauthorized: false))
        XCTAssertEqual(working.segments.count, 1, "a working run that is reconnecting is one segment, not two")
        XCTAssertEqual(working.segments[0].detail, "Reconnecting…")
        XCTAssertEqual(working.segments[0].symbol, "wifi.exclamationmark", "the compact form still says the stream is down")
    }
}
