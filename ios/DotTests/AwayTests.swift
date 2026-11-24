import XCTest
@testable import Dot

/// "While you were away" (item 1443): what Dot did on its own, and the card that says it.
final class AwayTests: XCTestCase {
    /// The reply of `GET /api/away` as `docs/phone.md` writes it.
    private static let example: String = """
        {
          "since": 1764000000.0, "until": 1764003600.0,
          "done": 3, "failed": 1, "cancelled": 0, "working": 1, "campaign_sessions": 0,
          "runs": [
            {"run_id": "run_mf3k2x1a_1c04d1", "title": "Deploy the site", "source": "background", "campaign": false,
             "agent_id": null, "status": "done", "steps": 4, "created_at": 1764000100.0, "finished_at": 1764000160.0,
             "answer": "The deploy finished."}
          ],
          "decisions": {"reviewer_approved": 0, "reviewer_denied": 0, "yolo_approved": 2, "yolo_denied": 0},
          "decision_runs": [{"run_id": "run_mf3k2x1a_1c04d1", "title": "Deploy the site", "reviewer": 0, "yolo": 2, "denied": 0}],
          "drafts": 1
        }
        """

    private func view(_ json: String = AwayTests.example) throws -> AwayView {
        try JSONDecoder().decode(AwayView.self, from: Data(json.utf8))
    }

    private func run(_ id: String, _ status: String = "done", title: String = "A schedule", at: Double = 100, finished: Double? = nil, answer: String? = nil) -> AwayRun {
        AwayRun(runId: id, title: title, source: "schedule", status: status, createdAt: at, finishedAt: finished, answer: answer)
    }

    // MARK: Reading

    func testTheDaemonsOwnExampleReads() throws {
        let read: AwayView = try view()
        XCTAssertEqual(read.since, 1_764_000_000)
        XCTAssertEqual(read.until, 1_764_003_600)
        XCTAssertEqual([read.done, read.failed, read.cancelled, read.working, read.campaignSessions], [3, 1, 0, 1, 0])
        XCTAssertEqual(read.runs.count, 1)
        XCTAssertEqual(read.runs[0].runId, "run_mf3k2x1a_1c04d1")
        XCTAssertEqual(read.runs[0].source, "background")
        XCTAssertEqual(read.runs[0].answer, "The deploy finished.")
        XCTAssertEqual(read.decided, 2, "the approvals Dot answered for the owner, added")
    }

    func testAKeyTheDaemonLeavesOutReadsAsNoneAndARunThatDoesNotReadIsLeftOutOfTheCard() throws {
        let read: AwayView = try view(
            """
            {"since": 5, "until": 9, "done": 2,
             "runs": [{"run_id": "run_a", "title": "Ok", "status": "done", "created_at": 6},
                      {"title": "no id at all"}]}
            """)
        XCTAssertEqual([read.done, read.failed, read.cancelled, read.working], [2, 0, 0, 0])
        XCTAssertEqual(read.runs.map(\.runId), ["run_a"])
        XCTAssertEqual(read.decided, 0)
        XCTAssertEqual(read.runs[0].source, "")
    }

    func testWhatThePhoneKeepsReadsBackTheSame() throws {
        let read: AwayView = try view()
        let again: AwayView = try JSONDecoder().decode(AwayView.self, from: try JSONEncoder().encode(read))
        XCTAssertEqual(again, read)
    }

