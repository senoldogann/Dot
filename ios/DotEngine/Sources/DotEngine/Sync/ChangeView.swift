import Foundation

// A file a chat's runs changed, as the phone shows it (item 2017): the unified diff read into numbered rows, a
// new file read as the file, a format chosen from its name (code by language, Markdown, JSON, an image or a PDF) and
// code coloured by a small scanner of its language. The web reads the same way (static/js/diffview.js); the two keep
// one set of rules. Pure.

/// What a row of a diff is: a hunk's head, a line added, removed or kept.
public enum DiffRowKind: String, Sendable, Hashable {
    case hunk
    case added
    case removed
    case context
}

/// A row of a diff: its line's number before and after the change (`nil` on the side it is not on), and the line
/// without its sign. A hunk's row holds the numbers it starts at and what follows its second `@@` (the enclosing
/// function or heading, trimmed; often "").
public struct DiffRow: Sendable, Hashable {
    public let kind: DiffRowKind
    public let old: Int?
    public let new: Int?
    public let text: String

    public init(kind: DiffRowKind, old: Int?, new: Int?, text: String) {
        self.kind = kind
        self.old = old
        self.new = new
        self.text = text
    }
}

/// The rows of the unified diff `diff`: the `---`/`+++` heads before the first hunk and `\ No newline` marks are left out.
public func diffRows(_ diff: String) -> [DiffRow] {
    var rows: [DiffRow] = []
    var old: Int = 0
    var new: Int = 0
    var inHunk: Bool = false
    for raw in diff.split(separator: "\n", omittingEmptySubsequences: false) {
        let line: String = String(raw)
        if line.hasPrefix("@@"), let head = hunkHead(line) {
            old = head.old
            new = head.new
            inHunk = true
            rows.append(DiffRow(kind: .hunk, old: head.old, new: head.new, text: head.heading))
            continue
        }
        guard inHunk, let sign = line.first else { continue }
        let text: String = String(line.dropFirst())
        switch sign {
        case "+":
            rows.append(DiffRow(kind: .added, old: nil, new: new, text: text))
            new += 1
        case "-":
            rows.append(DiffRow(kind: .removed, old: old, new: nil, text: text))
            old += 1
        case " ":
            rows.append(DiffRow(kind: .context, old: old, new: new, text: text))
            old += 1
            new += 1
        default:
            continue
        }
    }
    return rows
}

/// `@@ -a,b +c,d @@ heading`: the first old and new line numbers and the heading; `nil` for no such head.
private func hunkHead(_ line: String) -> (old: Int, new: Int, heading: String)? {
    let parts: [Substring] = line.split(separator: " ", maxSplits: 3, omittingEmptySubsequences: true)
    guard parts.count >= 3, parts[1].hasPrefix("-"), parts[2].hasPrefix("+") else { return nil }
    guard let old = Int(parts[1].dropFirst().split(separator: ",").first ?? ""), let new = Int(parts[2].dropFirst().split(separator: ",").first ?? "") else {
        return nil
    }
    let rest: String = parts.count == 4 ? String(parts[3]) : ""
    let heading: String = rest.hasPrefix("@@") ? String(rest.dropFirst(2)).trimmingCharacters(in: .whitespaces) : ""
    return (old, new, heading)
}

/// Whether the rows are a whole file made new: the runs created it, or its one diff adds every line to nothing.
public func isNewFile(_ rows: [DiffRow], created: Bool) -> Bool {
    if created { return true }
    guard let first = rows.first, first.kind == .hunk, first.old == 0 else { return false }
    return rows.allSatisfy { $0.kind == .hunk || $0.kind == .added }
}

/// A language the scanner colours.
public enum CodeLanguage: String, Sendable, Hashable, CaseIterable {
    case python
    case javascript
    case typescript
    case swift
    case json
    case toml
    case yaml
    case shell
    case markdown
    case html
    case css
    case rust
    case go
}

/// How a changed file is shown: code (coloured when its language is known), Markdown, JSON, an image or a PDF.
public enum ChangeFormat: Sendable, Hashable {
    case code(CodeLanguage?)
    case markdown
    case json
    case image
    case pdf
}

