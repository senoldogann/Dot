import DotEngine
import SwiftUI
import UIKit

// A text file read by its format in the file preview (the owner's shot of 2026-11-28: a Markdown report showed its
// `##`, `**` and table pipes as raw text): Markdown drawn as the chat draws an answer, a table as a grid, plain text
// in pieces. Each is drawn lazily, so a file of hundreds of kilobytes or thousands of rows opens at once.

/// What a text file's preview draws, built once off the main thread (`fileTextBody`).
enum FileTextBody {
    case markdown([MarkdownBlock])
    case code([CodeLine])
    case plain([String])
}

/// The body of the text of a file of `format` (pure, run off the main thread): Markdown's blocks, the lines of code
/// (JSON laid out first), or plain text in pieces of 60 lines.
func fileTextBody(_ text: String, format: FileTextFormat) -> FileTextBody {
    switch format {
    case .markdown: .markdown(markdownBlocks(text))
    case .json: .code(codeLines(text: prettyJSON(text) ?? text, language: .json))
    case .code(let language): .code(codeLines(text: text, language: language))
    case .plain: .plain(textPieces(text, lines: 60))
    }
}

/// A Markdown file as the chat draws an answer: its blocks one under another, drawn as they scroll into sight. A table
/// of more than `inlineRows` rows shows its first rows, with all of them a tap away on a grid of their own.
struct MarkdownDocument: View {
    let blocks: [MarkdownBlock]
    /// The rows a table shows in the flow of the text; a longer one opens whole on its own grid.
    static let inlineRows: Int = 40

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.s) {
                ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                    if case .table(let header, let rows) = block, rows.count > Self.inlineRows {
                        LongTable(header: header, rows: rows)
                    } else {
                        MarkdownBlockView(block: block, fading: 0)
                    }
                }
            }
            .padding(Space.gutter)
            .readableColumn()
        }
        .accessibilityIdentifier("file.markdown")
    }
}

/// A Markdown table too long for the flow of the text: its first rows as the chat draws a table, then a row that opens
/// the whole table on its own grid.
private struct LongTable: View {
    let header: [String]
    let rows: [[String]]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            MarkdownBlockView(block: .table(header: header, rows: Array(rows.prefix(MarkdownDocument.inlineRows))), fading: 0)
            NavigationLink {
                FileTableView(table: FileTable(rows: [header] + rows, cutAtRow: nil))
                    .navigationTitle(String(localized: "Table", comment: "File preview: the title of a long table opened whole"))
                    .navigationBarTitleDisplayMode(.inline)
                    .background(Tone.sheet)
            } label: {
                Label(
                    String(localized: "Show all \(rows.count) rows", comment: "File preview: opens a long table whole; the argument is its row count"),
                    systemImage: "tablecells"
                )
                .textRole(.label)
                .frame(minHeight: Metric.hit)
            }
            .accessibilityIdentifier("file.table.all")
        }
    }
}

/// A table of a file (a CSV or TSV file, or a long Markdown table): its header pinned at the top, its rows drawn as
/// they scroll into sight, every column as wide as its widest cell among the first rows (a longer cell wraps onto a few
/// lines), numbers aligned to the right; the grid scrolls both ways.
struct FileTableView: View {
    let table: FileTable
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let header: [String] = table.rows.first ?? []
        let rows: ArraySlice<[String]> = table.rows.dropFirst()
        let character: CGFloat = tableCharacterWidth(dynamicTypeSize)
        let widths: [CGFloat] = fileTableColumnCharacters(table.rows, sample: 200, minimum: 3, maximum: 32).map { CGFloat($0) * character + Space.s * 2 }
        ScrollView([.horizontal, .vertical]) {
            LazyVStack(alignment: .leading, spacing: 0, pinnedViews: [.sectionHeaders]) {
                Section {
                    ForEach(rows.indices, id: \.self) { index in
                        FileTableRow(cells: rows[index], header: header, widths: widths)
                    }
                    if let cut = table.cutAtRow {
                        Text(String(localized: "The table stops at row \(cut) here; open it on the Mac for the rest.", comment: "File preview: a table too large was read only to a row; the argument is that row"))
                            .textRole(.footnote).foregroundStyle(Tone.tertiary)
                            .padding(Space.s)
                    }
                } header: {
                    FileTableRow(cells: header, header: nil, widths: widths)
                        .fontWeight(.semibold)
                        .background(Tone.nested)
                }
            }
        }
        .scrollIndicators(.visible)
        .accessibilityIdentifier("file.table")
    }
}

/// One row of a file's table: its cells at the columns' widths over a hairline. `header` names the columns for
/// VoiceOver ("Note: N1, Characters: 521"); the header row itself has none.
private struct FileTableRow: View {
    let cells: [String]
    let header: [String]?
    let widths: [CGFloat]

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(widths.indices, id: \.self) { column in
                let cell: String = column < cells.count ? cells[column] : ""
                Text(verbatim: cell)
                    .textRole(.footnote)
                    .foregroundStyle(Tone.text)
                    .lineLimit(4)
                    .multilineTextAlignment(header != nil && fileTableCellIsNumber(cell) ? .trailing : .leading)
                    .frame(width: widths[column] - Space.s * 2, alignment: header != nil && fileTableCellIsNumber(cell) ? .trailing : .leading)
                    .padding(.horizontal, Space.s)
            }
        }
        .padding(.vertical, Space.xs)
        .overlay(alignment: .bottom) { Rectangle().fill(Tone.separator).frame(height: Space.hair) }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
        .accessibilityIdentifier(header == nil ? "file.table.header" : "file.table.row")
    }

    private var spoken: String {
        guard let header else { return cells.joined(separator: ", ") }
        return zip(header, cells).filter { !$0.1.isEmpty }.map { "\($0.0): \($0.1)" }.joined(separator: ", ")
    }
}

/// The width of an average character of the footnote role at the reader's text size, for a table's columns.
private func tableCharacterWidth(_ size: DynamicTypeSize) -> CGFloat {
    let metrics: UIFontMetrics = UIFontMetrics(forTextStyle: TypeRole.footnote.uiTextStyle)
    let points: CGFloat = metrics.scaledValue(for: TypeRole.footnote.points.size, compatibleWith: UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(size)))
    let sample: String = "abcdefghijklmnopqrstuvwxyz0123456789"
    return (sample as NSString).size(withAttributes: [.font: UIFont.systemFont(ofSize: points)]).width / CGFloat(sample.count)
}

/// Plain text that wraps, in pieces drawn as they scroll into sight; selectable.
struct PlainTextDocument: View {
    let pieces: [String]

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(pieces.indices, id: \.self) { index in
                    Text(verbatim: pieces[index])
                        .textRole(.body)
                        .foregroundStyle(Tone.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(Space.gutter)
            .readableColumn()
        }
        .accessibilityIdentifier("file.text")
    }
}
