#if DEBUG
    import Foundation

    /// Campaigns on the fixture daemon (item 1895): what a throwaway daemon (`e2e_daemon.py --seed`) answered on
    /// 2026-11-25, with a working campaign and an achieved one. Stop, Resume and Mark achieved answer the campaign
    /// with the status the action leaves (the fixture keeps no state between requests).
    enum CampaignFixture {
        static func answer(method: String, path: String) -> (Int, String)? {
            if method == "GET", let body = reads[path] { return (200, body) }
            guard method == "POST", path.hasPrefix("/api/campaigns/cmp_fixture/"), let read = reads["/api/campaigns/cmp_fixture"] else { return nil }
            let status: String
            switch path.split(separator: "/").last {
            case "stop": status = "stopped"
            case "resume": status = "active"
            case "achieved": status = "achieved"
            default: return nil
            }
            return (200, read.replacingOccurrences(of: #""status":"active""#, with: #""status":"\#(status)""#))
        }

        private static let reads: [String: String] = [
        "/api/campaigns": #"[{"record":{"id":"cmp_fixture","title":"Keep the test suite green","goal":"Keep the test suite green.","targets":"Every test of tests/unit passes on main.","verification":"The gate on main is green.","status":"active","thread_id":"thr_mwvnsjt5_0a282b","model":"ollama:scripted","effort":"medium","speed":"standard","once":null,"approvals":"supervised","files":[],"cron":null,"next_session_at":null,"created_at":1795583059.5318348,"updated_at":1795583059.5318348,"evidence":"","browser":null},"sessions":3,"working":"run_w","next_local":null,"progress":{"id":"cme_mwvnsjtb_afa248","campaign_id":"cmp_mwvnsjt7_656c85","run_id":null,"ts":1795583059.5359821,"kind":"progress","text":"Two failing tests fixed.","numbers":{"tests_fixed":2.0}}},{"record":{"id":"cmp_site","title":"Ship the new landing page","goal":"Ship the landing page with the new pricing.","targets":"","verification":"","status":"achieved","thread_id":"thr_mwvnsjt5_0a282b","model":"ollama:scripted","effort":"medium","speed":"standard","once":null,"approvals":"supervised","files":[],"cron":null,"next_session_at":null,"created_at":1795583059.5318348,"updated_at":1795583059.5318348,"evidence":"The page is live and the checks pass.","browser":null},"sessions":4,"working":null,"next_local":null,"progress":{"id":"cme_mwvnsjtb_afa248","campaign_id":"cmp_mwvnsjt7_656c85","run_id":null,"ts":1795583059.5359821,"kind":"progress","text":"Pricing table and checkout done.","numbers":{}}}]"#,
        "/api/campaigns/cmp_fixture": #"{"record":{"id":"cmp_fixture","title":"Keep the test suite green","goal":"Keep the test suite green.","targets":"Every test of tests/unit passes on main.","verification":"The gate on main is green.","status":"active","thread_id":"thr_mwvnsjt5_0a282b","model":"ollama:scripted","effort":"medium","speed":"standard","once":null,"approvals":"supervised","files":[],"cron":null,"next_session_at":null,"created_at":1795583059.5318348,"updated_at":1795583059.5318348,"evidence":"","browser":null},"sessions":[{"id":"run_0","thread_id":"thr","goal":"g","mode":"task","model":"m","status":"done","created_at":1795500000,"finished_at":null,"steps":10,"result":"Fixed two failing tests in test_store.py.","input_tokens":900000,"output_tokens":40000},{"id":"run_1","thread_id":"thr","goal":"g","mode":"task","model":"m","status":"done","created_at":1795503600,"finished_at":null,"steps":10,"result":"Fixed the flaky timeout.","input_tokens":900000,"output_tokens":40000},{"id":"run_2","thread_id":"thr","goal":"g","mode":"task","model":"m","status":"running","created_at":1795507200,"finished_at":null,"steps":10,"result":"","input_tokens":900000,"output_tokens":40000}],"working":"run_w","next_local":null,"entries":[{"id":"cme_mwvnsjtb_afa248","campaign_id":"cmp_mwvnsjt7_656c85","run_id":null,"ts":1795583059.5359821,"kind":"progress","text":"Two failing tests fixed.","numbers":{"tests_fixed":2.0}},{"id":"cme_2","campaign_id":"cmp_fixture","run_id":"run_1","ts":1795503600,"kind":"setback","text":"test_live_mail needs a network; left for the owner.","numbers":{}}],"questions":[{"id":"apr_1","run_id":"run_2","created_at":1795507000,"decided_at":null,"status":"pending","tool":"ask_user","summary":"May I skip the live mail test?","severity":"normal","args":{},"decided_by":null,"note":""}],"tokens":4200000}"#,
        ]
    }
#endif
