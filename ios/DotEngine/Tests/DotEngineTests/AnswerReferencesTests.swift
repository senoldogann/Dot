import XCTest

@testable import DotEngine

/// The links and files an answer names (backlog LF1): the web's rules for files (prose.js), each once in order, none
/// from a fenced code block; and what the Mac's file preview reads as.
final class AnswerReferencesTests: XCTestCase {
    func testLinksAreTheAnswersWebAddressesEachOnceWithTheirBareSite() {
        let answer: String = """
            Read [the post](https://www.senoldogan.com/blog/dot?x=1). The data is at https://example.com/a.csv, and again
            https://www.senoldogan.com/blog/dot?x=1.
            ```
            curl https://code.example.com/not-a-link
            ```
            Mail me@example.com, see http://127.0.0.1:8765/page.
            """
        let links: [AnswerLink] = answerLinks(answer)
        XCTAssertEqual(links.map(\.url.absoluteString), ["https://www.senoldogan.com/blog/dot?x=1", "https://example.com/a.csv", "http://127.0.0.1:8765/page"])
        XCTAssertEqual(links.map(\.site), ["senoldogan.com", "example.com", "127.0.0.1"])
    }

    func testFilesAreTheWebsOwnPathsAndNamesNotCodeOrAddresses() {
        let answer: String = """
            I wrote `~/Desktop/report.pdf` and `notes/plan.md`; the chart is chart.png? No: `os.path` is code.
            The summary is in summary.docx and ~/Library/Application Support/Dot/workspace/brief.md.
            See https://example.com/files/guide.pdf for theirs.
            ```
            open draft.md
            ```
            """
        XCTAssertEqual(
            answerFiles(answer),
            ["~/Desktop/report.pdf", "notes/plan.md", "summary.docx", "~/Library/Application Support/Dot/workspace/brief.md"])
    }

    func testAFileNamedTwiceIsOneChipAndTheChipsStopAtTheirMost() {
        let names: String = (1...12).map { "`f\($0).md`" }.joined(separator: " ")
        XCTAssertEqual(answerFiles("`a.md` and a.md " + names).count, answerFilesShown)
        XCTAssertEqual(answerFiles("`a.md` and a.md").count, 1)
    }

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
