import XCTest

@testable import DotEngine

/// A bot's work (items 87, 907, 908 on the phone): the three routes decode from what a throwaway daemon gave
/// (`e2e_daemon.py --seed-work`, 2026-11-27, with the phone's key). The scoping to a bot is the phone's own filter (the web's
/// Agents page lists everything unscoped), so its tests check that filter and not parity with the web.
final class WorkTests: XCTestCase {
    private let workOrders: String = #"[{"order":{"id":"wo_mwyf8ycj_458c48","agent_id":"agt_mwyf8ycg_90012a","title":"Draft the weekly note","brief":"Collect the week's highlights and write the note.","acceptance":"A note under 200 words, ready to send.","objective_id":"obj_mwyf8ych_f52b81","status":"assigned","attempts":0,"run_ids":[],"run_id":"run_mwyf8yci_525567","thread_id":"thr_mwyf8ych_ca9c7d","lead_run_id":"run_mwyf8yci_525567","due_at":1795760906.85164,"tainted":false,"note":"","created_at":1795750106.8516438,"updated_at":1795750106.8516438},"agent_name":"Scribe","objective_title":"Keep the weekly note short"},{"order":{"id":"wo_mwyf8yck_b5e4e9","agent_id":"agt_mwyf8ycg_90012a","title":"Pick the note's topics","brief":"Choose three topics.","acceptance":"Three topics, one line each.","objective_id":null,"status":"accepted","attempts":0,"run_ids":[],"run_id":"run_mwyf8yci_525567","thread_id":"thr_mwyf8ych_ca9c7d","lead_run_id":"run_mwyf8yci_525567","due_at":null,"tainted":false,"note":"Good.","created_at":1795750106.852101,"updated_at":1795750106.853951},"agent_name":"Scribe","objective_title":null}]"#
    private let objectives: String = #"[{"id":"obj_mwyf8ych_f52b81","title":"Keep the weekly note short","why":"The owner reads it on the phone.","measure":"Under 200 words, every Friday.","status":"active","notes":[],"tainted":false,"created_at":1795750106.849257,"updated_at":1795750106.849257,"workflow":{"sources":"","rules":"","evidence":"","exceptions":"","next_step":"Draft this Friday's note","record":"","next_step_tainted":false}}]"#
    private let team: String = #"{"messages":[{"run_id":"run_mwyf8ycm_6679d2","parent_run_id":"run_mwyf8yci_525567","from_id":"dot","from_name":"Dot","to_id":"agt_mwyf8ycg_90012a","to_name":"Scribe","message":"Which topics fit this week?","reply":"Launch, the offsite and the new price list.","status":"done","ts":1795750106.854357,"finished_at":1795750106.855511},{"run_id":"run_mwyf8yco_062be1","parent_run_id":"run_mwyf8yco_b1579c","from_id":"agt_mwyf8ycg_90012a","from_name":"Scribe","to_id":"dot","to_name":"Dot","message":"Is the offsite date final?","reply":null,"status":"queued","ts":1795750106.856853,"finished_at":null}]}"#

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(T.self, from: Data(json.utf8))
    }

    private struct Scoped {
        let orders: [WorkOrderView]
        let objectives: [ObjectiveRecord]
        let handoffs: BotHandoffs
    }

    private func work(botId: String) throws -> Scoped {
        let orders: [WorkOrderView] = try decode([WorkOrderView].self, workOrders)
        return Scoped(
            orders: botOrders(botId: botId, orders: orders),
            objectives: botObjectives(botId: botId, orders: orders, objectives: try decode([ObjectiveRecord].self, objectives)),
            handoffs: botHandoffs(botId: botId, team: try decode(TeamView.self, team)))
    }

    func testTheDaemonsAnswersDecode() throws {
        let orders: [WorkOrderView] = try decode([WorkOrderView].self, workOrders)
        XCTAssertEqual(orders.map(\.order.title), ["Draft the weekly note", "Pick the note's topics"], "the open order first, as the daemon sent them")
        XCTAssertEqual(orders[0].agentName, "Scribe")
        XCTAssertEqual(orders[0].objectiveTitle, "Keep the weekly note short")
        XCTAssertEqual(orders[0].order.state, .assigned)
        XCTAssertEqual(orders[1].order.state, .accepted)
        XCTAssertNil(orders[1].objectiveTitle)
        let objective: ObjectiveRecord = try XCTUnwrap(try decode([ObjectiveRecord].self, objectives).first)
        XCTAssertTrue(objective.isActive)
        XCTAssertEqual(objective.workflow.nextStep, "Draft this Friday's note")
        XCTAssertFalse(objective.hasOutsideWords)
        let messages: [TeamMessage] = try decode(TeamView.self, team).messages
        XCTAssertEqual(messages.map(\.fromName), ["Dot", "Scribe"])
        XCTAssertEqual(messages[0].reply, "Launch, the offsite and the new price list.")
        XCTAssertFalse(messages[0].isAtWork)
        XCTAssertNil(messages[1].reply)
        XCTAssertTrue(messages[1].isAtWork, "queued: the run has not answered")
    }

    func testAnAgentHasItsOwnOrdersTheirObjectivesAndItsMessages() throws {
        let scribe: String = try decode(TeamView.self, team).messages[0].toId
        let own: Scoped = try work(botId: scribe)
        XCTAssertEqual(own.orders.count, 2)
        XCTAssertEqual(own.orders.filter { $0.order.isOpen }.count, 1, "the accepted one is over")
        XCTAssertEqual(own.objectives.map(\.title), ["Keep the weekly note short"], "the objective its order serves")
        XCTAssertEqual(own.handoffs.received.map(\.fromName), ["Dot"])
        XCTAssertEqual(own.handoffs.sent.map(\.toName), ["Dot"])
        XCTAssertEqual(own.handoffs.count, 2)
        let other: Scoped = try work(botId: "agt_nobody")
        XCTAssertEqual(other.orders.count + other.objectives.count + other.handoffs.count, 0, "another agent has none of it")
    }

    func testDotHasEveryOrderEveryObjectiveAndTheMessagesItSentOrWasSent() throws {
        let lead: Scoped = try work(botId: dotAgentId)
        XCTAssertEqual(lead.orders.count, 2)
        XCTAssertEqual(lead.objectives.count, 1)
        XCTAssertEqual(lead.handoffs.sent.map(\.toName), ["Scribe"], "what Dot asked of Scribe")
        XCTAssertEqual(lead.handoffs.received.map(\.fromName), ["Scribe"], "what Scribe asked of Dot")
    }

    func testAMessageBetweenTwoOtherAgentsIsNotDots() {
        let between: TeamMessage = TeamMessage(runId: "r", fromId: "a", fromName: "A", toId: "b", toName: "B", message: "m", reply: nil, status: "done", ts: 1, finishedAt: nil)
        XCTAssertEqual(botHandoffs(botId: dotAgentId, team: TeamView(messages: [between])).count, 0)
        XCTAssertEqual(botHandoffs(botId: "a", team: TeamView(messages: [between])).sent.count, 1)
    }

    func testMessagesAreNewestFirst() {
        func message(_ id: String, _ ts: Double) -> TeamMessage {
            TeamMessage(runId: id, fromId: "dot", fromName: "Dot", toId: "a", toName: "A", message: id, reply: nil, status: "done", ts: ts, finishedAt: nil)
        }
        let built: BotHandoffs = botHandoffs(botId: "dot", team: TeamView(messages: [message("old", 1), message("new", 9), message("mid", 5)]))
        XCTAssertEqual(built.sent.map(\.runId), ["new", "mid", "old"])
    }

    func testDueWordsAreTheWebsSpansAndNothingForAnOrderThatIsOver() throws {
        let open: WorkOrderRecord = try XCTUnwrap(try decode([WorkOrderView].self, workOrders).first).order
        let due: Double = try XCTUnwrap(open.dueAt)
        XCTAssertEqual(open.due(now: due - 3 * 3600 - 5), .dueIn(.hours(3)))
        XCTAssertEqual(open.due(now: due - 59 * 60), .dueIn(.minutes(59)))
        XCTAssertEqual(open.due(now: due - 2 * 86_400 - 60), .dueIn(.days(2)))
        XCTAssertEqual(open.due(now: due + 2 * 3600), .late(.hours(2)))
        let accepted: WorkOrderRecord = WorkOrderRecord(
            id: "wo", agentId: "a", title: "t", brief: "", acceptance: "", objectiveId: nil, status: "accepted", attempts: 1, dueAt: 5, tainted: false, note: "",
            createdAt: 0, updatedAt: 0)
        XCTAssertNil(accepted.due(now: 100), "an order that is over is never late")
        let unknown: WorkOrderRecord = WorkOrderRecord(
            id: "wo", agentId: "a", title: "t", brief: "", acceptance: "", objectiveId: nil, status: "someday", attempts: 0, dueAt: nil, tainted: false, note: "",
            createdAt: 0, updatedAt: 0)
        XCTAssertNil(unknown.state)
        XCTAssertTrue(unknown.isOpen, "a status this app does not know is not hidden as over")
    }

    func testWordsWrittenAfterOutsideContentAreMarkedOnTheObjectiveAndOnItsNotes() {
        func objective(tainted: Bool, noteTainted: Bool) -> ObjectiveRecord {
            ObjectiveRecord(
                id: "obj", title: "t", why: "", measure: "", status: "active", notes: [ObjectiveNote(at: 1, text: "n", tainted: noteTainted)], tainted: tainted,
                createdAt: 0, updatedAt: 0, workflow: ObjectiveWorkflow(nextStep: ""))
        }
        XCTAssertFalse(objective(tainted: false, noteTainted: false).hasOutsideWords)
        XCTAssertTrue(objective(tainted: true, noteTainted: false).hasOutsideWords)
        XCTAssertTrue(objective(tainted: false, noteTainted: true).hasOutsideWords, "one line written after outside content marks it")
    }

    func testTheDaemonsStatusesAreAllKnown() {
        XCTAssertEqual(
            Set(WorkOrderStatus.allCases.map(\.rawValue)), ["assigned", "working", "in_review", "returned", "accepted", "escalated", "cancelled"],
            "WORK_ORDER_MOVES' states")
        XCTAssertEqual(WorkOrderStatus.allCases.filter(\.isOpen).count, 5, "OPEN_WORK_ORDERS")
    }

    func testPausingAndResumingPostToTheAgentsRoutesAndReadThePausedRecord() async throws {
        let daemon: FakeDaemon = FakeDaemon(version: "1.0")
        let api: DaemonAPI = daemon.api()
        let paused: AgentRecord = try await api.pauseAgent("agt_1")
        XCTAssertNotNil(paused.pausedAt)
        let resumed: AgentRecord = try await api.resumeAgent("agt_1")
        XCTAssertNil(resumed.pausedAt)
        XCTAssertEqual(daemon.requests.filter { $0.method == "POST" }.map(\.path), ["/api/agents/agt_1/pause", "/api/agents/agt_1/resume"])
        let orders: [WorkOrderView] = try await api.workOrders()
        let objectives: [ObjectiveRecord] = try await api.objectives()
        let messages: TeamView = try await api.team()
        XCTAssertTrue(orders.isEmpty && objectives.isEmpty && messages.messages.isEmpty)
    }
}
