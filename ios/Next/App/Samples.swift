import Foundation

/// A chat as the placeholder screens of wave N0 show it; wave N1-client replaces this with the store's projection.
struct SampleChat: Identifiable, Hashable, Sendable {
    let id: String
    let title: String
    let preview: String
    let time: String
    let status: ChatStatus?
    let agent: String
    let isPinned: Bool
}

struct SampleAgent: Identifiable, Hashable, Sendable {
    let id: String
    let name: String
    let role: String
    let status: ChatStatus?
}

struct HelperStep: Identifiable, Hashable, Sendable {
    let id: String
    let agent: String
    let step: String
    let status: ChatStatus
}

/// Made-up content for the placeholder tabs and the gallery: no daemon is read in N0.
enum Samples {
    /// One chat per role Dot plays (plan §0): assistant with computer use, coding, teacher, employee, and a failure.
    static let chats: [SampleChat] = [
        SampleChat(
            id: "c1", title: String(localized: "Book the dentist for next week", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), preview: String(localized: "Pay the 20 € deposit on the clinic's page?", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"),
            time: "14:02", status: .waitingOnYou, agent: "Dot", isPinned: false),
        SampleChat(
            id: "c2", title: String(localized: "Which invoices are overdue", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), preview: String(localized: "Should I include the two drafts from October?", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"),
            time: "13:40", status: .question, agent: "Finance", isPinned: false),
        SampleChat(
            id: "c3", title: String(localized: "Fix the flaky parser test", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), preview: String(localized: "Running the unit tests again with the fix", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"),
            time: "13:12", status: .working, agent: "Dot", isPinned: true),
        SampleChat(
            id: "c4", title: String(localized: "Teach me how SwiftUI lays out a stack", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"),
            preview: String(localized: "Lesson 2 of 5: proposals go down, sizes come up.", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), time: "12:30", status: nil, agent: "Dot",
            isPinned: false),
        SampleChat(
            id: "c5", title: String(localized: "Weekly client mail", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), preview: String(localized: "Sent 12 mails; 2 bounced and are listed below.", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"),
            time: String(localized: "Yesterday", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: .done, agent: "Outreach", isPinned: false),
        SampleChat(
            id: "c6", title: String(localized: "Back up the photo library", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), preview: String(localized: "The disk is full.", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), time: String(localized: "Mon", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"),
            status: .failed, agent: "Dot", isPinned: false),
    ]

    /// The agents strip and the Agents tab.
    static let agents: [SampleAgent] = [
        SampleAgent(id: "dot", name: "Dot", role: String(localized: "Your assistant · runs everything", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: .working),
        SampleAgent(id: "research", name: "Research", role: String(localized: "Reads and summarises sources", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: nil),
        SampleAgent(id: "finance", name: "Finance", role: String(localized: "Invoices and the books", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: .question),
        SampleAgent(id: "outreach", name: "Outreach", role: String(localized: "Client mail, twice a week", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: .done),
    ]

    /// Helpers a run started, each with its current step (R-e #2).
    static let helpers: [HelperStep] = [
        HelperStep(id: "h1", agent: "Research", step: String(localized: "Reading the clinic's opening hours", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: .working),
        HelperStep(id: "h2", agent: "Finance", step: String(localized: "Waits for your answer about the deposit", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: .waitingOnYou),
        HelperStep(id: "h3", agent: "Research", step: String(localized: "Compared three clinics", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), status: .done),
    ]

    /// The Mac's name as the daemon reports it (content, not a UI string).
    static let macName: String = "Studio Mac"

    /// The helpers of the sample chat, as the agents sheet lists them; the clocks run from these starts.
    static func helperRuns(now: Date) -> [HelperRun] {
        let engine: String = "Claude · Opus 5.5 · Medium"
        return [
            HelperRun(
                id: "r1", title: String(localized: "Find a free afternoon slot", comment: "Sample helper title (wave N0)"),
                state: .working, startedAt: now.addingTimeInterval(-2475), endedAt: nil, engine: engine),
            HelperRun(
                id: "r2", title: String(localized: "Check the deposit with Finance", comment: "Sample helper title (wave N0)"),
                state: .waitingOnYou, startedAt: now.addingTimeInterval(-1412), endedAt: nil, engine: engine),
            HelperRun(
                id: "r3", title: String(localized: "Compare three clinics", comment: "Sample helper title (wave N0)"),
                state: .done, startedAt: now.addingTimeInterval(-3900), endedAt: now.addingTimeInterval(-3100),
                engine: "DeepSeek · V4 · High"),
            HelperRun(
                id: "r4", title: String(localized: "Read the insurance terms", comment: "Sample helper title (wave N0)"),
                state: .failed, startedAt: now.addingTimeInterval(-5000), endedAt: now.addingTimeInterval(-4700),
                engine: engine),
        ]
    }

    static let backgroundWork: [BackgroundWork] = [
        BackgroundWork(
            id: "b1", title: String(localized: "Find a free afternoon slot", comment: "Sample helper title (wave N0)"),
            kind: .subagent),
        BackgroundWork(
            id: "b2", title: String(localized: "Check the deposit with Finance", comment: "Sample helper title (wave N0)"),
            kind: .subagent),
        BackgroundWork(id: "b3", title: "uv run pytest tests/unit -q -n 4", kind: .command),
        BackgroundWork(
            id: "b4", title: String(localized: "Every 10 min: watch the clinic's calendar", comment: "Sample loop title (wave N0)"),
            kind: .loop),
    ]

    /// A computer-use run: the browser, the screen and a click, then a step that waits on the owner.
    static let computerUse: [ToolCall] = [
        ToolCall(
            id: "u1", symbol: "globe", label: String(localized: "Opened the clinic's booking page", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .done,
            detail: "https://clinic.example.invalid/book"),
        ToolCall(
            id: "u2", symbol: "camera.viewfinder", label: String(localized: "Looked at the screen", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .done,
            detail: String(localized: "Calendar for week 49; free: Tue 10:00, Thu 15:30", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send")),
        ToolCall(
            id: "u3", symbol: "cursorarrow.click", label: String(localized: "Clicked “Thu 15:30”", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .done, detail: nil),
        ToolCall(
            id: "u4", symbol: "keyboard", label: String(localized: "Typing your name and phone number", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .running, detail: nil),
    ]

    static let toolCalls: [ToolCall] = [
        ToolCall(
            id: "t1", symbol: "doc.text", label: String(localized: "Read src/parser/tokens.py", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .done,
            detail: "def tokens(text: str) -> list[Token]:\n    ...\n(212 lines)"),
        ToolCall(
            id: "t2", symbol: "magnifyingglass", label: String(localized: "Searched for \"unterminated string\"", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .done,
            detail: String(localized: "3 matches in 2 files", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send")),
        ToolCall(
            id: "t3", symbol: "terminal", label: String(localized: "uv run pytest tests/unit/test_parser.py -q", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .failed,
            detail: "1 failed, 41 passed in 2.31s\nFAILED test_parser.py::test_unterminated - AssertionError"),
        ToolCall(id: "t4", symbol: "pencil", label: String(localized: "Edited src/parser/tokens.py", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .done, detail: "+4 −1"),
        ToolCall(
            id: "t5", symbol: "terminal", label: String(localized: "uv run pytest tests/unit -q -n 4", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send"), state: .running, detail: nil),
    ]

    static let answerMarkdown: String =
        String(localized: "The test failed because the tokenizer read past the end of an **unterminated string**. I stopped the loop at the last character and kept the error the parser already raises; see [the failing case](https://example.invalid/case) and the change below.", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send")

    static let code: String =
        "if index >= len(text):\n    raise ParseError(\"unterminated string\", at=start)  # the loop stops here now"

    static let bookingMarkdown: String =
        String(localized: "Thursday at 15:30 is free at the clinic on Main Street. The clinic asks for a **20 € deposit**, so I stopped before paying; the slot is held for ten minutes on [the booking page](https://clinic.example.invalid/book).", comment: "Sample content of the placeholder screens and gallery (wave N0); stands for what the daemon will send")

    static func booking() -> AttributedString { parse(bookingMarkdown) }

    /// The answer's attributed text; the sample is constant markdown, so a parse failure is a programming error.
    static func answer() -> AttributedString { parse(answerMarkdown) }

    private static func parse(_ markdown: String) -> AttributedString {
        do {
            return try answerText(markdown: markdown)
        } catch {
            preconditionFailure("the sample answer's markdown does not parse: \(error)")
        }
    }
}