private let languageByExtension: [String: CodeLanguage] = [
    "py": .python, "pyi": .python,
    "js": .javascript, "mjs": .javascript, "cjs": .javascript, "jsx": .javascript,
    "ts": .typescript, "tsx": .typescript, "mts": .typescript, "cts": .typescript,
    "swift": .swift,
    "json": .json, "jsonl": .json, "webmanifest": .json,
    "toml": .toml,
    "yaml": .yaml, "yml": .yaml,
    "sh": .shell, "bash": .shell, "zsh": .shell,
    "md": .markdown, "markdown": .markdown,
    "html": .html, "htm": .html, "xml": .html, "svg": .html, "plist": .html,
    "css": .css, "scss": .css,
    "rs": .rust,
    "go": .go,
]

private let imageExtensions: Set<String> = ["png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff"]

/// The file's extension in lower case, "" for none.
private func fileExtension(_ path: String) -> String {
    let name: Substring = path.split(separator: "/").last ?? Substring(path)
    guard let dot = name.lastIndex(of: "."), dot != name.startIndex else { return "" }
    return name[name.index(after: dot)...].lowercased()
}

/// The language of the file at `path`, by its extension; `nil` for one the scanner does not know.
public func codeLanguage(path: String) -> CodeLanguage? {
    languageByExtension[fileExtension(path)]
}

/// How the file at `path` is shown, by its extension.
public func changeFormat(path: String) -> ChangeFormat {
    let ext: String = fileExtension(path)
    if imageExtensions.contains(ext) { return .image }
    if ext == "pdf" { return .pdf }
    if ext == "md" || ext == "markdown" { return .markdown }
    if ext == "json" { return .json }
    return .code(languageByExtension[ext])
}

/// What the change's sheet shows.
public enum ChangeLayout: Sendable, Hashable {
    /// A file changed: its diff's rows.
    case diff([DiffRow])
    /// A file the runs made: its text, read as the file (no sign on every line), and how it is shown.
    case newFile(text: String, format: ChangeFormat)
    /// An image or a PDF: shown as it is now, from the Mac.
    case media(ChangeFormat)
    /// No diff, and why ("" when the daemon said nothing).
    case note(String)
}

/// The layout of `file`'s change.
public func changeLayout(_ file: FileChange) -> ChangeLayout {
    let format: ChangeFormat = changeFormat(path: file.path)
    if (format == .image || format == .pdf) && !file.gone { return .media(format) }
    let rows: [DiffRow] = diffRows(file.diff)
    guard rows.contains(where: { $0.kind != .hunk }) else { return .note(file.note ?? "") }
    if isNewFile(rows, created: file.created) {
        return .newFile(text: rows.filter { $0.kind == .added }.map(\.text).joined(separator: "\n"), format: format)
    }
    return .diff(rows)
}

/// `text` as JSON laid out two spaces to a level, its keys in their own order; `nil` when it is no one JSON value (JSON
/// Lines, a file cut short).
public func prettyJSON(_ text: String) -> String? {
    guard let data = text.data(using: .utf8), (try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])) != nil else { return nil }
    var out: String = ""
    var depth: Int = 0
    var inString: Bool = false
    var escaped: Bool = false
    let chars: [Character] = Array(text)
    var index: Int = 0
    func newline() { out.append("\n" + String(repeating: "  ", count: depth)) }
    while index < chars.count {
        let char: Character = chars[index]
        index += 1
        if inString {
            out.append(char)
            if escaped {
                escaped = false
            } else if char == "\\" {
                escaped = true
            } else if char == "\"" {
                inString = false
            }
            continue
        }
        switch char {
        case "\"":
            inString = true
            out.append(char)
        case "{", "[":
            var next: Int = index
            while next < chars.count, chars[next].isWhitespace { next += 1 }
            if next < chars.count, chars[next] == "}" || chars[next] == "]" {
                out.append(char)
                out.append(chars[next])
                index = next + 1
            } else {
                out.append(char)
                depth += 1
                newline()
            }
        case "}", "]":
            depth -= 1
            newline()
            out.append(char)
        case ",":
            out.append(char)
            newline()
        case ":":
            out.append(": ")
        default:
            if !char.isWhitespace { out.append(char) }
        }
    }
    return out
}

// MARK: Colouring

/// What a piece of code is, for its colour.
public enum CodeTokenKind: String, Sendable, Hashable {
    case plain
    case keyword
    case string
    case comment
    case number
    /// A key of JSON, YAML or TOML, a tag's name, a Markdown list's mark.
    case key
    /// A Markdown heading.
    case heading
}

