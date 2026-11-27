import XCTest

@testable import DotEngine

/// The files an answer names (backlog LF1): their kinds, and what the Mac's file preview reads as.
final class AnswerReferencesTests: XCTestCase {
    func testAKindIsReadFromTheSuffix() {
        XCTAssertEqual(answerFileKind("Shot.PNG"), .image)
        XCTAssertEqual(answerFileKind("r.pdf"), .pdf)
        XCTAssertEqual(answerFileKind("x.swift"), .code)
        XCTAssertEqual(answerFileKind("plan.md"), .text)
        XCTAssertEqual(answerFileKind("s.xlsx"), .table)
        XCTAssertEqual(answerFileKind("archive.zip"), .other)
    }

    func testTheMacsPreviewIsReadByItsKind() throws {
        let json: String = """
            [{"path":"/w/r.pdf","name":"r.pdf","size":2048,"modified":1.5,"opens":true,
              "preview":{"kind":"pdf","pages":[{"width":612,"height":792}],"widths":[600,900,1200,1800],"text":"","cut":false}},
             {"path":"/w/a.md","name":"a.md","size":5,"modified":1,"opens":true,"preview":{"kind":"text","text":"hello","cut":true}},
             {"path":"/w/s.csv","name":"s.csv","size":5,"modified":1,"opens":true,"preview":{"kind":"table","sheet":{},"max_cells":1}},
             {"path":"/w/x.bin","name":"x.bin","size":5,"modified":1,"opens":false,"preview":{"kind":"unshown","reason":"no preview"}}]
            """
        let files: [ShownFile] = try JSONDecoder().decode([ShownFile].self, from: Data(json.utf8))
        XCTAssertEqual(files.map(\.preview), [
            .pdf(pages: [FilePageSize(width: 612, height: 792)], widths: [600, 900, 1200, 1800]), .text("hello", cut: true),
            .drawn(kind: "table"), .unshown(reason: "no preview"),
        ])
        XCTAssertEqual(phonePageWidth([600, 900, 1200, 1800]), 1200)
        XCTAssertEqual(phonePageWidth([1800]), 1800)
    }
}

/// How a link and a file show inline in an answer's words (the owner's look of 2026-11-26: `#1012` with the GitHub mark).
final class InlineReferenceTests: XCTestCase {
    func testAGitHubPullRequestIsItsNumberAndOtherSitesTheirBareDomain() throws {
        let label = { (address: String) in inlineLinkLabel(URL(string: address)!) }
        XCTAssertEqual(label("https://github.com/senoldogann/Dot/pull/1012"), InlineLinkLabel(text: "#1012", mark: .github))
        XCTAssertEqual(label("https://github.com/senoldogann/Dot/issues/7#c"), InlineLinkLabel(text: "#7", mark: .github))
        XCTAssertEqual(label("https://github.com/senoldogann/Dot"), InlineLinkLabel(text: "senoldogann/Dot", mark: .github))
        XCTAssertEqual(label("https://www.senoldogan.com/blog"), InlineLinkLabel(text: "senoldogan.com", mark: .site("senoldogan.com")))
    }

    func testFilesAndAddressesAreFoundInTheWords() {
        XCTAssertTrue(codeNamesFile("~/Desktop/report.pdf"))
        XCTAssertTrue(codeNamesFile("notes/plan.md"))
        XCTAssertFalse(codeNamesFile("os.path"))
        let words: String = "The summary is in summary.docx, see https://example.com/a."
        XCTAssertEqual(fileWordRanges(words).map { String(words[$0]) }, ["summary.docx"])
        XCTAssertEqual(webAddressRanges(words).map { String(words[$0]) }, ["https://example.com/a"])
    }
}

/// An answer's table: its columns fit the reading width where they can, the long ones wrapping.
final class TableColumnTests: XCTestCase {
    func testATableThatFitsKeepsItsIdealWidths() {
        XCTAssertEqual(tableColumnWidths(ideal: [80, 120], fit: 343, minimum: 96), [80, 120])
    }

    func testTheWidestColumnsWrapSoTheTableFits() {
        let widths: [Double] = tableColumnWidths(ideal: [130, 600], fit: 343, minimum: 96)
        XCTAssertEqual(widths, [130, 213])
        XCTAssertEqual(widths.reduce(0, +), 343, accuracy: 0.001)
        let three: [Double] = tableColumnWidths(ideal: [60, 400, 500], fit: 360, minimum: 96)
        XCTAssertEqual(three, [60, 150, 150])
    }

    func testATableTooWideForItsMinimumsScrolls() {
        let widths: [Double] = tableColumnWidths(ideal: [300, 300, 300, 300, 300], fit: 343, minimum: 96)
        XCTAssertEqual(widths, [96, 96, 96, 96, 96], "wider than the column: it scrolls sideways")
    }
}
