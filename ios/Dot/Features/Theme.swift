import SwiftUI
import UIKit

// MARK: - Tokens

/// The app's look (items 1380 to 1382; made professional by items 1640 to 1647): ONE neutral graphite family from the web app's
/// Graphite theme, flat surfaces with hairlines, no accent hue, no gradient, no shadow, no coloured tile. Colour is for two
/// states only: amber for what waits on the owner, a muted red for what failed. The primary action is the ink itself.
/// Colours come from `DotPalette` (also read by the Live Activity and measured by `DotPaletteTests`); they follow the system's
/// appearance. Glass is for floating chrome only: see `dotGlass(in:)`.
enum Theme {
    /// The ground of every screen and of every sheet.
    static let background: Color = dynamic(\.background)
    /// A card: one step above the ground, never darker than it.
    static let surface: Color = dynamic(\.surface)
    /// A fill a step off the surface: the owner's message, an avatar's disc, a field.
    static let raised: Color = dynamic(\.raised)
    static let text: Color = dynamic(\.text)
    static let muted: Color = dynamic(\.muted)
    /// The quiet tier: a footer, a time, a placeholder.
    static let faint: Color = dynamic(\.faint)
    /// The primary action is the text colour on the ground: this is its fill, and `onAction` the label on it.
    static let action: Color = dynamic(\.action)
    static let onAction: Color = dynamic(\.onAction)
    /// The track of a switch that is on: a mid grey of the quiet tier, so that the white knob reads on it (the system's own tint is
    /// the app's ink, which would hide the knob).
    static let toggleOn: Color = dynamic(\.faint)
    /// Waiting on the owner: an amber ink, and the one amber fill.
    static let warn: Color = dynamic(\.warn)
    static let warnSurface: Color = dynamic(\.warnSurface)
    /// A failure: a muted red ink for a glyph and its words.
    static let bad: Color = dynamic(\.bad)

    /// The fills of a row's swipe actions. The system draws their label in white in both looks, so the fills are mid-dark ones
    /// in both: a graphite for an action and a deep muted red for the one that removes. (`DotPaletteTests` holds white on them
    /// to 4.5:1.)
    static let swipeNeutral: Color = Color(swipeNeutralRGB)
    static let swipeDestructive: Color = Color(swipeDestructiveRGB)
    static let swipeNeutralRGB: DotRGB = DotRGB(hex: 0x58585C)
    static let swipeDestructiveRGB: DotRGB = DotRGB(hex: 0xB3261E)

    /// A hairline: 10 % of the ink over the ground.
    static let hairline: Color = Color(
        uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark ? UIColor(white: 1, alpha: 0.10) : UIColor(white: 0, alpha: 0.10)
        })
    /// The hairline when the reader asked for more contrast.
    static let hairlineStrong: Color = Color(
        uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark ? UIColor(white: 1, alpha: 0.38) : UIColor(white: 0, alpha: 0.42)
        })
    /// The ground of inline code and code blocks: a step off the surface it sits on.
    static let codeFill: Color = Color(
        uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark ? UIColor(white: 1, alpha: 0.07) : UIColor(white: 0, alpha: 0.06)
        })

    /// The widest the words of a chat and the list of chats run on a wide screen (an iPad): about 70 characters of an answer. On a
    /// phone nothing is as wide, so nothing changes there.
    static let readingWidth: CGFloat = 720

    /// The corner radius of controls; `Radius` has the rest of the scale.
    static let radius: CGFloat = Radius.s
    static let radiusSmall: CGFloat = Radius.xs

    // Type: SF through the system's text styles, so every size follows Dynamic Type. Code is SF Mono.
    static let title: Font = .title2.weight(.semibold)
    static let headline: Font = .headline
    static let body: Font = .callout
    static let small: Font = .footnote
    static let meta: Font = .caption
    static let tiny: Font = .caption2
    static let mono: Font = .system(.footnote, design: .monospaced)

    private static func dynamic(_ token: KeyPath<DotPalette, DotRGB>) -> Color {
        Color(
            uiColor: UIColor { trait in
                let palette: DotPalette = trait.userInterfaceStyle == .dark ? .dark : .light
                return UIColor(palette[keyPath: token])
            })
    }
}

extension UIColor {
    convenience init(_ rgb: DotRGB) { self.init(red: rgb.red, green: rgb.green, blue: rgb.blue, alpha: 1) }
}

extension Color {
    init(_ rgb: DotRGB) { self.init(red: rgb.red, green: rgb.green, blue: rgb.blue) }
}

/// The radius scale: 4 for a chip's inner mark, 8 for a control, 12 for a field, 16 for a card, 24 for the floating bar.
enum Radius {
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 12
    static let l: CGFloat = 16
    static let xl: CGFloat = 24
}

