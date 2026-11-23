import XCTest
@testable import Dot

// The pure rules of slice 2: older runs in the state, a tool step's detail, a message's actions, dictation's gate, links.

final class OlderRunsTests: XCTestCase {
    private func state() -> ChatState {
        var state: ChatState = ChatState()
        state.addRun(makeRun("run_5", goal: "five", status: "running", created: 5000))
        return state
    }

    func testOlderRunsGoBeforeEveryRowHeldOldestFirst() {
        var held: ChatState = state()
        let added: Int = held.prependOlder([
            makeRun("run_3", goal: "three", status: "done", created: 3000, result: "answer three"),
            makeRun("run_2", goal: "two", status: "done", created: 2000, result: "answer two"),
        ])
        XCTAssertEqual(added, 2)
        XCTAssertEqual(held.items.map(\.id), ["u:run_2", "r:run_2", "u:run_3", "r:run_3", "u:run_5"])
        XCTAssertEqual(held.rootRunIds, ["run_2", "run_3", "run_5"])
        XCTAssertEqual(held.newestRunId, "run_5", "the newest run is still the chat's run")
        XCTAssertTrue(held.isWorking)
    }

    func testARunHeldAlreadyAndAHelpersRunAreSkipped() {
        var held: ChatState = state()
        let added: Int = held.prependOlder([
            makeRun("run_5", goal: "five", status: "running", created: 5000),
            makeRun("run_4", goal: "helper", status: "done", parent: "run_5", created: 4000, result: "x"),
        ])
        XCTAssertEqual(added, 0)
        XCTAssertEqual(held.items.map(\.id), ["u:run_5"])
        XCTAssertEqual(held.prependOlder([]), 0)
    }

    func testReadingTheSamePageTwiceAddsNothingTheSecondTime() {
        var held: ChatState = state()
        let page: [RunRecord] = [makeRun("run_4", goal: "four", status: "done", created: 4000, result: "four!")]
        XCTAssertEqual(held.prependOlder(page), 1)
        XCTAssertEqual(held.prependOlder(page), 0)
        XCTAssertEqual(held.items.map(\.id), ["u:run_4", "r:run_4", "u:run_5"])
    }

    func testAFollowUpOfAReactionHasNoMessageOfTheOwnersButItsAnswerShows() {
        var held: ChatState = state()
        var follow: RunRecord = makeRun("run_4", goal: "do it again", status: "done", created: 4000, result: "again!")
        follow.followsReaction = "again"
        held.prependOlder([follow])
        XCTAssertEqual(held.items.map(\.id), ["r:run_4", "u:run_5"])
    }

    func testTheRowsAfterAreNotMovedSoTheirIdsStayForTheListToKeyOn() {
        var held: ChatState = state()
        held.apply(event(3, "run_5", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object([:])]))
        let before: [String] = held.items.map(\.id)
        held.prependOlder([makeRun("run_1", goal: "one", status: "done", created: 1000, result: "")])
        XCTAssertEqual(Array(held.items.map(\.id).suffix(before.count)), before)
        // A run that was cancelled with no answer still has its message.
        XCTAssertEqual(held.items.first?.id, "u:run_1")
    }

    func testAToolRowRemembersTheEventItsDetailIsReadFrom() {
        var held: ChatState = state()
        held.apply(event(41, "run_5", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object(["command": .string("ls")])]))
        guard case .tool(let row) = held.items.last?.kind else { return XCTFail("a tool row") }
        XCTAssertEqual(row.eventId, 41)
        XCTAssertEqual(row.callId, "c1")
    }
}

func olderRunJSON(_ n: Int, parent: String? = nil) -> String {
    let parentJSON: String = parent.map { "\"\($0)\"" } ?? "null"
    return #"{"id":"run_\#(n)","thread_id":"thr_1","goal":"goal \#(n)","mode":"task","model":"m","status":"done","created_at":\#(1000 + n),"finished_at":\#(1000 + n),"result":"result \#(n)","steps":1,"parent_run_id":\#(parentJSON),"source":"ui","agent_id":null,"follows_reaction":null}"#
}

final class ChatSessionOlderTests: XCTestCase {
    private var directory: URL!

