import XCTest

@testable import DotEngine

/// The chat screen's pure rules (wave N2): durations, the ask dock, the composer's buttons, the queue's edit, markdown.
final class ChatRulesTests: XCTestCase {
    func testDurationsAreHoursMinutesSecondsWithSecondsDroppedPastTenHours() {
        XCTAssertEqual(DurationParts.of(2475), DurationParts(hours: 0, minutes: 41, seconds: 15, showsSeconds: true))
        XCTAssertEqual(DurationParts.of(6443), DurationParts(hours: 1, minutes: 47, seconds: 23, showsSeconds: true))
        XCTAssertEqual(DurationParts.of(-3), DurationParts(hours: 0, minutes: 0, seconds: 0, showsSeconds: true))
        XCTAssertFalse(DurationParts.of(36_000).showsSeconds)
    }

    private func record(_ id: String, at: Double, severity: String = "medium", run: String = "run_a", args: [String: JSONValue] = [:],
        summary: String = "Run the command", kept: Double? = nil, tool: String = "shell") -> ApprovalRecord
    {
        ApprovalRecord(id: id, runId: run, createdAt: at, status: "pending", tool: tool, summary: summary, severity: severity, args: args, note: nil, keptUntil: kept)
    }

    func testTheDockKeepsAStableOrderAndTellsEachPhase() {
        let asks: [ApprovalRecord] = [record("apr_b", at: 2), record("apr_a", at: 1), record("apr_c", at: 3, run: "run_helper")]
        var offline: OutboxItem = OutboxItem(id: "k1", action: .decision(approval: "apr_b", run: "run_a", thread: "thr_1", approve: true, note: "", remember: false), createdAt: 5)
        offline.phase = .waiting
        let entries: [DockEntry] = dockEntries(
            asks: asks, chatRuns: ["run_a"], outbox: [offline], online: false, local: ["apr_c": .submitting], shown: [], decided: [:], answeredHere: [])
        XCTAssertEqual(entries.map(\.id), ["apr_a", "apr_b", "apr_c"], "oldest first, whatever order the view gave")
        XCTAssertEqual(entries.map(\.phase), [.pending, .queuedOffline, .submitting])
        XCTAssertEqual(entries.map(\.ask.fromHelper), [false, false, true])
    }

    func testAnAskThatLeftWithoutAnAnswerFromHereSaysAnsweredElsewhereOrExpired() {
        let shown: [DockEntry] = dockEntries(
            asks: [record("apr_a", at: 1), record("apr_b", at: 2), record("apr_c", at: 3)], chatRuns: ["run_a"], outbox: [], online: true,
            local: ["apr_c": .submitting], shown: [], decided: [:], answeredHere: [])
        let after: [DockEntry] = dockEntries(
            asks: [], chatRuns: ["run_a"], outbox: [], online: true, local: ["apr_c": .submitting], shown: shown, decided: ["apr_b": "expired"], answeredHere: [])
        XCTAssertEqual(after.map(\.id), ["apr_a", "apr_b"], "the one answered here goes at once")
        XCTAssertEqual(after.map(\.phase), [.answeredElsewhere, .expired])
        // Answered on this phone, its outbox item already gone when the new view came (seen end to end).
        let mine: [DockEntry] = dockEntries(
            asks: [], chatRuns: ["run_a"], outbox: [], online: true, local: [:], shown: shown, decided: [:], answeredHere: ["apr_a"])
        XCTAssertEqual(mine.map(\.id), ["apr_b"], "never 'answered elsewhere' for an answer of this phone")
    }

    func testARefusedAnswerSaysTheDaemonsWords() {
        var refused: OutboxItem = OutboxItem(id: "k1", action: .decision(approval: "apr_a", run: "run_a", thread: "thr_1", approve: true, note: "", remember: false), createdAt: 5)
        refused.phase = .failed
        refused.failure = .refused(code: 409, detail: "already decided")
        let entries: [DockEntry] = dockEntries(
            asks: [record("apr_a", at: 1)], chatRuns: ["run_a"], outbox: [refused], online: true, local: [:], shown: [], decided: [:], answeredHere: [])
        XCTAssertEqual(entries.first?.phase, .refused("already decided"))
    }

