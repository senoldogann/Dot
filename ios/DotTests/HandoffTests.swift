import XCTest
@testable import Dot

/// The card of a step only the owner can do in the browser (item 1444): what it says, what it never says, and where its
/// button goes.
final class HandoffTests: XCTestCase {
    private func item(_ kind: NeedsYouItem.Kind = .handoff, title: String = "Dot needs you on bank.example", id: String = "apr_h1", thread: String = "thr_1", run: String = "run_1", since: Double = 100) -> NeedsYouItem {
        NeedsYouItem(id: id, kind: kind, runId: run, threadId: thread, agent: "Dot", title: title, since: since, route: "dotagent://run/\(run)")
    }

    private func list(_ items: [NeedsYouItem]) -> NeedsYouList { NeedsYouList(count: items.count, items: items) }

    func testTheCardNamesTheHostTheDaemonNames() throws {
        let card: HandoffCard = try XCTUnwrap(HandoffCard.make(from: item()))
        XCTAssertEqual(card.host, "bank.example")
        XCTAssertEqual(card.headline, "Dot is waiting for you in the browser on bank.example")
        XCTAssertEqual(card.approvalId, "apr_h1")
        XCTAssertEqual(card.runId, "run_1")
        XCTAssertEqual(card.threadId, "thr_1")
    }

    func testAStepThatNamesNoPageSaysOnlyThatDotIsWaiting() throws {
        let card: HandoffCard = try XCTUnwrap(HandoffCard.make(from: item(title: "Dot needs your help")))
        XCTAssertNil(card.host)
        XCTAssertEqual(card.headline, "Dot is waiting for you in the browser")
    }

    /// The daemon's line holds nothing but a host, and the card trusts none of it: a host is what a host looks like, or it is
    /// not said. No credential, address part or page text can come to a card through its line.
    func testAHostThatIsNotPlainlyOneIsNeverPutOnACard() {
        for title in [
            "Dot needs you on bank.example/login?user=anna", "Dot needs you on anna:hunter2@bank.example",
            "Dot needs you on bank.example and your code is 123456", "Dot needs you on <script>alert(1)</script>",
            "Dot needs you on :::", "Dot needs you on ", "Dot needs you on \(String(repeating: "a", count: 254))",
            "Dot needs you on bank.example\nPassword: hunter2", "Dot needs you on bank.example%20x", "Please log in to bank.example",
        ] {
            XCTAssertNil(HandoffCard.host(inLine: title), title)
            let card: HandoffCard? = HandoffCard.make(from: item(title: title))
            XCTAssertEqual(card?.headline, "Dot is waiting for you in the browser", "\(title) puts nothing of itself on the card")
            XCTAssertFalse(card?.headline.contains("hunter2") ?? true)
        }
    }

    func testAHostThatIsPlainlyOneIsSaid() {
        XCTAssertEqual(HandoffCard.host(inLine: "Dot needs you on login.bank-example.co.uk"), "login.bank-example.co.uk")
        XCTAssertEqual(HandoffCard.host(inLine: "Dot needs you on 192.168.1.20"), "192.168.1.20")
        XCTAssertEqual(HandoffCard.host(inLine: "Dot needs you on ::1"), "::1")
        XCTAssertEqual(HandoffCard.host(inLine: "Dot needs you on xn--bcher-kva.example"), "xn--bcher-kva.example")
    }

    func testOnlyAHandOffMakesACard() {
        for kind in [NeedsYouItem.Kind.approval, .question, .secret, .other] {
            XCTAssertNil(HandoffCard.make(from: item(kind)), "\(kind)")
        }
    }

    func testAChatShowsItsOwnHandOffAndTheNewestOfIt() {
        let waiting: NeedsYouList = list([
            item(id: "apr_old", thread: "thr_1", since: 10), item(id: "apr_new", thread: "thr_1", since: 20),
            item(id: "apr_other", thread: "thr_2", since: 30), item(.approval, id: "apr_ask", thread: "thr_3", since: 40),
        ])
        XCTAssertEqual(HandoffCard.card(forChat: "thr_1", in: waiting)?.approvalId, "apr_new")
        XCTAssertEqual(HandoffCard.card(forChat: "thr_2", in: waiting)?.approvalId, "apr_other")
        XCTAssertNil(HandoffCard.card(forChat: "thr_3", in: waiting), "an approval is not a hand-off")
        XCTAssertNil(HandoffCard.card(forChat: "thr_9", in: waiting))
        XCTAssertNil(HandoffCard.card(forChat: "thr_1", in: .empty))
    }

    func testTheButtonOpensTheRunsPageOfTheWebUIOnTheDaemonsOwnAddress() throws {
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        let card: HandoffCard = try XCTUnwrap(HandoffCard.make(from: item(run: "run_mf3k2w9z_a1b2c3")))
        XCTAssertEqual(card.webURL(on: host)?.absoluteString, "https://mac.example.ts.net/#/runs/run_mf3k2w9z_a1b2c3")
        XCTAssertNotNil(card.webURL(on: host).flatMap { isDaemonOrigin($0, host: host) ? $0 : nil }, "never an address of another host")
        let odd: HandoffCard = HandoffCard(approvalId: "apr_1", runId: "../../evil", threadId: "thr_1", host: nil)
        XCTAssertNil(odd.webURL(on: host), "a run id that is not plainly one opens nothing")
        let long: HandoffCard = HandoffCard(approvalId: "apr_1", runId: String(repeating: "a", count: 65), threadId: "thr_1", host: nil)
        XCTAssertNil(long.webURL(on: host))
    }

    func testTheChatIsStillOneTapAwayByTheDeepLink() throws {
        let row: NeedsYouItem = item(run: "run_mf3k2w9z_a1b2c3")
        XCTAssertEqual(row.route, "dotagent://run/run_mf3k2w9z_a1b2c3")
        XCTAssertEqual(DotDeepLink.parse(try XCTUnwrap(URL(string: row.route))), .run("run_mf3k2w9z_a1b2c3"))
        XCTAssertEqual(DotDeepLink.url(forRun: row.runId)?.absoluteString, row.route)
    }

    func testTheInboxRowSaysTheCardsWordsAndCannotBeAnsweredOnThePhone() throws {
        let row: NeedsYouRow = NeedsYouRules.rows(from: list([item()]))[0]
        XCTAssertEqual(row.ask, .handoff(line: "Dot is waiting for you in the browser on bank.example"))
        XCTAssertNil(row.decision(approve: true), "the step is done in the browser, not answered with a tap")
        XCTAssertNil(row.decision(approve: false))
    }

    func testTheCardSaysInTheAppsVoiceWhatIsLeftToDo() {
        let said: String = [HandoffCard.explanation, HandoffCard.buttonTitle, HandoffCard(approvalId: "a", runId: "r", threadId: "t", host: "bank.example").headline]
            .joined(separator: " ").lowercased()
        XCTAssertTrue(said.contains("take over"), "the way to do it from the phone is named as the app's page names it")
        XCTAssertTrue(said.contains("safari"))
        for word in ["daemon", "token", "payload", "api", "credential", "password", "mcp"] { XCTAssertFalse(said.contains(word), word) }
    }
}
