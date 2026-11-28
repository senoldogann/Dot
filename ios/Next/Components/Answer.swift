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
                MarkdownBlockView(block: block, fading: index == blocks.count - 1 ? tail : 0)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        // A site's icon is asked for only once the answer is whole: never for the half-written address of
        // words still coming (`https://gi…` would name another site).
        .environment(\.fetchesSiteIcons, !writing && shown >= markdown.count)
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
}

/// One block of markdown as an answer draws it: a heading, a paragraph, a list's item, a code block, a table that
/// scrolls sideways, a quote or a rule; the newest `fading` characters of its words fading in. The chat's answers and
/// a Markdown file's preview draw their blocks with it.
struct MarkdownBlockView: View {
    let block: MarkdownBlock
    let fading: Int

    var body: some View {
        switch block {
        case .heading(let level, let text):
            ReferenceText(text: fadedTail(inline(text), characters: fading), role: level <= 1 ? .title : level == 2 ? .headline : .body).fontWeight(.semibold)
                .foregroundStyle(Tone.text).tint(Tone.text).accessibilityAddTraits(.isHeader)
                .padding(.top, level <= 2 ? Space.xs : 0)
        case .paragraph(let text):
            AnswerWords(text: fadedTail(inline(text), characters: fading))
        case .listItem(let depth, let number, let text):
            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                Text(verbatim: number.map { "\($0)." } ?? "•").textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
                    .frame(minWidth: 16, alignment: .trailing)
                    .accessibilityHidden(number == nil)
                AnswerWords(text: fadedTail(inline(text), characters: fading))
            }
            .padding(.leading, CGFloat(depth) * Space.l)
        case .code(let language, let text):
            CodeBlock(language: language.isEmpty ? String(localized: "Code", comment: "Code block header when no language is named") : language, code: text)
        case .table(let header, let rows):
            TableBlock(header: header.map(inline), rows: rows.map { $0.map(inline) })
        case .quote(let text):
            HStack(spacing: Space.s) {
                Rectangle().fill(Tone.separator).frame(width: 3).accessibilityHidden(true)
                AnswerWords(text: inline(text)).foregroundStyle(Tone.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .rule:
            Rectangle().fill(Tone.separator).frame(height: 1).padding(.vertical, Space.xs).accessibilityHidden(true)
        }
    }

    private func inline(_ text: String) -> AttributedString {
        (try? answerInline(markdown: text)) ?? AttributedString(text)
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

/// An answer's words with their references inline (`ReferenceText`): full width plain text in the body role,
/// selectable; links in the ink.
struct AnswerWords: View {
    let text: AttributedString

    var body: some View {
        ReferenceText(text: text, role: .body)
            .foregroundStyle(Tone.text)
            .tint(Tone.text)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A table of an answer: its columns fit the reading column where they can, a long cell wrapping onto lines of its own
/// so its row grows to fit it (`TableGrid`); a table too wide for that scrolls sideways. The header row in the medium
/// weight over a rule, on the nested fill.
struct TableBlock: View {
    let header: [AttributedString]
    let rows: [[AttributedString]]
    @State private var width: CGFloat = 0

    var body: some View {
        ScrollView(.horizontal) {
            TableGrid(columns: header.count, fit: max(0, width - Space.s * 2), spacing: Space.m, rowSpacing: Space.xs) {
                ForEach(Array(header.enumerated()), id: \.offset) { _, cell in
                    ReferenceText(text: cell, role: .footnote).fontWeight(.semibold).foregroundStyle(Tone.text)
                }
                Rectangle().fill(Tone.separator)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    ForEach(0..<header.count, id: \.self) { column in
                        ReferenceText(text: column < row.count ? row[column] : AttributedString(), role: .footnote).foregroundStyle(Tone.text).tint(Tone.text)
                    }
                }
            }
            .padding(Space.s)
        }
        .scrollIndicators(.hidden)
        .scrollBounceBehavior(.basedOnSize, axes: .horizontal)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .background(Tone.nested, in: rounded(Radius.inner))
        .textSelection(.enabled)
        .accessibilityIdentifier("answer.table")
    }
}

/// The grid of an answer's table: the header's cells, a rule, then the rows' cells, `columns` to a row. The columns'
/// widths come from `tableColumnWidths` (their widest cell on one line, the widest wrapping first to fit `fit`); each
/// row is as tall as its tallest cell at those widths, so a wrapped cell never draws over the next row.
struct TableGrid: Layout {
    let columns: Int
    let fit: CGFloat
    let spacing: CGFloat
    let rowSpacing: CGFloat

    /// The narrowest a column wraps to, and the widest a column's ideal is taken as before the table knows its width.
    static let minimumColumn: Double = 96
    static let unmeasuredColumn: CGFloat = 260

    struct Plan {
        var widths: [CGFloat]
        var heights: [CGFloat]
        var size: CGSize
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        plan(subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let plan: Plan = plan(subviews)
        var y: CGFloat = bounds.minY
        for (row, cells) in rowsOf(subviews).enumerated() {
            var x: CGFloat = bounds.minX
            for (column, cell) in cells.enumerated() {
                cell.place(at: CGPoint(x: x, y: y), anchor: .topLeading, proposal: ProposedViewSize(width: plan.widths[column], height: plan.heights[row]))
                x += plan.widths[column] + spacing
            }
            y += plan.heights[row] + rowSpacing
            if row == 0, let rule: LayoutSubview = ruleOf(subviews) {
                rule.place(at: CGPoint(x: bounds.minX, y: y), anchor: .topLeading, proposal: ProposedViewSize(width: plan.size.width, height: 1))
                y += 1 + rowSpacing
            }
        }
    }

    private func plan(_ subviews: Subviews) -> Plan {
        let rows: [[LayoutSubview]] = rowsOf(subviews)
        guard columns > 0, !rows.isEmpty else { return Plan(widths: [], heights: [], size: .zero) }
        let ideal: [Double] = (0..<columns).map { column in
            Double(rows.compactMap { $0.count > column ? $0[column].sizeThatFits(.unspecified).width.rounded(.up) : nil }.max() ?? 0)
        }
        let room: Double = fit > 0 ? Double(fit - spacing * CGFloat(columns - 1)) : Double(Self.unmeasuredColumn) * Double(columns)
        let widths: [CGFloat] = tableColumnWidths(ideal: ideal, fit: room, minimum: Self.minimumColumn).map { CGFloat($0) }
        let heights: [CGFloat] = rows.map { cells in
            cells.enumerated().map { column, cell in cell.sizeThatFits(ProposedViewSize(width: widths[column], height: nil)).height }.max() ?? 0
        }
        let width: CGFloat = widths.reduce(0, +) + spacing * CGFloat(columns - 1)
        let height: CGFloat = heights.reduce(0, +) + rowSpacing * CGFloat(rows.count - 1) + 1 + rowSpacing
        return Plan(widths: widths, heights: heights, size: CGSize(width: width, height: height))
    }

    /// The cells by row: the header's first, then each row's; the rule after the header is not a cell.
    private func rowsOf(_ subviews: Subviews) -> [[LayoutSubview]] {
        guard columns > 0, subviews.count > columns else { return [] }
        let cells: [LayoutSubview] = Array(subviews.prefix(columns)) + Array(subviews.dropFirst(columns + 1))
        return stride(from: 0, to: cells.count, by: columns).map { Array(cells[$0..<min($0 + columns, cells.count)]) }
    }

    private func ruleOf(_ subviews: Subviews) -> LayoutSubview? {
        subviews.count > columns ? subviews[columns] : nil
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