    func testAnAskReadsItsKindTextOptionsAndWhetherAlwaysAllowIsOffered() {
        let question: Ask = ask(
            of: record("q", at: 1, severity: "question", args: [
                "question": .string("Which day?"), "multiple": .bool(true),
                "options": .array([.object(["label": .string("Monday"), "recommended": .bool(true)]), .string("Tuesday")]),
            ]), chatRuns: ["run_a"])
        XCTAssertEqual(question.kind, .question)
        XCTAssertEqual(question.text, "Which day?")
        XCTAssertEqual(question.options.map(\.label), ["Monday", "Tuesday"])
        XCTAssertTrue(question.options[0].recommended)
        XCTAssertTrue(question.multiple)
        XCTAssertFalse(question.rememberable)
        let command: Ask = ask(of: record("a", at: 1, args: ["target": .string("rm -rf build -- --force"), "kind": .string("shell")]), chatRuns: ["run_a"])
        XCTAssertTrue(command.rememberable)
        XCTAssertEqual(command.target, "rm -rf build -- --force", "the whole command, never cut at a dash")
        XCTAssertFalse(ask(of: record("m", at: 1, args: ["flags": .array([.string("financial")])]), chatRuns: []).rememberable)
        XCTAssertFalse(ask(of: record("k", at: 1, kept: 9), chatRuns: []).rememberable, "a kept step is allowed once")
        XCTAssertFalse(ask(of: record("t", at: 1, args: ["kind": .string("shell")], tool: "applescript"), chatRuns: []).rememberable)
        let secret: Ask = ask(of: record("s", at: 1, severity: "secret", args: ["label": .string("GitHub token"), "why": .string("To push")]), chatRuns: [])
        XCTAssertEqual(secret.kind, .secret)
        XCTAssertEqual(secret.text, "GitHub token")
        XCTAssertEqual(secret.why, "To push")
        XCTAssertEqual(chosenAnswer(chosen: ["Monday", "Tuesday"], written: " after 5 "), "Monday; Tuesday\nafter 5")
    }

    func testTheComposersButtonsSayWhatAPressDoes() {
        XCTAssertEqual(composerButtons(hasContent: false, runActive: false, followUp: .queue, editing: false), ComposerButtons(primary: .send, alternate: nil, enabled: false))
        XCTAssertEqual(composerButtons(hasContent: true, runActive: false, followUp: .queue, editing: false), ComposerButtons(primary: .send, alternate: nil, enabled: true))
        XCTAssertEqual(composerButtons(hasContent: false, runActive: true, followUp: .queue, editing: false), ComposerButtons(primary: .stop, alternate: nil, enabled: true))
        XCTAssertEqual(composerButtons(hasContent: true, runActive: true, followUp: .queue, editing: false), ComposerButtons(primary: .queue, alternate: .steer, enabled: true))
        XCTAssertEqual(composerButtons(hasContent: true, runActive: true, followUp: .steer, editing: false), ComposerButtons(primary: .steer, alternate: .queue, enabled: true))
        XCTAssertEqual(composerButtons(hasContent: true, runActive: true, followUp: .queue, editing: true), ComposerButtons(primary: .confirmEdit, alternate: nil, enabled: true))
    }

    func testSlashAndAtOfferTheWordBeingTyped() {
        XCTAssertEqual(completing("ask @res", with: "@research"), "ask @research ")
    }

    private let queue: QueueView = QueueView(
        held: false,
        messages: [QueuedMessageView(id: "q1", threadId: "thr_1", position: 1, text: "First", createdAt: 1, source: "phone"),
            QueuedMessageView(id: "q2", threadId: "thr_1", position: 2, text: "Second", createdAt: 2, source: "phone")],
        besideRunId: "run_a", besideThreadId: nil)

