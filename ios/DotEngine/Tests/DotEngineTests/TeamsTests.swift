import Foundation
import XCTest

@testable import DotEngine

/// The group chats and the bots to start from (PLAN §27's "+"): a team the daemon takes, the answers a template's Use
/// sends, and the daemon's answers as the phone reads them.
final class TeamsTests: XCTestCase {
    func testATeamIsCheckedAsTheDaemonChecksIt() {
        XCTAssertEqual(
            TeamEdit.checked(name: "  Launch  ", purpose: " Ship it ", agents: ["a", "b", "a"]),
            TeamEdit(name: "Launch", purpose: "Ship it", agents: ["a", "b"]), "trimmed, each bot once, in order")
        XCTAssertNil(TeamEdit.checked(name: " ", purpose: "", agents: ["a"]), "a name is needed")
        XCTAssertNil(TeamEdit.checked(name: "Launch", purpose: "", agents: []), "a bot is needed")
        XCTAssertNil(TeamEdit.checked(name: String(repeating: "x", count: 61), purpose: "", agents: ["a"]), "60 characters at most")
        XCTAssertNil(TeamEdit.checked(name: "Launch", purpose: String(repeating: "x", count: 501), agents: ["a"]), "a purpose of 500 at most")
        XCTAssertNil(TeamEdit.checked(name: "Launch", purpose: "", agents: (0...50).map(String.init)), "50 bots at most")
    }

    func testATemplatesAnswersGoOnlyWhenOneWasGiven() {
        XCTAssertNil(templateAnswers(["", "  "]), "no body: the template's own instructions")
        XCTAssertNil(templateAnswers([]))
        XCTAssertEqual(templateAnswers([" Oulu ", ""]), ["Oulu", ""], "in order, an empty answer kept in its place")
    }

    func testTheDaemonsTemplatesAndTeamPageRead() throws {
        let templates: Data = Data(
            #"""
            [{"id":"inbox","name":"Inbox keeper","role":"Reads your mail","instructions":"…","tools":["apple_script"],
              "look":{"shape":"blob","expression":"calm","colour":"teal"},"needs":"Mail on this Mac",
              "schedule":"Every morning at 8","cron":"0 8 * * *","task":"Read the inbox","refuses":[],"questions":["Which account?"]}]
            """#.utf8)
        let read: [BotTemplate] = try JSONDecoder().decode([BotTemplate].self, from: templates)
        XCTAssertEqual(read.first?.questions, ["Which account?"])
        XCTAssertEqual(read.first?.cron, "0 8 * * *")

        let page: Data = Data(
            #"""
            {"record":{"id":"t1","name":"Launch","purpose":"Ship it","agents":["a1"],"created_at":1,"ready_team":null},
             "members":[{"id":"a1","name":"Scout","role":"Looks","instructions":"","model":"","effort":null,"approvals":null,
               "tools":[],"created_at":1,"updated_at":1,"look":null,"paused_at":null,"refuses":null}],
             "chats":[]}
            """#.utf8)
        let team: TeamDetail = try JSONDecoder().decode(TeamDetail.self, from: page)
        XCTAssertEqual(team.record.agents, ["a1"])
        XCTAssertEqual(team.members.first?.name, "Scout")
    }
}