    override func setUp() {
        directory = FileManager.default.temporaryDirectory.appending(path: "dot-older-\(UUID().uuidString)")
        BodyStub.seen = []
        BodyStub.respond = nil
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: directory)
        BodyStub.respond = nil
    }

    private func session() async -> (ChatSession, AsyncStream<ChatOutput>) {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BodyStub.self]
        let host: DaemonHost = DaemonHost(name: "mac.example.ts.net", origin: URL(string: "https://mac.example.ts.net")!)
        let api: APIClient = APIClient(host: host, key: "phone.a.b", session: URLSession(configuration: configuration))
        let cache: ChatCache = ChatCache(directory: directory)
        // The chat as the phone last held it: its newest twelve runs (runs 40 to 51).
        let runs: [RunRecord] = (40...51).map { makeRun("run_\($0)", goal: "goal \($0)", status: "done", created: Double(1000 + $0), result: "result \($0)") }
        await cache.saveChat(StoredChat(threadId: "thr_1", runs: runs, helpers: [], events: [], savedAt: 1))
        let session: ChatSession = ChatSession(threadId: "thr_1", api: api, cache: cache)
        return (session, await session.start())
    }

    private func firstState(_ stream: AsyncStream<ChatOutput>) async -> ChatState? {
        for await output in stream {
            if case .state(let state) = output { return state }
        }
        return nil
    }

    func testAPageOfOlderRunsStartsBeforeTheOldestHeldAndPutsThemAbove() async throws {
        // The daemon is unreachable for the catch-up (500) and has older runs for the page.
        BodyStub.respond = { seen in
            if seen.path == "/api/runs", let query: String = seen.query, query.contains("before=") {
                return (200, "[" + (30..<40).reversed().map { olderRunJSON($0) }.joined(separator: ",") + "]")
            }
            return (500, "down")
        }
        let (session, stream): (ChatSession, AsyncStream<ChatOutput>) = await session()
        let loaded: ChatState? = await firstState(stream)
        XCTAssertEqual(loaded?.rootRunIds.count, 12, "drawn from disk first")
        let outcome: OlderOutcome = await session.loadOlder()
        // Ten runs came for a page of twenty: that is the end of the chat.
        XCTAssertEqual(outcome, .loaded(added: 10, exhausted: true))
        let asked: BodyStub.Seen? = BodyStub.seen.last { $0.query?.contains("before=") == true }
        let items: [URLQueryItem] = URLComponents(string: "https://x/?\(asked?.query ?? "")")?.queryItems ?? []
        XCTAssertEqual(Double(items.first { $0.name == "before" }?.value ?? ""), 1040, "the page starts at the oldest run held")
        XCTAssertEqual(items.first { $0.name == "limit" }?.value, "20")
        // Nothing more is asked after the end.
        let again: OlderOutcome = await session.loadOlder()
        XCTAssertEqual(again, .nothing)
        await session.stop()
    }

    func testAFailedPageIsSaidAndMayBeTriedAgain() async {
        BodyStub.respond = { _ in (500, "down") }
        let (session, stream): (ChatSession, AsyncStream<ChatOutput>) = await session()
        _ = await firstState(stream)
        let outcome: OlderOutcome = await session.loadOlder()
        XCTAssertEqual(outcome, .failed)
        BodyStub.respond = { seen in
            seen.query?.contains("before=") == true ? (200, "[" + olderRunJSON(30) + "]") : (500, "down")
        }
        let retried: OlderOutcome = await session.loadOlder()
        XCTAssertEqual(retried, .loaded(added: 1, exhausted: true))
        await session.stop()
    }

    func testAPageOfOnlyHelpersIsReadPastToTheNextOne() async {
        let pages: LockedCounter = LockedCounter()
        BodyStub.respond = { seen in
            guard seen.query?.contains("before=") == true else { return (500, "down") }
            // A full page of twenty that holds helpers only, then one run of the chat's own.
            if pages.next() == 1 {
                return (200, "[" + (0..<20).map { olderRunJSON(10 + $0, parent: "run_x") }.joined(separator: ",") + "]")
            }
            return (200, "[" + olderRunJSON(5) + "]")
        }
        let (session, stream): (ChatSession, AsyncStream<ChatOutput>) = await session()
        _ = await firstState(stream)
        let outcome: OlderOutcome = await session.loadOlder()
        XCTAssertEqual(outcome, .loaded(added: 1, exhausted: true))
        XCTAssertEqual(BodyStub.seen.filter { $0.query?.contains("before=") == true }.count, 2)
        await session.stop()
    }
}

