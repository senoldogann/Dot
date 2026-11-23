import XCTest
@testable import Dot

/// The requests of slice 2, as the daemon's routes take them (and as the web composer writes them).
final class Slice2RequestTests: XCTestCase {
    private func api() -> APIClient {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        return APIClient(host: host, key: "phone.a.b", session: URLSession(configuration: configuration))
    }

    override func setUp() {
        BodyStub.reply = "{}"
        BodyStub.seen = []
        BodyStub.respond = nil
        BodyStub.status = 200
    }

    private func lastBody() throws -> [String: Any] {
        let data: Data = try XCTUnwrap(BodyStub.seen.last?.body)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private static let run: String = #"{"id":"run_9","thread_id":"thr_9","goal":"hi","mode":"task","model":"ollama:m","status":"running","created_at":5,"finished_at":null,"result":"","steps":0,"parent_run_id":null,"source":"ui","agent_id":null,"follows_reaction":null}"#

    // MARK: New chat

    func testANewChatIsARunWithTheModelPickedAndNoChatToGoOnIn() async throws {
        BodyStub.reply = Self.run
        let key: String = "0123456789abcdef-key"
        let started: RunRecord = try await api().start(
            chat: NewChat(goal: "plan my week", key: key, choices: MessageChoices(mode: "auto", model: "openai:gpt-5.5", effort: "high")))
        XCTAssertEqual(started.threadId, "thr_9")
        XCTAssertEqual(BodyStub.seen.last?.method, "POST")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/runs")
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(body["goal"] as? String, "plan my week")
        XCTAssertEqual(body["model"] as? String, "openai:gpt-5.5", "the model picked rides with the message (item 1350)")
        XCTAssertEqual(body["effort"] as? String, "high")
        XCTAssertEqual(body["mode"] as? String, "auto")
        XCTAssertEqual(body["key"] as? String, key)
        XCTAssertEqual(body["standing"] as? Bool, false)
        XCTAssertTrue(body["thread_id"] is NSNull, "a new chat names no chat: thread_id is null, as the web writes it")
        XCTAssertNil(body["agent_id"])
    }

    func testAMessageWithNoPickLeavesTheModelToTheDaemon() async throws {
        BodyStub.reply = Self.run
        _ = try await api().start(chat: NewChat(goal: "hi", key: "0123456789abcdef-key"))
        let body: [String: Any] = try lastBody()
        XCTAssertNil(body["model"])
        XCTAssertNil(body["effort"])
        XCTAssertEqual(body["mode"] as? String, "auto")
    }

    func testAnAgentsFirstMessageNamesTheAgent() async throws {
        BodyStub.reply = Self.run
        _ = try await api().start(
            chat: NewChat(goal: "check", key: "0123456789abcdef-key", choices: MessageChoices(mode: "auto", agentId: "agt_1")))
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(body["agent_id"] as? String, "agt_1")
        XCTAssertTrue(body["thread_id"] is NSNull)
    }

    func testAMessageInAChatCarriesTheChatsOwnChoicesAndItsAgent() async throws {
        BodyStub.reply = #"{"held":false,"messages":[]}"#
        let choices: ChatChoices = ChatChoices(mode: "chat", model: "ollama:big", effort: "low", speed: "standard", approvals: "ask", goalMode: false)
        _ = try await api().send(
            message: Message(text: "go on", key: "0123456789abcdef-key", attachments: [], choices: MessageChoices(choices, agentId: "agt_2")),
            thread: "thr_1")
        let body: [String: Any] = try lastBody()
        XCTAssertEqual(body["model"] as? String, "ollama:big")
        XCTAssertEqual(body["effort"] as? String, "low")
        XCTAssertEqual(body["mode"] as? String, "chat")
        XCTAssertEqual(body["speed"] as? String, "standard")
        XCTAssertEqual(body["approvals"] as? String, "ask")
        XCTAssertEqual(body["goal_mode"] as? Bool, false)
        XCTAssertEqual(body["agent_id"] as? String, "agt_2")
    }

    func testAChatsChoicesAreKeptWithEveryKeyNullIncluded() async throws {
        let choices: ChatChoices = ChatChoices(mode: "auto", model: "ollama:m", effort: nil, speed: "standard", approvals: nil, goalMode: nil)
        try await api().keep(choices: choices, thread: "thr_1")
        XCTAssertEqual(BodyStub.seen.last?.method, "PATCH")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/threads/thr_1")
        let sent: [String: Any] = try XCTUnwrap(try lastBody()["choices"] as? [String: Any])
        // The daemon requires every key: a model left to the default is null, not missing.
        XCTAssertEqual(Set(sent.keys), ["mode", "model", "effort", "speed", "approvals", "goal_mode"])
        XCTAssertEqual(sent["model"] as? String, "ollama:m")
        XCTAssertTrue(sent["effort"] is NSNull)
        XCTAssertTrue(sent["approvals"] is NSNull)
        XCTAssertTrue(sent["goal_mode"] is NSNull)
    }

    // MARK: Older runs

    func testOlderRunsAreAskedForBeforeTheOldestHeld() async throws {
        BodyStub.reply = "[]"
        // A time with every digit a double holds: the page must start strictly before the run it was read from.
        let exact: Double = 1700000123.123456789
        _ = try await api().runs(thread: "thr_1", limit: 20, before: exact)
        let request: BodyStub.Seen = try XCTUnwrap(BodyStub.seen.last)
        XCTAssertEqual(request.path, "/api/runs")
        let items: [URLQueryItem] = URLComponents(string: "https://x/?\(request.query ?? "")")?.queryItems ?? []
        XCTAssertEqual(items.first { $0.name == "thread_id" }?.value, "thr_1")
        XCTAssertEqual(items.first { $0.name == "limit" }?.value, "20")
        XCTAssertEqual(Double(items.first { $0.name == "before" }?.value ?? ""), exact, "the time round-trips exactly")
        _ = try await api().runs(thread: "thr_1")
        XCTAssertFalse((BodyStub.seen.last?.query ?? "").contains("before"), "the newest page names no time")
    }

    // MARK: Actions

    func testEditFromHereRewindsAndKeepsTheFiles() async throws {
        BodyStub.reply = #"{"thread_id":"thr_1","back_to":"run_0","text":"the words","files":[],"reverted":[],"left":[]}"#
        let rewound: RewindResult = try await api().rewind(run: "run_2")
        XCTAssertEqual(BodyStub.seen.last?.method, "POST")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/runs/run_2/rewind")
        XCTAssertEqual(try lastBody()["files"] as? String, "keep")
        XCTAssertEqual(rewound.text, "the words")
        XCTAssertEqual(rewound.backTo, "run_0")
        BodyStub.reply = #"{"thread_id":"thr_1","back_to":null,"text":"first","files":[],"reverted":[],"left":[]}"#
        let emptied: RewindResult = try await api().rewind(run: "run_1")
        XCTAssertNil(emptied.backTo)
    }

    func testAReactionIsPutOnAMessageOfARunAndTakenBack() async throws {
        BodyStub.reply = #"{"reactions":[{"run_id":"run_1","message":"step-2","reaction":"good","thread_id":"thr_1","created_at":1,"follow_up_run_id":null,"note_id":null}],"effect":"noted","said":"","run_id":null}"#
        let outcome: ReactionOutcome = try await api().react(run: "run_1", message: "step-2", reaction: "good")
        XCTAssertEqual(BodyStub.seen.last?.method, "POST")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/runs/run_1/messages/step-2/reactions")
        XCTAssertEqual(try lastBody()["reaction"] as? String, "good")
        XCTAssertEqual(outcome.reactions.map(\.reaction), ["good"])
        _ = try await api().unreact(run: "run_1", message: "answer", reaction: "good")
        XCTAssertEqual(BodyStub.seen.last?.method, "DELETE")
        XCTAssertEqual(BodyStub.seen.last?.path, "/api/runs/run_1/messages/answer/reactions/good")
    }

    // MARK: A model that cannot answer

    func testARefusedModelIsReadFromTheDaemonsAnswerWithItsFallback() async {
        BodyStub.status = 422
        BodyStub.reply = #"{"message":"openai:gpt-5.5 cannot answer this message: no openai key is set. Send it on ollama:x, the default model, or fix the model in Settings › Models.","context":{"model":"openai:gpt-5.5","problem":"no openai key is set","fallback":"ollama:x"}}"#
        do {
            _ = try await api().start(chat: NewChat(goal: "hi", key: "0123456789abcdef-key"))
            XCTFail("a 422 is an error")
        } catch {
            let refusal: ModelRefusal? = ModelRefusal.from(error)
            XCTAssertEqual(refusal?.model, "openai:gpt-5.5")
            XCTAssertEqual(refusal?.fallback, "ollama:x")
            XCTAssertTrue(refusal?.message.contains("no openai key") ?? false)
        }
    }

    func testA422ThatNamesNoModelIsNotARefusalOfAModel() {
        XCTAssertNil(ModelRefusal.parse(#"{"message":"goal too long","context":{}}"#))
        XCTAssertNil(ModelRefusal.parse("not json"))
        XCTAssertNil(ModelRefusal.from(DaemonRequestError.status(path: "/api/runs", code: 500, body: #"{"context":{"model":"m","problem":"p"}}"#)))
        let none: ModelRefusal? = ModelRefusal.parse(#"{"message":"m","context":{"model":"x:y","problem":"p","fallback":null}}"#)
        XCTAssertNil(none?.fallback, "the default cannot answer either: no button")
    }
}
