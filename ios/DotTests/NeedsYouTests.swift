import XCTest
@testable import Dot

/// What waits on the owner (item 1442): the daemon's list, its rows, and what may be answered from a list on a phone.
final class NeedsYouTests: XCTestCase {
    /// The reply of `GET /api/needs-you` as `docs/phone.md` writes it: a secret, a hand-off, a question, an approval.
    private static let example: String = """
        {"count": 4, "items": [
         {"id": "apr_mf3k2x1a_1c04d1", "kind": "secret", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
          "agent": "Dot", "title": "Dot asks for a secret", "since": 1764000400.0, "route": "dotagent://run/run_mf3k2w9z_a1b2c3"},
         {"id": "apr_mf3k2x11_7e8f9a", "kind": "handoff", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
          "agent": "Dot", "title": "Dot needs you on bank.example", "since": 1764000300.0, "route": "dotagent://run/run_mf3k2w9z_a1b2c3"},
         {"id": "apr_mf3k2x00_4e5f6a", "kind": "question", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
          "agent": "Scout", "title": "Which day should it ship?", "since": 1764000200.0, "route": "dotagent://run/run_mf3k2w9z_a1b2c3",
          "choices": ["Friday", "Monday"], "recommended": "Friday"},
         {"id": "apr_mf3k2wzz_7b8c9d", "kind": "approval", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
          "agent": "Dot", "title": "Run: make deploy", "since": 1764000100.0, "route": "dotagent://run/run_mf3k2w9z_a1b2c3"}]}
        """

    private func list(_ json: String = NeedsYouTests.example) throws -> NeedsYouList {
        try JSONDecoder().decode(NeedsYouList.self, from: Data(json.utf8))
    }

    private func item(_ kind: NeedsYouItem.Kind, id: String = "apr_1", title: String = "Run: ls", since: Double = 100, choices: [String]? = nil, recommended: String? = nil, agent: String = "Dot") -> NeedsYouItem {
        NeedsYouItem(
            id: id, kind: kind, runId: "run_1", threadId: "thr_1", agent: agent, title: title, since: since, route: "dotagent://run/run_1",
            choices: choices, recommended: recommended)
    }

    private func row(_ kind: NeedsYouItem.Kind, title: String = "Run: ls", choices: [String]? = nil, recommended: String? = nil, id: String = "apr_1", answering: Bool = false) -> NeedsYouRow {
        NeedsYouRules.rows(
            from: NeedsYouList(count: 1, items: [item(kind, id: id, title: title, choices: choices, recommended: recommended)]),
            answering: answering ? [id] : [])[0]
    }

    // MARK: The list

    func testTheDaemonsOwnExampleReads() throws {
        let read: NeedsYouList = try list()
        XCTAssertEqual(read.count, 4)
        XCTAssertEqual(read.items.map(\.kind), [.secret, .handoff, .question, .approval])
        XCTAssertEqual(read.items[2].choices, ["Friday", "Monday"])
        XCTAssertEqual(read.items[2].recommended, "Friday")
        XCTAssertNil(read.items[3].choices, "left out when there is none")
        XCTAssertEqual(read.items[3].threadId, "thr_mf3k2w00_9d8e7f")
        XCTAssertEqual(read.items[3].runId, "run_mf3k2w9z_a1b2c3")
    }

    func testAKindThisBuildDoesNotKnowIsKeptAsOtherAndAnItemThatDoesNotReadIsLeftOutButCounted() throws {
        let read: NeedsYouList = try list(
            """
            {"count": 3, "items": [
             {"id": "apr_a", "kind": "approval", "run_id": "r", "thread_id": "t", "agent": "Dot", "title": "Run: ls", "since": 3, "route": "x"},
             {"id": "apr_b", "kind": "review", "run_id": "r", "thread_id": "t", "agent": "Dot", "title": "Look at this", "since": 2, "route": "x"},
             {"id": "apr_c", "kind": "approval", "title": 17}]}
            """)
        XCTAssertEqual(read.count, 3, "the badge says how many things wait, whatever this build can draw")
        XCTAssertEqual(read.items.map(\.id), ["apr_a", "apr_b"])
        XCTAssertEqual(read.items[1].kind, .other)
    }

    func testTheRowsAreNewestFirstWhateverOrderTheyCameIn() {
        let rows: [NeedsYouRow] = NeedsYouRules.rows(
            from: NeedsYouList(
                count: 3,
                items: [
                    item(.approval, id: "apr_old", since: 10), item(.question, id: "apr_new", title: "Which?", since: 30),
                    item(.approval, id: "apr_mid", since: 20),
                ]))
        XCTAssertEqual(rows.map(\.id), ["apr_new", "apr_mid", "apr_old"])
    }

