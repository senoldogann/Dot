import DotEngine
import SwiftUI

/// Dot's answer as plain markdown (plan §4.1): headings, paragraphs, lists, tables that scroll sideways, quotes, rules
/// and code blocks with a header row; links in the ink and underlined. Selectable; a long press offers Copy.
///
/// Words being written arrive softly (item 1769): they are shown at a steady pace however the stream bunches them
/// (`revealStep`), and the newest few fade in from the quiet ink (`fadedTail`), so no block jumps in whole; once the
/// words caught up the fade settles over a few frames. An answer that is whole when it is first drawn shows at once.
/// Under Reduce Motion every word shows as it comes.
struct AnswerView: View {
    let markdown: String
    let writing: Bool
    /// How many characters show; the whole text unless words are being revealed.
    @State private var shown: Int
    /// How many of the newest shown characters are still fading in.
    @State private var tail: Int = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    init(markdown: String, writing: Bool) {
        self.markdown = markdown
        self.writing = writing
        _shown = State(initialValue: writing ? 0 : markdown.count)
    }

    var body: some View {
        let visible: String = shown >= markdown.count ? markdown : String(markdown.prefix(shown))
        let blocks: [MarkdownBlock] = markdownBlocks(visible)
        VStack(alignment: .leading, spacing: Space.s) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { index, block in
                view(of: block, fading: index == blocks.count - 1 ? tail : 0)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .task(id: markdown) { await reveal() }
    }

    /// Shows the words up to the text's end at a steady pace, then lets the newest settle.
    private func reveal() async {
        if reduceMotion {
            shown = markdown.count
            tail = 0
            return
        }
        let frame: Double = 1.0 / 60
        while shown < markdown.count || tail > 0 {
            do { try await Task.sleep(for: .seconds(frame)) } catch { return }
            PerfCounters.shared.revealed()
            if shown < markdown.count {
                shown = revealStep(shown: shown, target: markdown.count, seconds: frame)
                tail = fadeCharacters
            } else {
                tail = max(0, tail - 2)
            }
        }
    }

    @ViewBuilder private func view(of block: MarkdownBlock, fading: Int) -> some View {
        switch block {
        case .heading(let level, let text):
            Text(fadedTail(inline(text), characters: fading)).textRole(level <= 1 ? .title : level == 2 ? .headline : .body).fontWeight(.semibold)
                .foregroundStyle(Tone.text).accessibilityAddTraits(.isHeader)
                .padding(.top, level <= 2 ? Space.xs : 0)
        case .paragraph(let text):
            AnswerText(text: fadedTail(inline(text), characters: fading))
        case .listItem(let depth, let number, let text):
            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                Text(verbatim: number.map { "\($0)." } ?? "•").textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
                    .frame(minWidth: 16, alignment: .trailing)
                    .accessibilityHidden(number == nil)
                AnswerText(text: fadedTail(inline(text), characters: fading))
            }
            .padding(.leading, CGFloat(depth) * Space.l)
        case .code(let language, let text):
            CodeBlock(language: language.isEmpty ? String(localized: "Code", comment: "Code block header when no language is named") : language, code: text)
        case .table(let header, let rows):
            TableBlock(header: header.map(inline), rows: rows.map { $0.map(inline) })
        case .quote(let text):
            HStack(spacing: Space.s) {
                Rectangle().fill(Tone.separator).frame(width: 3).accessibilityHidden(true)
                AnswerText(text: inline(text)).foregroundStyle(Tone.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .rule:
            Rectangle().fill(Tone.separator).frame(height: 1).padding(.vertical, Space.xs).accessibilityHidden(true)
        }
    }

    private func inline(_ text: String) -> AttributedString {
        (try? answerText(markdown: text)) ?? AttributedString(text)
    }
}

/// How many of the newest characters fade in while words are revealed.
let fadeCharacters: Int = 28

/// How many characters show a frame of `seconds` after `shown` of `target` did: at least 80 a second, faster the more
/// waits (three times the backlog a second), so a bunch of words from the stream spreads over about a second instead of
/// landing at once, and a long wait never grows. Pure.
func revealStep(shown: Int, target: Int, seconds: Double) -> Int {
    guard shown < target else { return target }
    let rate: Double = max(80, Double(target - shown) * 3)
    return min(target, shown + max(1, Int((rate * seconds).rounded(.up))))
}

/// `text` with its last `characters` characters fading from the quiet ink (the newest) to the full one, in four steps.
/// Pure.
func fadedTail(_ text: AttributedString, characters: Int) -> AttributedString {
    let count: Int = text.characters.count
    guard characters > 0, count > 0 else { return text }
    var faded: AttributedString = text
    let steps: [Double] = [0.8, 0.6, 0.4, 0.2]
    let span: Int = min(characters, count)
    let each: Int = max(1, span / steps.count)
    for (index, opacity) in steps.enumerated() {
        let start: Int = count - span + index * each
        let end: Int = index == steps.count - 1 ? count : min(count, start + each)
        guard start < end else { continue }
        let lower = faded.characters.index(faded.startIndex, offsetBy: start)
        let upper = faded.characters.index(faded.startIndex, offsetBy: end)
        faded[lower..<upper].foregroundColor = Tone.text.opacity(opacity)
    }
    return faded
}

/// A table of an answer: its own width, scrolling sideways inside the reading column; the header row in the medium
/// weight on the nested fill.
struct TableBlock: View {
    let header: [AttributedString]
    let rows: [[AttributedString]]

    var body: some View {
        ScrollView(.horizontal) {
            Grid(alignment: .leading, horizontalSpacing: Space.m, verticalSpacing: Space.xs) {
                GridRow {
                    ForEach(Array(header.enumerated()), id: \.offset) { _, cell in
                        Text(cell).textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.text)
                    }
                }
                Divider().overlay(Tone.separator)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            Text(cell).textRole(.footnote).foregroundStyle(Tone.text).tint(Tone.text).fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: 260, alignment: .leading)
                        }
                    }
                }
            }
            .padding(Space.s)
        }
        .scrollIndicators(.hidden)
        .background(Tone.nested, in: rounded(Radius.inner))
        .textSelection(.enabled)
    }
}

/// The owner's message, folded at twice its folded height with "Show full message" (plan §4.1, R-c #10).
struct OwnerMessage: View {
    let text: String
    @State private var unfolded: Bool = false
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    /// The lines shown while folded; a message more than twice as long folds.
    static let foldedLines: Int = 12

    var body: some View {
        let (quote, words): (String?, String) = splitQuote(text)
        let long: Bool = words.split(whereSeparator: \.isNewline).count > Self.foldedLines * 2 || words.count > Self.foldedLines * 2 * 42
        VStack(alignment: .trailing, spacing: Space.xxs) {
            if let quote { QuoteBlock(quote: quote) }
            if !words.isEmpty { UserBubble(text: words, lineLimit: long && !unfolded ? Self.foldedLines : nil) }
            if long {
                Button(
                    unfolded
                        ? String(localized: "Show less", comment: "Under a long owner's message: folds it again")
                        : String(localized: "Show full message", comment: "Under a long owner's message: unfolds it")
                ) { unfolded.toggle() }
                .textRole(.label).fontWeight(.medium).foregroundStyle(Tone.secondary)
                .frame(minHeight: Metric.hit)
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }
}
