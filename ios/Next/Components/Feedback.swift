import SwiftUI

/// The empty state of a list or screen: a still glyph, the title and one line, in the palette's ink.
struct EmptyState: View {
    let title: String
    let systemImage: String
    let detail: String
    let actionTitle: String?
    let action: () -> Void

    @ScaledMetric(relativeTo: .title3) private var glyph: CGFloat = 28

    /// The app's own type roles, not the system's larger empty-state styles (owner 2026-11-27): the words follow Settings ›
    /// Appearance's text size and Dynamic Type as every other line does; the glyph is still, sized with the title.
    var body: some View {
        VStack(spacing: Space.s) {
            Image(systemName: systemImage)
                .font(.system(size: glyph, weight: .regular))
                .foregroundStyle(Tone.tertiary)
                .accessibilityHidden(true)
                .padding(.bottom, Space.xxs)
            Text(title).textRole(.headline).foregroundStyle(Tone.text)
            Text(detail).textRole(.footnote).foregroundStyle(Tone.secondary)
            if let actionTitle {
                Button(actionTitle, action: action).buttonStyle(PrimaryButtonStyle()).padding(.top, Space.xs)
            }
        }
        .multilineTextAlignment(.center)
        .padding(.horizontal, Space.xl)
        .padding(.vertical, Space.xxl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .contain)
    }
}

/// A search that found nothing, in the empty state's look (the system's `ContentUnavailableView.search` has its own
/// larger type, which Settings › Appearance's text size does not reach).
struct SearchEmptyState: View {
    let query: String

    var body: some View {
        EmptyState(
            title: String(localized: "No results for “\(query)”", comment: "Search: nothing matches the words; the argument is what was searched"),
            systemImage: "magnifyingglass",
            detail: String(localized: "Check the spelling or try a new search.", comment: "Search: what to do when nothing matches"),
            actionTitle: nil, action: {})
    }
}

/// A screen that waits for its first answer: the large system spinner and one line.
struct LoadingState: View {
    let message: String

    var body: some View {
        VStack(spacing: Space.l) {
            ProgressView().controlSize(.large).tint(Tone.secondary)
            Text(message).textRole(.headline).foregroundStyle(Tone.text).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}

extension View {
    /// A loading, empty or failed state that stands alone on a scrolling page: as tall as the page's visible area less
    /// the page's own top and bottom padding, so it is centred both ways between the navigation bar and the bottom (item
    /// 1904). Outside a scroll view a state fills its place with `maxWidth`/`maxHeight` and needs none of this.
    func fillsScrollPage() -> some View {
        frame(maxWidth: .infinity)
            .containerRelativeFrame(.vertical) { length, _ in max(length - 2 * Space.groupedGutter, 0) }
    }
}

/// What a notice is about: neutral information, something that waits on the owner, or a failure.
enum NoticeTone: Sendable {
    case info
    case warn
    case failure

    var symbol: String {
        switch self {
        case .info: "info.circle"
        case .warn: "exclamationmark.circle"
        case .failure: "exclamationmark.triangle"
        }
    }

    var ink: Color {
        switch self {
        case .info: Tone.secondary
        case .warn: Tone.warn
        case .failure: Tone.failure
        }
    }

    var fill: Color {
        switch self {
        case .info, .failure: Tone.nested
        case .warn: Tone.warnFill
        }
    }
}

/// An inline notice (R-a §2): radius 12, a tonal fill and NO stroke; announced to VoiceOver when it appears; an
/// optional ✕ with a 44 pt target.
struct Notice: View {
    let tone: NoticeTone
    let message: String
    let dismiss: (() -> Void)?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            Symbol(name: tone.symbol, context: .feedback).foregroundStyle(tone.ink)
            Text(message).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let dismiss {
                Button(action: dismiss) {
                    Image(systemName: "xmark").font(.system(.footnote, weight: .semibold))
                        .foregroundStyle(Tone.glyph)
                        .frame(width: Metric.hit, height: Metric.hit)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(String(localized: "Dismiss", comment: "VoiceOver: the ✕ that closes a notice"))
                .padding(.vertical, -Space.s)
                .padding(.trailing, -Space.s)
            }
        }
        .padding(.horizontal, Space.m)
        .padding(.vertical, Space.s)
        .background(tone.fill, in: rounded(Radius.inner))
        .accessibilityElement(children: .contain)
        .onAppear { AccessibilityNotification.Announcement(message).post() }
    }
}

/// A short-lived message over the screen, with an optional action (Undo).
struct ToastMessage: Identifiable, Equatable, Sendable {
    let id: UUID
    let text: String
    let systemImage: String
    let actionTitle: String?

