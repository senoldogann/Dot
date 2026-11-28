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
             {"path":"/w/s.csv","name":"s.csv","size":5,"modified":1,"opens":true,"preview":{"kind":"table","sheet":{"title":"s.csv","state":"visible","cells":[{"row":1,"column":1,"value":"Note","formula":null},{"row":1,"column":2,"value":"Size","formula":null},{"row":2,"column":1,"value":"N1","formula":null},{"row":4,"column":2,"value":"521","formula":null}],"cut_at_row":null},"max_cells":20000}},
             {"path":"/w/b.docx","name":"b.docx","size":5,"modified":1,"opens":true,"preview":{"kind":"document","blocks":[]}},
             {"path":"/w/x.bin","name":"x.bin","size":5,"modified":1,"opens":false,"preview":{"kind":"unshown","reason":"no preview"}}]
            """
        let files: [ShownFile] = try JSONDecoder().decode([ShownFile].self, from: Data(json.utf8))
        XCTAssertEqual(files.map(\.preview), [
            .pdf(pages: [FilePageSize(width: 612, height: 792)], widths: [600, 900, 1200, 1800]), .text("hello", cut: true),
            .table(FileTable(rows: [["Note", "Size"], ["N1", ""], ["", "521"]], cutAtRow: nil)), .drawn(kind: "document"), .unshown(reason: "no preview"),
        ])
        XCTAssertEqual(phonePageWidth([600, 900, 1200, 1800]), 1200)
        XCTAssertEqual(phonePageWidth([1800]), 1800)
    }

    /// A table's columns are measured from its first rows alone, each between the narrowest and the widest it may be.
    func testATablesColumnsAreMeasuredFromItsFirstRows() {
        let rows: [[String]] = [["id", "a long description of the row"], ["1", "x"], ["22", String(repeating: "y", count: 90)]]
        XCTAssertEqual(fileTableColumnCharacters(rows, sample: 2, minimum: 3, maximum: 40), [3, 29])
        XCTAssertEqual(fileTableColumnCharacters(rows, sample: 10, minimum: 3, maximum: 40), [3, 40])
        XCTAssertEqual(fileTableColumnCharacters([], sample: 10, minimum: 3, maximum: 40), [])
    }

    func testATableIsWrittenAgainAsItsText() {
        XCTAssertEqual(fileTableText([["a", "b,c"], ["say \"hi\"", "x\ny"]], delimiter: ","), "a,\"b,c\"\n\"say \"\"hi\"\"\",\"x\ny\"\n")
        XCTAssertEqual(fileTableText([["a", "b,c"]], delimiter: "\t"), "a\tb,c\n")
    }

    func testANumberCellIsTold() {
        XCTAssertTrue(fileTableCellIsNumber("1,234.50"))
        XCTAssertTrue(fileTableCellIsNumber("-12%"))
        XCTAssertFalse(fileTableCellIsNumber("N12"))
        XCTAssertFalse(fileTableCellIsNumber(""))
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

/// How a text file reads in the file preview (the owner's shot of 2026-11-28: a Markdown report showed as raw text).
final class FileTextFormatTests: XCTestCase {
    func testATextFileIsReadByItsName() {
        XCTAssertEqual(fileTextFormat(name: "C8.out.md"), .markdown)
        XCTAssertEqual(fileTextFormat(name: "data.json"), .json)
        XCTAssertEqual(fileTextFormat(name: "tool.py"), .code(.python))
        XCTAssertEqual(fileTextFormat(name: "page.html"), .code(.html))
        XCTAssertEqual(fileTextFormat(name: "run.log"), .plain)
        XCTAssertEqual(fileTextFormat(name: "notes.txt"), .plain)
        XCTAssertEqual(fileTextFormat(name: "README"), .plain)
    }

    func testALongTextIsCutIntoPiecesOfLines() {
        XCTAssertEqual(textPieces("a\nb\nc\nd\ne", lines: 2), ["a\nb", "c\nd", "e"])
        XCTAssertEqual(textPieces("", lines: 2), [""])
        let long: String = (1...1000).map(String.init).joined(separator: "\n")
        let pieces: [String] = textPieces(long, lines: 60)
        XCTAssertEqual(pieces.count, 17)
        XCTAssertEqual(pieces.joined(separator: "\n"), long)
    }
}