    func testAQueuedEditSavesKeepsTheLiveDraftAndKnowsWhenTheMessageLeft() {
        var edit: QueueEdit = QueueEdit(message: queue.messages[0], liveDraft: "my half-written words")
        XCTAssertEqual(edit.text, "First")
        XCTAssertEqual(confirm(edit, queue: queue), .unchanged)
        edit.text = "First, but better "
        XCTAssertEqual(confirm(edit, queue: queue), .save(message: "q1", text: "First, but better"))
        XCTAssertEqual(edit.liveDraft, "my half-written words", "Cancel puts it back")
        let started: QueueView = QueueView(held: false, messages: [queue.messages[1]], besideRunId: "run_b", besideThreadId: nil)
        XCTAssertEqual(confirm(edit, queue: started), .gone(text: "First, but better "))
        edit.text = "  "
        XCTAssertEqual(confirm(edit, queue: queue), .empty)
    }

    func testReorderMovesOneMessageAndKeepsTheRest() {
        XCTAssertEqual(reordered(["a", "b", "c"], moving: "c", to: 0), ["c", "a", "b"])
        XCTAssertEqual(reordered(["a", "b", "c"], moving: "a", to: 9), ["b", "c", "a"])
        XCTAssertEqual(reordered(["a", "b"], moving: "x", to: 0), ["a", "b"])
    }

    func testTheChatsPhaseSaysWaitingOnYouOverWorking() {
        XCTAssertEqual(chatPhase(of: nil), .idle)
        let thread: ThreadRecord = ThreadRecord(id: "thr_1", title: "Chat", source: "ui", createdAt: 1, updatedAt: 1, agentId: nil)
        let working: RunRecord = RunRecord(id: "run_a", threadId: "thr_1", goal: "g", mode: "task", model: "m", status: "running", createdAt: 7,
            finishedAt: nil, result: "", parentRunId: nil, source: "ui", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        let view: (Bool) -> ThreadView = { asked in
            ThreadView(revision: 1, thread: thread, summary: nil, runs: [working], older: false, newest: nil, newestEvents: [], newestEventsCut: false,
                queue: QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil),
                asks: asked ? [self.record("apr", at: 1)] : [], helpers: [], background: [], reactions: [], drafts: [])
        }
        XCTAssertEqual(chatPhase(of: view(false)), .working(since: 7))
        XCTAssertEqual(chatPhase(of: view(true)), .waitingOnYou, "an ask is never Working")
        XCTAssertTrue(isAtEnd(distanceFromEnd: 0))
        XCTAssertTrue(isAtEnd(distanceFromEnd: -150))
        XCTAssertFalse(isAtEnd(distanceFromEnd: 120))
    }

    func testMarkdownBlocks() {
        let blocks: [MarkdownBlock] = markdownBlocks("""
            # Title
            Some **bold** words
            and more.

            - one
              continued
            - two
            1. first

            | A | B |
            |---|:-:|
            | 1 | 2 |
            | 3 |

            > quoted

            ---
            ```swift
            let x = 1
            """)
        XCTAssertEqual(blocks, [
            .heading(level: 1, text: "Title"),
            .paragraph("Some **bold** words\nand more."),
            .listItem(depth: 0, number: nil, text: "one\ncontinued"),
            .listItem(depth: 0, number: nil, text: "two"),
            .listItem(depth: 0, number: 1, text: "first"),
            .table(header: ["A", "B"], rows: [["1", "2"], ["3", ""]]),
            .quote("quoted"),
            .rule,
            .code(language: "swift", text: "let x = 1"),
        ])
    }

    func testAHelpersTitleIsItsFirstSentenceWithoutIdsAndAtMostFortyCharacters() {
        XCTAssertEqual(shortTitle("wait for the test, then say done"), "Wait for the test, then say done")
        XCTAssertEqual(shortTitle("Check run_ab12cd for errors. Then report."), "Check for errors")
        XCTAssertEqual(shortTitle("Read every file under the project folder and list the ones that import the old parser"), "Read every file under the project…")
        XCTAssertLessThanOrEqual(shortTitle(String(repeating: "word ", count: 30)).count, 40)
        XCTAssertEqual(shortTitle("Görev: hafıza notlarını birleştirmek.\n\nGirdi dosyası: C8.md"), "Hafıza notlarını birleştirmek")
        XCTAssertEqual(shortTitle("Fix this: the build"), "Fix this")
    }