/// The spacing scale (points).
enum Space {
    static let xxs: CGFloat = 2
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 12
    static let l: CGFloat = 16
    static let xl: CGFloat = 24
    static let xxl: CGFloat = 32
}

// MARK: - Motion

/// Spring presets: few and slow, nothing bounces for fun. A reader who turned Reduce Motion on gets no movement from
/// `.dotAnimation`.
enum Motion {
    static let snappy: Animation = .snappy(duration: 0.28)
    static let smooth: Animation = .smooth(duration: 0.35)
}

private struct DotAnimation<V: Equatable>: ViewModifier {
    let animation: Animation
    let value: V
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    func body(content: Content) -> some View {
        content.animation(reduceMotion ? nil : animation, value: value)
    }
}

extension View {
    /// `animation(_:value:)` that stays still under Reduce Motion.
    func dotAnimation<V: Equatable>(_ animation: Animation = Motion.snappy, value: V) -> some View {
        modifier(DotAnimation(animation: animation, value: value))
    }
}

// MARK: - Glass

/// Whether the iOS 26 look is used. It is on wherever the system has it; only a debug build started with
/// `-DotMaterialLook 1` turns it off, to look at the look an iOS 18 phone gets on a newer simulator.
enum DotDesign {
    static let liquidGlass: Bool = {
        #if DEBUG
        return !UserDefaults.standard.bool(forKey: "DotMaterialLook")
        #else
        return true
        #endif
    }()
}

/// Liquid Glass on iOS 26, an intentional material look before it: a thin material, a hairline, the tint over it.
/// With Reduce Transparency on it is a solid raised surface, and with Increase Contrast the hairline is stronger.
private struct DotGlass<S: Shape>: ViewModifier {
    let shape: S
    let tint: Color?
    let interactive: Bool
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency: Bool
    @Environment(\.colorSchemeContrast) private var contrast: ColorSchemeContrast

    @ViewBuilder func body(content: Content) -> some View {
        if reduceTransparency {
            content
                .background(tint == nil ? Theme.raised : Theme.warnSurface, in: shape)
                .overlay(shape.stroke(stroke, lineWidth: 1))
        } else if #available(iOS 26, *), DotDesign.liquidGlass {
            content.glassEffect(glass, in: shape)
        } else {
            content
                .background(.ultraThinMaterial, in: shape)
                .background((tint ?? .clear).opacity(0.22), in: shape)
                .overlay(shape.stroke(stroke, lineWidth: 1))
        }
    }

    private var stroke: Color { contrast == .increased ? Theme.hairlineStrong : Theme.hairline }

    @available(iOS 26, *)
    private var glass: Glass {
        var glass: Glass = .regular
        if let tint { glass = glass.tint(tint) }
        if interactive { glass = glass.interactive() }
        return glass
    }
}

extension View {
    /// Floating chrome only (the composer, the capsule over it, a round button, a chip): glass on iOS 26, a material before.
    /// Never on message text, on a card or on a long list. `tint` is for the one amber fill, "Waiting on you".
    func dotGlass<S: Shape>(in shape: S, tint: Color? = nil, interactive: Bool = false) -> some View {
        modifier(DotGlass(shape: shape, tint: tint, interactive: interactive))
    }

    /// Gives glass shapes that sit near each other one id, so that they morph into each other (iOS 26 only).
    @ViewBuilder func dotGlassID(_ id: some Hashable & Sendable, in namespace: Namespace.ID) -> some View {
        if #available(iOS 26, *), DotDesign.liquidGlass { glassEffectID(id, in: namespace) } else { self }
    }
}

/// Glass shapes that sit near each other share one container, so that they blend and morph as one piece of glass.
struct DotGlassGroup<Content: View>: View {
    var spacing: CGFloat = Space.m
    @ViewBuilder var content: Content

    var body: some View {
        if #available(iOS 26, *), DotDesign.liquidGlass {
            GlassEffectContainer(spacing: spacing) { content }
        } else {
            content
        }
    }
}

// MARK: - Buttons

/// The app's four buttons, flat in both looks (no glass, no gradient): ONE primary (the ink as a fill, the ground as its label),
/// ONE secondary (a raised fill with a hairline), ONE destructive (the secondary, its words in the failure's ink) and a quiet one
/// (words in a hairline outline).
enum DotButtonKind { case primary, secondary, destructive, quiet }

private struct DotButtonModifier: ViewModifier {
    let kind: DotButtonKind

    func body(content: Content) -> some View { content.buttonStyle(DotButtonStyle(kind: kind)) }
}

extension View {
    /// `Button { } label: { Text("Allow once").frame(maxWidth: .infinity) }.dotButton(.primary)`; `.controlSize(.large)` makes it
    /// 50 points tall, `.small` 36, else it is 44.
    func dotButton(_ kind: DotButtonKind = .secondary) -> some View { modifier(DotButtonModifier(kind: kind)) }
}

