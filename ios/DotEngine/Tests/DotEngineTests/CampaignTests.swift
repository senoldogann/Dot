import XCTest

@testable import DotEngine

/// Settings › Agents › Campaigns (item 1895): the list and a campaign decode from what a throwaway daemon's routes gave
/// (`e2e_daemon.py --seed`, 2026-11-25), and a working one with its sessions and a question from the daemon's shape.
final class CampaignTests: XCTestCase {
    private func decodes<T: Decodable>(_ type: T.Type, _ json: String) -> String? {
        do {
            _ = try JSONDecoder().decode(T.self, from: Data(json.utf8))
            return nil
        } catch {
            return String(describing: error)
        }
    }

    func testTheDaemonsAnswersDecode() {
        let answers: [(String, String?)] = [
        ("campaigns", decodes([CampaignSummary].self, #"[{"record":{"id":"cmp_mwvnsjt7_656c85","title":"E2E campaign","goal":"Keep the test suite green.","targets":"","verification":"","status":"active","thread_id":"thr_mwvnsjt5_0a282b","model":"ollama:scripted","effort":"medium","speed":"standard","once":null,"approvals":"supervised","files":[],"cron":null,"next_session_at":null,"created_at":1795583059.5318348,"updated_at":1795583059.5318348,"evidence":"","browser":null},"sessions":0,"working":null,"next_local":null,"progress":{"id":"cme_mwvnsjtb_afa248","campaign_id":"cmp_mwvnsjt7_656c85","run_id":null,"ts":1795583059.5359821,"kind":"progress","text":"Two failing tests fixed.","numbers":{"tests_fixed":2.0}}}]"#)),
        ("campaign", decodes(CampaignDetail.self, #"{"record":{"id":"cmp_mwvnsjt7_656c85","title":"E2E campaign","goal":"Keep the test suite green.","targets":"","verification":"","status":"active","thread_id":"thr_mwvnsjt5_0a282b","model":"ollama:scripted","effort":"medium","speed":"standard","once":null,"approvals":"supervised","files":[],"cron":null,"next_session_at":null,"created_at":1795583059.5318348,"updated_at":1795583059.5318348,"evidence":"","browser":null},"sessions":[],"working":null,"next_local":null,"entries":[{"id":"cme_mwvnsjtb_afa248","campaign_id":"cmp_mwvnsjt7_656c85","run_id":null,"ts":1795583059.5359821,"kind":"progress","text":"Two failing tests fixed.","numbers":{"tests_fixed":2.0}}],"questions":[],"tokens":0}"#)),

        ]
        for (route, failure) in answers { XCTAssertNil(failure, route) }
    }

    func testAWorkingCampaignKeepsItsSessionsInOrderAndItsQuestions() throws {
        let json: String =
            #"{"record":{"id":"cmp_a","title":"T","goal":"G","targets":"t","verification":"v","status":"active","thread_id":"thr","model":"claude_code:sonnet","effort":"medium","speed":"standard","once":null,"approvals":"yolo","files":[],"cron":null,"next_session_at":1795600000.0,"created_at":1795500000.0,"updated_at":1795500000.0,"evidence":"","browser":"own"},"sessions":[{"id":"run_1","status":"done","created_at":1795500000.0,"result":"Fixed.","input_tokens":10,"output_tokens":5},{"id":"run_2","status":"running","created_at":1795503600.0,"result":null,"input_tokens":0,"output_tokens":0}],"working":"run_2","next_local":"tomorrow 08:00","entries":[],"questions":[{"id":"apr_1","run_id":"run_2","created_at":1795503700.0,"decided_at":null,"status":"pending","tool":"ask_user","summary":"May I skip it?","severity":"normal","args":{},"decided_by":null,"note":"","digest":null}],"tokens":15}"#
        let detail: CampaignDetail = try JSONDecoder().decode(CampaignDetail.self, from: Data(json.utf8))
        XCTAssertEqual(detail.sessions.map(\.id), ["run_1", "run_2"], "oldest first, as the daemon sends them")
        XCTAssertNil(detail.sessions[1].result)
        XCTAssertEqual(detail.working, "run_2")
        XCTAssertEqual(detail.questions.first?.status, "pending")
        XCTAssertEqual(CampaignAction.stop.rawValue, "stop", "the daemon's stop, which Resume takes back")
        XCTAssertEqual(detail.record.actions, [.stop, .achieved])
    }

    func testTheActionsAreWhatTheDaemonTakesInEachState() throws {
        func record(_ status: String) throws -> CampaignRecord {
            let json: String = #"{"id":"c","title":"T","goal":"G","targets":"","verification":"","status":"\#(status)","model":"m","approvals":"yolo","created_at":0,"evidence":""}"#
            return try JSONDecoder().decode(CampaignRecord.self, from: Data(json.utf8))
        }
        XCTAssertEqual(try record("active").actions, [.stop, .achieved])
        XCTAssertEqual(try record("stopped").actions, [.resume, .achieved])
        XCTAssertEqual(try record("achieved").actions, [.resume])
    }
}