final class LockedCounter: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var value: Int = 0
    func next() -> Int {
        lock.lock()
        defer { lock.unlock() }
        value += 1
        return value
    }
}

final class ToolDetailTests: XCTestCase {
    private let row: ToolRow = ToolRow(callId: "c1", runId: "run_1", tool: "shell", line: "Shell: ls", state: .ok, eventId: 7)

    func testClippingNeverSplitsACharacterAndSaysHowMuchWentAway() {
        XCTAssertEqual(Clipped("short", limit: 10), Clipped("short", limit: 10))
        let whole: Clipped = Clipped("short", limit: 10)
        XCTAssertEqual(whole.text, "short")
        XCTAssertEqual(whole.omitted, 0)
        XCTAssertFalse(whole.isCut)
        // A family emoji is one character of seven scalars: cut at its edge, never inside it.
        let family: String = "👨‍👩‍👧‍👦"
        let cut: Clipped = Clipped(String(repeating: family, count: 5), limit: 3)
        XCTAssertEqual(cut.text, String(repeating: family, count: 3))
        XCTAssertEqual(cut.omitted, 2)
        XCTAssertTrue(cut.isCut)
        XCTAssertEqual(Clipped("e\u{301}e\u{301}e\u{301}", limit: 2).text, "e\u{301}e\u{301}", "an accent stays on its letter")
        XCTAssertEqual(Clipped("", limit: 5).omitted, 0)
        XCTAssertEqual(Clipped("abc", limit: 0).omitted, 3)
    }

    func testArgumentsAreLinesInNameOrderAndALongTextGoesUnderItsName() {
        let args: JSONValue = .object([
            "timeout": .int(30), "command": .string("echo hi"), "script": .string("a\nb"), "env": .object(["X": .string("1")]), "ok": .bool(true),
        ])
        let text: String = ToolDetail.renderArguments(args)
        XCTAssertEqual(text, "command: echo hi\nenv: {\"X\":\"1\"}\nok: true\nscript:\n  a\n  b\ntimeout: 30")
        XCTAssertEqual(ToolDetail.renderArguments(.object([:])), "")
        XCTAssertEqual(ToolDetail.renderArguments(nil), "")
        XCTAssertEqual(ToolDetail.renderArguments(.string("bare")), "\"bare\"")
    }

    func testAHugeArgumentIsClippedAndSaysSo() {
        let huge: String = String(repeating: "x", count: ToolDetailLimits.eachArgument + 500)
        let text: String = ToolDetail.renderArguments(.object(["data": .string(huge)]))
        XCTAssertTrue(text.contains("… (500 more characters)"))
        XCTAssertLessThan(text.count, ToolDetailLimits.eachArgument + 100)
    }

    func testTheDetailJoinsTheCallAndItsResult() {
        let call: EventRecord = event(7, "run_1", "tool_call", ["call_id": .string("c1"), "tool": .string("shell"), "args": .object(["command": .string("ls")])])
        let result: EventRecord = event(8, "run_1", "tool_result", [
            "call_id": .string("c1"), "ok": .bool(true), "text": .string("a.txt\nb.txt"), "seconds": .double(0.4), "target": .string("~/Documents"),
        ])
        let detail: ToolDetail = ToolDetail.make(row: row, call: call, result: result)
        XCTAssertEqual(detail.arguments.text, "command: ls")
        XCTAssertEqual(detail.result?.text, "a.txt\nb.txt")
        XCTAssertEqual(detail.seconds, 0.4)
        XCTAssertEqual(detail.target, "~/Documents")
    }