/// The button of every screen: a capsule, 44 points tall (50 large, 36 small).
struct DotButtonStyle: ButtonStyle {
    let kind: DotButtonKind
    @Environment(\.isEnabled) private var isEnabled: Bool
    @Environment(\.controlSize) private var controlSize: ControlSize
    @Environment(\.colorSchemeContrast) private var contrast: ColorSchemeContrast

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(label)
            .padding(.horizontal, Space.l)
            .frame(minHeight: height)
            .background(fill, in: Capsule())
            .overlay(Capsule().strokeBorder(border, lineWidth: 1))
            .opacity(configuration.isPressed ? 0.78 : 1)
            .contentShape(Capsule())
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }

    private var height: CGFloat {
        switch controlSize {
        case .large, .extraLarge: 50
        case .small, .mini: 36
        default: 44
        }
    }

    private var fill: Color {
        switch kind {
        case .primary: isEnabled ? Theme.action : Theme.raised
        case .secondary, .destructive: Theme.raised
        case .quiet: .clear
        }
    }

    private var label: Color {
        guard isEnabled else { return Theme.faint }
        switch kind {
        case .primary: return Theme.onAction
        case .secondary, .quiet: return Theme.text
        case .destructive: return Theme.bad
        }
    }

    private var border: Color {
        switch kind {
        case .primary: .clear
        default: contrast == .increased ? Theme.hairlineStrong : Theme.hairline
        }
    }
}

/// A round glass button for the floating chrome (jump to the newest, a toolbar-like action): 44 points.
struct GlassCircleButton: View {
    let icon: IconName
    let label: String
    var tint: Color?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            DotIcon(icon, size: 18)
                .foregroundStyle(tint ?? Theme.text)
                .frame(width: 44, height: 44)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .dotGlass(in: Circle(), interactive: true)
        .accessibilityLabel(label)
    }
}

/// THE chip: a small capsule of glass with a glyph and words (floating chrome: the model chip over a composer). 36 points to the
/// eye, 44 to the finger.
struct DotChip: View {
    let icon: IconName
    let words: String
    var showsChevrons: Bool = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.xs + 1) {
                DotIcon(icon, size: 13, relativeTo: .caption)
                Text(words).font(Theme.meta.weight(.medium)).lineLimit(1)
                if showsChevrons { DotIcon(.chevronsUpDown, size: 11, relativeTo: .caption2) }
            }
            .foregroundStyle(Theme.text)
            .padding(.horizontal, Space.m).frame(minHeight: 36)
            .dotGlass(in: Capsule(), interactive: true)
            .padding(.vertical, Space.xs)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Surfaces

/// THE card: a surface one step above the ground, a 1-point hairline, a 16-point radius, flat in both looks (no shadow, no
/// gradient, no tint). Used for every group of rows, every approval, every status.
struct Card: ViewModifier {
    var fill: Color = Theme.surface
    var radius: CGFloat = Radius.l
    var border: Color?
    @Environment(\.colorSchemeContrast) private var contrast: ColorSchemeContrast

    func body(content: Content) -> some View {
        content
            .background(fill, in: RoundedRectangle(cornerRadius: radius))
            .overlay(
                RoundedRectangle(cornerRadius: radius)
                    .strokeBorder(border ?? (contrast == .increased ? Theme.hairlineStrong : Theme.hairline), lineWidth: 1))
    }
}

extension View {
    /// `border` is for the one card that asks the owner: an amber hairline (`Theme.warn`) instead of the grey one.
    func card(fill: Color = Theme.surface, radius: CGFloat = Radius.l, border: Color? = nil) -> some View {
        modifier(Card(fill: fill, radius: radius, border: border))
    }
}

/// The ground of a screen: flat. (It had a glow of the accent under the top until item 1640.)
struct DotBackground: View {
    var body: some View { Theme.background.ignoresSafeArea() }
}

// MARK: - Pieces

/// A state, small and honest (rule 3 of the look): a mark and words, no capsule and no fill. Working is neutral words with a
/// small moving indicator; waiting on the owner is amber (a dot and amber words); failed is a muted red glyph and words; done
/// is muted. The one amber fill, "Waiting on you", is the capsule over the composer's.
struct StateMark: View {
    enum Kind { case working, waiting, failed, done, quiet }

    let words: String
    var kind: Kind