public struct CodeToken: Sendable, Hashable {
    public let kind: CodeTokenKind
    public let text: String

    public init(kind: CodeTokenKind, text: String) {
        self.kind = kind
        self.text = text
    }
}

private struct Grammar {
    let lineComments: [String]
    let blockComment: (open: String, close: String)?
    let quotes: Set<Character>
    /// Strings that may run over lines: Python's and Swift's triple quotes, JavaScript's backtick.
    let longStrings: [String]
    let keywords: Set<String>
}

private func words(_ text: String) -> Set<String> { Set(text.split(separator: " ").map(String.init)) }

private let jsKeywords: Set<String> = words(
    "async await break case catch class const continue debugger default delete do else export extends false finally for from function if import in instanceof let new null of return static super switch this throw true try typeof undefined var void while yield"
)

private func grammar(_ language: CodeLanguage) -> Grammar {
    switch language {
    case .python:
        Grammar(
            lineComments: ["#"], blockComment: nil, quotes: ["\"", "'"], longStrings: ["\"\"\"", "'''"],
            keywords: words(
                "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda match case nonlocal not or pass raise return self try while with yield"
            ))
    case .javascript:
        Grammar(lineComments: ["//"], blockComment: ("/*", "*/"), quotes: ["\"", "'"], longStrings: ["`"], keywords: jsKeywords)
    case .typescript:
        Grammar(
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: ["\"", "'"], longStrings: ["`"],
            keywords: jsKeywords.union(words("abstract any as boolean declare enum implements interface keyof never number private protected public readonly string type unknown")))
    case .swift:
        Grammar(
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: ["\""], longStrings: ["\"\"\""],
            keywords: words(
                "actor any as associatedtype async await break case catch class continue default defer do else enum extension fallthrough false fileprivate final for func guard if import in init inout internal is let mutating nil nonisolated open override private protocol public repeat rethrows return self Self some static struct subscript super switch throw throws true try typealias var weak where while"
            ))
    case .json:
        Grammar(lineComments: [], blockComment: nil, quotes: ["\""], longStrings: [], keywords: words("true false null"))
    case .toml:
        Grammar(lineComments: ["#"], blockComment: nil, quotes: ["\"", "'"], longStrings: ["\"\"\"", "'''"], keywords: words("true false"))
    case .yaml:
        Grammar(lineComments: ["#"], blockComment: nil, quotes: ["\"", "'"], longStrings: [], keywords: words("true false null yes no on off"))
    case .shell:
        Grammar(
            lineComments: ["#"], blockComment: nil, quotes: ["\"", "'"], longStrings: [],
            keywords: words("if then else elif fi for while until do done case esac function in return export local readonly set unset source exit trap shift"))
    case .css:
        Grammar(lineComments: [], blockComment: ("/*", "*/"), quotes: ["\"", "'"], longStrings: [], keywords: words("important"))
    case .rust:
        Grammar(
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: ["\""], longStrings: [],
            keywords: words(
                "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while"
            ))
    case .go:
        Grammar(
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: ["\"", "'"], longStrings: ["`"],
            keywords: words(
                "break case chan const continue default defer else fallthrough for func go goto if import interface map nil package range return select struct switch type var true false"
            ))
    case .markdown, .html:
        Grammar(lineComments: [], blockComment: nil, quotes: [], longStrings: [], keywords: [])
    }
}

/// What a line leaves open for the next one.
private enum Carry: Equatable {
    case none
    /// Inside a comment that ends with `close`.
    case comment(close: String)
    /// Inside a string that ends with `close`.
    case string(close: String)
    /// Inside a Markdown fence.
    case fence
    /// Inside an HTML tag.
    case tag
}

/// The pieces of each of `lines`, coloured as `language` reads them; a comment or a long string left open runs on to
/// the next line.
public func highlightLines(_ lines: [String], language: CodeLanguage) -> [[CodeToken]] {
    let rules: Grammar = grammar(language)
    var carry: Carry = .none
    return lines.map { line in
        let chars: [Character] = Array(line)
        var pieces: Pieces = Pieces()
        switch language {
        case .markdown: carry = markdownLine(chars, carry: carry, into: &pieces)
        case .html: carry = htmlLine(chars, carry: carry, into: &pieces)
        default: carry = codeLine(chars, rules: rules, language: language, carry: carry, into: &pieces)
        }
        return pieces.tokens
    }
}

