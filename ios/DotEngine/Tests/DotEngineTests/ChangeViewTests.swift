import XCTest

@testable import DotEngine

/// A changed file's diff read into rows, a new file read as the file, the format by the name, and the colouring.
final class ChangeViewTests: XCTestCase {
    private func change(_ path: String, diff: String, created: Bool = false, gone: Bool = false, note: String? = nil) -> FileChange {
        let json: String = """
            {"path":\(String(reflecting: path)),"created":\(created),"gone":\(gone),"added":0,"removed":0,"diff":\(String(reflecting: diff)),"note":\(note.map { String(reflecting: $0) } ?? "null")}
            """
        do {
            return try JSONDecoder().decode(FileChange.self, from: Data(json.utf8))
        } catch {
            preconditionFailure("the fixture does not decode: \(error)")
        }
    }

    func testRowsCarryBothLineNumbersAndDropTheSign() {
        let diff: String = "--- a\n+++ b\n@@ -10,3 +10,4 @@ def run():\n     keep = 1\n-    old = 2\n+    new = 2\n+    more = 3\n     end = 4\n\\ No newline at end of file\n"
        let rows: [DiffRow] = diffRows(diff)
        XCTAssertEqual(
            rows,
            [
                DiffRow(kind: .hunk, old: 10, new: 10, text: "def run():"),
                DiffRow(kind: .context, old: 10, new: 10, text: "    keep = 1"),
                DiffRow(kind: .removed, old: 11, new: nil, text: "    old = 2"),
                DiffRow(kind: .added, old: nil, new: 11, text: "    new = 2"),
                DiffRow(kind: .added, old: nil, new: 12, text: "    more = 3"),
                DiffRow(kind: .context, old: 12, new: 13, text: "    end = 4"),
            ])
    }

    func testAnAddedLineThatStartsWithPlusesIsALine() {
        let rows: [DiffRow] = diffRows("@@ -1 +1 @@\n-a\n++++ b\n")
        XCTAssertEqual(rows.last, DiffRow(kind: .added, old: nil, new: 1, text: "+++ b"))
    }

    func testANewFileReadsAsTheFile() {
        let file: FileChange = change("/ws/lib.py", diff: "@@ -0,0 +1,3 @@\n+import os\n+\n+print(os)\n")
        XCTAssertEqual(changeLayout(file), .newFile(text: "import os\n\nprint(os)", format: .code(.python)))
        // Created is enough on its own, and a diff that adds to nothing is new though the flag is false.
        XCTAssertTrue(isNewFile(diffRows("@@ -0,0 +1 @@\n+x\n"), created: false))
        XCTAssertFalse(isNewFile(diffRows("@@ -1 +1,2 @@\n x\n+y\n"), created: false))
    }

    func testAChangedFileIsADiff() {
        let file: FileChange = change("/ws/app.ts", diff: "@@ -1 +1 @@\n-let a = 1\n+let a = 2\n")
        guard case .diff(let rows) = changeLayout(file) else { return XCTFail("a diff: \(changeLayout(file))") }
        XCTAssertEqual(rows.count, 3)
    }

    func testTheFormatComesFromTheName() {
        XCTAssertEqual(changeFormat(path: "/a/notes.MD"), .markdown)
        XCTAssertEqual(changeFormat(path: "/a/data.json"), .json)
        XCTAssertEqual(changeFormat(path: "/a/shot.PNG"), .image)
        XCTAssertEqual(changeFormat(path: "/a/report.pdf"), .pdf)
        XCTAssertEqual(changeFormat(path: "/a/run.sh"), .code(.shell))
        XCTAssertEqual(changeFormat(path: "/a/pyproject.toml"), .code(.toml))
        XCTAssertEqual(changeFormat(path: "/a/ci.yml"), .code(.yaml))
        XCTAssertEqual(changeFormat(path: "/a/page.html"), .code(.html))
        XCTAssertEqual(changeFormat(path: "/a/Makefile"), .code(nil))
        XCTAssertEqual(changeFormat(path: "/a/.env"), .code(nil))
    }

    func testAPictureShowsFromTheMacAndAFileWithoutADiffSaysWhy() {
        XCTAssertEqual(changeLayout(change("/a/shot.png", diff: "", note: "a binary file")), .media(.image))
        XCTAssertEqual(changeLayout(change("/a/shot.png", diff: "", gone: true, note: "the file is not there any more")), .note("the file is not there any more"))
        XCTAssertEqual(changeLayout(change("/a/big.log", diff: "", note: "too large to show")), .note("too large to show"))
    }

