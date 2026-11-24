import SwiftUI

/// One of the app's icons (item 1461): Lucide's shapes, the web app's set, drawn as a template so that it takes the colour
/// of the text round it (`foregroundStyle`). The image is a vector of `Icons.xcassets`, so it stays sharp at any size, and
/// its size follows Dynamic Type like the words beside it (`relativeTo`). It is decoration unless a `label` is given.
struct DotIcon: View {
    let name: IconName
    var label: String?
    var pulse: Bool
    @ScaledMetric private var side: CGFloat

    init(_ name: IconName, size: CGFloat = 16, pulse: Bool = false, label: String? = nil, relativeTo style: Font.TextStyle = .body) {
        self.name = name
        self.label = label
        self.pulse = pulse
        _side = ScaledMetric(wrappedValue: size, relativeTo: style)
    }

    var body: some View {
        Image(name.rawValue)
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .frame(width: side, height: side)
            .modifier(IconPulse(active: pulse))
            .accessibilityHidden(label == nil)
            .accessibilityLabel(label ?? "")
    }
}

/// A slow breath of opacity for an icon that stands for something at work (a step that runs, a link that reconnects). It
/// stands still under Reduce Motion, and when the work ends.
private struct IconPulse: ViewModifier {
    let active: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @State private var dim: Bool = false

    private var pulsing: Bool { active && !reduceMotion }

    func body(content: Content) -> some View {
        content
            .opacity(pulsing && dim ? 0.35 : 1)
            .animation(pulsing ? .easeInOut(duration: 1.1).repeatForever(autoreverses: true) : .easeOut(duration: 0.2), value: dim)
            .onAppear { dim = pulsing }
            .onChange(of: pulsing) { dim = pulsing }
    }
}

extension Label where Title == Text, Icon == DotIcon {
    /// A label in a view: the words and one of the app's icons beside them.
    init(_ title: String, icon: IconName, size: CGFloat = 14) {
        self.init { Text(title) } icon: { DotIcon(icon, size: size) }
    }
}

extension Label where Title == Text, Icon == Image {
    /// A label of a menu, a context menu or a swipe action, whose icon the system sizes itself (its image says 20 points).
    init(_ title: String, menuIcon icon: IconName) {
        self.init(title, image: icon.rawValue)
    }
}