    func testAHelpersChatShowsItsStepsAsTheChatThatStartedItUnlessItChoseItself() {
        let parents: [String: String] = ["thr_h": "thr_p", "thr_hh": "thr_h", "thr_a": "thr_b", "thr_b": "thr_a"]
        XCTAssertFalse(showsSteps(of: "thr_h", overrides: ["thr_p": false], parents: parents, everywhere: true))
        XCTAssertFalse(showsSteps(of: "thr_hh", overrides: ["thr_p": false], parents: parents, everywhere: true))
        XCTAssertTrue(showsSteps(of: "thr_h", overrides: ["thr_p": false, "thr_h": true], parents: parents, everywhere: true))
        XCTAssertTrue(showsSteps(of: "thr_h", overrides: [:], parents: parents, everywhere: true))
        XCTAssertFalse(showsSteps(of: "thr_x", overrides: [:], parents: parents, everywhere: false))
        XCTAssertTrue(showsSteps(of: "thr_a", overrides: [:], parents: parents, everywhere: true))
    }

    func testAnAcceptedQueuedMessageFollowsItsEditSoTheOverlayGoesOnceItsRunShows() {
        var chat: ChatProjection = ChatProjection(threadId: "thr_1")
        let thread: ThreadRecord = ThreadRecord(id: "thr_1", title: "Chat", source: "ui", createdAt: 1, updatedAt: 1, agentId: nil)
        let view: (QueueView, [RunRecord]) -> ThreadView = { queue, runs in
            ThreadView(revision: 1, thread: thread, summary: nil, runs: runs, older: false, newest: nil, newestEvents: [], newestEventsCut: false,
                queue: queue, asks: [], helpers: [], background: [], reactions: [], drafts: [])
        }
        let sent: AcceptedMessage = AcceptedMessage(key: "k", thread: "thr_1", text: "First", kind: .queue, queuedId: "q1", runId: nil, at: 1, runsBefore: ["run_a"])
        chat.view = view(queue, [])
        let edited: QueueView = QueueView(
            held: false, messages: [QueuedMessageView(id: "q1", threadId: "thr_1", position: 1, text: "First, edited", createdAt: 1, source: "phone")],
            besideRunId: "run_a", besideThreadId: nil)
        chat.view = view(edited, [])
        let followed: AcceptedMessage = following(sent, in: chat)
        XCTAssertEqual(followed.text, "First, edited")
        let started: RunRecord = RunRecord(id: "run_b", threadId: "thr_1", goal: "First, edited", mode: "task", model: "m", status: "running",
            createdAt: 2, finishedAt: nil, result: "", parentRunId: nil, source: "phone", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
        chat.view = view(QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil), [started])
        XCTAssertNil(delivery(of: followed, in: chat), "the run with the edited words shows: the overlay goes")
        XCTAssertEqual(delivery(of: sent, in: chat), .delivered, "with the old words it would have stayed as Sent")
    }

    func testOneSubmitSendsEveryAnsweredQuestionAndTheRecommendedOptionComesFirst() {
        let options: [AskOption] = [AskOption(label: "Keep", detail: "", recommended: false), AskOption(label: "Close both", detail: "", recommended: true)]
        XCTAssertEqual(shownOptions(options).map(\.label), ["Close both", "Keep"])
        let one: QuestionDraft = QuestionDraft.empty.picking("Keep", multiple: false).picking("Close both", multiple: false)
        XCTAssertEqual(one.chosen, ["Close both"], "one answer: the new pick replaces the old")
        let several: QuestionDraft = QuestionDraft.empty.picking("Keep", multiple: true).picking("Close both", multiple: true)
        XCTAssertEqual(several.chosen, ["Keep", "Close both"])
        let asks: [ApprovalRecord] = [
            record("q1", at: 1, severity: "question", args: ["question": .string("A?"), "options": .array([.string("Keep"), .string("Close both")])]),
            record("q2", at: 2, severity: "question", args: ["question": .string("B?")]),
            record("q3", at: 3, severity: "question", args: ["question": .string("C?")]),
            record("a1", at: 4),
        ]
        let entries: [DockEntry] = dockEntries(
            asks: asks, chatRuns: ["run_a"], outbox: [], online: true, local: ["q3": .submitting], shown: [], decided: [:], answeredHere: [])
        let drafts: [String: QuestionDraft] = [
            "q1": QuestionDraft(chosen: ["Close both", "Keep"], written: "today"), "q2": .empty, "q3": QuestionDraft(chosen: [], written: "x"),
            "a1": QuestionDraft(chosen: [], written: "y"),
        ]
        let sent = submittedAnswers(entries, drafts: drafts)
        XCTAssertEqual(sent.map(\.ask.id), ["q1"], "unanswered, already sending and non-questions are left")
        XCTAssertEqual(sent.first?.answer, "Keep; Close both\ntoday", "the labels in Dot's order, then the words")
    }

