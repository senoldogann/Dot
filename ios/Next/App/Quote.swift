import SwiftUI

// Quoting a message (item 1972): a swipe on a message, or Quote in its menu, puts its words in the composer as a chip above
// the text; the message sent starts with them as a "> quote", which the model reads and the chat shows as a quote.

/// The most characters of a message a quote keeps.
let quoteLimit: Int = 280

/// The quote of a message's words: trimmed, runs of blank lines folded into one, cut at `quoteLimit` with an ellipsis;
/// `nil` when there is nothing to quote (pure).
func quoteOf(_ text: String) -> String? {
    let lines: [String] = text.split(separator: "\n", omittingEmptySubsequences: false).map { $0.trimmingCharacters(in: .whitespaces) }
    var kept: [String] = []
    for line in lines where !(line.isEmpty && (kept.last?.isEmpty ?? true)) { kept.append(line) }
    let joined: String = kept.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    guard !joined.isEmpty else { return nil }
    return joined.count > quoteLimit ? String(joined.prefix(quoteLimit - 1)) + "…" : joined
}

/// The quote's first line, for the chip (pure).
func quoteChipText(_ quote: String) -> String {
    let first: Substring = quote.split(separator: "\n", omittingEmptySubsequences: true).first ?? Substring(quote)
    return first.split(whereSeparator: \.isWhitespace).joined(separator: " ")
}

/// The words to send: each line of the quote behind "> ", a blank line, then the owner's words (pure).
func quotedWords(_ words: String, quote: String?) -> String {
    guard let quote else { return words }
    let block: String = quote.split(separator: "\n", omittingEmptySubsequences: false).map { $0.isEmpty ? ">" : "> " + $0 }.joined(separator: "\n")
    return block + "\n\n" + words
}

/// A sent message split into its leading quote and the rest (pure): the quote's lines without their "> ", `nil` when the
/// message does not start with one.
func splitQuote(_ text: String) -> (quote: String?, rest: String) {
    var quoted: [String] = []
    var lines: ArraySlice<Substring> = text.split(separator: "\n", omittingEmptySubsequences: false)[...]
    while let line = lines.first, line.hasPrefix(">") {
        quoted.append(String(line.dropFirst().drop { $0 == " " }))
        lines = lines.dropFirst()
    }
    guard !quoted.isEmpty else { return (nil, text) }
    if lines.first?.isEmpty == true { lines = lines.dropFirst() }
    return (quoted.joined(separator: "\n"), lines.joined(separator: "\n"))
}

/// How far a drag pulls a message to the right (pure): nothing unless it starts away from the screen's edge (the back
/// gesture's) and goes more across than down, so a scroll is never taken; then a rubber band that follows the finger and
/// slows towards `swipeLimit`.
func swipePull(startX: CGFloat, width: CGFloat, height: CGFloat) -> CGFloat {
    guard startX >= swipeEdge, width > 0, width > abs(height) * 1.5 else { return 0 }
    return swipeLimit * (1 - exp(-width / swipeLimit))
}

/// How far a message is pulled at most, the pull at which it counts, and the strip of the screen's edge left to the system.
let swipeLimit: CGFloat = 72
let swipeThreshold: CGFloat = 44
let swipeEdge: CGFloat = 32

/// A message pulled to the right by a swipe draws a reply arrow behind it; past the threshold it is quoted when the finger
/// lets go (a light tap is felt as it passes). It follows the finger as a rubber band and snaps back.
struct SwipeToQuote: ViewModifier {
    let words: String
    let quote: (String) -> Void
    @State private var pull: CGFloat = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    private var passed: Bool { pull >= swipeThreshold }