/// Tokens gathered with neighbours of one kind joined.
private struct Pieces {
    var tokens: [CodeToken] = []

    mutating func add(_ kind: CodeTokenKind, _ chars: ArraySlice<Character>) {
        guard !chars.isEmpty else { return }
        if let last = tokens.last, last.kind == kind {
            tokens[tokens.count - 1] = CodeToken(kind: kind, text: last.text + String(chars))
        } else {
            tokens.append(CodeToken(kind: kind, text: String(chars)))
        }
    }
}

private func starts(_ chars: [Character], at index: Int, with prefix: String) -> Bool {
    let wanted: [Character] = Array(prefix)
    guard !wanted.isEmpty, index + wanted.count <= chars.count else { return false }
    return Array(chars[index..<(index + wanted.count)]) == wanted
}

/// Where `close` ends from `index` on (the index after it), `nil` when it does not in this line; `escapes` skips a
/// character after a backslash.
private func end(of close: String, in chars: [Character], from index: Int, escapes: Bool) -> Int? {
    var at: Int = index
    while at < chars.count {
        if escapes && chars[at] == "\\" {
            at += 2
            continue
        }
        if starts(chars, at: at, with: close) { return at + close.count }
        at += 1
    }
    return nil
}

private func isWordStart(_ char: Character) -> Bool { char.isLetter || char == "_" || char == "$" }
private func isWordPart(_ char: Character) -> Bool { char.isLetter || char.isNumber || char == "_" || char == "$" }

/// Where the YAML or TOML key that opens `chars` stands (after its indent, and a YAML list's `- `); `nil` for none.
private func keyPrefix(_ chars: [Character], language: CodeLanguage) -> (start: Int, end: Int)? {
    var start: Int = 0
    while start < chars.count, chars[start] == " " || chars[start] == "\t" { start += 1 }
    if language == .yaml, starts(chars, at: start, with: "- ") { start += 2 }
    guard start < chars.count, chars[start] != "#" else { return nil }
    var at: Int = start
    if language == .yaml {
        while at < chars.count, !(chars[at] == ":" && (at + 1 == chars.count || chars[at + 1] == " ")) { at += 1 }
    } else {
        while at < chars.count, chars[at] != "=" { at += 1 }
        guard chars[start..<min(at, chars.count)].allSatisfy({ isWordPart($0) || "-. \"'".contains($0) }) else { return nil }
    }
    guard at < chars.count, at > start else { return nil }
    var keyEnd: Int = at
    while keyEnd > start, chars[keyEnd - 1] == " " { keyEnd -= 1 }
    return keyEnd > start ? (start, keyEnd) : nil
}

