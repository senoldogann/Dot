import SwiftUI

/// What a screen puts round its content: the sheet's look, the ground, the navigation subtitle, the scroll edges.
/// Each of them uses the iOS 26 system look where there is one and keeps a deliberate material look before it.

private struct DotSheetStyle: ViewModifier {
    @ViewBuilder func body(content: Content) -> some View {
        if #available(iOS 26, *), DotDesign.liquidGlass {
            // The system's own sheet: Liquid Glass, rounded and inset. Painting a ground over it would hide it.
            content.tint(Theme.accent).presentationDragIndicator(.visible)
        } else {
            content.tint(Theme.accent).presentationBackground(Theme.background).presentationDragIndicator(.visible)
        }
    }
}

private struct DotScreenBackground: ViewModifier {
    let inSheet: Bool

    @ViewBuilder func body(content: Content) -> some View {
        if inSheet, #available(iOS 26, *), DotDesign.liquidGlass {
            content
        } else {
            content.background { DotBackground(glow: inSheet ? 0.5 : 1) }
        }
    }
}

private struct DotSubtitle: ViewModifier {
    let words: String

    @ViewBuilder func body(content: Content) -> some View {
        if #available(iOS 26, *), DotDesign.liquidGlass { content.navigationSubtitle(words) } else { content }
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
    /// For the content of a `.sheet`: the accent, a drag indicator, and the sheet's own look (glass on iOS 26, the
    /// app's ground before it). The sheet's detents stay with the caller.
    func dotSheet() -> some View { modifier(DotSheetStyle()) }

    /// The ground of a screen. In a sheet on iOS 26 it is left to the system's glass.
    func dotScreenBackground(inSheet: Bool = false) -> some View { modifier(DotScreenBackground(inSheet: inSheet)) }

    /// A line under the navigation title (iOS 26); nothing before it.
    func dotSubtitle(_ words: String) -> some View { modifier(DotSubtitle(words: words)) }

    /// Soft edges where a scroll view runs under the bars (iOS 26's scroll edge effect).
    func dotScrollEdges() -> some View { modifier(DotScrollEdges()) }
}

/// A group of rows on a card: the app's own grouped look, so that a screen reads the same whether it is a `List` or not.
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

/// A row of a `CardGroup`: a symbol in a tinted square, a title, a value or a detail, and a chevron when it opens
/// something.
struct CardRow<Trailing: View>: View {
    let symbol: String?
    var tint: Color = Theme.accent
    let title: String
    var detail: String?
    var showsChevron: Bool = false
    @ViewBuilder var trailing: Trailing
    @Environment(\.dynamicTypeSize) private var typeSize: DynamicTypeSize

    var body: some View {
        let layout: AnyLayout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.s)) : AnyLayout(HStackLayout(spacing: Space.m))
        layout {
            if let symbol {
                Image(systemName: symbol)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 30, height: 30)
                    .background(
                        LinearGradient(colors: [tint, tint.opacity(0.78)], startPoint: .top, endPoint: .bottom),
                        in: RoundedRectangle(cornerRadius: Radius.s))
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline).foregroundStyle(Theme.text)
                if let detail { Text(detail).font(Theme.meta).foregroundStyle(Theme.faint) }
            }
            Spacer(minLength: Space.s)
            trailing
            if showsChevron {
                Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.faint)
            }
        }
        .padding(.horizontal, Space.l)
        .padding(.vertical, Space.m - 2)
        .frame(minHeight: 52)
        .contentShape(Rectangle())
    }
}

extension CardRow where Trailing == EmptyView {
    init(symbol: String?, tint: Color = Theme.accent, title: String, detail: String? = nil, showsChevron: Bool = false) {
        self.init(symbol: symbol, tint: tint, title: title, detail: detail, showsChevron: showsChevron) { EmptyView() }
    }
}

/// The line between two rows of a card.
struct CardDivider: View {
    var inset: CGFloat = 58
    var body: some View { Rectangle().fill(Theme.hairline).frame(height: 1).padding(.leading, inset) }
}
