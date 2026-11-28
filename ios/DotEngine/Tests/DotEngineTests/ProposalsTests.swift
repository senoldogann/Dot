import Foundation
import XCTest

@testable import DotEngine

/// The skill and plugin proposals Activity answers, as the daemon writes them (`ProposalView`, `ScoutView`).
final class ProposalsTests: XCTestCase {
    func testASkillProposalReads() throws {
        let body: Data = Data(
            #"""
            {"items":[{"proposal":{"id":"p1","kind":"merge","names":["find flights","book trains"],
              "draft":{"kind":"plan a trip","description":"Plans a trip end to end","when":"a trip","steps":["Find","Book"],
                       "parameters":[],"recurs":false},
              "reason":"They overlap","words":null,"bases":{"find flights":2},"tainted":true,"created_at":1},
             "stale":null,"current":[],"fields":[]}]}
            """#.utf8)
        let read: SkillProposalsView = try JSONDecoder().decode(SkillProposalsView.self, from: body)
        XCTAssertEqual(read.items.first?.proposal.names, ["find flights", "book trains"])
        XCTAssertEqual(read.items.first?.proposal.draft.steps, ["Find", "Book"])
        XCTAssertNil(read.items.first?.stale)
        XCTAssertEqual(read.items.first?.proposal.tainted, true)
    }

    func testAPluginProposalReads() throws {
        let body: Data = Data(
            #"""
            {"proposals":[{"id":"pdf-tools","kind":"update","name":"PDF tools","publisher":"acme","tier":"b","source":"github",
              "category":"docs","repository":"acme/pdf","commit":"abc","homepage":"","skills":["split"],"licence":"MIT","stars":3,
              "pushed":"2026-11-01","risk":null,"refuse":false,"does":"Splits PDFs","why":"You read PDFs","needs":"",
              "fit":0.7,"findings":[],"previous":"abb","changed":[],"state":"proposed","found_at":1,"failure":null}],
             "refused":[],"last_run":null,"last_summary":"","running":false,"time":"03:00"}
            """#.utf8)
        let read: ScoutProposals = try JSONDecoder().decode(ScoutProposals.self, from: body)
        XCTAssertEqual(read.proposals.first?.key, "update:pdf-tools", "an update and a new plugin of one name are two proposals")
        XCTAssertNil(read.proposals.first?.risk, "no review yet")
    }
}
