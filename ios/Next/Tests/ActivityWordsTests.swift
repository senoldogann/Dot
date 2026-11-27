import DotEngine
import XCTest

@testable import DotNext

/// The Activity tab's words and rules (item 1826), pure functions of what the daemon said.
final class ActivityWordsTests: XCTestCase {
    private func away(done: Int, failed: Int, drafts: Int, yolo: Int) -> AwayView {
        AwayView(
            since: 0, until: 1, done: done, failed: failed, cancelled: 0, working: 0, campaignSessions: 0, runs: [],
            decisions: AwayDecisions(reviewerApproved: 0, reviewerDenied: 0, yoloApproved: yolo, yoloDenied: 0), decisionRuns: [], drafts: drafts)
    }

    private func row(kind: String, section: String, href: String, runId: String?) -> ActivityRow {
        row(kind: kind, section: section, href: href, runId: runId, actor: nil)
    }

    private func row(kind: String, section: String, href: String, runId: String?, actor: String?) -> ActivityRow {
        ActivityRow(
            id: runId ?? "x", section: section, kind: kind, title: "T", detail: "", since: 0, href: href, runId: runId, status: "done", minutes: nil, tokens: nil,
            steps: nil, parentRunId: nil, rootThreadId: nil, actorId: actor)
    }

    private func run(_ id: String, agent: String?, status: String) -> AwayRun {
        AwayRun(runId: id, title: id, source: "team", campaign: false, agentId: agent, status: status, createdAt: 0, finishedAt: nil, answer: "")
    }

    func testAnAgentsProfileShowsOnlyItsOwnWork() {
        let all: ActivityView = ActivityView(
            waiting: [], working: [row(kind: "run", section: "working", href: "", runId: "a", actor: "agt_scout")],
            endedDelegated: [], recent: [row(kind: "run", section: "recent", href: "", runId: "b", actor: nil), row(kind: "run", section: "recent", href: "", runId: "c", actor: "agt_scout")],
            learnedToday: [], due: [], generatedAt: 0)
        let scout: ActivityView = scoped(all, to: "agt_scout")
        XCTAssertEqual((scout.working + scout.recent).map(\.id), ["a", "c"])
        let away: AwayView = AwayView(
            since: 0, until: 1, done: 2, failed: 1, cancelled: 0, working: 0, campaignSessions: 0,
            runs: [run("r1", agent: "agt_scout", status: "failed"), run("r2", agent: nil, status: "done"), run("r3", agent: "agt_scout", status: "done")],
            decisions: AwayDecisions(reviewerApproved: 0, reviewerDenied: 0, yoloApproved: 4, yoloDenied: 0), decisionRuns: [], drafts: 3)
        let mine: AwayView = scoped(away, to: "agt_scout")
        XCTAssertEqual(mine.runs.map(\.runId), ["r1", "r3"])
        XCTAssertEqual([mine.done, mine.failed, mine.drafts, mine.decisions.yoloApproved], [1, 1, 0, 0], "Dot-wide counts are not the agent's")
        XCTAssertEqual(scoped(away, to: "agt_other").runs, [])
        XCTAssertEqual(awayLine(scoped(away, to: "agt_other")), "", "no card for an agent that did nothing")
    }

    func testNothingDoneWhileAwayIsNoCardAndEachKindOfNewsIsOnePart() {
        XCTAssertEqual(awayLine(away(done: 0, failed: 0, drafts: 0, yolo: 0)), "", "an empty card is never shown")
        XCTAssertEqual(awayLine(away(done: 1, failed: 1, drafts: 0, yolo: 0)).components(separatedBy: " · ").count, 1)
        XCTAssertEqual(awayLine(away(done: 1, failed: 0, drafts: 2, yolo: 3)).components(separatedBy: " · ").count, 3)
        XCTAssertEqual(awayLine(away(done: 0, failed: 0, drafts: 1, yolo: 0)).components(separatedBy: " · ").count, 1)
    }

    func testARowOpensItsRunOrTheRunItsLinkNamesAndNothingElse() {
        XCTAssertEqual(runOpened(by: row(kind: "run", section: "recent", href: "#/runs/run_a", runId: "run_a")), "run_a")
        XCTAssertEqual(runOpened(by: row(kind: "lesson", section: "learned", href: "#/runs/run_b", runId: nil)), "run_b", "a lesson opens the run it came from")
        XCTAssertNil(runOpened(by: row(kind: "schedule", section: "due", href: "#/settings/schedules", runId: nil)))
        XCTAssertNil(runOpened(by: row(kind: "journal", section: "learned", href: "#/memory", runId: nil)))
        XCTAssertNil(runOpened(by: row(kind: "run", section: "recent", href: "#/runs/", runId: nil)))
    }

    func testARunsStatusIsOneOfFourOutcomes() {
        XCTAssertEqual(RunOutcome(status: "done"), .done)
        XCTAssertEqual(RunOutcome(status: "failed"), .failed)
        XCTAssertEqual(RunOutcome(status: "cancelled"), .stopped)
        for working in ["queued", "running", "blocked", "ending"] { XCTAssertEqual(RunOutcome(status: working), .working) }
    }

    func testAVersionsTextIsReadAsPointsWithoutTheirDashes() {
        XCTAssertEqual(points(of: "- Run the tests first.\n\n  * Read the README.\n• Ask once\nplain line\n-dash glued"), [
            "Run the tests first.", "Read the README.", "Ask once", "plain line", "-dash glued",
        ])
        XCTAssertEqual(points(of: ""), [])
    }

    func testADraftWithoutASubjectIsNamedByItsFirstLine() {
        let message: DraftView = DraftView(
            id: "d", runId: "r", channel: "message", to: ["+90"], cc: [], subject: "", body: "\nRunning late.\nSee you.", status: "waiting", createdAt: 0, error: nil, mailbox: nil)
        XCTAssertEqual(draftTitle(message), "Running late.")
        let mail: DraftView = DraftView(id: "d", runId: "r", channel: "mail", to: ["a@b.c"], cc: [], subject: "Invoice", body: "Paid.", status: "failed", createdAt: 0, error: "x", mailbox: nil)
        XCTAssertEqual(draftTitle(mail), "Invoice")
        XCTAssertEqual(draftLine(mail).components(separatedBy: " · ").count, 3, "a failed send is said")
    }

    func testTheInitiativeCountsWhatIsBeingDoneOrToDo() {
        let todo: (String) -> InitiativeTodo = { status in
            InitiativeTodo(id: status, title: "t", why: "", source: "owner", status: status, attempts: 0, runId: nil, note: "", updatedAt: 0)
        }
        let view: InitiativeView = InitiativeView(
            enabled: true, lookedAt: nil, todos: ["doing", "open", "done", "dropped"].map(todo), idleReadable: true, approvals: "yolo", peakHoursUtc: [], lookModel: "")
        XCTAssertEqual(view.waiting, 2)
    }
}