    func testJSONIsLaidOutInItsOwnOrder() {
        XCTAssertEqual(prettyJSON(#"{"b":1,"a":[1,2],"e":{},"s":"x, {y}: \"z\""}"#), "{\n  \"b\": 1,\n  \"a\": [\n    1,\n    2\n  ],\n  \"e\": {},\n  \"s\": \"x, {y}: \\\"z\\\"\"\n}")
        XCTAssertNil(prettyJSON("{\"a\":1}\n{\"a\":2}"), "JSON Lines stay as they are")
    }

    private func kinds(_ line: String, _ language: CodeLanguage) -> [String] {
        highlightLines([line], language: language)[0].filter { $0.kind != .plain }.map { "\($0.kind.rawValue):\($0.text)" }
    }

    func testPythonKeywordsStringsCommentsAndNumbers() {
        XCTAssertEqual(kinds("def f(x): return \"a#b\" + 42  # done", .python), ["keyword:def", "keyword:return", "string:\"a#b\"", "number:42", "comment:# done"])
    }

    func testALongStringOrCommentRunsOnToTheNextLine() {
        let lines: [[CodeToken]] = highlightLines(["x = \"\"\"start", "middle", "end\"\"\" + y"], language: .python)
        XCTAssertEqual(lines[1], [CodeToken(kind: .string, text: "middle")])
        XCTAssertEqual(lines[2].first, CodeToken(kind: .string, text: "end\"\"\""))
        let swift: [[CodeToken]] = highlightLines(["let a = 1 /* open", "still */ let b"], language: .swift)
        XCTAssertEqual(swift[1].first, CodeToken(kind: .comment, text: "still */"))
        XCTAssertEqual(swift[1].last, CodeToken(kind: .plain, text: " b"))
    }

    func testKeysOfJSONYAMLAndTOML() {
        XCTAssertEqual(kinds(#"  "name": "dot", "on": true"#, .json), [#"key:"name""#, #"string:"dot""#, #"key:"on""#, "keyword:true"])
        XCTAssertEqual(kinds("  - name: build # step", .yaml), ["key:name", "comment:# step"])
        XCTAssertEqual(kinds("[tool.ruff]", .toml), ["keyword:[tool.ruff]"])
        XCTAssertEqual(kinds("line-length = 120", .toml), ["key:line-length", "number:120"])
    }

    func testShellMarkdownAndHTML() {
        XCTAssertEqual(kinds("if [ $# -gt 0 ]; then echo 'hi'; fi # x", .shell), ["keyword:if", "number:0", "keyword:then", "string:'hi'", "keyword:fi", "comment:# x"])
        XCTAssertEqual(kinds("## Title", .markdown), ["heading:## Title"])
        XCTAssertEqual(kinds("- use `dotx` now", .markdown), ["key:-", "string:`dotx`"])
        let fenced: [[CodeToken]] = highlightLines(["```py", "# not a heading", "```"], language: .markdown)
        XCTAssertEqual(fenced[1], [CodeToken(kind: .string, text: "# not a heading")])
        XCTAssertEqual(kinds(#"<a href="x">go</a> <!-- c -->"#, .html), ["key:a", #"string:"x""#, "key:a", "comment:<!-- c -->"])
    }
}

/// The lines a code view draws.
final class CodeLinesTests: XCTestCase {
    func testADiffKeepsItsNumbersAndColoursEachHunkOnItsOwn() {
        let rows: [DiffRow] = diffRows("@@ -1,2 +1,2 @@\n x = \"\"\"open\n-a\n+b\n@@ -9 +9 @@ def f():\n-\tc = 1\n+\tc = 2\n")
        let lines: [CodeLine] = codeLines(rows: rows, language: .python)
        XCTAssertEqual(lines.map(\.id), Array(0..<lines.count))
        XCTAssertEqual(lines[4].kind, .hunk)
        XCTAssertEqual(lines[4].text, "def f():")
        // The string left open in the first hunk does not run into the second.
        XCTAssertEqual(lines[5].tokens.first, CodeToken(kind: .plain, text: "    c = "))
        XCTAssertEqual(lines[6].new, 9)
        XCTAssertNil(lines[6].old)
    }

    func testAFileIsNumberedFromOne() {
        let lines: [CodeLine] = codeLines(text: "{\n  \"a\": 1\n}", language: codeLanguage(of: .json))
        XCTAssertEqual(lines.map(\.new), [1, 2, 3])
        XCTAssertEqual(lines.map(\.kind), [nil, nil, nil])
        XCTAssertEqual(lines[1].tokens.first { $0.kind != .plain }, CodeToken(kind: .key, text: "\"a\""))
        XCTAssertNil(codeLanguage(of: .image))
    }
}