    func testAStepWithNoResultYetHasNoResultAndAnEmptyTargetIsNone() {
        let call: EventRecord = event(7, "run_1", "tool_call", ["call_id": .string("c1"), "args": .object(["a": .int(1)])])
        let waiting: ToolDetail = ToolDetail.make(row: row, call: call, result: nil)
        XCTAssertNil(waiting.result)
        XCTAssertNil(waiting.seconds)
        XCTAssertEqual(waiting.arguments.text, "a: 1")
        let empty: EventRecord = event(8, "run_1", "tool_result", ["call_id": .string("c1"), "text": .string(""), "target": .string("")])
        let done: ToolDetail = ToolDetail.make(row: row, call: nil, result: empty)
        XCTAssertEqual(done.result?.text, "")
        XCTAssertNil(done.target)
        XCTAssertEqual(done.arguments.text, "")
    }

    func testAHugeResultIsClipped() {
        let long: String = String(repeating: "line\n", count: 5000)
        let result: EventRecord = event(8, "run_1", "tool_result", ["call_id": .string("c1"), "text": .string(long)])
        let detail: ToolDetail = ToolDetail.make(row: row, call: nil, result: result)
        XCTAssertEqual(detail.result?.text.count, ToolDetailLimits.result)
        XCTAssertEqual(detail.result?.omitted, long.count - ToolDetailLimits.result)
    }

    func testTheStepsEventsAreFoundByTheirCallIdAmongOthers() {
        let events: [EventRecord] = [
            event(7, "run_1", "tool_call", ["call_id": .string("c0")]),
            event(8, "run_1", "tool_call", ["call_id": .string("c1")]),
            event(9, "run_1", "tool_result", ["call_id": .string("c0")]),
            event(10, "run_1", "tool_result", ["call_id": .string("c1")]),
        ]
        XCTAssertEqual(ToolEvents.call("c1", in: events)?.id, 8)
        XCTAssertEqual(ToolEvents.result("c1", in: events)?.id, 10)
        XCTAssertNil(ToolEvents.result("c2", in: events))
    }
}

final class MessageActionsTests: XCTestCase {
    private func item(_ id: String, _ kind: ChatItem.Kind) -> ChatItem { ChatItem(id: id, kind: kind, ts: 1) }

    func testAReactionGoesOnTheAnswerOrAStepKeyedAsTheDaemonKeysThem() {
        XCTAssertEqual(MessageActions.reactionTarget(of: item("r:run_1", .answer(text: "x", status: "done")))?.message, "answer")
        XCTAssertEqual(MessageActions.reactionTarget(of: item("r:run_1", .answer(text: "x", status: "done")))?.runId, "run_1")
        let step: (runId: String, message: String)? = MessageActions.reactionTarget(of: item("a:run_2:3", .assistant(text: "x", streaming: false)))
        XCTAssertEqual(step?.runId, "run_2")
        XCTAssertEqual(step?.message, "step-3")
    }

    func testNoReactionOnWhatIsStillBeingWrittenTheOwnersWordsOrACard() {
        XCTAssertNil(MessageActions.reactionTarget(of: item("a:run_2:3", .assistant(text: "x", streaming: true))))
        XCTAssertNil(MessageActions.reactionTarget(of: item("u:run_1", .user("hi"))))
        XCTAssertNil(MessageActions.reactionTarget(of: item("n:4", .note("n", .plain))))
        XCTAssertNil(MessageActions.reactionTarget(of: item("a:run_2:0", .assistant(text: "x", streaming: false))), "steps count from 1")
        XCTAssertNil(MessageActions.reactionTarget(of: item("a:run_2:x", .assistant(text: "x", streaming: false))))
    }

    func testCopyTakesTheWordsOfAMessageAndNothingFromACard() {
        XCTAssertEqual(MessageActions.copyText(of: item("u:r", .user("hello"))), "hello")
        XCTAssertEqual(MessageActions.copyText(of: item("a:r:1", .assistant(text: "hi there", streaming: false))), "hi there")
        XCTAssertEqual(MessageActions.copyText(of: item("r:r", .answer(text: "done", status: "done"))), "done")
        XCTAssertNil(MessageActions.copyText(of: item("u:r", .user(""))))
        let card: ApprovalCard = ApprovalCard(approvalId: "a", runId: "r", tool: "shell", summary: "s", severity: "", question: nil, status: "pending")
        XCTAssertNil(MessageActions.copyText(of: item("p:a", .approval(card))))
    }

