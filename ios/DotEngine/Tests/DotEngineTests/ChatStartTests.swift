import Foundation
import XCTest

@testable import DotEngine

/// What a new chat starts with (PLAN §24: model, level, speed, approvals, goal mode and "plan first" picked before the
/// first message).
final class ChatStartTests: XCTestCase {
    private var folder: URL!
    private var daemon: FakeDaemon!

    override func setUp() async throws {
        folder = scratchFolder()
        daemon = FakeDaemon(version: "1.0")
    }

    override func tearDown() async throws { try? FileManager.default.removeItem(at: folder) }

    private func sentRun(_ start: ChatStart?) async throws -> [String: JSONValue] {
        let outbox: Outbox = Outbox(
            directory: folder, api: daemon.api(), link: LinkWatcher(initial: .up), clock: ManualClock(at: 1_800_000_000),
            policy: RetryPolicy(attempts: 1, base: 1, cap: 1, budget: 1), jitter: { 0 }, maySend: { _ in true })
        let outcomes: AsyncStream<OutboxOutcome> = await outbox.outcomes()
        try await outbox.enqueue(.newChat(text: "Plan the trip", images: [], uploaded: [], start: start, agent: nil), id: newMessageKey(), images: [])
        for await _ in outcomes { break }
        let body: Data = try XCTUnwrap(daemon.bodies("POST", "/api/runs").last)
        return try JSONDecoder().decode([String: JSONValue].self, from: body)
    }

    func testANewChatCarriesWhatWasPickedBeforeItsFirstMessage() async throws {
        let picked: ChatChoices = ChatChoices(mode: "auto", model: "deepseek:deepseek-v4", effort: "high", speed: "fast", approvals: "yolo", goalMode: true)
        let sent: [String: JSONValue] = try await sentRun(ChatStart(choices: picked, planFirst: true))
        XCTAssertEqual(sent["goal"]?.string, "Plan the trip")
        XCTAssertEqual(sent["model"]?.string, "deepseek:deepseek-v4")
        XCTAssertEqual(sent["effort"]?.string, "high")
        XCTAssertEqual(sent["speed"]?.string, "fast")
        XCTAssertEqual(sent["approvals"]?.string, "yolo")
        XCTAssertEqual(sent["plan_first"]?.bool, true)
        XCTAssertEqual(sent["mode"]?.string, "auto")
        XCTAssertEqual(sent["goal_mode"]?.bool, true)
    }

    func testANewChatWithNothingPickedLeavesTheDaemonsChoices() async throws {
        let sent: [String: JSONValue] = try await sentRun(nil)
        XCTAssertNil(sent["model"], "left out: the daemon's main model")
        XCTAssertNil(sent["effort"])
        XCTAssertNil(sent["approvals"], "left out: [policy] approvals")
        XCTAssertNil(sent["goal_mode"], "left out: [agent] goal_mode")
        XCTAssertEqual(sent["speed"]?.string, "standard")
        XCTAssertEqual(sent["plan_first"]?.bool, false)
    }

    func testANewChatKeptByAnEarlierBuildStillReads() throws {
        let kept: Data = Data(
            #"{"id":"k1","action":{"newChat":{"text":"hi","images":[],"uploaded":[]}},"kind":"run","createdAt":1,"attempts":0,"phase":"waiting"}"#.utf8)
        let item: OutboxItem = try JSONDecoder().decode(OutboxItem.self, from: kept)
        XCTAssertEqual(item.action, .newChat(text: "hi", images: [], uploaded: [], start: nil, agent: nil))
    }

    func testTheChoicesChangeOneThingAtATime() {
        let base: ChatChoices = ChatChoices(mode: "task", model: "a:b", effort: "low", speed: "standard", approvals: "ask", goalMode: true)
        XCTAssertEqual(base.with(speed: "fast"), ChatChoices(mode: "task", model: "a:b", effort: "low", speed: "fast", approvals: "ask", goalMode: true))
        XCTAssertEqual(base.with(approvals: nil), ChatChoices(mode: "task", model: "a:b", effort: "low", speed: "standard", approvals: nil, goalMode: true))
    }

    func testTheModelsListSaysFastModeAndToolsAndReadsAnOlderDaemon() throws {
        let now: Data = Data(#"{"ref":"claude_code:opus","label":"Opus 5.5","efforts":["low","high"],"default_effort":"high","context_length":200000,"fast_mode":true,"tools":true,"free":false}"#.utf8)
        let option: ModelOption = try JSONDecoder().decode(ModelOption.self, from: now)
        XCTAssertEqual(option.fastMode, true)
        XCTAssertEqual(option.tools, true)
        XCTAssertEqual(option.free, false)
        let older: ModelOption = try JSONDecoder().decode(ModelOption.self, from: Data(#"{"ref":"x:y","label":"y","efforts":[],"default_effort":null,"context_length":null}"#.utf8))
        XCTAssertNil(older.fastMode)
        XCTAssertNil(older.tools, "a daemon that does not say keeps the model (the picker leaves out only tools: false)")
        XCTAssertNil(older.free)
    }
}
