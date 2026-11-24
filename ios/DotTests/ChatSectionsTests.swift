import XCTest
@testable import Dot

/// The code reviews of the pre-push hook stand apart from the owner's chats (item 1664), working or not.
final class ChatSectionsTests: XCTestCase {
    private func thread(_ id: String, source: String, status: String) -> ThreadSummary {
        ThreadSummary(
            id: id, title: id, source: source, updatedAt: 1, pinnedAt: nil, runCount: 1, latestRunId: "run_\(id)",
            latestStatus: status, latestMode: "research", unread: false, actorId: nil, lastMessage: "", lastRole: "",
            loopCron: nil, loopNextAt: nil)
    }

    func testAReviewIsNeverAmongTheOwnersChats() {
        let threads: [ThreadSummary] = [
            thread("mine", source: "ui", status: "done"),
            thread("review_running", source: "review", status: "running"),
            thread("cli", source: "cli", status: "running"),
            thread("review_done", source: "review", status: "done"),
        ]
        XCTAssertEqual(ChatSections.owners(threads).map(\.id), ["mine", "cli"])
        XCTAssertEqual(ChatSections.reviews(threads).map(\.id), ["review_running", "review_done"])
    }
}