private func codeLine(_ chars: [Character], rules: Grammar, language: CodeLanguage, carry start: Carry, into pieces: inout Pieces) -> Carry {
    var at: Int = 0
    switch start {
    case .comment(let close):
        guard let stop = end(of: close, in: chars, from: 0, escapes: false) else {
            pieces.add(.comment, chars[...])
            return start
        }
        pieces.add(.comment, chars[0..<stop])
        at = stop
    case .string(let close):
        guard let stop = end(of: close, in: chars, from: 0, escapes: true) else {
            pieces.add(.string, chars[...])
            return start
        }
        pieces.add(.string, chars[0..<min(stop, chars.count)])
        at = min(stop, chars.count)
    case .none, .fence, .tag:
        if language == .toml, let first = chars.firstIndex(where: { !$0.isWhitespace }), chars[first] == "[" {
            pieces.add(.plain, chars[0..<first])
            pieces.add(.keyword, chars[first...])
            return .none
        }
        if language == .yaml || language == .toml, let key = keyPrefix(chars, language: language) {
            pieces.add(.plain, chars[0..<key.start])
            pieces.add(.key, chars[key.start..<key.end])
            at = key.end
        }
    }
    while at < chars.count {
        let char: Character = chars[at]
        // A `#` opens a comment after a space or at the line's start (`$#`, a URL's anchor are not), always in Python.
        if rules.lineComments.contains(where: { starts(chars, at: at, with: $0) }), char != "#" || language == .python || at == 0 || chars[at - 1].isWhitespace {
            pieces.add(.comment, chars[at...])
            return .none
        }
        if let block = rules.blockComment, starts(chars, at: at, with: block.open) {
            guard let stop = end(of: block.close, in: chars, from: at + block.open.count, escapes: false) else {
                pieces.add(.comment, chars[at...])
                return .comment(close: block.close)
            }
            pieces.add(.comment, chars[at..<stop])
            at = stop
            continue
        }
        if let long = rules.longStrings.first(where: { starts(chars, at: at, with: $0) }) {
            guard let stop = end(of: long, in: chars, from: at + long.count, escapes: true) else {
                pieces.add(.string, chars[at...])
                return .string(close: long)
            }
            pieces.add(.string, chars[at..<min(stop, chars.count)])
            at = min(stop, chars.count)
            continue
        }
        if rules.quotes.contains(char) {
            let stop: Int = min(end(of: String(char), in: chars, from: at + 1, escapes: true) ?? chars.count, chars.count)
            var after: Int = stop
            while after < chars.count, chars[after] == " " { after += 1 }
            let isKey: Bool = language == .json && after < chars.count && chars[after] == ":"
            pieces.add(isKey ? .key : .string, chars[at..<stop])
            at = stop
            continue
        }
        if char.isNumber && (at == 0 || !isWordPart(chars[at - 1])) {
            var stop: Int = at + 1
            while stop < chars.count, chars[stop].isHexDigit || chars[stop] == "." || chars[stop] == "_" || chars[stop] == "x" || chars[stop] == "X" { stop += 1 }
            pieces.add(.number, chars[at..<stop])
            at = stop
            continue
        }
        if language == .css && (char == "@" || char == "#" || char == "!") && at + 1 < chars.count && isWordPart(chars[at + 1]) {
            var stop: Int = at + 1
            while stop < chars.count, isWordPart(chars[stop]) || chars[stop] == "-" { stop += 1 }
            pieces.add(char == "#" ? .number : .keyword, chars[at..<stop])
            at = stop
            continue
        }
        if isWordStart(char) {
            var stop: Int = at + 1
            while stop < chars.count, isWordPart(chars[stop]) { stop += 1 }
            let word: String = String(chars[at..<stop])
            pieces.add(rules.keywords.contains(word) ? .keyword : .plain, chars[at..<stop])
            at = stop
            continue
        }
        pieces.add(.plain, chars[at...at])
        at += 1
    }
    return .none
}

private func markdownLine(_ chars: [Character], carry: Carry, into pieces: inout Pieces) -> Carry {
    let trimmed: ArraySlice<Character> = chars.drop { $0 == " " }
    if starts(Array(trimmed), at: 0, with: "```") || starts(Array(trimmed), at: 0, with: "~~~") {
        pieces.add(.comment, chars[...])
        return carry == .fence ? .none : .fence
    }
    if carry == .fence {
        pieces.add(.string, chars[...])
        return .fence
    }
    if trimmed.first == "#" {
        pieces.add(.heading, chars[...])
        return .none
    }
    if trimmed.first == ">" {
        pieces.add(.comment, chars[...])
        return .none
    }
    var at: Int = chars.count - trimmed.count
    pieces.add(.plain, chars[0..<at])
    let rest: [Character] = Array(trimmed)
    if rest.count >= 2, rest[0] == "-" || rest[0] == "*" || rest[0] == "+", rest[1] == " " {
        pieces.add(.key, chars[at..<(at + 1)])
        at += 1
    } else if let dot = rest.firstIndex(where: { !$0.isNumber }), dot > 0, rest[dot] == "." || rest[dot] == ")" {
        pieces.add(.key, chars[at..<(at + dot + 1)])
        at += dot + 1
    }
    while at < chars.count {
        if chars[at] == "`", let stop = end(of: "`", in: chars, from: at + 1, escapes: false) {
            pieces.add(.string, chars[at..<stop])
            at = stop
            continue
        }
        pieces.add(.plain, chars[at...at])
        at += 1
    }
    return .none
}