    func testARowSaysWhoAsksAndInWhichChat() {
        let rows: [NeedsYouRow] = NeedsYouRules.rows(
            from: NeedsYouList(count: 1, items: [item(.question, title: "Which?", agent: "Scout")]), chatTitle: { $0 == "thr_1" ? "Deploys" : nil })
        XCTAssertEqual(rows[0].who, "Scout")
        XCTAssertEqual(rows[0].chat, "Deploys")
        XCTAssertEqual(NeedsYouRules.rows(from: NeedsYouList(count: 1, items: [item(.approval, agent: "")]))[0].who, "Dot", "no name: Dot asks")
    }

    // MARK: An approval

    func testAnApprovalSaysWhatDotWantsToDo() {
        let ask: NeedsYouRow.Ask = row(.approval, title: "Run: make deploy (deploys the site) — aggressive preset still asks for: network").ask
        XCTAssertEqual(ask, .approval(title: "Run a command", command: "make deploy", why: "deploys the site; network", mayAllow: true))
    }

    func testAnApprovalWhoseLineSaysNothingOfItsToolIsStillPlain() {
        let ask: NeedsYouRow.Ask = row(.approval, title: "Write notes.md").ask
        XCTAssertEqual(ask, .approval(title: "Dot wants to do this", command: "Write notes.md", why: nil, mayAllow: true))
    }

    func testAnApprovalIsAnsweredThroughTheRouteTheChatUses() {
        let approval: NeedsYouRow = row(.approval)
        XCTAssertEqual(
            approval.decision(approve: true), .decision(approval: "apr_1", run: "run_1", thread: "thr_1", approve: true, note: ""))
        XCTAssertEqual(
            approval.decision(approve: false), .decision(approval: "apr_1", run: "run_1", thread: "thr_1", approve: false, note: ""))
    }

    /// The daemon cuts a title at 120 characters and says nothing of it: a command shown to the end might be the start of a
    /// longer one, and nobody allows what they did not read.
    func testALineThatMayBeCutIsNotAllowedFromTheListButMayBeDenied() {
        let long: String = "Run: " + String(repeating: "x", count: NeedsYouRules.titleLimit - 5)
        XCTAssertEqual(long.count, NeedsYouRules.titleLimit)
        let cut: NeedsYouRow = row(.approval, title: long)
        guard case .approval(_, _, _, let mayAllow) = cut.ask else { return XCTFail("an approval") }
        XCTAssertFalse(mayAllow)
        XCTAssertNil(cut.decision(approve: true), "Allow once is not offered for a line that may be the start of a longer one")
        XCTAssertNotNil(cut.decision(approve: false), "a no is always safe")
        let whole: NeedsYouRow = row(.approval, title: "Run: " + String(repeating: "x", count: NeedsYouRules.titleLimit - 6))
        XCTAssertNotNil(whole.decision(approve: true), "one character short of the cut was shown whole")
    }

    func testAnEmptyLineIsNotAllowed() {
        XCTAssertNil(row(.approval, title: "").decision(approve: true))
    }

    // MARK: A question

    func testAQuestionShowsItsChoicesWithTheRecommendedOneMarked() {
        let ask: NeedsYouRow.Ask = row(.question, title: "Which day should it ship?", choices: ["Friday", "Monday"], recommended: "Friday").ask
        XCTAssertEqual(
            ask,
            .question(
                text: "Which day should it ship?",
                choices: [NeedsYouRow.Choice(label: "Friday", recommended: true), NeedsYouRow.Choice(label: "Monday", recommended: false)]))
    }

    func testAChoiceIsTheAnswerAsItsLabelAndSkipIsADenial() {
        let question: NeedsYouRow = row(.question, title: "Which?", choices: ["Friday", "Monday"])
        XCTAssertEqual(
            question.decision(approve: true, note: "Monday"),
            .decision(approval: "apr_1", run: "run_1", thread: "thr_1", approve: true, note: "Monday"))
        XCTAssertEqual(
            question.decision(approve: false, note: "ignored"),
            .decision(approval: "apr_1", run: "run_1", thread: "thr_1", approve: false, note: ""), "a skip carries no words")
    }

    func testAQuestionWithNoChoicesTakesWordsAndNotNothing() {
        let question: NeedsYouRow = row(.question, title: "Which folder?")
        guard case .question(_, let choices) = question.ask else { return XCTFail("a question") }
        XCTAssertEqual(choices, [])
        XCTAssertNil(question.decision(approve: true, note: "   \n"), "an empty answer is no answer")
        XCTAssertEqual(
            question.decision(approve: true, note: "  ~/Archive  "),
            .decision(approval: "apr_1", run: "run_1", thread: "thr_1", approve: true, note: "~/Archive"), "what was written, trimmed")
    }