    func testTheMarkIsNilBeforeAnyDeviceSetItAndTheMomentAfter() throws {
        XCTAssertNil(try JSONDecoder().decode(AwayMark.self, from: Data(#"{"read_at": null}"#.utf8)).readAt)
        XCTAssertEqual(try JSONDecoder().decode(AwayMark.self, from: Data(#"{"read_at": 1764003000.0}"#.utf8)).readAt, 1_764_003_000)
    }

    // MARK: The card

    func testNothingDoneIsNoCardAtAll() {
        XCTAssertNil(AwayRules.card(from: AwayView(since: 1, until: 2)))
        XCTAssertNil(AwayRules.card(from: AwayView(since: 1, until: 2, decided: 4)), "approvals alone are no news worth a card")
    }

    func testTheCardSaysHowManyThingsDotDidAndHowTheyEnded() throws {
        let card: AwayCard = try XCTUnwrap(AwayRules.card(from: try view()))
        XCTAssertEqual(card.headline, "Dot did 5 things on its own")
        XCTAssertEqual(card.counts, "3 done · 1 failed · 1 at work")
        XCTAssertEqual(card.decisions, "Dot answered 2 approvals for you")
        XCTAssertEqual(card.until, 1_764_003_600, "dismissing marks this moment")
        XCTAssertEqual(card.since, 1_764_000_000)
        XCTAssertEqual(card.rows.map(\.run.runId), ["run_mf3k2x1a_1c04d1"])
        XCTAssertEqual(card.more, 4, "five things, one listed")
    }

    func testOneThingIsSaidInTheSingular() {
        let card: AwayCard? = AwayRules.card(from: AwayView(since: 1, until: 2, done: 1, runs: [run("run_1")], decided: 1))
        XCTAssertEqual(card?.headline, "Dot did 1 thing on its own")
        XCTAssertEqual(card?.counts, "1 done")
        XCTAssertEqual(card?.decisions, "Dot answered 1 approval for you")
        XCTAssertEqual(card?.more, 0)
    }

    func testTheFewNewestAreListedNewestFirstByTheirEnd() {
        let runs: [AwayRun] = [
            run("run_old", at: 10, finished: 20), run("run_new", at: 50, finished: 90), run("run_mid", at: 30, finished: 60),
            run("run_working", "running", at: 95), run("run_older", at: 5, finished: 8),
        ]
        let card: AwayCard? = AwayRules.card(from: AwayView(since: 1, until: 2, done: 4, working: 1, runs: runs))
        XCTAssertEqual(card?.rows.map(\.run.runId), ["run_working", "run_new", "run_mid"], "three newest; one that still works counts from its start")
        XCTAssertEqual(card?.more, 2)
    }

    func testTheCountsOfEverythingDotDidAreKeptWhenOnlyTheNewestFiftyCame() {
        let card: AwayCard? = AwayRules.card(from: AwayView(since: 1, until: 2, done: 120, runs: (0..<50).map { run("run_\($0)", at: Double($0)) }))
        XCTAssertEqual(card?.headline, "Dot did 120 things on its own")
        XCTAssertEqual(card?.more, 117)
    }

    func testARowSaysHowItEndedWhatItWasAndItsFirstLine() {
        let failed: AwayCard.Row = AwayRules.row(of: run("run_1", "failed", title: "Back up the notes", answer: "  The disk was full.  "))
        XCTAssertEqual(failed.status, "Failed")
        XCTAssertEqual(failed.title, "Back up the notes")
        XCTAssertEqual(failed.detail, "The disk was full.")
        XCTAssertNil(AwayRules.row(of: run("run_2", answer: "   ")).detail)
        XCTAssertEqual(AwayRules.row(of: run("run_3", title: "")).title, "A task", "a run with no name is still a row")
    }

    func testEachStatusIsSaidInTheOwnersWords() {
        XCTAssertEqual(AwayRules.status(of: "done"), "Done")
        XCTAssertEqual(AwayRules.status(of: "failed"), "Failed")
        XCTAssertEqual(AwayRules.status(of: "cancelled"), "Stopped")
        XCTAssertEqual(AwayRules.status(of: "blocked"), "Waiting for you")
        for working in ["queued", "running", "ending"] { XCTAssertEqual(AwayRules.status(of: working), "At work") }
    }

    func testNoWordOfTheCardIsTheEnginesOwn() throws {
        let card: AwayCard = try XCTUnwrap(AwayRules.card(from: try view()))
        let said: String = ([card.headline, card.counts, card.decisions ?? ""] + card.rows.map(\.status)).joined(separator: " ").lowercased()
        for word in ["daemon", "token", "run ", "api", "payload", "yolo", "reviewer"] { XCTAssertFalse(said.contains(word), word) }
    }
}
