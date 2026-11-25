import XCTest

@testable import DotEngine

/// The contract of the Settings pages made native in items 1770-1774 (PLAN §26's part): the daemon's JSON (as its routes answer, keys
/// the phone does not read included) decodes, and the bodies the phone sends carry every key the daemon requires.
final class SettingsDetailTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T { try JSONDecoder().decode(type, from: Data(json.utf8)) }

    private func object(_ value: some Encodable) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as? [String: Any])
    }

    func testTheCodeReviewChangeSendsBothEfforts() throws {
        let config: CodeReviewConfig = try decode(
            CodeReviewConfig.self,
            #"{"model":"claude_code:opus","effort":"medium","fallback":["opencode_go:deepseek-v4.1-flash"],"fallback_effort":"high","small_model":"","small_max_lines":150,"max_minutes":15}"#)
        var change: CodeReviewChange = CodeReviewChange(config)
        change.effort = "high"
        let body: [String: Any] = try object(change)
        XCTAssertEqual(Set(body.keys), ["model", "effort", "fallback", "fallback_effort", "small_model", "small_max_lines", "max_minutes"])
        XCTAssertEqual(body["effort"] as? String, "high")
        XCTAssertEqual(body["fallback_effort"] as? String, "high")
    }

    func testANotesPageDecodesAndAnEditSendsWordsAndTags() throws {
        let record: String =
            #"{"id":"m1","content":"Short answers.","tags":["owner"],"created_at":1,"updated_at":2,"source_run":"reflection","agent_id":null,"used_count":4,"last_used_at":3,"expires_at":null,"replaced_by":null,"tainted":false,"merged_into":null,"merged_from":[],"pinned_at":null,"rested_at":null,"edited_by":null,"proposed":null}"#
        let page: MemoryList = try decode(MemoryList.self, #"{"total":1,"items":[\#(record)],"resting":[],"rested":{},"scores":{},"agents":{}}"#)
        XCTAssertEqual(page.items.first?.content, "Short answers.")
        XCTAssertEqual(page.total, 1)
        XCTAssertEqual(Set(try object(MemoryWrite(content: "x", tags: [])).keys), ["content", "tags"])
    }

    func testAnAgentDecodesAndIsWrittenBackWholeWithItsLook() throws {
        let agent: String =
            #"{"id":"ag","name":"Scout","role":"","instructions":"","model":"","effort":null,"approvals":null,"tools":[],"created_at":0,"updated_at":0,"made_by":null,"look":{"shape":"pebble","expression":"happy","colour":"teal"},"paused_at":null,"refuses":""}"#
        let list: [AgentListItem] = try decode(
            [AgentListItem].self,
            #"[{"record":\#(agent),"latest":null,"latest_title":null,"home":{"id":"thr_1","title":"Hello","source":"ui","created_at":0,"updated_at":5,"pinned_at":null,"run_count":1,"latest_run_id":"r","latest_status":"done","latest_mode":"task","latest_created_at":0,"unread":false,"actor_id":"ag","last_message":"","last_role":"agent","loop_cron":null,"loop_next_at":null,"team":null,"campaign_id":null}}]"#)
        XCTAssertEqual(list.first?.home?.id, "thr_1")
        let record: AgentRecordView = try XCTUnwrap(list.first?.record)
        let body: [String: Any] = try object(
            AgentWrite(name: record.name, role: record.role, instructions: record.instructions, model: "ollama:qwen3:cloud", effort: nil, approvals: nil, tools: nil, refuses: "", look: record.look))
        XCTAssertTrue(body["effort"] is NSNull)
        XCTAssertTrue(body["approvals"] is NSNull)
        XCTAssertTrue(body["tools"] is NSNull, "null is every action")
        XCTAssertEqual((body["look"] as? [String: String])?["colour"], "teal", "the face is kept: a null look would wipe it")
    }
}
