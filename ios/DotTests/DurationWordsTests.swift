import XCTest
@testable import Dot

/// The words under a finished run and under the title of a chat that finished a moment ago (item 1393).
final class DurationWordsTests: XCTestCase {
    func testDurationsAreShort() {
        XCTAssertEqual(DurationWords.short(0), "0s")
        XCTAssertEqual(DurationWords.short(44.6), "45s")
        XCTAssertEqual(DurationWords.short(72), "1m 12s")
        XCTAssertEqual(DurationWords.short(3_725), "1h 2m")
        XCTAssertEqual(DurationWords.short(-5), "0s")
    }

    func testAFinishedRunSaysItsStepsAndItsTime() {
        XCTAssertEqual(DurationWords.finished(status: "done", steps: 3, startedAt: 100, finishedAt: 172), "Done · 3 steps · 1m 12s")
        XCTAssertEqual(DurationWords.finished(status: "done", steps: 1, startedAt: 100, finishedAt: 110), "Done · 1 step · 10s")
        XCTAssertEqual(DurationWords.finished(status: "done", steps: 0, startedAt: 100, finishedAt: 101), "Done · 1s")
        XCTAssertNil(DurationWords.finished(status: "failed", steps: 3, startedAt: 100, finishedAt: 172), "a failed run has its own line")
        XCTAssertNil(DurationWords.finished(status: "done", steps: 3, startedAt: 100, finishedAt: nil))
    }

    func testTheSubtitleOfAChatAtRestLastsTenMinutes() {
        let now: Date = Date(timeIntervalSince1970: 10_000)
        XCTAssertEqual(DurationWords.subtitle(status: "done", finishedAt: 9_990, now: now), "Done · just now")
        XCTAssertEqual(DurationWords.subtitle(status: "done", finishedAt: 9_700, now: now), "Done · 5 min ago")
        XCTAssertEqual(DurationWords.subtitle(status: "failed", finishedAt: 9_700, now: now), "Failed · 5 min ago")
        XCTAssertEqual(DurationWords.subtitle(status: "cancelled", finishedAt: 9_700, now: now), "Stopped · 5 min ago")
        XCTAssertNil(DurationWords.subtitle(status: "done", finishedAt: 9_000, now: now), "after ten minutes it is quiet")
        XCTAssertNil(DurationWords.subtitle(status: "running", finishedAt: 9_990, now: now))
        XCTAssertNil(DurationWords.subtitle(status: "done", finishedAt: nil, now: now))
    }
}
