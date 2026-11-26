import DotEngine
import XCTest

@testable import DotNext

/// The chat list's pure rules (item 1800): its sections, which chats the daemon would refuse to delete, a bot's scope.
final class ChatListTests: XCTestCase {
    private func row(_ id: String, status: String, asks: Int, pinned: Bool, actor: String?) throws -> ThreadRow {
        let actorJSON: String = actor.map { "\"\($0)\"" } ?? "null"
        let json: String = """
            {"summary":{"id":"\(id)","title":"\(id)","source":"ui","created_at":1,"updated_at":1,"pinned_at":\(pinned ? "5" : "null"),
            "run_count":1,"latest_run_id":"run_\(id)","latest_status":"\(status)","latest_created_at":1,"unread":false,
            "actor_id":\(actorJSON),"last_message":"","last_role":"assistant"},"revision":1,"asks":\(asks)}
            """
        return try JSONDecoder().decode(ThreadRow.self, from: Data(json.utf8))
    }

    /// Item 1914: words half-written to a new chat with one bot never open in a new chat with another.
    @MainActor func testEachBotKeepsItsOwnNewChatDraft() {
        XCTAssertEqual(AppModel.newChatDraft(agent: nil), AppModel.newChatDraft, "Dot's is the draft a reset fills")
        XCTAssertNotEqual(AppModel.newChatDraft(agent: "agt_scout"), AppModel.newChatDraft(agent: nil))
        XCTAssertNotEqual(AppModel.newChatDraft(agent: "agt_scout"), AppModel.newChatDraft(agent: "agt_writer"))
    }

    func testAPinnedChatStaysUnderPinnedWhateverItsState() throws {
        let waiting: ThreadRow = try row("a", status: "blocked", asks: 1, pinned: true, actor: nil)
        let working: ThreadRow = try row("b", status: "running", asks: 0, pinned: false, actor: nil)
        let asking: ThreadRow = try row("c", status: "blocked", asks: 1, pinned: false, actor: nil)
        let done: ThreadRow = try row("d", status: "done", asks: 0, pinned: false, actor: nil)
        let sections: ChatListSections = chatListSections([waiting, working, asking, done])
        XCTAssertEqual(sections.pinned.map(\.summary.id), ["a"], "pinned on top, though it waits on the owner")
        XCTAssertEqual(sections.needsYou.map(\.summary.id), ["c"])
        XCTAssertEqual(sections.working.map(\.summary.id), ["b"])
        XCTAssertEqual(sections.recent.map(\.summary.id), ["d"])
    }

    func testAChatWaitingOnTheOwnerHoldsWorkAsTheDaemonSaysIt() throws {
        XCTAssertTrue(chatHoldsWork(try row("a", status: "blocked", asks: 0, pinned: false, actor: nil)), "the daemon refuses a blocked chat")
        XCTAssertTrue(chatHoldsWork(try row("b", status: "queued", asks: 0, pinned: false, actor: nil)))
        XCTAssertTrue(chatHoldsWork(try row("c", status: "ending", asks: 0, pinned: false, actor: nil)))
        XCTAssertTrue(chatHoldsWork(try row("d", status: "done", asks: 1, pinned: false, actor: nil)), "an ask open")
        XCTAssertFalse(chatHoldsWork(try row("e", status: "failed", asks: 0, pinned: false, actor: nil)))
    }

    func testABotsScopeHoldsItsOwnChatsOnly() throws {
        let dots: ThreadRow = try row("a", status: "done", asks: 0, pinned: false, actor: nil)
        let agents: ThreadRow = try row("b", status: "done", asks: 0, pinned: false, actor: "agt_1")
        XCTAssertTrue(ChatListScope.all.holds(agents))
        XCTAssertTrue(ChatListScope.bot(agent: nil).holds(dots))
        XCTAssertFalse(ChatListScope.bot(agent: nil).holds(agents))
        XCTAssertTrue(ChatListScope.bot(agent: "agt_1").holds(agents))
        let campaign: ThreadRow = try row("c", status: "done", asks: 0, pinned: false, actor: "camp_1")
        let dotsAsTheHomeCounts: ChatListScope = .chats(of: ["a", "c"], agent: nil)
        XCTAssertTrue(dotsAsTheHomeCounts.holds(campaign), "a campaign's chat is Dot's, as on the home")
        XCTAssertFalse(dotsAsTheHomeCounts.holds(agents))
        XCTAssertNil(dotsAsTheHomeCounts.agent)
        XCTAssertEqual(ChatListScope.chats(of: [], agent: "agt_1").agent, "agt_1", "the pencil starts a chat with the bot")
    }
}
