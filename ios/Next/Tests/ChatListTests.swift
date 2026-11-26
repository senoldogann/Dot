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

    /// Item 1966: the app's own language, as iOS keeps it.
    func testTheAppsLanguageIsReadFromWhatIOSKeepsForIt() {
        XCTAssertEqual(AppLanguage.chosen(kept: []), .iPhone)
        XCTAssertEqual(AppLanguage.chosen(kept: ["tr"]), .turkish)
        XCTAssertEqual(AppLanguage.chosen(kept: ["en-FI"]), .english, "a region after the language")
        XCTAssertEqual(AppLanguage.chosen(kept: ["fi"]), .iPhone, "a language the app does not speak")
        XCTAssertTrue(AppLanguage.turkish.isSpoken(running: "tr"))
        XCTAssertFalse(AppLanguage.turkish.isSpoken(running: "en"), "the page says it applies from the next start")
        XCTAssertTrue(AppLanguage.iPhone.isSpoken(running: "en"))
        let suite: String = "item1966-\(UUID().uuidString)"
        let defaults: UserDefaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        AppLanguage.turkish.keep(defaults)
        XCTAssertEqual(defaults.stringArray(forKey: "AppleLanguages"), ["tr"])
        AppLanguage.iPhone.keep(defaults)
        XCTAssertNil(defaults.stringArray(forKey: "AppleLanguages").flatMap { $0 == ["tr"] ? $0 : nil }, "the iPhone's again")
    }

    /// Item 1923: Dot's own work (a code review, a schedule's chat) stands last, apart from the owner's chats; one that
    /// waits on the owner stands under Needs you.
    func testDotsOwnWorkStandsApartFromTheOwnersChats() throws {
        func chat(_ id: String, source: String, status: String, asks: Int) throws -> ThreadRow {
            let json: String = """
                {"summary":{"id":"\(id)","title":"\(id)","source":"\(source)","created_at":1,"updated_at":1,"pinned_at":null,
                "run_count":1,"latest_run_id":"run_\(id)","latest_status":"\(status)","latest_created_at":1,"unread":true,
                "actor_id":null,"last_message":"","last_role":"assistant"},"revision":1,"asks":\(asks)}
                """
            return try JSONDecoder().decode(ThreadRow.self, from: Data(json.utf8))
        }
        let sections: ChatListSections = chatListSections([
            try chat("review", source: "review", status: "running", asks: 0), try chat("talk", source: "phone", status: "done", asks: 0),
            try chat("daily", source: "schedule", status: "done", asks: 0), try chat("asks", source: "schedule", status: "blocked", asks: 1),
        ])
        XCTAssertEqual(sections.recent.map(\.summary.id), ["talk"])
        XCTAssertEqual(sections.working.map(\.summary.id), [], "a code review at work is not the owner's work in progress")
        XCTAssertEqual(sections.dotsWork.map(\.summary.id), ["review", "daily"])
        XCTAssertEqual(sections.needsYou.map(\.summary.id), ["asks"])
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
        XCTAssertTrue(ChatListScope.all.holds(agents, bots: []))
        XCTAssertTrue(ChatListScope.bot(agent: nil).holds(dots, bots: []))
        XCTAssertFalse(ChatListScope.bot(agent: nil).holds(agents, bots: []))
        XCTAssertTrue(ChatListScope.bot(agent: "agt_1").holds(agents, bots: []))
        let campaign: ThreadRow = try row("c", status: "done", asks: 0, pinned: false, actor: "camp_1")
        let dot: AgentRecord = AgentRecord(
            id: "dot", name: "Dot", role: "", instructions: "", model: "", effort: nil, approvals: nil, tools: [], createdAt: 0, updatedAt: 0, look: nil,
            pausedAt: nil, refuses: nil)
        let agent: AgentRecord = AgentRecord(
            id: "agt_1", name: "Scout", role: "", instructions: "", model: "", effort: nil, approvals: nil, tools: [], createdAt: 0, updatedAt: 0, look: nil,
            pausedAt: nil, refuses: nil)
        let built: [Bot] = DotEngine.bots(
            agents: [AgentSummary(record: dot, home: nil), AgentSummary(record: agent, home: nil)], rows: [dots, agents, campaign], pinned: [],
            fallbackDot: dot, gone: [])
        let dotsAsTheHomeCounts: ChatListScope = .botChats(bot: "dot", agent: nil)
        XCTAssertTrue(dotsAsTheHomeCounts.holds(campaign, bots: built), "a campaign's chat is Dot's, as on the home")
        XCTAssertFalse(dotsAsTheHomeCounts.holds(agents, bots: built))
        XCTAssertNil(dotsAsTheHomeCounts.agent)
        XCTAssertEqual(ChatListScope.botChats(bot: "agt_1", agent: "agt_1").agent, "agt_1", "the pencil starts a chat with the bot")
        let newer: ThreadRow = try row("d", status: "done", asks: 0, pinned: false, actor: nil)
        let later: [Bot] = DotEngine.bots(
            agents: [AgentSummary(record: dot, home: nil)], rows: [dots, newer], pinned: [], fallbackDot: dot, gone: [])
        XCTAssertTrue(dotsAsTheHomeCounts.holds(newer, bots: later), "item 1965: a chat made after the page opened is there")
    }
}