    private func runRecord(_ id: String, parent: String?) -> RunRecord {
        RunRecord(
            id: id, threadId: "thr", goal: "g", mode: "task", model: "m", status: "running", createdAt: 1, finishedAt: nil, result: "", parentRunId: parent,
            source: "ui", effort: nil, inputTokens: nil, outputTokens: nil, approvals: nil, followsReaction: nil)
    }

    func testAChatOwnsTheRunsNoRunOfItStartedAndAHelpersOwnChatOwnsItsRun() {
        let parentChat: [RunRecord] = [runRecord("run_b", parent: nil), runRecord("run_h1", parent: "run_b")]
        XCTAssertEqual(ownRuns(in: parentChat, helperRunIds: []).map(\.id), ["run_b"], "a helper's run is told by the helpers' group")
        // A window that lost the parent run still hides the helper the view lists.
        XCTAssertEqual(ownRuns(in: [runRecord("run_h1", parent: "run_gone")], helperRunIds: ["run_h1"]).map(\.id), [])
        // The helper's own chat: its parent run is in another chat.
        XCTAssertEqual(ownRuns(in: [runRecord("run_h1", parent: "run_b")], helperRunIds: []).map(\.id), ["run_h1"])
    }

    /// Item 2040: a card of several secrets is read with each field's kind and the daemon's limits; one of one value keeps
    /// its one box. Save waits until every field holds a value long enough and not too long.
    func testACardOfSeveralSecretsIsReadWithItsFieldsAndSaveWaitsForEach() {
        func field(_ name: String, _ label: String, _ kind: String, _ most: Int) -> JSONValue {
            .object(["name": .string(name), "label": .string(label), "kind": .string(kind), "min_chars": .int(8), "max_chars": .int(most)])
        }
        let card: Ask = ask(
            of: record("s", at: 1, severity: "secret", args: [
                "label": .string("App Store Connect API key"), "why": .string("To upload the build."),
                "fields": .array([field("ASC_ISSUER_ID", "Issuer ID", "text", 8192), field("ASC_KEY", "Key file", "file", 16384)]),
            ], tool: "ask_secret"), chatRuns: ["run_a"])
        XCTAssertEqual(card.kind, .secret)
        XCTAssertEqual(card.secretFields.map(\.name), ["ASC_ISSUER_ID", "ASC_KEY"])
        XCTAssertEqual(card.secretFields.map(\.kind), [.text, .file])
        XCTAssertEqual(card.secretFields.map(\.most), [8192, 16384])
        let single: Ask = ask(
            of: record("t", at: 1, severity: "secret", args: [
                "name": .string("DEMO_TOKEN"), "label": .string("Demo"), "fields": .array([field("DEMO_TOKEN", "Demo", "secret", 8192)]),
            ], tool: "ask_secret"), chatRuns: ["run_a"])
        XCTAssertEqual(single.secretFields, [], "a card of one value keeps its one box")
        let fields: [SecretField] = card.secretFields
        XCTAssertFalse(secretFieldsReady(fields, values: [:]))
        XCTAssertFalse(secretFieldsReady(fields, values: ["ASC_ISSUER_ID": "69a6de70-made-up"]), "a field left empty")
        XCTAssertFalse(secretFieldsReady(fields, values: ["ASC_ISSUER_ID": "short", "ASC_KEY": String(repeating: "x", count: 40)]))
        XCTAssertFalse(secretFieldsReady(fields, values: ["ASC_ISSUER_ID": "69a6de70-made-up", "ASC_KEY": String(repeating: "x", count: 16385)]))
        XCTAssertTrue(secretFieldsReady(fields, values: ["ASC_ISSUER_ID": "69a6de70-made-up", "ASC_KEY": String(repeating: "x", count: 40)]))
    }
}
