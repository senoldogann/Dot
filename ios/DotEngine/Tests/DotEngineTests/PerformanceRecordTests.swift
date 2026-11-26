import XCTest

@testable import DotEngine

/// Item 1926: the phone's performance record as it goes to the Mac, and the counts it is made of.
final class PerformanceRecordTests: XCTestCase {
    private func report(_ at: Double) -> PhoneReport {
        var made: PhoneReport = PhoneReport(source: .app, beginsAt: at - 10, endsAt: at, appVersion: "1.0 (1)", osVersion: "iOS 26.1")
        made.bytesIn = 100
        return made
    }

    func testAReportLeavesOutWhatItDoesNotMeasureAndCutsLongVersions() throws {
        let made: PhoneReport = PhoneReport(source: .metrickit, beginsAt: 1, endsAt: 2, appVersion: String(repeating: "9", count: 60), osVersion: "iOS 26.1")
        let json: [String: JSONValue] = try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(made))
        XCTAssertEqual(Set(json.keys), ["source", "begins_at", "ends_at", "app_version", "os_version"])
        XCTAssertEqual(json["app_version"]?.string?.count, 40, "the daemon takes at most 40 characters")
        XCTAssertEqual(json["source"]?.string, "metrickit")
    }

    func testReportsGoFiftyAtATimeAndTheNewestAreKept() {
        let held: [PhoneReport] = (0..<120).map { report(Double($0)) }
        let (send, keep): ([PhoneReport], [PhoneReport]) = reportsToSend(held)
        XCTAssertEqual(send.count, PhoneReports.most)
        XCTAssertEqual(send.first?.endsAt, 0)
        XCTAssertEqual(keep.count, 70)
        let kept: [PhoneReport] = heldReports(held, adding: [report(500)], limit: 100)
        XCTAssertEqual(kept.count, 100)
        XCTAssertEqual(kept.first?.endsAt, 21, "the oldest go first")
        XCTAssertEqual(kept.last?.endsAt, 500)
    }

    func testTrafficCountsSubtract() {
        let earlier: TrafficCounts = TrafficCounts(bytesIn: 10, bytesOut: 5, requests: 1, streamOpens: 1, streamBreaks: 0)
        let later: TrafficCounts = earlier.adding(TrafficCounts(bytesIn: 90, bytesOut: 15, requests: 2, streamOpens: 1, streamBreaks: 1))
        XCTAssertEqual(later.since(earlier), TrafficCounts(bytesIn: 90, bytesOut: 15, requests: 2, streamOpens: 1, streamBreaks: 1))
    }

    func testOnlyLifecycleFramesOfTheListAreHeard() {
        let event: EventRecord = EventRecord(id: 4, runId: "run_1", ts: 1, kind: "run_started", data: [:])
        XCTAssertTrue(ListReducer.isLifecycle(.live(event)))
        XCTAssertTrue(ListReducer.isLifecycle(.event(event)))
        XCTAssertFalse(ListReducer.isLifecycle(.resync))
        XCTAssertFalse(ListReducer.isLifecycle(.delta(event)))
    }

    func testOnlyARefusalThatNothingWillChangeDropsTheReports() {
        func status(_ code: Int) -> DaemonRequestError { .status(path: "/api/phone/metrics", code: code, body: "") }
        XCTAssertTrue(status(422).refusesTheReportsForGood)
        XCTAssertTrue(status(400).refusesTheReportsForGood)
        for code in [401, 403, 404, 408, 429, 500, 503] { XCTAssertFalse(status(code).refusesTheReportsForGood, "\(code) keeps them") }
        XCTAssertFalse(DaemonRequestError.transport(path: "/p", underlying: URLError(.timedOut)).refusesTheReportsForGood)
    }
}
