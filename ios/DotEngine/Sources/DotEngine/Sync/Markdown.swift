import Foundation

/// A block of an answer's markdown (plan §4.1: headings, lists, tables that scroll sideways, code blocks with a header
/// row, quotes). The text of a block keeps its inline markdown (bold, links, code spans) for the app to draw.
public enum MarkdownBlock: Sendable, Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    /// One item of a list: its depth (0 for the top), its number for an ordered list (`nil` for a bullet), its text.
    case listItem(depth: Int, number: Int?, text: String)
    case code(language: String, text: String)
    case table(header: [String], rows: [[String]])
    case quote(String)
    case rule
}

/// The blocks of `markdown`, in order. A fence left open runs to the end (an answer still being written); a table needs
/// its header row and its `---` row. Pure.
public func markdownBlocks(_ markdown: String) -> [MarkdownBlock] {
    var blocks: [MarkdownBlock] = []
    var paragraph: [String] = []
    var quote: [String] = []
    let lines: [String] = markdown.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
    var index: Int = 0

    func flushParagraph() {
        if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))) }
        paragraph = []
    }
    func flushQuote() {
        if !quote.isEmpty { blocks.append(.quote(quote.joined(separator: "\n"))) }
        quote = []
    }

    while index < lines.count {
        let line: String = lines[index]
        let trimmed: String = line.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
            flushParagraph()
            flushQuote()
            let fence: String = String(trimmed.prefix(3))
            let language: String = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
            var code: [String] = []
            index += 1
            while index < lines.count, !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix(fence) {
                code.append(lines[index])
                index += 1
            }
            blocks.append(.code(language: language, text: code.joined(separator: "\n")))
            index += 1
            continue
        }
        if trimmed.isEmpty {
            flushParagraph()
            flushQuote()
            index += 1
            continue
        }
        if let heading: (Int, String) = headingOf(trimmed) {
            flushParagraph()
            flushQuote()
            blocks.append(.heading(level: heading.0, text: heading.1))
            index += 1
            continue
        }
        if isRule(trimmed) {
            flushParagraph()
            flushQuote()
            blocks.append(.rule)
            index += 1
            continue
        }
        if trimmed.hasPrefix(">") {
            flushParagraph()
            quote.append(String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces))
            index += 1
            continue
        }
        if trimmed.hasPrefix("|"), index + 1 < lines.count, isTableRule(lines[index + 1]) {
            flushParagraph()
            flushQuote()
            let header: [String] = cells(trimmed)
            var rows: [[String]] = []
            index += 2
            while index < lines.count, lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("|") {
                let row: [String] = cells(lines[index].trimmingCharacters(in: .whitespaces))
                rows.append(Array((row + Array(repeating: "", count: max(0, header.count - row.count))).prefix(header.count)))
                index += 1
            }
            blocks.append(.table(header: header, rows: rows))
            continue
        }
        if let item: (Int?, String) = listItemOf(trimmed) {
            flushParagraph()
            flushQuote()
            let indent: Int = line.prefix { $0 == " " || $0 == "\t" }.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
            blocks.append(.listItem(depth: min(indent / 2, 4), number: item.0, text: item.1))
            index += 1
            continue
        }
        flushQuote()
        // A line that continues a list item (indented, no marker) belongs to the item.
        if paragraph.isEmpty, case .listItem(let depth, let number, let text)? = blocks.last, line.hasPrefix("  ") {
            blocks[blocks.count - 1] = .listItem(depth: depth, number: number, text: text + "\n" + trimmed)
            index += 1
            continue
        }
        paragraph.append(trimmed)
        index += 1
    }
    flushParagraph()
    flushQuote()
    return blocks
}

private func headingOf(_ line: String) -> (Int, String)? {
    let marks: Int = line.prefix { $0 == "#" }.count
    guard (1...6).contains(marks), line.dropFirst(marks).first == " " else { return nil }
    return (marks, String(line.dropFirst(marks + 1)).trimmingCharacters(in: .whitespaces))
}

private func isRule(_ line: String) -> Bool {
    let compact: String = line.replacingOccurrences(of: " ", with: "")
    guard compact.count >= 3, let first: Character = compact.first, "-*_".contains(first) else { return false }
    return compact.allSatisfy { $0 == first }
}

private func isTableRule(_ line: String) -> Bool {
    let trimmed: String = line.trimmingCharacters(in: .whitespaces)
    guard trimmed.contains("-"), trimmed.contains("|") || trimmed.hasPrefix("-") else { return false }
    return trimmed.allSatisfy { "|:- ".contains($0) }
}

private func cells(_ row: String) -> [String] {
    var inner: Substring = Substring(row)
    if inner.hasPrefix("|") { inner = inner.dropFirst() }
    if inner.hasSuffix("|") { inner = inner.dropLast() }
    return inner.split(separator: "|", omittingEmptySubsequences: false).map { $0.trimmingCharacters(in: .whitespaces) }
}

private func listItemOf(_ line: String) -> (Int?, String)? {
    for bullet in ["- ", "* ", "+ "] where line.hasPrefix(bullet) {
        return (nil, String(line.dropFirst(2)))
    }
    let digits: Substring = line.prefix { $0.isNumber }
    guard !digits.isEmpty, digits.count <= 4, let number: Int = Int(digits) else { return nil }
    let rest: Substring = line.dropFirst(digits.count)
    guard rest.hasPrefix(". ") || rest.hasPrefix(") ") else { return nil }
    return (number, String(rest.dropFirst(2)))
}
