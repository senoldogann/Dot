import Foundation

/// A CSV or TSV file as the Mac reads it for the file panel (`TablePreview` of `previews/shown.py`): its rows of cells
/// as text, the first row the header, and the row reading stopped at when the file held more cells than were read.
public struct FileTable: Sendable, Hashable {
    public let rows: [[String]]
    public let cutAtRow: Int?

    public init(rows: [[String]], cutAtRow: Int?) {
        self.rows = rows
        self.cutAtRow = cutAtRow
    }
}

/// A cell of the Mac's sheet (`SheetCell`): its row and column from 1 and the value it shows (`nil` while a formula is
/// not worked out).
struct FileSheetCell: Decodable, Sendable {
    let row: Int
    let column: Int
    let value: String?
}

/// The Mac's sheet of a table (`Sheet`): only its cells and where reading stopped are read.
struct FileSheet: Decodable, Sendable {
    let cells: [FileSheetCell]
    let cutAtRow: Int?

    private enum Keys: String, CodingKey {
        case cells
        case cutAtRow = "cut_at_row"
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: Keys.self)
        cells = try container.decode([FileSheetCell].self, forKey: .cells)
        cutAtRow = try container.decodeIfPresent(Int.self, forKey: .cutAtRow)
    }
}

/// The rows of a sheet's `cells` (which hold only the cells with anything in them): each row that holds a cell, in
/// order, as wide as the widest, an empty cell "" (pure).
func fileTableRows(_ cells: [FileSheetCell]) -> [[String]] {
    let width: Int = cells.map(\.column).max() ?? 0
    guard width > 0 else { return [] }
    var byRow: [Int: [String]] = [:]
    for cell in cells where cell.column >= 1 {
        var row: [String] = byRow[cell.row] ?? Array(repeating: "", count: width)
        row[cell.column - 1] = cell.value ?? ""
        byRow[cell.row] = row
    }
    return byRow.keys.sorted().compactMap { byRow[$0] }
}

/// The width of each of a table's columns in characters: its longest cell among the first `sample` rows, at least
/// `minimum` and at most `maximum` (a longer cell wraps), so a table of thousands of rows is measured once, from its
/// start, and never by laying every cell out (pure).
public func fileTableColumnCharacters(_ rows: [[String]], sample: Int, minimum: Int, maximum: Int) -> [Int] {
    let width: Int = rows.map(\.count).max() ?? 0
    var widest: [Int] = Array(repeating: minimum, count: width)
    for row in rows.prefix(sample) {
        for (column, cell) in row.enumerated() {
            widest[column] = max(widest[column], min(maximum, cell.count))
        }
    }
    return widest
}

/// Whether a cell reads as a number, which a table aligns to the right as a spreadsheet does (the web's `NUMBER` of
/// filepanel.js): digits with signs, separators, spaces and a closing percent (pure).
public func fileTableCellIsNumber(_ cell: String) -> Bool {
    cell.wholeMatch(of: /[-+]?[\d\s.,']*\d%?/) != nil
}

/// How a text file is read in the file preview, by its name: Markdown formatted, JSON laid out, code coloured by its
/// language (monospaced, never wrapped), or plain text that wraps.
public enum FileTextFormat: Sendable, Hashable {
    case markdown
    case json
    case code(CodeLanguage?)
    case plain
}

/// The format of the text file named `name` (pure): the change view's rules (`changeFormat`), a file the scanner has
/// no language for read as code when it is a source file, else as plain text.
public func fileTextFormat(name: String) -> FileTextFormat {
    switch changeFormat(path: name) {
    case .markdown: return .markdown
    case .json: return .json
    case .code(let language?): return .code(language)
    case .code(nil), .image, .pdf: return answerFileKind(name) == .code ? .code(nil) : .plain
    }
}

/// `text` in pieces of at most `lines` lines each, so a long text is drawn piece by piece as it scrolls into sight
/// rather than laid out whole when it opens (pure).
public func textPieces(_ text: String, lines: Int) -> [String] {
    let all: [Substring] = text.split(separator: "\n", omittingEmptySubsequences: false)
    return stride(from: 0, to: all.count, by: max(1, lines)).map { all[$0..<min($0 + max(1, lines), all.count)].joined(separator: "\n") }
}

/// `rows` written as a CSV (or TSV) text again, a cell that holds the delimiter, a quote or a line break quoted, for
/// Share and Save to Files of a table the Mac read (pure).
public func fileTableText(_ rows: [[String]], delimiter: Character) -> String {
    rows.map { row in
        row.map { cell in
            guard cell.contains(delimiter) || cell.contains("\"") || cell.contains("\n") || cell.contains("\r") else { return cell }
            return "\"" + cell.replacingOccurrences(of: "\"", with: "\"\"") + "\""
        }
        .joined(separator: String(delimiter))
    }
    .joined(separator: "\n") + "\n"
}
