import Foundation
import XCTest

@testable import DotEngine

/// Where a chat's row and a bot stand (item 1920), from the list's rows alone.
final class RowStateTests: XCTestCase {
    private func row(_ id: String, status: String, asks: Int, helpers: Int?, background: [String]?, source: String, updated: Double) -> ThreadRow {
        ThreadRow(
            summary: ThreadSummary(
                id: id, title: id, source: source, createdAt: updated, updatedAt: updated, pinnedAt: nil, runCount: 1, latestRunId: "r-\(id)",
                latestStatus: status, latestCreatedAt: updated, unread: false, actorId: nil, lastMessage: "", lastRole: "assistant"),
            revision: 1, asks: asks, helpers: helpers,
            background: background?.map { BackgroundWork(kind: "command", id: "s-\($0)", title: $0, startedAt: updated) })
    }

    func testEachChatSaysOneStateTheStrongestFirst() {
        XCTAssertEqual(rowState(row("a", status: "running", asks: 1, helpers: 2, background: nil, source: "ui", updated: 1)), .needsYou)
        XCTAssertEqual(rowState(row("b", status: "blocked", asks: 0, helpers: nil, background: nil, source: "ui", updated: 1)), .needsYou)
        XCTAssertEqual(rowState(row("c", status: "running", asks: 0, helpers: 2, background: ["npm run dev"], source: "ui", updated: 1)), .waitingOnHelpers(2))
        XCTAssertEqual(rowState(row("d", status: "running", asks: 0, helpers: 0, background: nil, source: "ui", updated: 1)), .working)
        XCTAssertEqual(rowState(row("e", status: "queued", asks: 0, helpers: nil, background: nil, source: "ui", updated: 1)), .working, "an older daemon")
        XCTAssertEqual(rowState(row("f", status: "done", asks: 0, helpers: 0, background: ["npm run dev"], source: "ui", updated: 1)), .waiting("npm run dev"))
        XCTAssertEqual(rowState(row("g", status: "done", asks: 0, helpers: nil, background: nil, source: "ui", updated: 1)), .rest)
        XCTAssertTrue(RowState.working.isLive && RowState.waitingOnHelpers(1).isLive)
        XCTAssertFalse(RowState.needsYou.isLive || RowState.waiting("x").isLive || RowState.rest.isLive)
    }

    func testABotSaysItsStrongestConversationAndDotsOwnWorkDoesNotMakeItBusy() {
        let dot: AgentRecord = AgentRecord(
            id: "dot", name: "Dot", role: "", instructions: "", model: "", effort: nil, approvals: nil, tools: [], createdAt: 0, updatedAt: 0,
            look: nil, pausedAt: nil, refuses: nil)
        func state(_ rows: [ThreadRow]) -> RowState {
            botState(bots(agents: [AgentSummary(record: dot, home: nil, load: nil)], rows: rows, pinned: [], fallbackDot: dot, gone: [])[0])
        }
        let review: ThreadRow = row("review", status: "running", asks: 0, helpers: 0, background: nil, source: "review", updated: 9)
        let resting: ThreadRow = row("talk", status: "done", asks: 0, helpers: 0, background: nil, source: "ui", updated: 1)
        XCTAssertEqual(state([review, resting]), .rest, "a code review at work is Dot's own work")
        let waiting: ThreadRow = row("loop", status: "done", asks: 0, helpers: 0, background: ["Daily news"], source: "ui", updated: 5)
        let working: ThreadRow = row("work", status: "running", asks: 0, helpers: 0, background: nil, source: "phone", updated: 2)
        XCTAssertEqual(state([review, waiting, working, resting]), .working, "what works is stronger than what waits")
        let asking: ThreadRow = row("ask", status: "done", asks: 0, helpers: 0, background: nil, source: "schedule", updated: 3)
        XCTAssertEqual(state([waiting, ThreadRow(summary: asking.summary, revision: 1, asks: 1, helpers: 0, background: [])]), .needsYou,
                       "an ask of Dot's own work still waits on the owner")
    }
}
