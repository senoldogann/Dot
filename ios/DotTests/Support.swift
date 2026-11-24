import Foundation
@testable import Dot

/// An event as the daemon writes it, for the tests.
func event(
    _ id: Int, _ run: String = "run_1", _ kind: String, _ data: [String: JSONValue] = [:], ts: Double = 1000
) -> EventRecord {
    EventRecord(id: id, runId: run, ts: ts + Double(id), kind: kind, data: data)
}

func makeRun(
    _ id: String = "run_1", thread: String = "thr_1", goal: String = "Do it", status: String = "running", parent: String? = nil,
    created: Double = 1000, result: String = ""
) -> RunRecord {
    RunRecord(
        id: id, threadId: thread, goal: goal, mode: "task", model: "ollama:scripted", status: status, createdAt: created,
        finishedAt: nil, result: result, steps: 0, parentRunId: parent, source: "ui", agentId: nil, followsReaction: nil)
}

func text(of item: ChatItem) -> String? {
    switch item.kind {
    case .user(let text), .assistant(let text, _), .answer(let text, _): text
    case .note(let text, _): text
    case .tool(let row): row.line
    case .approval(let card): card.question ?? card.summary
    case .shownPage(_, let title): title
    }
}
