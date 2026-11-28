import Foundation
import XCTest

@testable import DotEngine

final class SkillManagementTests: XCTestCase {
    func testRestoredVersionAndRevertedPluginUseExistingRoutes() async throws {
        let daemon = FakeDaemon(version: "1")
        let versions = #"{"items":[{"note":{"n":2,"at":123,"tainted":false},"current":false,"text":{"description":"Do this","when":"On Monday","steps":["Read"]}}]}"#
        let gallery = #"{"plugins":[{"id":"reports","name":"Reports","does":"Writes reports","state":"installed","revertible":false}],"others":[]}"#
        daemon.failNext("GET", path: "/api/skills/learned/weekly-review/versions", status: 200, body: versions)
        daemon.failNext("POST", path: "/api/skills/learned/weekly-review/restore", status: 200, body: #"{"items":[]}"#)
        daemon.failNext("POST", path: "/api/plugin-gallery/reports/revert", status: 200, body: gallery)
        daemon.failNext("DELETE", path: "/api/installed-plugins/reports", status: 200, body: #"{"servers":[]}"#)
        let api = daemon.api()

        let history: SkillVersions = try await api.learnedSkillVersions("weekly-review")
        let version: LearnedSkillVersion = try XCTUnwrap(history.items.first)
        XCTAssertEqual(version.id, 2)
        _ = try await api.restoreLearnedSkill("weekly-review", version: version.id)
        let body: Data = try XCTUnwrap(daemon.bodies("POST", "/api/skills/learned/weekly-review/restore").last)
        XCTAssertEqual(try JSONDecoder().decode([String: Int].self, from: body), ["version": 2])

        let installed: InstalledPlugins = try await api.revertInstalledPlugin("reports")
        XCTAssertEqual(installed.plugins.first?.installed, true)
        XCTAssertEqual(installed.plugins.first?.revertible, false)
        try await api.removeInstalledPlugin("reports")
        XCTAssertTrue(daemon.requests.contains { $0.method == "DELETE" && $0.path == "/api/installed-plugins/reports" })
    }
}
