import DotEngine
import SwiftUI
import UIKit

/// A file's change, opened from "This chat" (item 2017; the owner's shot of 2026-11-27 showed a raw diff in the
/// body font, wrapped, with no colour). Read by its format (`changeLayout`): a file changed as a diff, a new file as the
/// file (no sign on every line), a new Markdown file formatted with its source a tap away, JSON laid out. Code is
/// monospaced, never wrapped (it scrolls sideways), numbered, coloured by its language; a line added or removed is
/// tinted with its sign and a hunk's head is a quiet band. The lines are built off the main thread and drawn lazily, so
/// a file of thousands of lines opens at once. An image or a PDF opens in the file preview instead (`FilePreviewSheet`).
struct ChangeSheet: View {
    let file: FileChange
    let close: () -> Void
    /// The change read once, when the sheet is made: the toolbar, the body and the subtitle all read it, and a diff of
    /// thousands of lines is not parsed again on each draw.
    @State private var layout: ChangeLayout
    @State private var lines: [CodeLine]?
    /// A new Markdown file's blocks, read once off the main thread.
    @State private var blocks: [MarkdownBlock]?
    @State private var showsSource: Bool = false

    init(file: FileChange, close: @escaping () -> Void) {
        self.file = file
        self.close = close
        _layout = State(initialValue: changeLayout(file))
    }
    private var name: String { (file.path as NSString).lastPathComponent }

    var body: some View {
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Tone.sheet)
                .navigationTitle(name)
                .navigationSubtitle(subtitle)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                    }
                    if case .newFile(_, .markdown) = layout {
                        ToolbarItem(placement: .principal) { markdownPicker }
                    }
                    ToolbarItem(placement: .primaryAction) {
                        Button(String(localized: "Copy", comment: "Change sheet: copies the change's text"), systemImage: "doc.on.doc") { UIPasteboard.general.string = copiedText }
                            .accessibilityIdentifier("change.copy")
                    }
                }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .tint(Tone.text)
        .task(id: file) {
            let built: ChangeLayout = layout
            let format: ChangeFormat = changeFormat(path: file.path)
            // A new Markdown file opens formatted: its blocks are read first, its lines for Source after.
            if case .newFile(let text, .markdown) = built {
                blocks = await Task.detached(priority: .userInitiated) { markdownBlocks(text) }.value
            }
            lines = await Task.detached(priority: .userInitiated) { () -> [CodeLine] in buildLines(built, format: format) }.value
        }
    }

    @ViewBuilder private var content: some View {
        switch layout {
        case .newFile(_, .markdown) where !showsSource:
            if let blocks {
                MarkdownDocument(blocks: blocks)
                    .textSelection(.enabled)
                    .accessibilityIdentifier("change.markdown")
            } else {
                ProgressView().accessibilityLabel(String(localized: "Loading the change", comment: "VoiceOver: a change's lines are being laid out"))
            }
        case .note(let why):
            EmptyState(
                title: String(localized: "No diff to show", comment: "Change sheet: the daemon has no diff of the file"), systemImage: "doc.questionmark",
                detail: why, actionTitle: nil, action: {})
        case .media:
            EmptyState(
                title: String(localized: "No diff to show", comment: "Change sheet: the daemon has no diff of the file"), systemImage: "photo",
                detail: "", actionTitle: nil, action: {})
        case .diff, .newFile:
            if let lines {
                CodeLinesView(lines: lines)
            } else {
                ProgressView().accessibilityLabel(String(localized: "Loading the change", comment: "VoiceOver: a change's lines are being laid out"))
            }
        }
    }

    private var markdownPicker: some View {
        Picker(String(localized: "View", comment: "Change sheet: how a Markdown file is shown"), selection: $showsSource) {
            Text(String(localized: "Formatted", comment: "Change sheet: a Markdown file shown formatted")).tag(false)
            Text(String(localized: "Source", comment: "Change sheet: a Markdown file shown as it is written")).tag(true)
        }
        .pickerStyle(.segmented)
        .fixedSize()
        .accessibilityIdentifier("change.view")
    }

    /// "New · 53 lines", "+3 −1", "Deleted".
    private var subtitle: String {
        if file.gone { return String(localized: "Deleted", comment: "Change sheet subtitle: the file is gone") }
        if case .newFile(let text, _) = layout {
            let count: Int = text.split(separator: "\n", omittingEmptySubsequences: false).count
            return String(localized: "New · \(count) lines", comment: "Change sheet subtitle: a file the task made; the argument is its line count")
        }
        return "+\(file.added) −\(file.removed)"
    }

    private var copiedText: String {
        if case .newFile(let text, _) = layout { return text }
        return file.diff
    }
}

/// The lines of a layout's code view (pure, off the main thread): a diff's rows or a new file's lines (JSON laid out),
/// coloured as the file's `format` reads; none for what has no code.
private func buildLines(_ layout: ChangeLayout, format: ChangeFormat) -> [CodeLine] {
    switch layout {
    case .diff(let rows):
        codeLines(rows: rows, language: codeLanguage(of: format))
    case .newFile(let text, let format):
        codeLines(text: format == .json ? (prettyJSON(text) ?? text) : text, language: codeLanguage(of: format))
    case .media, .note:
        []
    }
}

