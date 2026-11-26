import Foundation
import XCTest

@testable import DotEngine

/// The home's bots (PLAN §27, items 1881 and 1882): which chat belongs to which bot, the unread counts, the pinned order,
/// the search, and the agent shapes as the daemon writes them.
final class BotsTests: XCTestCase {
    private func record(_ id: String, _ name: String, role: String, created: Double) -> AgentRecord {
        AgentRecord(
            id: id, name: name, role: role, instructions: "", model: "", effort: nil, approvals: nil, tools: [], createdAt: created, updatedAt: created,
            look: nil, pausedAt: nil, refuses: nil)
    }

    private func row(_ id: String, actor: String?, updated: Double, unread: Bool, asks: Int, title: String, last: String) -> ThreadRow {
        ThreadRow(
            summary: ThreadSummary(
                id: id, title: title, source: "ui", createdAt: updated, updatedAt: updated, pinnedAt: nil, runCount: 1, latestRunId: "r-\(id)",
                latestStatus: "done", latestCreatedAt: updated, unread: unread, actorId: actor, lastMessage: last, lastRole: "assistant"),
            revision: 1, asks: asks, helpers: 0, background: [])
    }

    private var agents: [AgentSummary] {
        [
            AgentSummary(record: record("dot", "Dot", role: "", created: 0), home: nil),
            AgentSummary(record: record("a1", "Researcher", role: "Finds sources", created: 10), home: nil),
            AgentSummary(record: record("a2", "Writer", role: "Drafts posts", created: 20), home: nil),
        ]
    }

    private var rows: [ThreadRow] {
        [
            row("t1", actor: nil, updated: 100, unread: false, asks: 0, title: "Morning brief", last: "Three meetings today"),
            row("t2", actor: "a1", updated: 300, unread: true, asks: 1, title: "Sources", last: "Which of these should I read?"),
            row("t3", actor: "camp-1", updated: 400, unread: true, asks: 0, title: "Campaign", last: "Session 2 done"),
            row("t4", actor: "a1", updated: 200, unread: false, asks: 0, title: "Older", last: "Done"),
        ]
    }

