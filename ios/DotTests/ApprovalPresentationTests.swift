import XCTest
@testable import Dot

/// What an approval card says (item 1392): the title in words, the command alone, the reasons in a line of their own.
final class ApprovalPresentationTests: XCTestCase {
    func testTheDaemonsOneLineIsTakenApart() {
        let card: ApprovalPresentation = ApprovalPresentation.make(
            tool: "shell",
            summary: "Run: mv ~/Documents/Invoices/old.pdf ~/Archive/ (mv takes files from outside the workspace) — aggressive preset still asks for: destructive outside the workspace")
        XCTAssertEqual(card.title, "Run a command")
        XCTAssertEqual(card.command, "mv ~/Documents/Invoices/old.pdf ~/Archive/")
        XCTAssertEqual(card.why, "mv takes files from outside the workspace; destructive outside the workspace")
    }

    func testASummaryWithNoReasonHasNone() {
        let card: ApprovalPresentation = ApprovalPresentation.make(tool: "write_file", summary: "Write notes.md")
        XCTAssertEqual(card.title, "Write a file")
        XCTAssertEqual(card.command, "Write notes.md")
        XCTAssertNil(card.why)
    }

    func testACommandThatEndsInParenthesesIsNotCutWhenNothingIsLeft() {
        XCTAssertEqual(ApprovalPresentation.make(tool: "shell", summary: "(only a note)").command, "(only a note)")
        XCTAssertEqual(ApprovalPresentation.make(tool: "shell", summary: "Run: echo $(date)").command, "echo $(date)", "a subshell is part of the command")
    }

    func testAReasonAfterTheDashWithoutThePolicyWordsIsKept() {
        let card: ApprovalPresentation = ApprovalPresentation.make(tool: "send_to_chat", summary: "To: anna@example.com — sends a message in your name")
        XCTAssertEqual(card.title, "Send a message")
        XCTAssertEqual(card.command, "To: anna@example.com")
        XCTAssertEqual(card.why, "sends a message in your name")
    }

    func testAnEmptySummaryShowsNothingLost() {
        let card: ApprovalPresentation = ApprovalPresentation.make(tool: "shell", summary: "")
        XCTAssertEqual(card.command, "")
        XCTAssertNil(card.why)
    }

    func testToolsAreNamedAsWhatTheyDo() {
        XCTAssertEqual(ApprovalPresentation.title(of: "browser_act"), "Use the browser")
        XCTAssertEqual(ApprovalPresentation.title(of: "desktop_act"), "Use the Mac's screen")
        XCTAssertEqual(ApprovalPresentation.title(of: "memory_save"), "Change what Dot remembers")
        XCTAssertEqual(ApprovalPresentation.title(of: "schedule_create"), "Create a schedule")
        XCTAssertEqual(ApprovalPresentation.title(of: "some_new_tool"), "Some new tool", "an unknown tool is still readable")
        XCTAssertEqual(ApprovalPresentation.title(of: ""), "A step")
    }
}
