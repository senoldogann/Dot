import DotEngine
import SwiftUI
import Synchronization

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
        // A whole answer's blocks are parsed once and kept (`DrawnAnswers`); words still being revealed as they show.
        let blocks: [DrawnBlock] = shown >= markdown.count ? DrawnAnswers.blocks(of: markdown) : drawnBlocks(String(markdown.prefix(shown)))
        VStack(alignment: .leading, spacing: Space.s) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { index, block in
                view(of: block, fading: index == blocks.count - 1 ? tail : 0)
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

    @ViewBuilder private func view(of block: DrawnBlock, fading: Int) -> some View {
        switch block {
        case .heading(let level, let text):
            ReferenceText(text: fadedTail(text, characters: fading), role: level <= 1 ? .title : level == 2 ? .headline : .body).fontWeight(.semibold)
                .foregroundStyle(Tone.text).tint(Tone.text).accessibilityAddTraits(.isHeader)
                .padding(.top, level <= 2 ? Space.xs : 0)
        case .paragraph(let text):
            AnswerWords(text: fadedTail(text, characters: fading))
        case .listItem(let depth, let number, let text):
            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                Text(verbatim: number.map { "\($0)." } ?? "•").textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
                    .frame(minWidth: 16, alignment: .trailing)
                    .accessibilityHidden(number == nil)
                AnswerWords(text: fadedTail(text, characters: fading))
            }
            .padding(.leading, CGFloat(depth) * Space.l)
        case .code(let language, let text):
            CodeBlock(language: language.isEmpty ? String(localized: "Code", comment: "Code block header when no language is named") : language, code: text)
        case .table(let header, let rows, let signature):
            TableBlock(header: header, rows: rows, signature: signature)
        case .quote(let text):
            HStack(spacing: Space.s) {
                Rectangle().fill(Tone.separator).frame(width: 3).accessibilityHidden(true)
                AnswerWords(text: text).foregroundStyle(Tone.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .rule:
            Rectangle().fill(Tone.separator).frame(height: 1).padding(.vertical, Space.xs).accessibilityHidden(true)
        }
    }
}

/// A block of an answer with its words parsed for drawing (`answerInline`): what `AnswerView` draws.
enum DrawnBlock {
    case heading(level: Int, text: AttributedString)
    case paragraph(AttributedString)
    case listItem(depth: Int, number: Int?, text: AttributedString)
    case code(language: String, text: String)
    /// A table's cells, and what they are drawn as (`tableSignature`), made once with the parse.
    case table(header: [AttributedString], rows: [[AttributedString]], signature: String)
    case quote(AttributedString)
    case rule
}

/// The blocks of `markdown` (`markdownBlocks`) with the words of each parsed (`answerInline`; words it cannot parse
/// show as they are).
func drawnBlocks(_ markdown: String) -> [DrawnBlock] {
    func inline(_ text: String) -> AttributedString { (try? answerInline(markdown: text)) ?? AttributedString(text) }
    return markdownBlocks(markdown).map { block in
        switch block {
        case .heading(let level, let text): .heading(level: level, text: inline(text))
        case .paragraph(let text): .paragraph(inline(text))
        case .listItem(let depth, let number, let text): .listItem(depth: depth, number: number, text: inline(text))
        case .code(let language, let text): .code(language: language, text: text)
        case .table(let header, let rows): drawnTable(header: header.map(inline), rows: rows.map { $0.map(inline) })
        case .quote(let text): .quote(inline(text))
        case .rule: .rule
        }
    }
}

/// A table's block, with what its cells are drawn as made once (`tableSignature`). Pure.
func drawnTable(header: [AttributedString], rows: [[AttributedString]]) -> DrawnBlock {
    .table(header: header, rows: rows, signature: tableSignature(header: header, rows: rows))
}

/// The parsed blocks of whole answers, kept while the app runs (owner 2026-11-27: scrolling a long chat stuttered; the
/// lazy transcript makes a row again each time it comes into sight, and each time its answer was parsed anew, headings,
/// tables and links). Keyed by the answer's words, so an answer that changes is parsed again; at most 400 are kept.
@MainActor
enum DrawnAnswers {
    /// The blocks of one answer, boxed for the cache.
    final class Kept {
        let blocks: [DrawnBlock]

        init(blocks: [DrawnBlock]) {
            self.blocks = blocks
        }
    }

    private static let kept: NSCache<NSString, Kept> = {
        let cache: NSCache<NSString, Kept> = NSCache<NSString, Kept>()
        cache.countLimit = 400
        return cache
    }()

    static func blocks(of markdown: String) -> [DrawnBlock] {
        let key: NSString = markdown as NSString
        if let held: Kept = kept.object(forKey: key) { return held.blocks }
        let blocks: [DrawnBlock] = drawnBlocks(markdown)
        kept.setObject(Kept(blocks: blocks), forKey: key)
        PerfCounters.shared.parsed()
        return blocks
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
    /// What the cells are drawn as (`tableSignature`), made once where the answer was parsed.
    let signature: String
    @State private var width: CGFloat = 0
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    @Environment(\.legibilityWeight) private var legibilityWeight: LegibilityWeight?

    var body: some View {
        let cells: [AttributedString] = header + rows.flatMap { $0 }
        let measures: TableMeasureKey = TableMeasureKey(
            cells: signature, type: dynamicTypeSize, bold: legibilityWeight == .bold,
            siteIcons: SiteIconBook.shared.drawnIcons(of: siteMarks(cells), side: referenceSide(role: .footnote, type: dynamicTypeSize)))
        ScrollView(.horizontal) {
            TableGrid(
                columns: header.count, fit: max(0, width - Space.s * 2), spacing: Space.m, rowSpacing: Space.xs,
                measures: measures
            ) {
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

/// What a table's cells measure by: the cells as drawn (`tableSignature`), the text size, Bold Text, and how many of the
/// cells' sites already have their icon at the size the cells draw it (an icon that arrives takes the place of a globe
/// of another width). Made once per drawing of the table, so a layout's lookup hashes one string.
struct TableMeasureKey: Hashable, Sendable {
    let cells: String
    let type: DynamicTypeSize
    let bold: Bool
    let siteIcons: Int
}

/// The cells of a table as they are drawn: each run's words with what decides their width (bold, italic, code and
/// strikethrough, a link's underline, the mark of a reference), so two tables of the same characters in other styles
/// never share their sizes; the header's cells first. Pure.
func tableSignature(header: [AttributedString], rows: [[AttributedString]]) -> String {
    ([header] + rows).map { row in
        row.map { cell in
            cell.runs.map { run in
                let words: String = String(cell[run.range].characters)
                let intent: UInt = run.inlinePresentationIntent?.rawValue ?? 0
                let mark: String = run.referenceMark.map { "\($0)" } ?? ""
                return "\(words)\u{1F}\(intent)\u{1F}\(run.link != nil)\u{1F}\(mark)"
            }.joined(separator: "\u{1E}")
        }.joined(separator: "\u{1D}")
    }.joined(separator: "\u{1C}")
}

/// The sites whose icons the cells draw before their links. Pure.
func siteMarks(_ cells: [AttributedString]) -> [URL] {
    cells.flatMap { cell in
        cell.runs.compactMap { run -> URL? in
            if case .site(let url)? = run.referenceMark { return url }
            return nil
        }
    }
}

/// The sizes of answers' tables, kept while the app runs (owner 2026-11-27: scrolling a long chat stuttered; each table
/// that came into sight measured every cell on one line and again at its column's width, for each of its two layouts,
/// before and after it knew its width: 47 % of the main thread's layout work while the chat scrolled). The one-line sizes
/// are kept by the table's `TableMeasureKey`, the plans by that and the room; a table seen again measures nothing. Layout
/// runs outside the main actor's isolation, so the book is behind a lock; at most 200 tables' sizes and 400 plans (a
/// table's before and after it knows its width) are kept, the oldest forgotten all at once.
enum TableMeasures {
    struct PlanKey: Hashable, Sendable {
        let measures: TableMeasureKey
        let fit: CGFloat
    }

    struct Book {
        var natural: [TableMeasureKey: [[CGSize]]]
        var plans: [PlanKey: TableGrid.Plan]
    }

    static let book: Mutex<Book> = Mutex(Book(natural: [:], plans: [:]))
    static let kept: Int = 200

    static func natural(_ measures: TableMeasureKey) -> [[CGSize]]? { book.withLock { $0.natural[measures] } }

    static func plan(_ key: PlanKey) -> TableGrid.Plan? { book.withLock { $0.plans[key] } }

    static func keep(natural: [[CGSize]], measures: TableMeasureKey, plan: TableGrid.Plan, key: PlanKey) {
        book.withLock { book in
            if book.natural.count >= kept { book.natural = [:] }
            if book.plans.count >= kept * 2 { book.plans = [:] }
            book.natural[measures] = natural
            book.plans[key] = plan
        }
    }
}

/// The grid of an answer's table: the header's cells, a rule, then the rows' cells, `columns` to a row. The columns'
/// widths come from `tableColumnWidths` (their widest cell on one line, the widest wrapping first to fit `fit`); each
/// row is as tall as its tallest cell at those widths, so a wrapped cell never draws over the next row. The sizes are
/// kept by `measures` (`TableMeasures`): a cell is measured on one line once, and only a cell its column wraps is
/// measured again at that column's width.
struct TableGrid: Layout {
    let columns: Int
    let fit: CGFloat
    let spacing: CGFloat
    let rowSpacing: CGFloat
    /// What the cells measure by (`TableMeasureKey`).
    let measures: TableMeasureKey

    /// The narrowest a column wraps to, and the widest a column's ideal is taken as before the table knows its width.
    static let minimumColumn: Double = 96
    static let unmeasuredColumn: CGFloat = 260

    struct Plan: Sendable {
        var widths: [CGFloat]
        var heights: [CGFloat]
        var size: CGSize
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        planned(subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let plan: Plan = planned(subviews)
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

    /// The kept plan of these cells in this room, else one made from their kept one-line sizes (measured the first time).
    private func planned(_ subviews: Subviews) -> Plan {
        let key: TableMeasures.PlanKey = TableMeasures.PlanKey(measures: measures, fit: fit)
        if let kept: Plan = TableMeasures.plan(key) { return kept }
        let rows: [[LayoutSubview]] = rowsOf(subviews)
        let natural: [[CGSize]] =
            TableMeasures.natural(measures).flatMap { $0.map(\.count) == rows.map(\.count) ? $0 : nil }
            ?? rows.map { cells in cells.map { $0.sizeThatFits(.unspecified) } }
        let made: Plan = plan(rows, natural: natural)
        TableMeasures.keep(natural: natural, measures: measures, plan: made, key: key)
        return made
    }

    /// The columns' widths from the cells' one-line widths; each row as tall as its tallest cell, a cell whose column
    /// holds its line keeping that line's height and a wrapped one measured at its column's width.
    private func plan(_ rows: [[LayoutSubview]], natural: [[CGSize]]) -> Plan {
        guard columns > 0, !rows.isEmpty else { return Plan(widths: [], heights: [], size: .zero) }
        let ideal: [Double] = (0..<columns).map { column in
            Double(natural.compactMap { $0.count > column ? $0[column].width.rounded(.up) : nil }.max() ?? 0)
        }
        let room: Double = fit > 0 ? Double(fit - spacing * CGFloat(columns - 1)) : Double(Self.unmeasuredColumn) * Double(columns)
        let widths: [CGFloat] = tableColumnWidths(ideal: ideal, fit: room, minimum: Self.minimumColumn).map { CGFloat($0) }
        let heights: [CGFloat] = zip(rows, natural).map { cells, sizes in
            zip(cells, sizes).enumerated().map { column, cell in
                widths[column] >= cell.1.width.rounded(.up)
                    ? cell.1.height : cell.0.sizeThatFits(ProposedViewSize(width: widths[column], height: nil)).height
            }.max() ?? 0
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
