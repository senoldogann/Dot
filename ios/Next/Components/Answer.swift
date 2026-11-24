import DotEngine
import SwiftUI

/// Dot's answer as plain markdown (plan §4.1): headings, paragraphs, lists, tables that scroll sideways, quotes, rules
/// and code blocks with a header row; links in the ink and underlined. Selectable; a long press offers Copy.
struct AnswerView: View {
    let markdown: String
    let writing: Bool

    var body: some View {
        let blocks: [MarkdownBlock] = markdownBlocks(markdown)
        VStack(alignment: .leading, spacing: Space.s) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                view(of: block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .opacity(writing ? 0.85 : 1)
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder private func view(of block: MarkdownBlock) -> some View {
        switch block {
        case .heading(let level, let text):
            Text(inline(text)).textRole(level <= 1 ? .title : level == 2 ? .headline : .body).fontWeight(.semibold)
                .foregroundStyle(Tone.text).accessibilityAddTraits(.isHeader)
                .padding(.top, level <= 2 ? Space.xs : 0)
        case .paragraph(let text):
            AnswerText(text: inline(text))
        case .listItem(let depth, let number, let text):
            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                Text(verbatim: number.map { "\($0)." } ?? "•").textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
                    .frame(minWidth: 16, alignment: .trailing)
                    .accessibilityHidden(number == nil)
                AnswerText(text: inline(text))
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
        let long: Bool = text.split(whereSeparator: \.isNewline).count > Self.foldedLines * 2 || text.count > Self.foldedLines * 2 * 42
        VStack(alignment: .trailing, spacing: Space.xxs) {
            UserBubble(text: text, lineLimit: long && !unfolded ? Self.foldedLines : nil)
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