    func testOnlyTheOwnersFirstMessageOfARunIsEditedFromHereAndNotWhileARunWorks() {
        XCTAssertEqual(MessageActions.rewindTarget(of: item("u:run_4", .user("hi")), chatIsWorking: false), "run_4")
        XCTAssertNil(MessageActions.rewindTarget(of: item("u:run_4", .user("hi")), chatIsWorking: true))
        XCTAssertNil(MessageActions.rewindTarget(of: item("s:12", .user("steer")), chatIsWorking: false), "a steer is part of its run")
        XCTAssertNil(MessageActions.rewindTarget(of: item("r:run_4", .answer(text: "x", status: "done")), chatIsWorking: false))
    }

    func testTheReactionsOnAMessageAreThoseOfItsRunAndKeyInThePalettesOrder() {
        let all: [ReactionRecord] = [
            ReactionRecord(runId: "run_1", message: "answer", reaction: "wrong"),
            ReactionRecord(runId: "run_1", message: "answer", reaction: "good"),
            ReactionRecord(runId: "run_1", message: "step-1", reaction: "love"),
            ReactionRecord(runId: "run_2", message: "answer", reaction: "hold"),
        ]
        XCTAssertEqual(MessageActions.standing(on: item("r:run_1", .answer(text: "x", status: "done")), among: all), ["good", "wrong"])
        XCTAssertEqual(MessageActions.standing(on: item("a:run_1:1", .assistant(text: "x", streaming: false)), among: all), ["love"])
        XCTAssertEqual(MessageActions.standing(on: item("u:run_1", .user("x")), among: all), [])
        XCTAssertEqual(Reaction.all.map(\.id), ["good", "love", "wrong", "again", "remember", "explain", "hold"], "the web's palette")
        XCTAssertEqual(Reaction.symbol(of: "remember"), "📌")
    }
}

final class DraftsTests: XCTestCase {
    func testADraftIsKeptPerChatAndAnEmptyOneIsNone() {
        var drafts: Drafts = Drafts()
        drafts.set("half a thought", for: "thr_1", at: 1)
        drafts.set("another", for: "thr_2", at: 2)
        XCTAssertEqual(drafts.text(for: "thr_1"), "half a thought")
        XCTAssertEqual(drafts.text(for: "thr_3"), "")
        drafts.set("  \n ", for: "thr_1", at: 3)
        XCTAssertEqual(drafts.text(for: "thr_1"), "", "whitespace is no draft")
        XCTAssertEqual(drafts.entries.count, 1)
    }

    func testTheOldestDraftsGoPastTheLimitAndALongOneIsCut() {
        var drafts: Drafts = Drafts()
        for n in 0..<(Drafts.kept + 5) { drafts.set("d\(n)", for: "thr_\(n)", at: Double(n)) }
        XCTAssertEqual(drafts.entries.count, Drafts.kept)
        XCTAssertEqual(drafts.text(for: "thr_0"), "", "the oldest went")
        XCTAssertEqual(drafts.text(for: "thr_\(Drafts.kept + 4)"), "d\(Drafts.kept + 4)")
        drafts.set(String(repeating: "x", count: Drafts.longest + 10), for: "long", at: 999)
        XCTAssertEqual(drafts.text(for: "long").count, Drafts.longest)
    }

    func testTheNewChatBoxHasItsOwnKeyPerAgent() {
        XCTAssertEqual(Drafts.newChatKey(agent: nil), "new")
        XCTAssertEqual(Drafts.newChatKey(agent: "agt_1"), "new:agt_1")
    }

