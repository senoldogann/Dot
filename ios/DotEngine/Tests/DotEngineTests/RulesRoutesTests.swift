import Foundation
import XCTest

@testable import DotEngine

final class RulesRoutesTests: XCTestCase {
    func testPhoneReadsDraftsPreviewsAndAddsARule() async throws {
        let daemon = FakeDaemon(version: "1")
        let draftBody = #"{"behavior":"handoff","tool_glob":"shell","match_regex":"rm","description":"Leave deletion to me."}"#
        let savedBody = #"{"id":"rule_1","enabled":true,"behavior":"handoff","tool_glob":"shell","match_regex":"rm","description":"Leave deletion to me.","never_decided_by":null}"#
        daemon.failNext("GET", path: "/api/rules", status: 200, body: "[\(savedBody)]")
        daemon.failNext("GET", path: "/api/rules/usage", status: 200, body: #"{"rules":[{"rule_id":"rule_1","decides":2}],"partial":false,"problem":null}"#)
        daemon.failNext("POST", path: "/api/rules/draft", status: 200, body: draftBody)
        daemon.failNext("POST", path: "/api/rules/preview", status: 200, body: #"{"scanned":5,"fits":3,"would_decide":2,"partial":false,"problem":null}"#)
        daemon.failNext("POST", path: "/api/rules", status: 201, body: savedBody)

        let api: DaemonAPI = daemon.api()
        let rules: [SavedRule] = try await api.rules()
        XCTAssertEqual(rules.first?.matchRegex, "rm")
        let usage: RuleActivity = try await api.ruleActivity(days: 30)
        XCTAssertEqual(usage.rules.first?.ruleId, "rule_1")
        let draft: DraftRule = try await api.draftRule("Leave deletion to me.")
        let preview: RuleTryout = try await api.previewRule(draft, answer: .handoff, days: 30)
        XCTAssertEqual(preview.wouldDecide, 2)
        let saved: SavedRule = try await api.addRule(draft, answer: .handoff)
        XCTAssertEqual(saved.id, "rule_1")

        let body: [String: String] = try JSONDecoder().decode(
            [String: String].self, from: try XCTUnwrap(daemon.bodies("POST", "/api/rules").last))
        XCTAssertEqual(body, ["behavior": "handoff", "tool_glob": "shell", "match_regex": "rm", "description": "Leave deletion to me."])
    }
}
