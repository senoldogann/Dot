#if DEBUG
    import Foundation

    /// What the fixture daemon shows of a file (`GET /api/files/preview`), by the path's name: a Markdown report like the
    /// owner's `C8.out.md` (headings, bold, code spans, lists and tables, then a last line), a CSV table, a JSON file on one line, a
    /// Python file, a plain log, and a Markdown report of 200 KB and a CSV of 5,000 rows for the open and scroll check;
    /// any other path the CI notes the chat's answer names.
    enum FileFixture {
        static func preview(path: String, now: Double) -> String {
            let name: String = (path as NSString).lastPathComponent
            let preview: [String: Any]
            switch name {
            case "C8.out.md": preview = text(report(parts: 3) + "\nSon satır: kontrol tamam.\n")
            case "big.md": preview = text(report(parts: 160))
            case "rows.csv": preview = table(rows(count: 12))
            case "big.csv": preview = table(rows(count: 5000))
            case "doneq.json": preview = text(#"{"done":["a1b2c3","d4e5f6"],"left":12,"merged":{"ci":["0f1e2d","3c4b5a"]}}"#)
            case "prune.py": preview = text("import json\n\n\ndef main() -> int:\n    # The notes kept.\n    return len(json.load(open(\"notes.json\")))\n")
            case "run.log": preview = text("13:33 started\n13:34 read 87 notes\n13:35 merged into 17\n")
            default: preview = text("CI notes\n\nNode 22 is pinned on CI.\n")
            }
            let file: [String: Any] = [
                "path": path.hasPrefix("/") ? path : "/Users/owner/Desktop/\(name)", "name": name, "size": 2150, "modified": now - 600, "opens": true,
                "preview": preview,
            ]
            guard let data: Data = try? JSONSerialization.data(withJSONObject: file), let json = String(data: data, encoding: .utf8) else {
                preconditionFailure("the file fixture is JSON")
            }
            return json
        }

        private static func text(_ text: String) -> [String: Any] { ["kind": "text", "text": text, "cut": false] }

        /// A CSV file as the Mac reads it: its cells, the header first.
        private static func table(_ rows: [[String]]) -> [String: Any] {
            var cells: [[String: Any]] = []
            for (row, fields) in rows.enumerated() {
                for (column, value) in fields.enumerated() {
                    cells.append(["row": row + 1, "column": column + 1, "value": value, "formula": NSNull()])
                }
            }
            return ["kind": "table", "sheet": ["title": "rows.csv", "state": "visible", "cells": cells, "cut_at_row": NSNull()], "max_cells": 20000]
        }

        private static func rows(count: Int) -> [[String]] {
            [["id", "name", "amount", "date", "note"]]
                + (1...count).map { ["\($0)", "Client \($0)", "\(($0 * 37) % 9000).\($0 % 100)", "2026-11-\(String(format: "%02d", 1 + $0 % 28))", "a note of row \($0)"] }
        }

        /// A report of `parts` parts, each a heading, a list with bold and code spans and a table of 14 notes.
        private static func report(parts: Int) -> String {
            (1...parts).map { part in
                let table: String = (1...14).map { "| N\($0) | \(500 + ($0 * 7 + part) % 100) | 268bd3, 3cf5a5, 29498e, 42116f, 63003e, b7a161 |" }.joined(separator: "\n")
                return """
                    ## Rapor \(part)

                    - Kaynak: `/Users/owner/Library/Application Support/Dot/workspace/memory-merge/C8.md` — **87 not** (toplam 18.615 karakter).
                    - Yeni not: **17** (hedef 10–16'nın bir üstü).
                    - Çelişki bulunmadı.

                    | Not | Karakter | Kapsadığı kaynak id'ler |
                    |---|---|---|
                    \(table)

                    """
            }
            .joined(separator: "\n")
        }
    }
#endif
