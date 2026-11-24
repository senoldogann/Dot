import XCTest
@testable import Dot

/// The helpers' list puts the ones at work first (item 1467). The owner's screenshot of 2026-11-24, 10:42 was the before: sixty
/// helpers that had ended stood first in the Run sheet, and the five that worked were below them, out of sight.
final class HelperOrderTests: XCTestCase {
    private func helper(_ id: String, _ status: String, at created: Double = 0) -> Helper {
        Helper(id: id, goal: "goal \(id)", status: status, words: nil, createdAt: created)
    }

    func testTheOnesAtWorkComeFirstNewestFirstThenTheFinishedNewestFirst() {
        let groups: HelperOrder.Groups = HelperOrder.groups([
            helper("a", "done", at: 10), helper("b", "running", at: 20), helper("c", "failed", at: 30),
            helper("d", "running", at: 40), helper("e", "cancelled", at: 5), helper("f", "queued", at: 50),
        ])
        XCTAssertEqual(groups.working.map(\.id), ["f", "d", "b"])
        XCTAssertEqual(groups.finished.map(\.id), ["c", "a", "e"])
        XCTAssertFalse(groups.isEmpty)
    }

    /// The screenshot: the state holds helpers oldest first (the daemon's list is newest first and is read back to front), sixty
    /// that ended and five that work, the five among the newest of all.
    func testFiveThatWorkAreNotBelowSixtyThatEnded() {
        var held: [Helper] = (0..<60).map { helper("done\($0)", $0 % 7 == 0 ? "cancelled" : "done", at: Double(100 + $0)) }
        held.append(contentsOf: (0..<5).map { helper("live\($0)", "running", at: Double(200 + $0)) })
        let groups: HelperOrder.Groups = HelperOrder.groups(held)
        XCTAssertEqual(groups.working.map(\.id), ["live4", "live3", "live2", "live1", "live0"])
        XCTAssertEqual(groups.finished.count, 60)
        XCTAssertEqual(groups.finished.first?.id, "done59", "the finished ones are newest first too")
        XCTAssertEqual(groups.finished.last?.id, "done0")
    }

    func testAQueuedAHelperThatAsksTheOwnerAndOneThatIsEndingAreAtWork() {
        let groups: HelperOrder.Groups = HelperOrder.groups([
            helper("q", "queued", at: 1), helper("b", "blocked", at: 2), helper("e", "ending", at: 3), helper("d", "done", at: 4),
        ])
        XCTAssertEqual(groups.working.map(\.id), ["e", "b", "q"])
        XCTAssertEqual(groups.finished.map(\.id), ["d"])
    }

    func testAHelperWhoseTimeIsNotKnownStandsWhereItCameTheLaterArrivalNewer() {
        let groups: HelperOrder.Groups = HelperOrder.groups([
            helper("first", "running"), helper("second", "running"), helper("third", "running"),
        ])
        XCTAssertEqual(groups.working.map(\.id), ["third", "second", "first"])
    }

    func testAnEmptyListHasNoGroupsAndOneGroupMayBeEmpty() {
        XCTAssertTrue(HelperOrder.groups([]).isEmpty)
        let onlyDone: HelperOrder.Groups = HelperOrder.groups([helper("a", "done", at: 1)])
        XCTAssertTrue(onlyDone.working.isEmpty)
        XCTAssertEqual(onlyDone.finished.count, 1)
    }

    func testEachGroupSaysHowManyItHolds() {
        XCTAssertEqual(HelperOrder.title(.working, count: 5), "Working · 5")
        XCTAssertEqual(HelperOrder.title(.finished, count: 12), "Finished · 12")
        XCTAssertEqual(HelperOrder.title(.working, count: 1), "Working · 1")
    }

    func testTheBadgeIsTheDaemonsWordAsBefore() {
        XCTAssertEqual(HelperOrder.badge(for: helper("a", "done")), "Done")
        XCTAssertEqual(HelperOrder.badge(for: helper("a", "failed")), "Failed")
        XCTAssertEqual(HelperOrder.badge(for: helper("a", "cancelled")), "Cancelled")
        XCTAssertEqual(HelperOrder.badge(for: helper("a", "running")), "Running")
    }

    /// The daemon's word for a helper that asks the owner something is "blocked"; the row says what it means.
    func testAHelperThatWaitsOnTheOwnerSaysSoAndOneThatIsEndingSaysStopping() {
        XCTAssertEqual(HelperOrder.badge(for: helper("a", "blocked")), "Waiting on you")
        XCTAssertEqual(HelperOrder.badge(for: helper("a", "ending")), "Stopping")
    }

    /// The state keeps the time the daemon queued each helper: from its list, and from the event that queued it.
    func testTheStateKeepsWhenEachHelperWasQueuedFromTheListAndFromTheEvents() {
        var state: ChatState = ChatState()
        state.addRun(makeRun("run_1", status: "running", created: 1000))
        // The daemon's list is newest first.
        state.setHelpers([
            makeRun("h3", status: "running", parent: "run_1", created: 1030),
            makeRun("h2", status: "done", parent: "run_1", created: 1020),
            makeRun("h1", status: "done", parent: "run_1", created: 1010),
        ])
        state.apply(event(7, "h4", "run_queued", ["goal": .string("Read the receipts"), "parent_run_id": .string("run_1")], ts: 1040))
        XCTAssertEqual(state.helpers.map(\.createdAt), [1010, 1020, 1030, 1047], "the event's own time (the test helper adds its id)")
        let groups: HelperOrder.Groups = HelperOrder.groups(state.helpers)
        XCTAssertEqual(groups.working.map(\.id), ["h4", "h3"])
        XCTAssertEqual(groups.finished.map(\.id), ["h2", "h1"])
    }

    func testAHelperThatEndsMovesFromWorkingToFinished() {
        var state: ChatState = ChatState()
        state.addRun(makeRun("run_1", status: "done", created: 1000))
        state.setHelpers([
            makeRun("h2", status: "running", parent: "run_1", created: 1020), makeRun("h1", status: "running", parent: "run_1", created: 1010),
        ])
        XCTAssertEqual(HelperOrder.groups(state.helpers).working.map(\.id), ["h2", "h1"])
        state.setHelpers([
            makeRun("h2", status: "running", parent: "run_1", created: 1020), makeRun("h1", status: "done", parent: "run_1", created: 1010),
        ])
        let groups: HelperOrder.Groups = HelperOrder.groups(state.helpers)
        XCTAssertEqual(groups.working.map(\.id), ["h2"])
        XCTAssertEqual(groups.finished.map(\.id), ["h1"])
    }
}