    func testAChatBelongsToItsAgentAndEveryOtherChatToDot() {
        let built: [Bot] = bots(agents: agents, rows: rows, pinned: [], fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])
        XCTAssertEqual(built.map(\.id), ["dot", "a1", "a2"], "Dot first, then the one that moved last, then one with no chat")
        XCTAssertEqual(built[0].chats.map(\.summary.id), ["t3", "t1"], "a campaign's chat is Dot's")
        XCTAssertEqual(built[1].chats.map(\.summary.id), ["t2", "t4"], "the chat that moved last first")
        XCTAssertEqual(built[1].latest?.summary.id, "t2")
        XCTAssertEqual(built[1].unread, 1)
        XCTAssertEqual(built[1].asks, 1)
        XCTAssertNil(built[2].latest)
    }

    func testPinnedBotsComeFirstInTheirOrderAndDotWithoutARecordKeepsItsChats() {
        let built: [Bot] = bots(agents: Array(agents.dropFirst()), rows: rows, pinned: ["a2"], fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])
        XCTAssertEqual(built.map(\.id), ["dot", "a2", "a1"])
        XCTAssertEqual(built[0].record.name, "Dot")
        XCTAssertEqual(built[0].chats.count, 2)
    }

    func testABotWhoseChatsAreOutsideTheListsWindowOpensItsHomeChat() throws {
        let home: ThreadSummary = row("t9", actor: "a2", updated: 5, unread: true, asks: 0, title: "Writer", last: "Draft ready").summary
        let withHome: [AgentSummary] = [agents[0], agents[1], AgentSummary(record: agents[2].record, home: home)]
        let built: [Bot] = bots(agents: withHome, rows: rows, pinned: [], fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])
        let writer: Bot = try XCTUnwrap(built.first { $0.id == "a2" })
        XCTAssertEqual(writer.latest?.summary.id, "t9", "the daemon's home chat, though the list's newest chats leave it out")
        XCTAssertEqual(writer.unread, 1)
        let again: [Bot] = bots(
            agents: [agents[0], AgentSummary(record: agents[1].record, home: rows[1].summary), agents[2]], rows: rows, pinned: [],
            fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])
        XCTAssertEqual(again.first { $0.id == "a1" }?.chats.map(\.summary.id), ["t2", "t4"], "a home already listed is not added twice")
        let seen: ThreadSummary = row("t9", actor: "a2", updated: 5, unread: false, asks: 0, title: "Writer", last: "Draft ready").summary
        let reread: [Bot] = bots(
            agents: [agents[0], agents[1], AgentSummary(record: agents[2].record, home: seen)], rows: rows, pinned: [],
            fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])
        XCTAssertEqual(reread.first { $0.id == "a2" }?.unread, 0, "the home read again once seen stops counting")
        let deleted: [Bot] = bots(agents: withHome, rows: rows, pinned: [], fallbackDot: record("dot", "Dot", role: "", created: 0), gone: ["t9"])
        XCTAssertEqual(deleted.first { $0.id == "a2" }?.chats.count, 0, "item 1965: a home deleted or being deleted is not shown")
    }

    /// The owner's build 6 (item 1912): the pre-push hook writes a code review chat on every push, and Dot's schedules
    /// one a day, all Dot's; its face wore the unread mark for ever and a tap on it opened the newest review.
    func testDotsOwnWorkNeitherMarksItUnreadNorIsWhatATapOpens() {
        func chat(_ id: String, source: String, updated: Double, unread: Bool, asks: Int) -> ThreadRow {
            ThreadRow(
                summary: ThreadSummary(
                    id: id, title: id, source: source, createdAt: updated, updatedAt: updated, pinnedAt: nil, runCount: 1, latestRunId: "r-\(id)",
                    latestStatus: "done", latestCreatedAt: updated, unread: unread, actorId: nil, lastMessage: "", lastRole: "assistant"),
                revision: 1, asks: asks, helpers: 0, background: [])
        }
        let work: [ThreadRow] = [
            chat("review", source: "review", updated: 900, unread: true, asks: 0),
            chat("daily", source: "schedule", updated: 800, unread: true, asks: 0),
            chat("idle", source: "initiative", updated: 700, unread: true, asks: 0),
            chat("sent", source: "team", updated: 600, unread: true, asks: 0),
        ]
        let talk: ThreadRow = chat("talk", source: "phone", updated: 100, unread: false, asks: 0)
        let dot: Bot = bots(agents: [agents[0]], rows: work + [talk], pinned: [], fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])[0]
        XCTAssertEqual(dot.unread, 0, "Dot's own work is no unread conversation")
        XCTAssertEqual(dot.latest?.summary.id, "talk", "a tap opens the owner's own chat, not the newest review")
        XCTAssertEqual(dot.chats.count, 5, "the work stays among Dot's chats")

        let asking: Bot = bots(
            agents: [agents[0]], rows: [chat("daily", source: "schedule", updated: 800, unread: true, asks: 1)], pinned: [],
            fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])[0]
        XCTAssertEqual(asking.asks, 1, "work that waits on the owner still marks Dot")
        XCTAssertEqual(asking.latest?.summary.id, "daily", "with no conversation, the newest chat opens")
        let read: Bot = bots(
            agents: [agents[0]], rows: [chat("talk", source: "ui", updated: 100, unread: true, asks: 0)], pinned: [],
            fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])[0]
        XCTAssertEqual(read.unread, 1, "an unread conversation still marks Dot")
    }

    func testTheUnreadCountLeavesOutTheCurrentBotAndTheMutedOnes() {
        let built: [Bot] = bots(agents: agents, rows: rows, pinned: [], fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])
        XCTAssertEqual(unreadBots(built, except: nil, muted: []), 2)
        XCTAssertEqual(unreadBots(built, except: "a1", muted: []), 1)
        XCTAssertEqual(unreadBots(built, except: nil, muted: ["dot"]), 1)
    }

    func testTheSearchFindsBotsChatsAndMessagesEachChatOnce() {
        let built: [Bot] = bots(agents: agents, rows: rows, pinned: [], fallbackDot: record("dot", "Dot", role: "", created: 0), gone: [])
        let hits: [ChatHit] = [
            ChatHit(threadId: "t2", seq: 3, kind: "m", ref: "user", snippet: "the [sources] list"),
            ChatHit(threadId: "t1", seq: 1, kind: "m", ref: "user", snippet: "no [sources] today"),
            ChatHit(threadId: "t1", seq: 4, kind: "r", ref: "r-t1", snippet: "[sources] again"),
        ]
        let found: HomeFinds = homeFinds("sources", bots: built, hits: hits)
        XCTAssertEqual(found.bots.map(\.id), ["a1"], "the role holds the word")
        XCTAssertEqual(found.chats.map(\.summary.id), ["t2"])
        XCTAssertEqual(found.messages.map(\.threadId), ["t1"], "a chat found by its title is not named again, nor twice")
        XCTAssertTrue(homeFinds("  ", bots: built, hits: hits).isEmpty)
        XCTAssertEqual(homeFinds("writer xyz", bots: built, hits: []).bots.map(\.id), [], "every word must match")
        XCTAssertEqual(homeFinds("drafts posts", bots: built, hits: []).bots.map(\.id), ["a2"])
    }

    func testANewBotIsItsNameAndWhatItDoesAndARenameKeepsEverythingElse() throws {
        let request: AgentRequest = try XCTUnwrap(AgentRequest.newBot(name: "  Scout ", does: "Watch the market\nand tell me each morning"))
        XCTAssertEqual(request.name, "Scout")
        XCTAssertEqual(request.role, "Watch the market")
        XCTAssertEqual(request.instructions, "Watch the market\nand tell me each morning")
        XCTAssertNil(request.tools, "the daemon gives a new agent its own set")
        XCTAssertNil(AgentRequest.newBot(name: " ", does: "x"))
        XCTAssertEqual(AgentRequest.newBot(name: String(repeating: "n", count: 90), does: String(repeating: "r", count: 200))?.role.count, 120)
        let body: [String: Any] = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any])
        XCTAssertEqual(body["model"] as? String, "")
        XCTAssertTrue(body["effort"] is NSNull, "the daemon requires effort and approvals, null for the configured ones")
        XCTAssertTrue(body["approvals"] is NSNull)
        XCTAssertNil(body["tools"])
        let saved: AgentRecord = AgentRecord(
            id: "a1", name: "Old", role: "R", instructions: "I", model: "anthropic:claude", effort: "high", approvals: "ask", tools: ["web_fetch"], createdAt: 1,
            updatedAt: 1, look: AgentLook(shape: "pebble", expression: "happy", colour: "teal"), pausedAt: nil, refuses: "Never pays")
        let renamed: AgentRequest = AgentRequest.renamed(saved, to: " New ")
        XCTAssertEqual(renamed.name, "New")
        XCTAssertEqual(renamed.tools, ["web_fetch"])
        XCTAssertEqual(renamed.look, saved.look)
        XCTAssertEqual(renamed.refuses, "Never pays")
    }

    func testTheAgentsListDecodesAsTheDaemonWritesIt() throws {
        let json: String = """
            [{"record":{"id":"dot","name":"Dot","role":"","instructions":"","model":"","effort":null,"approvals":null,"tools":[],
              "created_at":0,"updated_at":0,"made_by":null,"look":null,"paused_at":null,"refuses":""},"latest":null,"latest_title":null,"home":null},
             {"record":{"id":"a1","name":"Scout","role":"Watch","instructions":"Watch it","model":"","effort":"high","approvals":"ask",
              "tools":["web_fetch"],"created_at":5,"updated_at":6,"made_by":"owner","look":{"shape":"hexagon","expression":"neutral","colour":"teal"},
              "paused_at":null,"refuses":""},"latest":null,"latest_title":null,
              "home":{"id":"t1","title":"Scout","source":"ui","created_at":5,"updated_at":6,"pinned_at":null,"run_count":1,"latest_run_id":"r1",
              "latest_status":"blocked","latest_mode":"chat","latest_created_at":5,"unread":false,"actor_id":"a1","last_message":"Hi",
              "last_role":"assistant","loop_cron":null,"loop_next_at":null,"team":null,"campaign_id":null}}]
            """
        let decoded: [AgentSummary] = try JSONDecoder().decode([AgentSummary].self, from: Data(json.utf8))
        XCTAssertEqual(decoded.map(\.record.name), ["Dot", "Scout"])
        XCTAssertEqual(decoded[1].record.look?.colour, "teal")
        XCTAssertEqual(decoded[1].home?.id, "t1")
        let hits: [ChatHit] = try JSONDecoder().decode(
            [ChatHit].self, from: Data(#"[{"thread_id":"t1","seq":2,"kind":"m","ref":"user","snippet":"the [car]"}]"#.utf8))
        XCTAssertEqual(hits.first?.threadId, "t1")
    }

    func testANewChatWithAnAgentNamesItAndOneWithDotLeavesItOut() throws {
        let withAgent: [String: Any] = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(NewChatMessage(goal: "Hi", key: "k", attachments: [], start: .daemons, agentId: "a1"))) as? [String: Any])
        XCTAssertEqual(withAgent["agent_id"] as? String, "a1")
        let withDot: [String: Any] = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(NewChatMessage(goal: "Hi", key: "k", attachments: [], start: .daemons, agentId: nil))) as? [String: Any])
        XCTAssertNil(withDot["agent_id"])
        // A new chat kept on disk before the agent was added reads back as Dot's.
        let old: Data = Data(#"{"newChat":{"text":"Hi","images":[],"uploaded":[]}}"#.utf8)
        XCTAssertEqual(try JSONDecoder().decode(OutboxItem.Action.self, from: old), .newChat(text: "Hi", images: [], uploaded: [], start: nil, agent: nil))
    }
}
