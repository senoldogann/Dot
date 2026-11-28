import Foundation
import XCTest

@testable import DotEngine

final class MacMaintenanceRoutesTests: XCTestCase {
    func testMaskedLogUsesALimitedTail() async throws {
        let daemon = FakeDaemon(version: "1")
        daemon.failNext("GET", path: "/api/diagnostics/logs/dotx.log", status: 200,
                        body: #"{"name":"dotx.log","lines":["Dot is ready"]}"#)
        let tail: DiagnosticLogTail = try await daemon.api().diagnosticLog("dotx.log", lines: 300)
        XCTAssertEqual(tail.lines, ["Dot is ready"])
        XCTAssertEqual(daemon.requests.last?.query["lines"], "300")
    }

    func testSelectingAProfileAndCheckingItUsesTheMacsRoutes() async throws {
        let daemon = FakeDaemon(version: "1")
        let browser = #"{"mode":"own","enabled":true,"connected":true,"signing_in":false,"raise_window":false,"remote_input":false,"debug_port":0,"profile":"work","profiles":[{"name":"work","kind":"added"}],"look":{"fit_window":true,"viewport_width":1280,"viewport_height":720,"appearance":"system","zoom":100}}"#
        daemon.failNext("POST", path: "/api/browser/profile", status: 200, body: browser)
        daemon.failNext("POST", path: "/api/browser/profiles/work/check", status: 200, body: #"{"readable":2}"#)
        let api: DaemonAPI = daemon.api()
        let settings: BrowserSettingsView = try await api.useBrowserProfile("work")
        XCTAssertEqual(settings.profile, "work")
        XCTAssertEqual(settings.look.viewportWidth, 1280)
        let body: Data = try XCTUnwrap(daemon.bodies("POST", "/api/browser/profile").last)
        XCTAssertEqual(try JSONDecoder().decode([String: String].self, from: body), ["name": "work"])
        let checked: BrowserProfileCheck = try await api.checkBrowserProfile("work")
        XCTAssertEqual(checked.readable, 2)
    }
}
