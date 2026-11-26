import XCTest

@testable import DotEngine

/// The message actions and the inspector's depth (package W4): the daemon's answers decode as its models write them,
/// and the rules offer what the web offers (`views/run.js`).
final class MessageActionsTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    /// A run as the daemon's `RunRecord` writes it, with the fields a test changes.
    private func run(id: String, status: String, source: String, parent: String?, rewindable: Bool, follows: String?) throws -> RunRecord {
        try run(id: id, status: status, source: source, parent: parent, rewindable: rewindable, follows: follows, ended: nil)
    }

    private func run(id: String, status: String, source: String, parent: String?, rewindable: Bool, follows: String?, ended: String?) throws -> RunRecord {
        let parentJSON: String = parent.map { "\"\($0)\"" } ?? "null"
        let followsJSON: String = follows.map { "\"\($0)\"" } ?? "null"
        return try decode(
            RunRecord.self,
            """
            {"id":"\(id)","thread_id":"thr_1","goal":"Book the car service","mode":"task","model":"claude_code:opus","status":"\(status)",
             "created_at":10,"finished_at":20,"result":"","steps":3,"input_tokens":100,"output_tokens":10,"parent_run_id":\(parentJSON),
             "source":"\(source)","effort":"high","speed":"standard","once":null,"approvals":"yolo","goal_mode":false,"campaign_id":null,
             "agent_id":"agt_1","rewindable":\(rewindable),"harness_version":null,"follows_reaction":\(followsJSON),"end_reason":\(ended.map { "\"\($0)\"" } ?? "null"),"hops":0}
            """)
    }

    private func detail(_ run: RunRecord, resumable: Bool, cutOff: String?) throws -> RunDetail {
        let runJSON: String = String(decoding: try JSONEncoder().encode(run), as: UTF8.self)
        let cut: String = cutOff.map { "\"\($0)\"" } ?? "null"
        return try decode(
            RunDetail.self,
            """
            {"run":\(runJSON),"title":"Chat","children":[],"active":false,"elsewhere":false,"resumable":\(resumable),"cut_off":\(cut),
             "lessons":null,
             "questions":[],"agent":null,"campaign":null,"held":false,"holding":false,"keep_going":false,"choices":null,"team":null}
            """)
    }

    func testARunDecodesWhetherItsChatCanGoBackAndWhoseItIs() throws {
        let decoded: RunRecord = try run(id: "run_a", status: "done", source: "ui", parent: nil, rewindable: true, follows: nil)
        XCTAssertEqual(decoded.rewindable, true)
        XCTAssertEqual(decoded.agentId, "agt_1")
        let older: RunRecord = try decode(
            RunRecord.self,
            """
            {"id":"r","thread_id":"t","goal":"g","mode":"task","model":"m","status":"done","created_at":1,"finished_at":null,"result":"",
             "parent_run_id":null,"source":"ui","effort":null}
            """)
        XCTAssertNil(older.rewindable, "a daemon that does not say leaves it unknown")
    }

    func testTheNewestRunSaysWhetherItResumesAndHowItWasCut() throws {
        let failed: RunRecord = try run(id: "run_a", status: "failed", source: "ui", parent: nil, rewindable: true, follows: nil)
        let newest: RunDetail = try detail(failed, resumable: true, cutOff: "daemon_restarted")
        XCTAssertEqual(newest.resumable, true)
        XCTAssertEqual(newest.cutOff, "daemon_restarted")
    }

    func testAMessageOfTheOwnersOffersEditFromHereOnlyWhenTheChatCanGoBackAndRests() throws {
        let own: RunRecord = try run(id: "run_a", status: "done", source: "ui", parent: nil, rewindable: true, follows: nil)
        XCTAssertEqual(ownerMessageOffer(run: own, chatWorking: false), OwnerMessageOffer(canRewind: true, refusal: nil))
        XCTAssertEqual(ownerMessageOffer(run: own, chatWorking: true).refusal, .working, "the daemon refuses a rewind under a working run")
        let old: RunRecord = try run(id: "run_b", status: "done", source: "ui", parent: nil, rewindable: false, follows: nil)
        XCTAssertEqual(ownerMessageOffer(run: old, chatWorking: false).refusal, .notRewindable)
        let helper: RunRecord = try run(id: "run_c", status: "done", source: "ui", parent: "run_a", rewindable: true, follows: nil)
        XCTAssertFalse(ownerMessageOffer(run: helper, chatWorking: false).canRewind, "a helper's goal is not the owner's message")
        let resumed: RunRecord = try run(id: "run_d", status: "done", source: "resume", parent: nil, rewindable: true, follows: nil)
        XCTAssertFalse(ownerMessageOffer(run: resumed, chatWorking: false).canRewind, "a resumed run carries no words of the owner's")
        let reaction: RunRecord = try run(id: "run_e", status: "done", source: "ui", parent: nil, rewindable: true, follows: "thumbs_down")
        XCTAssertFalse(ownerMessageOffer(run: reaction, chatWorking: false).canRewind)
        XCTAssertEqual(runOfOwnerMessage("u:run_a"), "run_a")
        XCTAssertNil(runOfOwnerMessage("p:key"), "a message on its way has no run to go back to")
        XCTAssertEqual(runOfEndCard("e:run_a"), "run_a")
    }

    func testTheEndCardContinuesTheNewestResumableRunAndRetriesAFailedOne() throws {
        let failed: RunRecord = try run(id: "run_a", status: "failed", source: "ui", parent: nil, rewindable: true, follows: nil)
        let newest: RunDetail = try detail(failed, resumable: true, cutOff: nil)
        XCTAssertEqual(endOffer(run: failed, newest: newest, chatWorking: false), EndOffer(resume: true, retry: true, cutOff: nil))
        XCTAssertEqual(endOffer(run: failed, newest: newest, chatWorking: true), EndOffer(resume: false, retry: false, cutOff: nil), "nothing while a run works")
        let later: RunRecord = try run(id: "run_b", status: "done", source: "ui", parent: nil, rewindable: true, follows: nil)
        XCTAssertFalse(endOffer(run: failed, newest: try detail(later, resumable: false, cutOff: nil), chatWorking: false).resume, "only the newest run resumes")
        let stopped: RunRecord = try run(id: "run_c", status: "cancelled", source: "ui", parent: nil, rewindable: true, follows: nil)
        XCTAssertEqual(
            endOffer(run: stopped, newest: try detail(stopped, resumable: false, cutOff: nil), chatWorking: false), EndOffer(resume: false, retry: true, cutOff: nil),
            "a run the owner stopped is not resumed; its message can be retried, as the web's Retry under any answer")
        let declined: RunRecord = try run(id: "run_e", status: "failed", source: "ui", parent: nil, rewindable: true, follows: nil, ended: "model_refused")
        XCTAssertEqual(
            endOffer(run: declined, newest: try detail(declined, resumable: true, cutOff: nil), chatWorking: false), EndOffer(resume: false, retry: true, cutOff: nil),
            "a run whose model declined the task is never continued on that model (item 1889), though the daemon calls it resumable")
        let cut: RunRecord = try run(id: "run_d", status: "failed", source: "schedule", parent: nil, rewindable: false, follows: nil)
        XCTAssertEqual(endOffer(run: cut, newest: try detail(cut, resumable: true, cutOff: "daemon_restarted"), chatWorking: false), EndOffer(resume: true, retry: false, cutOff: "daemon_restarted"))
    }

    func testRetrySendsTheRewoundWordsWithTheChatsChoicesInTheSameChat() throws {
        let past: RunRecord = try run(id: "run_a", status: "failed", source: "ui", parent: nil, rewindable: true, follows: nil)
        let cut: RewindView = try decode(
            RewindView.self,
            """
            {"thread_id":"thr_1","back_to":"run_0","text":"Book the car service","files":[{"id":"att_1","name":"a.jpg","kind":"image","size":12}],
             "reverted":[],"left":[{"path":"/tmp/a","state":"conflict","action":"restore","reason":"changed since","tool":"write_file","run_id":"run_a","noted_at":1,"changes":1}]}
            """)
        let choices: ChatChoices = ChatChoices(mode: "task", model: "claude_code:sonnet", effort: "low", speed: "fast", approvals: "ask", goalMode: true)
        let body: RetryRun = retryRun(cut: cut, choices: choices, past: past, efforts: ["low", "high"], key: "k-0123456789abcdef")
        XCTAssertEqual(body.threadId, "thr_1")
        XCTAssertEqual(body.model, "claude_code:sonnet")
        XCTAssertEqual(body.speed, "fast")
        XCTAssertEqual(body.effort, "low", "a level the model offers goes")
        let levelless: RetryRun = retryRun(cut: cut, choices: choices, past: past, efforts: [], key: "k-0123456789abcdef")
        XCTAssertNil(levelless.effort, "a model with no levels gets none: the daemon refuses one it does not offer")
        XCTAssertEqual(body.agentId, "agt_1")
        XCTAssertEqual(body.attachments, ["att_1"])
        let emptied: RewindView = try decode(
            RewindView.self, #"{"thread_id":"thr_1","back_to":null,"text":"Hi","files":[],"reverted":[],"left":[]}"#)
        let fresh: RetryRun = retryRun(cut: emptied, choices: nil, past: past, efforts: [], key: "k-0123456789abcdef")
        XCTAssertEqual(fresh.threadId, "thr_1", "no run left in the chat: the message still goes on in it, where the phone is")
        XCTAssertEqual(fresh.mode, "task")
        let encoded: [String: JSONValue] = try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(body))
        XCTAssertEqual(encoded["goal_mode"], .bool(true), "the daemon's own key names")
        XCTAssertEqual(encoded["thread_id"], .string("thr_1"))
    }

    func testAModelReferenceSplitsIntoItsProviderAndItsModel() {
        XCTAssertEqual(modelParts("opencode_go:deepseek-v4.1-flash"), ModelParts(provider: "opencode_go", model: "deepseek-v4.1-flash"))
        XCTAssertEqual(modelParts("ollama:qwen3:8b"), ModelParts(provider: "ollama", model: "qwen3:8b"), "the first colon parts them")
        XCTAssertEqual(modelParts("opus"), ModelParts(provider: nil, model: "opus"))
        XCTAssertEqual(providerTitle("opencode_go"), "OpenCode Go")
        XCTAssertEqual(providerTitle("claude_code"), "Claude Code")
        XCTAssertEqual(providerTitle("some_new_one"), "Some New One", "an unknown provider still reads as words")
    }

    func testTheInspectorsAnswersDecode() throws {
        let costs: RunCosts = try decode(
            RunCosts.self,
            """
            {"run_id":"run_a","status":"done","minutes":2.5,"tool_seconds":30.5,"waited_s":4,"tools":[],"harness_version":null,"plan_window":null,
             "stats":{"model":"m","runs":1,"endings":[],"steps":7,"input_tokens":466304,"output_tokens":8142,"tool_calls":5,"tool_errors":1,
                      "invalid_arguments":0,"rejected_arguments":0,"stalls":0,"compaction":{},"web":{},"searches":[],"delegation":{},"acts":{},"bands":[],
                      "time":{"timed_runs":1,"model_seconds":90,"step_seconds_median":3,"step_seconds_p90":9,"compaction_seconds":0,"side_call_seconds":0,
                              "cached_tokens":400000,"cached_share":0.86}},
             "budget":{"estimate":{"runs":12,"median":300000,"p90":900000},"spent":474446,"cap":0,"source":"ui","day":1000000,"day_cap":0,
                       "source_day":1000000,"source_day_cap":0}}
            """)
        XCTAssertEqual(costs.stats.inputTokens, 466_304)
        XCTAssertEqual(costs.stats.time.cachedShare, 0.86)
        XCTAssertEqual(costs.budget?.estimate?.median, 300_000)
        let recap: RecapView = try decode(
            RecapView.self,
            #"{"enabled":true,"recap":{"text":"Booked.","model":"m","steps":7,"status":"done","written_at":30},"steps":7,"status":"done","due":false}"#)
        XCTAssertEqual(recap.recap?.text, "Booked.")
        let draft: DraftView = try decode(
            DraftView.self,
            """
            {"id":"drf_1","run_id":"run_a","channel":"mail","to":["a@b.c"],"cc":[],"subject":"Hi","body":"Hello","status":"failed",
             "created_at":1,"decided_at":null,"error":"Mail is not set up"}
            """)
        XCTAssertTrue(draft.isOpen, "a failed send still takes an answer")
        let terminal: TerminalView = try decode(
            TerminalView.self, #"{"id":"ses_1","command":"npm run dev","background":true,"started_at":5,"ended":false,"exit_status":null}"#)
        XCTAssertTrue(terminal.background)
    }
}
