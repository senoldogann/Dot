import SwiftUI
import UIKit

/// The box that holds what a step said: a command's output, a file, a thought. At most `Metric.toolDetailMax` tall, it
/// scrolls in itself. A short text is a SwiftUI `Text`; a long one is a `UITextView` (TextKit 2), which lays out only
/// the lines in view. A `Text` lays out the whole of its string before it draws, so a 600-line test report held the
/// main thread for 0.8-1.0 s each time its row opened (measured on the simulator: `scripts/jank.sh`).
struct OutputBox: View {
    let text: String
    let role: TypeRole
    /// A monospaced size chosen in Settings › Appearance; 0 keeps the role's own.
    let points: Double
    let ink: Color
    let anchorsAtEnd: Bool

    var body: some View {
        Group {
            if OutputBox.isLong(text) {
                LongOutputText(text: text, role: role, points: points, anchorsAtEnd: anchorsAtEnd)
                    .frame(height: Metric.toolDetailMax)
            } else {
                ScrollView {
                    Text(text).textRole(role).modifier(MonoSize(points: points)).foregroundStyle(ink).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(Space.s)
                }
                .frame(maxHeight: Metric.toolDetailMax)
                .fixedSize(horizontal: false, vertical: true)
                .defaultScrollAnchor(anchorsAtEnd ? .bottom : .top)
            }
        }
        .background(Tone.nested, in: rounded(Radius.inner))
        .clipShape(rounded(Radius.inner))
    }

    private static var warmed: Bool = false

    /// Makes the first text view of the process before the first row opens: building TextKit 2's machinery and the
    /// monospaced face's glyphs cost about 0.3 s on the first long output otherwise.
    @MainActor static func warmUp() {
        guard !warmed else { return }
        warmed = true
        let view: UITextView = UITextView(usingTextLayoutManager: true)
        view.frame = CGRect(x: 0, y: 0, width: 320, height: 120)
        view.attributedText = NSAttributedString(
            string: "  PASS  suite 1 > handles case 1 in 12 ms\n  FAIL  suite 2 { 0123456789 }", attributes: [.font: UIFont.monospacedSystemFont(ofSize: 13, weight: .regular)])
        view.layoutIfNeeded()
    }

    /// Whether a text is past what a `Text` lays out at once: over 24 lines or 3,000 characters, which is always taller than
    /// the box (so the long form's fixed height never shows an empty box).
    static func isLong(_ text: String) -> Bool {
        if text.utf16.count > 3000 { return true }
        var lines: Int = 1
        for byte in text.utf8 where byte == 10 {
            lines += 1
            if lines > 24 { return true }
        }
        return false
    }
}

/// A read-only, selectable text view with the app's type role and ink, for texts too long for `Text`.
struct LongOutputText: UIViewRepresentable {
    let text: String
    let role: TypeRole
    let points: Double
    let anchorsAtEnd: Bool

    func makeUIView(context: Context) -> UITextView {
        let view: UITextView = UITextView(usingTextLayoutManager: true)
        view.isEditable = false
        view.isSelectable = true
        view.isScrollEnabled = true
        view.backgroundColor = .clear
        view.textContainerInset = UIEdgeInsets(top: Space.s, left: Space.s, bottom: Space.s, right: Space.s)
        view.textContainer.lineFragmentPadding = 0
        view.alwaysBounceVertical = false
        view.dataDetectorTypes = []
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        let traits: UITraitCollection = UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(context.environment.dynamicTypeSize))
        let base: CGFloat = role == .code && points > 0 ? CGFloat(points) : role.points.size
        let size: CGFloat = UIFontMetrics(forTextStyle: role == .code ? .footnote : role.uiTextStyle).scaledValue(for: base, compatibleWith: traits)
        let font: UIFont = role == .code ? UIFont.monospacedSystemFont(ofSize: size, weight: .regular) : UIFont.systemFont(ofSize: size, weight: .regular)
        let style: NSMutableParagraphStyle = NSMutableParagraphStyle()
        style.lineSpacing = DotNext.lineSpacing(roleSize: role.points.size, roleLineHeight: role.points.lineHeight, scaledSize: size, naturalLineHeight: font.lineHeight)
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: UIColor.dynamic(\.secondary), .paragraphStyle: style]
        let signature: String = "\(text.utf16.count)|\(text.hashValue)|\(size)"
        if context.coordinator.signature != signature {
            context.coordinator.signature = signature
            view.attributedText = NSAttributedString(string: text, attributes: attributes)
            if anchorsAtEnd {
                view.scrollRangeToVisible(NSRange(location: max(0, view.attributedText.length - 1), length: 1))
            } else {
                view.setContentOffset(.zero, animated: false)
            }
        }
        view.tintColor = UIColor.dynamic(\.text)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UITextView, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 320, height: Metric.toolDetailMax)
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var signature: String = ""
    }
}