    @MainActor
    func testDraftsComeBackAtTheNextLaunchAndGoWhenTheKeyIsForgotten() throws {
        let folder: URL = FileManager.default.temporaryDirectory.appending(path: "dot-drafts-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder) }
        let first: DraftStore = DraftStore(directory: folder)
        first.save("dear Dot, ", for: "thr_1")
        first.save("new one", for: Drafts.newChatKey(agent: nil))
        first.flush()
        // A later launch reads the same file.
        let second: DraftStore = DraftStore(directory: folder)
        XCTAssertEqual(second.text(for: "thr_1"), "dear Dot, ")
        XCTAssertEqual(second.text(for: "new"), "new one")
        second.save("", for: "thr_1")
        second.flush()
        XCTAssertEqual(DraftStore(directory: folder).text(for: "thr_1"), "", "an emptied box keeps nothing")
        XCTAssertEqual(DraftStore(directory: folder).text(for: "new"), "new one")
        second.clear()
        XCTAssertEqual(DraftStore(directory: folder).text(for: "new"), "")
    }

    @MainActor
    func testAFileThatDoesNotReadIsNoDrafts() throws {
        let folder: URL = FileManager.default.temporaryDirectory.appending(path: "dot-drafts-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try Data("not json".utf8).write(to: folder.appending(path: "drafts.json"))
        XCTAssertEqual(DraftStore(directory: folder).text(for: "thr_1"), "")
    }
}

final class DictationRulesTests: XCTestCase {
    func testDictationStartsOnlyWithBothPermissionsAndAnOnDeviceRecognizer() {
        XCTAssertEqual(DictationGate.decide(speech: .granted, microphone: .granted, recognizerAvailable: true, onDevice: true), .ready)
        XCTAssertEqual(DictationGate.decide(speech: .undetermined, microphone: .undetermined, recognizerAvailable: true, onDevice: true), .needsSpeechPermission)
        XCTAssertEqual(DictationGate.decide(speech: .granted, microphone: .undetermined, recognizerAvailable: true, onDevice: true), .needsMicrophonePermission)
        XCTAssertEqual(DictationGate.decide(speech: .denied, microphone: .granted, recognizerAvailable: true, onDevice: true), .speechDenied)
        XCTAssertEqual(DictationGate.decide(speech: .granted, microphone: .denied, recognizerAvailable: true, onDevice: true), .microphoneDenied)
        XCTAssertEqual(DictationGate.decide(speech: .granted, microphone: .granted, recognizerAvailable: false, onDevice: true), .recognizerUnavailable)
        XCTAssertEqual(DictationGate.decide(speech: .granted, microphone: .granted, recognizerAvailable: true, onDevice: false), .notOnDevice)
    }

    func testADeniedPermissionOutranksAPromptStillToCome() {
        XCTAssertEqual(DictationGate.decide(speech: .undetermined, microphone: .denied, recognizerAvailable: true, onDevice: true), .microphoneDenied)
    }

    func testEveryWayToFailHasWordsAndAPromptHasNone() {
        for gate in [DictationGate.speechDenied, .microphoneDenied, .recognizerUnavailable, .notOnDevice] {
            XCTAssertNotNil(gate.failureWords, "\(gate)")
        }
        for gate in [DictationGate.ready, .needsSpeechPermission, .needsMicrophonePermission] { XCTAssertNil(gate.failureWords) }
        XCTAssertTrue(DictationGate.speechDenied.opensSettings)
        XCTAssertTrue(DictationGate.microphoneDenied.opensSettings)
        XCTAssertFalse(DictationGate.notOnDevice.opensSettings)
        XCTAssertTrue(DictationGate.notOnDevice.failureWords?.contains("on this phone") ?? false, "no audio goes elsewhere")
    }

    func testWordsHeardJoinWhatWasTypedOneSpaceApart() {
        XCTAssertEqual(DictationText.merged(base: "Hello", heard: "world"), "Hello world")
        XCTAssertEqual(DictationText.merged(base: "Hello ", heard: " world "), "Hello world")
        XCTAssertEqual(DictationText.merged(base: "", heard: "world"), "world")
        XCTAssertEqual(DictationText.merged(base: "   ", heard: "world"), "world")
        XCTAssertEqual(DictationText.merged(base: "Hello", heard: ""), "Hello", "nothing heard changes nothing")
        XCTAssertEqual(DictationText.merged(base: "Line one\n", heard: "line two"), "Line one\nline two")
        // Each partial result replaces the last one: the box never grows by the words said before.
        XCTAssertEqual(DictationText.merged(base: "Hi", heard: "the"), "Hi the")
        XCTAssertEqual(DictationText.merged(base: "Hi", heard: "the invoices"), "Hi the invoices")
    }

    func testTypingWhileItListensStopsItAndIsNeverWrittenOver() {
        // Nothing typed since it began, or since it last wrote: it writes the words heard so far over its own.
        XCTAssertEqual(DictationText.update(current: "Hi", lastWritten: nil, base: "Hi", heard: "the"), "Hi the")
        XCTAssertEqual(DictationText.update(current: "Hi the", lastWritten: "Hi the", base: "Hi", heard: "the invoices"), "Hi the invoices")
        // A letter typed before the first word, or after one: nil, and the box keeps it.
        XCTAssertNil(DictationText.update(current: "Hi!", lastWritten: nil, base: "Hi", heard: "the"))
        XCTAssertNil(DictationText.update(current: "Hi the, ", lastWritten: "Hi the", base: "Hi", heard: "the invoices"))
        XCTAssertNil(DictationText.update(current: "", lastWritten: "Hi the", base: "Hi", heard: "the invoices"), "a cleared box stays cleared")
    }

    func testTheStateKnowsWhenItListens() {
        XCTAssertTrue(DictationState.starting.isActive)
        XCTAssertTrue(DictationState.listening.isActive)
        XCTAssertFalse(DictationState.idle.isActive)
        XCTAssertFalse(DictationState.failed("x").isActive)
    }
}

final class DeepLinkTests: XCTestCase {
    func testALiveActivityLinkOpensARun() {
        XCTAssertEqual(DotDeepLink.parse(URL(string: "dotagent://run/run_abc-123")!), .run("run_abc-123"))
        XCTAssertEqual(DotDeepLink.url(forRun: "run_abc-123")?.absoluteString, "dotagent://run/run_abc-123")
        XCTAssertEqual(DotDeepLink.parse(DotDeepLink.url(forRun: "run_9")!), .run("run_9"))
    }

    func testAnyOtherLinkOpensNothing() {
        for text in [
            "https://run/run_1", "dotagent://chat/run_1", "dotagent://run", "dotagent://run/", "dotagent://run/a/b", "dotagent://run/..%2Fsecret",
            "dotagent://run/run_1?x=1#y", "other://run/run_1",
        ] {
            guard let url: URL = URL(string: text) else { continue }
            if text.contains("?") {
                XCTAssertEqual(DotDeepLink.parse(url), .run("run_1"), "a query changes nothing")
            } else {
                XCTAssertNil(DotDeepLink.parse(url), text)
            }
        }
        XCTAssertNil(DotDeepLink.url(forRun: ""))
        XCTAssertNil(DotDeepLink.url(forRun: "a/b"))
        XCTAssertNil(DotDeepLink.url(forRun: String(repeating: "a", count: 65)))
        XCTAssertNil(DotDeepLink.url(forRun: "run é"))
    }

    func testTheCardOpensTheRunThatWaitsForTheOwnerThenOneAtWorkThenTheFirst() {
        func row(_ id: String, _ status: String) -> DotFleetAttributes.Row {
            DotFleetAttributes.Row(runId: id, title: "t", agent: "Dot", status: status, kind: "", line: "", startedAt: 1)
        }
        XCTAssertEqual(
            DotFleetAttributes.ContentState(active: 2, rows: [row("run_a", "working"), row("run_b", "waiting")]).tapRunId, "run_b")
        XCTAssertEqual(DotFleetAttributes.ContentState(active: 2, rows: [row("run_a", "done"), row("run_b", "working")]).tapRunId, "run_b")
        XCTAssertEqual(DotFleetAttributes.ContentState(active: 0, rows: [row("run_a", "done"), row("run_b", "failed")]).tapRunId, "run_a")
        XCTAssertNil(DotFleetAttributes.ContentState(active: 0, rows: []).tapRunId)
        XCTAssertEqual(DotFleetAttributes.ContentState(active: 1, rows: [row("run_a", "waiting")]).tapURL?.absoluteString, "dotagent://run/run_a")
    }

    func testARouteOfAPushStillNamesItsRun() {
        XCTAssertEqual(runId(inRoute: "#/runs/run_7"), "run_7")
        XCTAssertNil(runId(inRoute: "#/settings"))
    }
}