private func htmlLine(_ chars: [Character], carry start: Carry, into pieces: inout Pieces) -> Carry {
    var at: Int = 0
    var carry: Carry = start
    while at < chars.count {
        if case .comment = carry {
            guard let stop = end(of: "-->", in: chars, from: at, escapes: false) else {
                pieces.add(.comment, chars[at...])
                return carry
            }
            pieces.add(.comment, chars[at..<stop])
            at = stop
            carry = .none
            continue
        }
        if carry == .tag {
            let char: Character = chars[at]
            if char == ">" || starts(chars, at: at, with: "/>") {
                let width: Int = char == ">" ? 1 : 2
                pieces.add(.plain, chars[at..<(at + width)])
                at += width
                carry = .none
            } else if char == "\"" || char == "'" {
                let stop: Int = end(of: String(char), in: chars, from: at + 1, escapes: false) ?? chars.count
                pieces.add(.string, chars[at..<stop])
                at = stop
            } else {
                pieces.add(.plain, chars[at...at])
                at += 1
            }
            continue
        }
        if starts(chars, at: at, with: "<!--") {
            carry = .comment(close: "-->")
            pieces.add(.comment, chars[at..<(at + 4)])
            at += 4
            continue
        }
        if chars[at] == "<", at + 1 < chars.count, chars[at + 1].isLetter || chars[at + 1] == "/" || chars[at + 1] == "!" || chars[at + 1] == "?" {
            var stop: Int = at + 1
            if chars[stop] == "/" || chars[stop] == "!" || chars[stop] == "?" { stop += 1 }
            pieces.add(.plain, chars[at..<stop])
            var nameEnd: Int = stop
            while nameEnd < chars.count, isWordPart(chars[nameEnd]) || chars[nameEnd] == "-" || chars[nameEnd] == ":" { nameEnd += 1 }
            pieces.add(.key, chars[stop..<nameEnd])
            at = nameEnd
            carry = .tag
            continue
        }
        pieces.add(.plain, chars[at...at])
        at += 1
    }
    return carry
}

// MARK: Lines of a code view

/// A line of a code view: a row of a diff (`kind` its kind; both numbers) or a line of a file (`kind` nil; `new` its
/// number), its text's coloured pieces. `id` is its place in the view.
public struct CodeLine: Sendable, Hashable, Identifiable {
    public let id: Int
    public let kind: DiffRowKind?
    public let old: Int?
    public let new: Int?
    public let tokens: [CodeToken]

    /// The text, its pieces joined.
    public var text: String { tokens.map(\.text).joined() }
}

/// The language a format's code is coloured in: a code file's own, Markdown's and JSON's; none for a picture or a PDF.
public func codeLanguage(of format: ChangeFormat) -> CodeLanguage? {
    switch format {
    case .code(let language): language
    case .markdown: .markdown
    case .json: .json
    case .image, .pdf: nil
    }
}

/// A tab drawn as four spaces: a code view's columns are of one width.
private func untabbed(_ text: String) -> String { text.replacingOccurrences(of: "\t", with: "    ") }

/// The pieces of `texts`, coloured in `language`; plain when there is none.
private func pieces(_ texts: [String], language: CodeLanguage?) -> [[CodeToken]] {
    guard let language else { return texts.map { [CodeToken(kind: .plain, text: $0)] } }
    return highlightLines(texts, language: language)
}

/// The lines of a diff's view: each hunk coloured on its own (a hunk starts in the middle of the file), a hunk's head
/// holding its heading as one plain piece.
public func codeLines(rows: [DiffRow], language: CodeLanguage?) -> [CodeLine] {
    var lines: [CodeLine] = []
    var start: Int = 0
    func flush(upTo end: Int) {
        let hunk: ArraySlice<DiffRow> = rows[start..<end]
        let coloured: [[CodeToken]] = pieces(hunk.map { untabbed($0.text) }, language: language)
        for (row, tokens) in zip(hunk, coloured) {
            lines.append(CodeLine(id: lines.count, kind: row.kind, old: row.old, new: row.new, tokens: tokens))
        }
    }
    for (index, row) in rows.enumerated() where row.kind == .hunk {
        flush(upTo: index)
        lines.append(CodeLine(id: lines.count, kind: .hunk, old: row.old, new: row.new, tokens: [CodeToken(kind: .plain, text: row.text)]))
        start = index + 1
    }
    flush(upTo: rows.count)
    return lines
}

/// The lines of a file's view: numbered from 1, coloured in `language`.
public func codeLines(text: String, language: CodeLanguage?) -> [CodeLine] {
    let texts: [String] = text.split(separator: "\n", omittingEmptySubsequences: false).map { untabbed(String($0)) }
    return zip(texts.indices, pieces(texts, language: language)).map { index, tokens in
        CodeLine(id: index, kind: nil, old: nil, new: index + 1, tokens: tokens)
    }
}
