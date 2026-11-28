import XCTest

@testable import DotEngine

final class RepairRoutesTests: XCTestCase {
    func testReadsTheBookAndStartsAndStopsRepairs() async throws {
        let daemon = FakeDaemon(version: "1")
        let idle = #"{"repo":"Dot","running":false,"worker":{"model":"opus","effort":"high","fast":false},"worker_lost":false,"problems":[],"live":null}"#
        let running = #"{"repo":"Dot","running":true,"worker":{"model":"opus","effort":"high","fast":false},"worker_lost":false,"problems":[],"live":{"title":"A browser stopped responding","phase":"checks"}}"#
        daemon.failNext("POST", path: "/api/repair/settle", status: 200, body: idle)
        daemon.failNext("POST", path: "/api/repair/now", status: 200, body: running)
        daemon.failNext("GET", path: "/api/repair", status: 200, body: running)
        daemon.failNext("POST", path: "/api/repair/stop", status: 200, body: idle)
        let api: DaemonAPI = daemon.api()
        let settled: RepairStatus = try await api.settleRepairs()
        let started: RepairStatus = try await api.startRepairs()
        let read: RepairStatus = try await api.repairStatus()
        let stopped: RepairStatus = try await api.stopRepairs()
        XCTAssertEqual(settled.repo, "Dot")
        XCTAssertTrue(started.running)
        XCTAssertEqual(read.live?.phase, .checks)
        XCTAssertFalse(stopped.running)
    }
}
