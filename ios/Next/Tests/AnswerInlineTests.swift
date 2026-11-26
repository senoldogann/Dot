import DotEngine
import XCTest

@testable import DotNext

/// An answer's references in its words (item 1961): a bare address is shortened, a written link keeps its words, a
/// file is its name linking to its sheet, and each reference's mark is drawn once however many runs its words are.
final class AnswerInlineTests: XCTestCase {
    func testABareAddressIsShortenedAndAWrittenLinkKeepsItsWords() throws {
        let text: AttributedString = try answerInline(markdown: "https://github.com/o/r/pull/1012 merged, see [the notes](https://nodejs.org/en/blog).")
        XCTAssertEqual(String(text.characters), "#1012 merged, see the notes.")
        XCTAssertEqual(markStarts(text).compactMap { $0 }, [.github, .site(URL(string: "https://nodejs.org/en/blog")!)])
    }

    func testAFileIsItsNameLinkingToItsSheet() throws {
        let text: AttributedString = try answerInline(markdown: "The notes are in `~/Desktop/ci-notes.md` and summary.docx.")
        XCTAssertEqual(String(text.characters), "The notes are in ci-notes.md and summary.docx.")
        let paths: [String] = text.runs.compactMap { $0.link.flatMap(fileReferencePath) }
        XCTAssertEqual(paths, ["~/Desktop/ci-notes.md", "summary.docx"])
        XCTAssertNil(fileReferencePath(URL(string: "dotfile://open?path=/etc/passwd")!), "a path the file rules do not take opens nothing")
    }

    func testAReferenceOfSeveralRunsDrawsItsMarkOnce() throws {
        let whole: AttributedString = try answerInline(markdown: "Read [the release notes of Node](https://nodejs.org/en/blog)")
        XCTAssertEqual(markStarts(whole).compactMap { $0 }.count, 1)
        // The colour steps of words fading in split the link's words into several runs.
        let fading: AttributedString = fadedTail(whole, characters: 24)
        XCTAssertGreaterThan(fading.runs.count, whole.runs.count, "the fade splits the runs")
        XCTAssertEqual(markStarts(fading).compactMap { $0 }.count, 1)
    }
}
