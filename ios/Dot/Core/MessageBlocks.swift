import Foundation

/// A message's text cut into what is drawn apart: prose and fenced code.
enum MessageBlock: Equatable, Sendable {
    case prose(String)
    case code(String)
}

enum MessageBlocks {
    /// Splits `text` at its ``` fences. An unclosed fence (a message still being written) is code to its end.
    static func parse(_ text: String) -> [MessageBlock] {
        var blocks: [MessageBlock] = []
        var prose: [Substring] = []
        var code: [Substring] = []
        var inCode: Bool = false
        func flushProse() {
            let joined: String = prose.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { blocks.append(.prose(bulleted(joined))) }
            prose = []
        }
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                if inCode {
                    blocks.append(.code(code.joined(separator: "\n")))
                    code = []
                } else {
                    flushProse()
                }
                inCode.toggle()
            } else if inCode {
                code.append(line)
            } else {
                prose.append(line)
            }
        }
        if inCode { blocks.append(.code(code.joined(separator: "\n"))) } else { flushProse() }
        return blocks
    }

    /// Markdown's list marks and headings as plain lines the inline reader keeps: "- x" is "• x", "## x" is bold.
    static func bulleted(_ prose: String) -> String {
        prose.split(separator: "\n", omittingEmptySubsequences: false).map { line -> String in
            let trimmed: String = line.trimmingCharacters(in: .whitespaces)
            let indent: String = String(line.prefix { $0 == " " })
            if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") { return indent + "• " + trimmed.dropFirst(2) }
            if trimmed.hasPrefix("#") {
                let title: String = trimmed.drop { $0 == "#" }.trimmingCharacters(in: .whitespaces)
                return title.isEmpty ? String(line) : "**\(title)**"
            }
            return String(line)
        }.joined(separator: "\n")
    }
}
