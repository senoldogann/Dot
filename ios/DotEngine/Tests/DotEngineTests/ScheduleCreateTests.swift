import XCTest

@testable import DotEngine

/// A schedule made on the phone: the rhythm the form picks becomes the daemon's cron or `at`, the body leaves out what
/// the owner left on Default, and its history decodes as `GET /api/schedules/{id}/firings` answers.
final class ScheduleCreateTests: XCTestCase {
    private func object(_ value: some Encodable) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as? [String: Any])
    }

    func testEachRhythmIsTheWebFormsCron() {
        XCTAssertEqual(ScheduleRhythm.hourly.cron, "0 * * * *")
        XCTAssertEqual(ScheduleRhythm.daily(hour: 9, minute: 0).cron, "0 9 * * *")
        XCTAssertEqual(ScheduleRhythm.weekdays(hour: 9, minute: 30).cron, "30 9 * * 1-5")
        XCTAssertEqual(ScheduleRhythm.weekly(weekday: 1, hour: 18, minute: 5).cron, "5 18 * * 1")
        XCTAssertEqual(ScheduleRhythm.custom("  0  9 * *   1-5 ").cron, "0 9 * * 1-5", "spaces typed by hand are folded")
        XCTAssertNil(ScheduleRhythm.once(Date(timeIntervalSince1970: 0)).cron)
        XCTAssertNil(ScheduleRhythm.daily(hour: 9, minute: 0).at(in: .gmt))
    }

    func testASingleRunCarriesThePhonesOffset() throws {
        let istanbul: TimeZone = try XCTUnwrap(TimeZone(identifier: "Europe/Istanbul"))
        // 2026-11-27 06:00 UTC is 09:00 in Istanbul.
        let rhythm: ScheduleRhythm = .once(Date(timeIntervalSince1970: 1_795_759_200))
        XCTAssertEqual(rhythm.at(in: istanbul), "2026-11-27T09:00:00+03:00")
        XCTAssertEqual(rhythm.at(in: .gmt), "2026-11-27T06:00:00Z")
    }

    func testTheBodyLeavesOutWhatIsLeftOnDefault() throws {
        let plain: [String: Any] = try object(
            ScheduleCreate(name: "Brief", goal: "Read my mail", rhythm: .daily(hour: 8, minute: 0), zone: .gmt, mode: "task", model: nil, effort: nil, approvals: nil, goalMode: nil, agentId: nil))
        XCTAssertEqual(Set(plain.keys), ["name", "goal", "cron", "mode"], "a null would be read as a choice; left out, the daemon's default stands")
        XCTAssertEqual(plain["cron"] as? String, "0 8 * * *")
        let full: [String: Any] = try object(
            ScheduleCreate(
                name: "Scout", goal: "Look for news", rhythm: .once(Date(timeIntervalSince1970: 1_795_759_200)), zone: .gmt, mode: "research", model: "ollama:qwen3:cloud",
                effort: "high", approvals: "yolo", goalMode: true, agentId: "ag_1"))
        XCTAssertEqual(Set(full.keys), ["name", "goal", "at", "mode", "model", "effort", "approvals", "goal_mode", "agent_id"])
        XCTAssertEqual(full["agent_id"] as? String, "ag_1")
        XCTAssertEqual(full["goal_mode"] as? Bool, true)
    }

    func testAFiringDecodesWithItsRunAndChat() throws {
        let json: String = """
            [{"run":{"id":"r1","thread_id":"t1","goal":"g","mode":"task","model":"m","status":"done","created_at":1,"finished_at":2,
              "result":"All read.\\nThree replies drafted.","parent_run_id":null,"source":"schedule","effort":null,"steps":4},
              "chat_title":"Brief"}]
            """
        let firings: [ScheduleFiring] = try JSONDecoder().decode([ScheduleFiring].self, from: Data(json.utf8))
        XCTAssertEqual(firings.first?.id, "r1")
        XCTAssertEqual(firings.first?.run.threadId, "t1")
        XCTAssertEqual(firings.first?.chatTitle, "Brief")
    }
}
