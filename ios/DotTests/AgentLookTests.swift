import XCTest
@testable import Dot

/// Agents are bots, not letters (item 1462): Dot wears its own face, every other agent the bot, one neutral disc whatever
/// its id; how it stands is its ring, and what VoiceOver says is its name and that state.
final class AgentLookTests: XCTestCase {
    private func agent(_ id: String, _ name: String, status: String? = nil, paused: Bool = false) -> AgentSummary {
        AgentSummary(
            record: AgentSummary.Record(id: id, name: name, role: "", pausedAt: paused ? 100 : nil),
            latest: status.map { makeRun("run_\(id)", status: $0) }, latestTitle: nil, home: nil)
    }

    func testDotWearsItsOwnFaceAndEveryOtherAgentTheBot() {
        XCTAssertEqual(AgentLook.face(ofAgent: AgentLook.dotId), .dot)
        XCTAssertEqual(AgentLook.face(ofAgent: nil), .dot, "a chat with no agent is Dot's")
        XCTAssertEqual(AgentLook.face(ofAgent: "agt_jobhunter"), .bot)
        XCTAssertEqual(AgentLook.face(ofAgent: "Sales Outbound"), .bot)
    }

    /// The hue of an id and the first letter of a name told agents apart by what they did not choose: the bot is one face.
    func testNoAgentIsToldApartByItsId() {
        let faces: Set<AgentLook.Face> = Set(["agt_a", "agt_b", "J", "S", "x1", "🙂"].map { AgentLook.face(ofAgent: $0) })
        XCTAssertEqual(faces, [.bot])
    }

    func testVoiceOverSaysTheNameAndHowItStands() {
        XCTAssertEqual(AgentLook.words(name: "Dot", state: .working), "Dot, working")
        XCTAssertEqual(AgentLook.words(name: "Research", state: .waiting), "Research, waits for you")
        XCTAssertEqual(AgentLook.words(name: "JobHunter", state: .idle), "JobHunter")
        XCTAssertEqual(AgentLook.words(name: "Sales Outbound", state: .idle, paused: true), "Sales Outbound, paused")
        XCTAssertEqual(AgentLook.words(name: "Scout", state: .working, paused: true), "Scout, working", "what it does now comes before the pause")
    }

    func testAnAgentStandsAsItsNewestRunDoes() {
        XCTAssertEqual(agent("dot", "Dot", status: "running").state, .working)
        XCTAssertEqual(agent("a", "A", status: "queued").state, .working)
        XCTAssertEqual(agent("a", "A", status: "blocked").state, .waiting)
        XCTAssertEqual(agent("a", "A", status: "done").state, .idle)
        XCTAssertEqual(agent("a", "A", status: "failed").state, .idle)
        XCTAssertEqual(agent("a", "A").state, .idle, "an agent that never ran is at rest")
        XCTAssertEqual(agent("dot", "Dot", status: "running").spokenWords, "Dot, working")
        XCTAssertEqual(agent("r", "Research", status: "blocked").spokenWords, "Research, waits for you")
        XCTAssertEqual(agent("j", "JobHunter", status: "done", paused: true).spokenWords, "JobHunter, paused")
    }

    // MARK: Whose line wears the face

    private func user(_ id: String) -> ChatItem { ChatItem(id: "u:\(id)", kind: .user("Hi"), ts: 1) }
    private func said(_ id: String) -> ChatItem { ChatItem(id: "a:\(id)", kind: .assistant(text: "On it", streaming: false), ts: 2) }
    private func answer(_ id: String) -> ChatItem { ChatItem(id: "r:\(id)", kind: .answer(text: "Done", status: "done"), ts: 3) }
    private func step(_ id: String) -> ChatItem {
        ChatItem(id: "t:\(id)", kind: .tool(ToolRow(callId: id, runId: "r", tool: "shell", line: "Shell", state: .ok)), ts: 2)
    }
    private func card(_ id: String) -> ChatItem {
        ChatItem(
            id: "p:\(id)", kind: .approval(ApprovalCard(approvalId: id, runId: "r", tool: "shell", summary: "", severity: "", question: nil, status: "approved")),
            ts: 2)
    }
    private func note(_ id: String) -> ChatItem { ChatItem(id: "n:\(id)", kind: .note("Saved", .plain), ts: 2) }

    func testTheFirstLineSinceTheOwnerSpokeWearsTheFace() {
        let items: [ChatItem] = [user("1"), said("1"), step("1"), said("2"), answer("1")]
        XCTAssertEqual((0..<items.count).map { SpeakerRule.wearsFace(at: $0, in: items) }, [false, true, false, false, false])
    }

    func testTheOwnersNextMessageGivesTheNextLineTheFaceAgain() {
        let items: [ChatItem] = [user("1"), answer("1"), user("2"), answer("2")]
        XCTAssertEqual((0..<items.count).map { SpeakerRule.wearsFace(at: $0, in: items) }, [false, true, false, true])
    }

    /// Steps, a card it asks and a note are not someone else speaking: the lines on either side of them are one voice.
    func testWhatStandsBetweenTwoLinesOfTheAgentIsNotSomeoneSpeaking() {
        let items: [ChatItem] = [user("1"), said("1"), step("1"), card("1"), note("1"), answer("1")]
        XCTAssertTrue(SpeakerRule.wearsFace(at: 1, in: items))
        XCTAssertFalse(SpeakerRule.wearsFace(at: 5, in: items))
    }

    func testAChatThatOpensWithTheAgentGivesItsFirstLineTheFaceAndTheOwnersLinesNone() {
        let items: [ChatItem] = [step("1"), said("1"), user("1")]
        XCTAssertTrue(SpeakerRule.wearsFace(at: 1, in: items))
        XCTAssertFalse(SpeakerRule.wearsFace(at: 0, in: items), "a step is no one's line")
        XCTAssertFalse(SpeakerRule.wearsFace(at: 2, in: items), "the owner wears no face")
        XCTAssertFalse(SpeakerRule.wearsFace(at: 9, in: items), "a row that is not there")
    }
}
