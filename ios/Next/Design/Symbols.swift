import SwiftUI

/// Where a symbol stands decides its size and weight: one weight per context (owner's decision D6, review S4), all
/// SF Symbols, monochrome, scaling with the text style beside them.
enum SymbolContext: String, CaseIterable, Sendable {
    /// The tab bar: drawn by the system; listed so the sample sheet shows the set.
    case tab
    /// A list row's leading glyph, in a 22 pt slot.
    case row
    /// A navigation bar or composer button.
    case toolbar
    /// A disclosure chevron.
    case chevron
    /// A tool call's glyph in the work log.
    case tool
    /// A status word's mark.
    case status
    /// A toast or notice glyph.
    case feedback

    var font: Font {
        switch self {
        case .tab: .system(.body, weight: .regular)
        case .row: .system(.body, weight: .regular)
        case .toolbar: .system(.body, weight: .medium)
        case .chevron: .system(.footnote, weight: .semibold)
        case .tool: .system(.footnote, weight: .medium)
        case .status: .system(.caption2, weight: .semibold)
        case .feedback: .system(.callout, weight: .medium)
        }
    }

    var title: String {
        switch self {
        case .tab: String(localized: "Tab bar · regular", comment: "Symbol sample sheet: a context and the symbol weight used there")
        case .row: String(localized: "List rows · regular", comment: "Symbol sample sheet: a context and the symbol weight used there")
        case .toolbar: String(localized: "Toolbar and composer · medium", comment: "Symbol sample sheet: a context and the symbol weight used there")
        case .chevron: String(localized: "Disclosure · semibold", comment: "Symbol sample sheet: a context and the symbol weight used there")
        case .tool: String(localized: "Work log · medium", comment: "Symbol sample sheet: a context and the symbol weight used there")
        case .status: String(localized: "Status · semibold", comment: "Symbol sample sheet: a context and the symbol weight used there")
        case .feedback: String(localized: "Toast and notice · medium", comment: "Symbol sample sheet: a context and the symbol weight used there")
        }
    }

    /// The symbols the app uses in this context.
    var symbols: [String] {
        switch self {
        case .tab: ["bubble.left.and.bubble.right", "person.2", "clock.arrow.circlepath", "gearshape"]
        case .row:
            [
                "desktopcomputer", "bell", "slider.horizontal.3", "textformat.size", "bubble.left", "cpu",
                "calendar.badge.clock", "list.bullet.rectangle", "brain", "puzzlepiece.extension", "lock.shield",
                "stop.circle", "heart.text.square", "info.circle",
            ]
        case .toolbar:
            [
                "square.and.pencil", "line.3.horizontal.decrease", "plus", "arrow.up", "stop.fill", "mic",
                "xmark", "checkmark", "ellipsis",
            ]
        case .chevron: ["chevron.right", "chevron.down", "chevron.up"]
        case .tool:
            [
                "terminal", "doc.text", "pencil", "magnifyingglass", "globe", "camera.viewfinder",
                "cursorarrow.click", "keyboard", "macwindow", "envelope", "hourglass", "person.2", "list.bullet",
                "graduationcap", "xmark",
            ]
        case .status: ["circle.fill", "exclamationmark.circle.fill", "xmark.circle.fill", "checkmark.circle.fill"]
        case .feedback: ["checkmark.circle", "exclamationmark.triangle", "info.circle", "arrow.uturn.backward"]
        }
    }
}

/// A symbol drawn in its context's size and weight, hidden from VoiceOver (the row or button names it).
struct Symbol: View {
    let name: String
    let context: SymbolContext

    var body: some View {
        Image(systemName: name)
            .font(context.font)
            .symbolRenderingMode(.monochrome)
            .accessibilityHidden(true)
    }
}
