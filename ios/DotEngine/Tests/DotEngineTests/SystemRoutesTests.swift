import XCTest

@testable import DotEngine

final class SystemRoutesTests: XCTestCase {
    func testReadsHealthChecksAndServiceStatusAndRequestsRestart() async throws {
        let daemon = FakeDaemon(version: "1")
        daemon.failNext("GET", path: "/api/doctor", status: 200,
                        body: #"[{"name":"Chrome","ok":false,"required":false,"detail":"Chrome is missing."}]"#)
        daemon.failNext("GET", path: "/api/desktop/permissions", status: 200, body: #"{"service":true,"service_problem":null}"#)
        daemon.failNext("POST", path: "/api/system/restart", status: 200, body: #"{"restarting":true}"#)

        let api: DaemonAPI = daemon.api()
        let checks: [SystemCheck] = try await api.systemChecks()
        XCTAssertEqual(checks.first?.detail, "Chrome is missing.")
        XCTAssertEqual(checks.first?.required, false)
        let availability: RestartAvailability = try await api.restartAvailability()
        XCTAssertTrue(availability.service)
        let receipt: RestartReceipt = try await api.restartDot()
        XCTAssertTrue(receipt.restarting)
    }
}