/// Code that never wraps: it scrolls both ways, every row as wide as the widest so a tint spans the view, the rows
/// drawn as they come into sight.
struct CodeLinesView: View {
    let lines: [CodeLine]
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let digits: Int = max(1, lines.map { max(String($0.old ?? 0).count, String($0.new ?? 0).count) }.max() ?? 1)
        let signed: Bool = lines.contains { $0.kind != nil }
        // The columns: the numbers (two for a diff), a sign for a diff, the code, a space at the end.
        let gutter: Int = signed ? digits * 2 + 1 + 3 : digits + 2
        let widest: Int = lines.map { $0.text.count }.max() ?? 0
        let width: CGFloat = CGFloat(gutter + widest + 2) * codeCharacterWidth(dynamicTypeSize)
        GeometryReader { proxy in
            ScrollView([.horizontal, .vertical]) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(lines) { line in
                        CodeLineRow(line: line, digits: digits, signed: signed)
                            .frame(width: max(width, proxy.size.width), alignment: .leading)
                    }
                }
                .padding(.vertical, Space.xs)
            }
            .scrollIndicators(.visible)
        }
        .accessibilityIdentifier("change.code")
    }
}

/// The width of one character of the code role at the reader's text size.
private func codeCharacterWidth(_ size: DynamicTypeSize) -> CGFloat {
    let metrics: UIFontMetrics = UIFontMetrics(forTextStyle: TypeRole.code.uiTextStyle)
    let points: CGFloat = metrics.scaledValue(for: TypeRole.code.points.size, compatibleWith: UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(size)))
    let font: UIFont = UIFont.monospacedSystemFont(ofSize: points, weight: .regular)
    return ("M" as NSString).size(withAttributes: [.font: font]).width
}

/// One row: the numbers in the faint ink, a sign, the coloured code; a hunk's head as a quiet band.
private struct CodeLineRow: View {
    let line: CodeLine
    let digits: Int
    let signed: Bool

    var body: some View {
        if line.kind == .hunk {
            Text(verbatim: line.text.isEmpty ? "⋯" : "⋯ \(line.text)")
                .textRole(.footnote).foregroundStyle(Tone.tertiary).lineLimit(1)
                .padding(.horizontal, Space.gutter).padding(.vertical, Space.xxs)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Tone.nested)
                .accessibilityLabel(hunkWords)
        } else {
            HStack(spacing: 0) {
                Text(verbatim: gutter).foregroundStyle(Tone.tertiary)
                if signed { Text(verbatim: sign).foregroundStyle(signTone) }
                Text(coloured)
            }
            .textRole(.code)
            .lineLimit(1)
            .fixedSize()
            .padding(.leading, Space.xs)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(ground)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken)
            .accessibilityIdentifier("change.line")
        }
    }

    private func padded(_ number: Int?) -> String {
        let text: String = number.map(String.init) ?? ""
        return String(repeating: " ", count: max(0, digits - text.count)) + text
    }

    private var gutter: String { signed ? "\(padded(line.old)) \(padded(line.new))  " : "\(padded(line.new))  " }

    private var sign: String {
        switch line.kind {
        case .added: "+ "
        case .removed: "− "
        default: "  "
        }
    }

    private var signTone: Color {
        switch line.kind {
        case .added: ChangeTone.addedSign
        case .removed: ChangeTone.removedSign
        default: Tone.tertiary
        }
    }

    private var ground: Color {
        switch line.kind {
        case .added: ChangeTone.added
        case .removed: ChangeTone.removed
        default: .clear
        }
    }

    private var coloured: AttributedString {
        var text: AttributedString = AttributedString()
        for token in line.tokens {
            var piece: AttributedString = AttributedString(token.text)
            piece.foregroundColor = ChangeTone.of(token.kind)
            if token.kind == .heading { piece.inlinePresentationIntent = .stronglyEmphasized }
            text.append(piece)
        }
        return text
    }

    private var hunkWords: String {
        String(localized: "Lines from \(line.new ?? 0)", comment: "VoiceOver: a hunk of a diff starts; the argument is its first line's number")
    }

    /// "Added, line 12: x = 2".
    private var spoken: String {
        let number: Int = line.new ?? line.old ?? 0
        let text: String = line.text.trimmingCharacters(in: .whitespaces)
        switch line.kind {
        case .added: return String(localized: "Added, line \(number): \(text)", comment: "VoiceOver: a line the change added; first argument its number, second its text")
        case .removed: return String(localized: "Removed, line \(number): \(text)", comment: "VoiceOver: a line the change removed; first argument its number, second its text")
        default: return String(localized: "Line \(number): \(text)", comment: "VoiceOver: a line of code; first argument its number, second its text")
        }
    }
}

/// The colours of a change, the web's tokens in themes.css (`--ok-weak`, `--bad-weak`, `--ok`, `--bad` and `--code-*`),
/// light and dark; a comment is in the faint ink, as the web's is.
enum ChangeTone {
    static let added: Color = pair(light: 0xE4F5EC, dark: 0x0D2A1F)
    static let removed: Color = pair(light: 0xFDECEC, dark: 0x2B1313)
    static let addedSign: Color = pair(light: 0x047857, dark: 0x34D399)
    static let removedSign: Color = pair(light: 0xB91C1C, dark: 0xF87171)
    private static let keyword: Color = pair(light: 0xA3123A, dark: 0xFF8FA3)
    private static let string: Color = pair(light: 0x0B5A2A, dark: 0x8FD9A8)
    private static let number: Color = pair(light: 0x0B4F9C, dark: 0x8CC4FF)
    private static let key: Color = pair(light: 0x6B2FB3, dark: 0xCFA8FF)

    static func of(_ kind: CodeTokenKind) -> Color {
        switch kind {
        case .plain, .heading: Tone.text
        case .keyword: keyword
        case .string: string
        case .comment: Tone.tertiary
        case .number: number
        case .key: key
        }
    }

    private static func pair(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in rgb(traits.userInterfaceStyle == .dark ? dark : light) })
    }

    private static func rgb(_ hex: UInt32) -> UIColor {
        UIColor(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255, blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}