    var body: some View {
        HStack(spacing: Space.xs + 2) {
            mark
            Text(words).font(.caption.weight(.medium)).foregroundStyle(ink).lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var mark: some View {
        switch kind {
        case .working: WorkingIndicator(size: 10)
        case .waiting: Circle().fill(Theme.warn).frame(width: 7, height: 7)
        case .failed: DotIcon(.circleAlert, size: 12, relativeTo: .caption).foregroundStyle(Theme.bad)
        case .done: DotIcon(.check, size: 11, relativeTo: .caption).foregroundStyle(Theme.faint)
        case .quiet: EmptyView()
        }
    }

    private var ink: Color {
        switch kind {
        case .working: Theme.muted
        case .waiting: Theme.warn
        case .failed: Theme.bad
        case .done, .quiet: Theme.faint
        }
    }
}

/// The small moving indicator of work: an arc that turns once in `RingMotion.secondsPerTurn`; it stands still, as an arc, under
/// Reduce Motion.
struct WorkingIndicator: View {
    var size: CGFloat = 10
    var ink: Color = Theme.text
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @State private var turning: Bool = false

    var body: some View {
        Circle()
            .trim(from: 0, to: 0.72)
            .stroke(ink, style: StrokeStyle(lineWidth: max(1.5, size * 0.16), lineCap: .round))
            .frame(width: size, height: size)
            .rotationEffect(.degrees(turning ? 360 : 0))
            .animation(
                turning ? .linear(duration: RingMotion.secondsPerTurn).repeatForever(autoreverses: false) : nil, value: turning
            )
            .onAppear { turning = !reduceMotion }
            .onDisappear { turning = false }
            .onChange(of: reduceMotion) { turning = !reduceMotion }
            .accessibilityHidden(true)
    }
}

/// An empty, error or offline state: a quiet symbol, a title, a line, and one button when there is a way out. Plain and adult:
/// no illustration, no colour.
struct DotEmptyState: View {
    let icon: IconName
    let title: String
    let message: String
    var tone: Color = Theme.faint
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        VStack(spacing: Space.m) {
            DotIcon(icon, size: 28, relativeTo: .title)
                .foregroundStyle(tone)
                .padding(.bottom, Space.xs)
            Text(title).font(.headline).foregroundStyle(Theme.text).multilineTextAlignment(.center)
            Text(message).font(.subheadline).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if let actionTitle, let action {
                Button(actionTitle, action: action).dotButton(.primary).padding(.top, Space.xs)
            }
        }
        .padding(.horizontal, Space.xl)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
    }
}

/// A section's title: small, in the secondary colour, sentence case, with a count when it has one.
struct SectionLabel: View {
    let title: String
    var count: Int?
    /// Only the inbox's and the list's "Needs you" header is amber.
    var tone: Color = Theme.muted

    var body: some View {
        HStack(spacing: Space.xs) {
            Text(title).textCase(nil).font(.footnote.weight(.medium)).foregroundStyle(tone)
            if let count {
                Text("\(count)").font(.footnote.monospacedDigit()).foregroundStyle(tone == Theme.muted ? Theme.faint : tone)
            }
        }
        .accessibilityAddTraits(.isHeader)
    }
}

// MARK: - The ring of an avatar

/// The ring round an avatar. While the run works a short arc turns once in `RingMotion.secondsPerTurn` (it stands still under
/// Reduce Motion); while it waits for the owner the ring is amber and whole. No gradient, no glow.
struct StateRing: View {
    let state: ThreadState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @State private var turning: Bool = false

    var body: some View {
        switch state {
        case .idle:
            EmptyView()
        case .working:
            Circle()
                .trim(from: 0, to: 0.3)
                .stroke(Theme.text.opacity(0.85), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .rotationEffect(.degrees(turning ? 360 : 0))
                .animation(
                    turning ? .linear(duration: RingMotion.secondsPerTurn).repeatForever(autoreverses: false) : nil, value: turning)
                // It turns every time the chat comes back to work (a ring that left with the state starts again) and it
                // follows Reduce Motion while it turns.
                .onAppear { turning = RingMotion.turns(state: state, reduceMotion: reduceMotion) }
                .onDisappear { turning = false }
                .onChange(of: reduceMotion) { turning = RingMotion.turns(state: state, reduceMotion: reduceMotion) }
        case .waiting:
            Circle().strokeBorder(Theme.warn, lineWidth: 2)
        }
    }
}

/// "2 min", "3 h", "Mon", "12 Oct": the age of a moment in the fewest characters.
func shortAge(_ seconds: Double, now: Date = Date()) -> String {
    let age: Double = max(0, now.timeIntervalSince1970 - seconds)
    switch age {
    case ..<60: return "now"
    case ..<3600: return "\(Int(age / 60)) min"
    case ..<86400: return "\(Int(age / 3600)) h"
    case ..<(86400 * 6): return "\(Int(age / 86400)) d"
    default:
        return Date(timeIntervalSince1970: seconds).formatted(.dateTime.day().month(.abbreviated))
    }
}
