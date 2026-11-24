import SwiftUI

/// Text for a chat message: a link is the text colour, underlined (the look has no link colour: item 1641), with no border, no
/// chip and no background. Markdown links and bare addresses both.
enum LinkStyle {
    private static let detector: NSDataDetector? = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)

    /// `text` as an attributed string: its inline markdown (bold, italic, code, links) read, its bare addresses made
    /// links, every link in the ink with an underline.
    static func styled(_ text: String, ink: Color = Theme.text) -> AttributedString {
        var result: AttributedString =
            (try? AttributedString(
                markdown: text,
                options: AttributedString.MarkdownParsingOptions(
                    allowsExtendedAttributes: false, interpretedSyntax: .inlineOnlyPreservingWhitespace,
                    failurePolicy: .returnPartiallyParsedIfPossible))) ?? AttributedString(text)
        link(bare: &result)
        for run in result.runs where run.link != nil {
            result[run.range].foregroundColor = ink
            result[run.range].underlineStyle = .single
            result[run.range].backgroundColor = nil
        }
        for run in result.runs where run.inlinePresentationIntent?.contains(.code) == true {
            result[run.range].font = Theme.mono
            result[run.range].backgroundColor = Theme.codeFill
        }
        return result
    }

    /// Makes the addresses in the text that are not links yet links.
    private static func link(bare string: inout AttributedString) {
        let plain: String = String(string.characters)
        guard let detector, plain.contains("://") || plain.contains("www.") || plain.contains("@") else { return }
        let matches: [NSTextCheckingResult] = detector.matches(in: plain, range: NSRange(plain.startIndex..., in: plain))
        for match in matches {
            guard let url: URL = match.url, let range: Range<String.Index> = Range(match.range, in: plain),
                let lower: AttributedString.Index = AttributedString.Index(range.lowerBound, within: string),
                let upper: AttributedString.Index = AttributedString.Index(range.upperBound, within: string)
            else { continue }
            if string[lower..<upper].link == nil { string[lower..<upper].link = url }
        }
    }
}
