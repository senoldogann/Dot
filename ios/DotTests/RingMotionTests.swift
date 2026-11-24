import XCTest
@testable import Dot

/// The ring round a working agent (item 1460): it turns slowly, evenly, only while the agent works, and not at all when the
/// reader asked for less motion.
final class RingMotionTests: XCTestCase {
    func testAWorkingRingTurnsOnceInFourSeconds() {
        // The owner's word after using the phone: the 1.4 s turn was "far too fast"; four seconds is the pace asked for.
        XCTAssertEqual(RingMotion.secondsPerTurn, 4)
    }

    func testOnlyAWorkingRingTurns() {
        XCTAssertTrue(RingMotion.turns(state: .working, reduceMotion: false))
        XCTAssertFalse(RingMotion.turns(state: .waiting, reduceMotion: false), "an amber ring waits for the owner, it does not spin")
        XCTAssertFalse(RingMotion.turns(state: .idle, reduceMotion: false))
    }

    func testReduceMotionLeavesTheRingStill() {
        XCTAssertFalse(RingMotion.turns(state: .working, reduceMotion: true))
        XCTAssertFalse(RingMotion.turns(state: .waiting, reduceMotion: true))
    }

    /// A chat's row reads its state from the run's status: blocked is waiting on the owner, a run in hand is working.
    func testAChatRowStandsAsItsRunDoes() {
        func row(_ status: String) -> ThreadSummary {
            ThreadSummary(
                id: "t", title: "", source: "ui", updatedAt: 0, pinnedAt: nil, runCount: 1, latestRunId: "r", latestStatus: status,
                latestMode: "task", unread: false, actorId: nil, lastMessage: "", lastRole: "", loopCron: nil, loopNextAt: nil)
        }
        XCTAssertEqual(row("running").state, .working)
        XCTAssertEqual(row("queued").state, .working)
        XCTAssertEqual(row("blocked").state, .waiting)
        XCTAssertEqual(row("done").state, .idle)
        XCTAssertEqual(row("failed").state, .idle)
    }
}