    static func == (lhs: ToastMessage, rhs: ToastMessage) -> Bool { lhs.id == rhs.id }
}

/// The toast itself (R-a §2): a glass capsule, the glyph, the words and the action inline (stacked at the accessibility
/// sizes); swiped sideways or the escape gesture dismisses it.
struct ToastView: View {
    let message: ToastMessage
    let act: () -> Void
    let dismiss: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    @State private var drag: CGFloat = 0

    var body: some View {
        let layout: AnyLayout =
            dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.xs))
            : AnyLayout(HStackLayout(spacing: Space.s))
        layout {
            HStack(spacing: Space.xs) {
                Symbol(name: message.systemImage, context: .feedback).foregroundStyle(Tone.text)
                Text(message.text).textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.text)
            }
            if let actionTitle = message.actionTitle {
                Button(actionTitle, action: act)
                    .textRole(.footnote).fontWeight(.semibold)
                    .foregroundStyle(Tone.onAction)
                    .padding(.horizontal, Space.m)
                    .frame(minHeight: 32)
                    .background(Tone.action, in: Capsule())
                    .frame(minHeight: Metric.hit)
                    .contentShape(Rectangle())
                    .buttonStyle(.plain)
            }
        }
        .padding(.leading, Space.m)
        .padding(.trailing, message.actionTitle == nil ? Space.m : Space.xxs)
        .padding(.vertical, Space.xxs)
        .frame(minHeight: Metric.hit + Space.xs)
        .floatingGlass(in: dynamicTypeSize.isAccessibilitySize ? AnyShape(rounded(Radius.bubble)) : AnyShape(Capsule()))
        .offset(x: drag)
        .gesture(
            DragGesture(minimumDistance: 12)
                .onChanged { drag = $0.translation.width }
                .onEnded { value in
                    if abs(value.translation.width) > 80 || abs(value.predictedEndTranslation.width) > 240 {
                        dismiss()
                    } else {
                        drag = 0
                    }
                }
        )
        .accessibilityElement(children: .contain)
        .accessibilityAction(.escape, dismiss)
    }
}

/// Shows `toast` at the bottom while it is set, announces it once, and clears it after `seconds` (the Undo window).
struct ToastHost: ViewModifier {
    @Binding var toast: ToastMessage?
    let seconds: Double
    let act: (ToastMessage) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    func body(content: Content) -> some View {
        content.overlay(alignment: .bottom) {
            if let message = toast {
                ToastView(message: message, act: { act(message); toast = nil }, dismiss: { toast = nil })
                    .padding(.horizontal, Space.gutter)
                    .padding(.bottom, Space.xs)
                    .transition(reduceMotion ? .opacity : .move(edge: .bottom).combined(with: .opacity))
                    .task(id: message.id) {
                        AccessibilityNotification.Announcement(
                            [message.text, message.actionTitle].compactMap { $0 }.joined(separator: ". ")
                        ).post()
                        do {
                            try await Task.sleep(for: .seconds(seconds))
                        } catch is CancellationError {
                            return  // a newer toast replaced this one, or the view left
                        } catch {
                            preconditionFailure("Task.sleep throws only CancellationError: \(error)")
                        }
                        if toast?.id == message.id { toast = nil }
                    }
            }
        }
        .animation(motionAnimation(toast == nil ? .exit : .enter, reduceMotion: reduceMotion), value: toast)
    }
}

extension View {
    func toastHost(_ toast: Binding<ToastMessage?>, seconds: Double, act: @escaping (ToastMessage) -> Void) -> some View {
        modifier(ToastHost(toast: toast, seconds: seconds, act: act))
    }
}