    // MARK: A secret and the steps only the owner can do

    /// The daemon's decision: a secret is never answered on a phone. Its row says where, and has no route to answer by.
    func testASecretIsNeverAnsweredOnThePhone() {
        let secret: NeedsYouRow = row(.secret, title: "Dot asks for a secret")
        XCTAssertEqual(secret.ask, .secret)
        XCTAssertNil(secret.decision(approve: true, note: "hunter2"), "no value goes anywhere")
        XCTAssertNil(secret.decision(approve: true))
        XCTAssertNil(secret.decision(approve: false))
        XCTAssertEqual(NeedsYouRules.preview(of: secret), "Dot needs a secret: enter it on your Mac")
    }

    func testAHandOffAndAKindThisBuildDoesNotKnowAreAnsweredInTheChat() {
        let handoff: NeedsYouRow = row(.handoff, title: "Dot needs you on bank.example")
        XCTAssertEqual(handoff.ask, .handoff(line: "Dot is waiting for you in the browser on bank.example"))
        XCTAssertNil(handoff.decision(approve: true))
        XCTAssertNil(handoff.decision(approve: false))
        let other: NeedsYouRow = row(.other, title: "Look at this")
        XCTAssertEqual(other.ask, .other(line: "Look at this"))
        XCTAssertNil(other.decision(approve: true, note: "yes"))
    }

    // MARK: The answer on its way, and ids

    func testARowWhoseAnswerWaitsInTheOutboxTakesNoSecondAnswer() {
        let waiting: NeedsYouRow = row(.approval, answering: true)
        XCTAssertTrue(waiting.answering)
        XCTAssertNil(waiting.decision(approve: true))
        XCTAssertNil(waiting.decision(approve: false))
    }

    func testTheApprovalsWhoseAnswerWaitsAreFoundInTheOutbox() {
        let outbox: [OutboxItem] = [
            OutboxItem(id: "k1", action: .decision(approval: "apr_1", run: "run_1", thread: "thr_1", approve: true, note: ""), createdAt: 1),
            OutboxItem(id: "k2", action: .stop(run: "run_1", thread: "thr_1"), createdAt: 2),
            OutboxItem(id: "k3", action: .decision(approval: "apr_9", run: "run_9", thread: "thr_9", approve: false, note: ""), createdAt: 3),
        ]
        XCTAssertEqual(NeedsYouRules.answering(in: outbox), ["apr_1", "apr_9"])
    }

    func testAnIdThatIsNoApprovalIdIsNoPathToAnswerOn() {
        XCTAssertNil(row(.approval, id: "../approvals/x").decision(approve: true))
        XCTAssertNil(row(.question, title: "Which?", choices: ["A"], id: "apr 1").decision(approve: true, note: "A"))
        XCTAssertNil(row(.approval, id: "").decision(approve: false))
    }

    // MARK: Words, and when to read again

    func testTheEntrySaysHowManyThingsWait() {
        XCTAssertEqual(NeedsYouRules.entry(count: 1), "1 thing waits on you")
        XCTAssertEqual(NeedsYouRules.entry(count: 3), "3 things wait on you")
    }

    func testThePreviewIsWhatTheNewestAsks() throws {
        let rows: [NeedsYouRow] = NeedsYouRules.rows(from: try list())
        XCTAssertEqual(rows.map(NeedsYouRules.preview(of:)), [
            "Dot needs a secret: enter it on your Mac", "Dot is waiting for you in the browser on bank.example", "Which day should it ship?",
            "Run a command: make deploy",
        ])
    }

    func testOnlyTheNoticesThatChangeWhatWaitsReadItAgain() {
        for kind in ["approval_requested", "approval_decided", "question_left", "run_finished"] {
            XCTAssertTrue(NeedsYouRules.refreshes(onNotice: kind), kind)
        }
        for kind in ["run_started", "run_queued", "queue_changed", "reaction_added", "message"] {
            XCTAssertFalse(NeedsYouRules.refreshes(onNotice: kind), kind)
        }
    }

    func testNoWordOfARowIsTheEnginesOwn() throws {
        let jargon: [String] = ["daemon", "token", "payload", "endpoint", "api", "mcp"]
        for row in NeedsYouRules.rows(from: try list()) {
            let said: String = NeedsYouRules.preview(of: row).lowercased()
            for word in jargon { XCTAssertFalse(said.contains(word), "\(said): \(word)") }
        }
    }
}
