import SwiftUI

/// What a screen puts round its content: the ONE sheet recipe, the ground, the navigation subtitle, the scroll edges.
/// Each of them uses the iOS 26 system look where there is one and keeps a deliberate material look before it.

/// The sheet's look, the same in every sheet and every detent: the app's ground as the background (so that a card on it, one step
/// above the ground, is never darker than what it lies on), the neutral tint (no sheet falls back to the system's blue), the drag
/// indicator, the system's own spring.
private struct DotSheetStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .tint(Theme.text)
            .presentationBackground(Theme.background)
            .presentationDragIndicator(.visible)
    }
}

private struct DotScreenBackground: ViewModifier {
    func body(content: Content) -> some View {
        content.background { DotBackground() }
    }
}

private struct DotSubtitle: ViewModifier {
    let words: String
    let tone: Color?

    @ViewBuilder func body(content: Content) -> some View {
        if #available(iOS 26, *), DotDesign.liquidGlass {
            if let tone { content.navigationSubtitle(Text(words).foregroundStyle(tone)) } else { content.navigationSubtitle(words) }
        } else {
            content
        }
    }
}

private struct DotScrollEdges: ViewModifier {
    @ViewBuilder func body(content: Content) -> some View {
        if #available(iOS 26, *), DotDesign.liquidGlass {
            content.scrollEdgeEffectStyle(.soft, for: .all)
        } else {
            content
        }
    }
}

extension View {
    /// For the content of a `.sheet`: the neutral tint, the app's ground, a drag indicator. The sheet's detents stay with the
    /// caller.
    func dotSheet() -> some View { modifier(DotSheetStyle()) }

    /// The ground of a screen. (`inSheet` is kept for the callers; a sheet's ground is the app's ground too.)
    func dotScreenBackground(inSheet: Bool = false) -> some View { modifier(DotScreenBackground()) }

    /// A line under the navigation title (iOS 26); nothing before it.
    func dotSubtitle(_ words: String, tone: Color? = nil) -> some View { modifier(DotSubtitle(words: words, tone: tone)) }

    /// Soft edges where a scroll view runs under the bars (iOS 26's scroll edge effect).
    func dotScrollEdges() -> some View { modifier(DotScrollEdges()) }
}

/// The ONE trailing "Done" of a sheet: the system's confirmation style (a filled glass capsule) in the neutral tint, so that it
/// is the same in the medium and the large detent, in every sheet, and never the system's blue or purple.
///
///     .toolbar { DoneButton { dismiss() } }
struct DoneButton: ToolbarContent {
    let action: () -> Void

    var body: some ToolbarContent {
        ToolbarItem(placement: .confirmationAction) {
            Button(action: action) {
                Text("Done").fontWeight(.semibold).foregroundStyle(Theme.onAction)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.action)
            .accessibilityIdentifier("sheet-done")
        }
    }
}

/// A group of rows on a card: the app's own grouped look, so that a screen reads the same whether it is a `List` or not.
/// A header in the secondary colour above, a footer in the quiet tier below, 16-point margins.
struct CardGroup<Content: View>: View {
    var title: String?
    var footer: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            if let title { SectionLabel(title: title).padding(.horizontal, Space.l) }
            VStack(alignment: .leading, spacing: 0) { content }
                .frame(maxWidth: .infinity, alignment: .leading)
                .card()
            if let footer {
                Text(footer).font(Theme.meta).foregroundStyle(Theme.faint).padding(.horizontal, Space.l)
            }
        }
    }
}

/// THE row of a `CardGroup`: a monochrome 22-point glyph in the secondary colour (no tile, no tint), a title, a value or a
/// detail in the quiet tier, and a chevron when it opens something. `destructive` is for the one row that ends something (its
/// words, and only its words, are in the failure's ink).
struct CardRow<Trailing: View>: View {
    let icon: IconName?
    let title: String
    var detail: String?
    var showsChevron: Bool = false
    var destructive: Bool = false
    @ViewBuilder var trailing: Trailing
    @Environment(\.dynamicTypeSize) private var typeSize: DynamicTypeSize

    var body: some View {
        let layout: AnyLayout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.s)) : AnyLayout(HStackLayout(spacing: Space.m))
        layout {
            if let icon {
                DotIcon(icon, size: 22, relativeTo: .title3)
                    .foregroundStyle(destructive ? Theme.bad : Theme.muted)
                    .frame(minWidth: 28, alignment: .leading)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.body).foregroundStyle(destructive ? Theme.bad : Theme.text)
                if let detail { Text(detail).font(Theme.meta).foregroundStyle(Theme.faint) }
            }
            Spacer(minLength: Space.s)
            trailing
            if showsChevron {
                DotIcon(.chevron, size: 14, relativeTo: .caption).foregroundStyle(Theme.faint)
            }
        }
        .padding(.horizontal, Space.l)
        .padding(.vertical, Space.m - 2)
        .frame(minHeight: 52)
        .contentShape(Rectangle())
    }
}

extension CardRow where Trailing == EmptyView {
    init(icon: IconName?, title: String, detail: String? = nil, showsChevron: Bool = false, destructive: Bool = false) {
        self.init(icon: icon, title: title, detail: detail, showsChevron: showsChevron, destructive: destructive) { EmptyView() }
    }
}

/// The line between two rows of a card: a hairline, inset to the row's words (56 points past a row with a glyph).
struct CardDivider: View {
    var inset: CGFloat = 56
    var body: some View { Rectangle().fill(Theme.hairline).frame(height: 1).padding(.leading, inset) }
}