    func body(content: Content) -> some View {
        ZStack(alignment: .leading) {
            Image(systemName: "arrowshape.turn.up.left")
                .font(.system(.body, weight: .medium))
                .foregroundStyle(passed ? Tone.text : Tone.secondary)
                .frame(width: Metric.hit, height: Metric.hit)
                .scaleEffect(passed ? 1.1 : 0.8)
                .opacity(min(1, Double(pull / swipeThreshold)))
                .accessibilityHidden(true)
            content.offset(x: pull)
        }
        // The whole width of the row takes the swipe, not only the bubble's own pixels.
        .contentShape(Rectangle())
        .gesture(
            SwipePan(
                follow: { pull = $0 },
                release: { final in
                    if final >= swipeThreshold { quote(words) }
                    withAnimation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 0.75)) { pull = 0 }
                })
        )
        .sensoryFeedback(.impact(weight: .light), trigger: passed) { _, now in now }
        .accessibilityAction(named: Text(String(localized: "Quote", comment: "Message menu and VoiceOver action: puts the message in the composer as a quote"))) { quote(words) }
    }
}

/// The swipe as a UIKit pan on the message's own view, so that it sees the touches that begin on the message's text or its
/// menu (a SwiftUI drag on a parent does not), and begins only for a pull to the right that is more across than down, from
/// away from the screen's edge: a scroll or the back gesture never meets it.
struct SwipePan: UIGestureRecognizerRepresentable {
    let follow: (CGFloat) -> Void
    let release: (CGFloat) -> Void

    @MainActor
    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var startX: CGFloat = 0

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            guard let pan = gestureRecognizer as? UIPanGestureRecognizer else { return false }
            let velocity: CGPoint = pan.velocity(in: pan.view)
            let moved: CGPoint = pan.translation(in: pan.view)
            startX = pan.location(in: nil).x - moved.x
            return startX >= swipeEdge && velocity.x > 0 && velocity.x > abs(velocity.y) * 1.5
        }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
    }

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator { Coordinator() }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let pan: UIPanGestureRecognizer = UIPanGestureRecognizer()
        pan.delegate = context.coordinator
        return pan
    }

    func handleUIGestureRecognizerAction(_ pan: UIPanGestureRecognizer, context: Context) {
        let moved: CGPoint = pan.translation(in: pan.view)
        let pull: CGFloat = swipePull(startX: context.coordinator.startX, width: moved.x, height: moved.y)
        switch pan.state {
        case .changed: follow(pull)
        case .ended: release(pull)
        case .cancelled, .failed: release(0)
        default: break
        }
    }
}

extension View {
    /// Swipe the message to the right to quote its `words`.
    func swipeToQuote(_ words: String, quote: @escaping (String) -> Void) -> some View {
        modifier(SwipeToQuote(words: words, quote: quote))
    }
}

/// The quote chip above the composer's text: its first line, and a cross that drops it.
struct QuoteChip: View {
    let quote: String
    let remove: () -> Void

    var body: some View {
        HStack(spacing: Space.xs) {
            Text(quoteChipText(quote)).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("composer.quote")
            Button(action: remove) {
                Symbol(name: "xmark", context: .status).foregroundStyle(Tone.glyph)
                    .frame(width: Metric.hit, height: Metric.hit)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(String(localized: "Remove the quote", comment: "VoiceOver: the cross on the composer's quote chip"))
            .accessibilityIdentifier("composer.quote.remove")
        }
        .padding(.leading, Space.m)
        .padding(.trailing, Space.xxs)
        .frame(minHeight: Metric.hit)
        .background(Tone.nested, in: Capsule())
        .padding(.horizontal, Space.xs)
        .padding(.top, Space.xs)
        .accessibilityElement(children: .contain)
    }
}

/// A quote in a sent message: a bar and the quoted words, small and quiet, above the message that quotes it.
struct QuoteBlock: View {
    let quote: String

    var body: some View {
        HStack(alignment: .top, spacing: Space.xs) {
            RoundedRectangle(cornerRadius: 1).fill(Tone.tertiary).frame(width: 2)
            Text(quote).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(4)
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.leading, Space.s)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(String(localized: "Quote: \(quote)", comment: "VoiceOver: a quoted message above a message; the argument is the quote"))
        .accessibilityIdentifier("message.quote")
    }
}
