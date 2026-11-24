import SwiftUI
import UIKit

// MARK: - Tokens

/// The app's look (item 1380): near-black ground with raised surfaces and hairlines in the dark, a cool white in the
/// light, one calm blue with a very slight gradient, an 8-point radius scale, SF for words and SF Mono for code.
/// Colours come from `DotPalette` (also read by the Live Activity and measured by `DotPaletteTests`); they follow the
/// system's appearance. Glass is for floating chrome only: see `dotGlass(in:)`.
enum Theme {
    static let background: Color = dynamic(\.background)
    static let surface: Color = dynamic(\.surface)
    static let raised: Color = dynamic(\.raised)
    static let text: Color = dynamic(\.text)
    static let muted: Color = dynamic(\.muted)
    static let faint: Color = dynamic(\.faint)
    static let accent: Color = dynamic(\.accent)
    /// A fill white text sits on (a prominent button, the send button).
    static let accentFill: Color = dynamic(\.accentFill)
    static let onAccent: Color = dynamic(\.onAccent)
    static let warn: Color = dynamic(\.warn)
    static let bad: Color = dynamic(\.bad)
    static let good: Color = dynamic(\.good)
    /// A link: plain words in the accent's lightest shade, no chip (brighter than the text round it in the dark).
    static let link: Color = dynamic(\.link)

    /// A hairline: 9 % of the ink over the ground.
    static let hairline: Color = Color(
        uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark ? UIColor(white: 1, alpha: 0.09) : UIColor(white: 0, alpha: 0.10)
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

    /// The accent as a fill: a very slight top-to-bottom gradient.
    static let accentGradient: LinearGradient = LinearGradient(
        colors: [dynamic(\.accentFillTop), dynamic(\.accentFill)], startPoint: .top, endPoint: .bottom)

    /// The text's brightest channel in the dark (a link has to be above it).
    static let textLevel: Double = 0.88

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

/// The radius scale: 4 for a chip's inner mark, 8 for a control, 12 for a card, 16 for a sheet's block, 24 for the
/// floating bar.
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

/// Spring presets. A reader who turned Reduce Motion on gets no movement from `.dotAnimation`.
enum Motion {
    static let snappy: Animation = .snappy(duration: 0.28)
    static let smooth: Animation = .smooth(duration: 0.35)
    static let bouncy: Animation = .bouncy(duration: 0.42, extraBounce: 0.12)
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
                .background(Theme.raised, in: shape)
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

/// The accent as glass: tinted Liquid Glass on iOS 26, the accent's gradient fill before it (and under Reduce Transparency).
private struct DotProminentGlass<S: Shape>: ViewModifier {
    let shape: S
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency: Bool

    @ViewBuilder func body(content: Content) -> some View {
        if #available(iOS 26, *), DotDesign.liquidGlass, !reduceTransparency {
            content.glassEffect(.regular.tint(Theme.accentFill).interactive(), in: shape)
        } else {
            content.background(Theme.accentGradient, in: shape)
        }
    }
}

extension View {
    /// A call to action floating over content: the accent as glass (no screen uses it now; the compose button is a plain
    /// glass button of the title bar).
    func dotProminentGlass<S: Shape>(in shape: S) -> some View { modifier(DotProminentGlass(shape: shape)) }

    /// Floating chrome (the composer, the capsule over it, a round button, a chip): glass on iOS 26, a material before.
    /// Never on message text or on a long list.
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

/// Glass buttons on iOS 26, filled and hairline buttons of the same shape before it.
enum DotButtonKind { case prominent, glass, quiet }

private struct DotButtonModifier: ViewModifier {
    let kind: DotButtonKind
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency: Bool

    @ViewBuilder func body(content: Content) -> some View {
        if #available(iOS 26, *), DotDesign.liquidGlass, !reduceTransparency, kind != .quiet {
            if kind == .prominent {
                content.buttonStyle(.glassProminent).tint(Theme.accentFill)
            } else {
                content.buttonStyle(.glass)
            }
        } else {
            content.buttonStyle(DotFallbackButtonStyle(kind: kind))
        }
    }
}

extension View {
    func dotButton(_ kind: DotButtonKind = .glass) -> some View { modifier(DotButtonModifier(kind: kind)) }
}

/// The button of the material look (and of Reduce Transparency): a capsule, 44 points tall.
struct DotFallbackButtonStyle: ButtonStyle {
    let kind: DotButtonKind
    @Environment(\.isEnabled) private var isEnabled: Bool
    @Environment(\.colorSchemeContrast) private var contrast: ColorSchemeContrast

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(kind == .prominent ? Theme.onAccent : Theme.text)
            .padding(.horizontal, Space.l)
            .frame(minHeight: 44)
            .background {
                if kind == .prominent {
                    Capsule().fill(Theme.accentGradient)
                } else {
                    Capsule().fill(kind == .glass ? AnyShapeStyle(.ultraThinMaterial) : AnyShapeStyle(Color.clear))
                }
            }
            .overlay(Capsule().strokeBorder(kind == .prominent ? Color.clear : (contrast == .increased ? Theme.hairlineStrong : Theme.hairline), lineWidth: 1))
            .opacity(isEnabled ? (configuration.isPressed ? 0.7 : 1) : 0.45)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.snappy(duration: 0.18), value: configuration.isPressed)
    }
}

/// A small text button of the app (kept for the compact places: a row's "Retry", a sheet's "Try again").
struct QuietButtonStyle: ButtonStyle {
    var tint: Color = Theme.text
    var filled: Bool = false
    @Environment(\.isEnabled) private var isEnabled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.footnote.weight(.semibold))
            .foregroundStyle(filled ? Theme.onAccent : tint)
            .padding(.horizontal, Space.m)
            .frame(minHeight: 36)
            .background {
                if filled { RoundedRectangle(cornerRadius: Radius.s).fill(Theme.accentGradient) }
            }
            .overlay(RoundedRectangle(cornerRadius: Radius.s).strokeBorder(filled ? Color.clear : Theme.hairline, lineWidth: 1))
            .opacity(isEnabled ? (configuration.isPressed ? 0.6 : 1) : 0.45)
    }
}

/// A round glass button for the floating chrome (jump to the newest, a toolbar-like action): 44 points.
struct GlassCircleButton: View {
    let symbol: String
    let label: String
    var tint: Color?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(tint ?? Theme.text)
                .frame(width: 44, height: 44)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .dotGlass(in: Circle(), interactive: true)
        .accessibilityLabel(label)
    }
}

// MARK: - Surfaces

/// A surface with a hairline border and the 12-point radius, flat in both looks (no shadow, no gradient: one card recipe).
struct Card: ViewModifier {
    var fill: Color = Theme.surface
    var radius: CGFloat = Radius.m
    @Environment(\.colorSchemeContrast) private var contrast: ColorSchemeContrast

    func body(content: Content) -> some View {
        content
            .background(fill, in: RoundedRectangle(cornerRadius: radius))
            .overlay(RoundedRectangle(cornerRadius: radius).strokeBorder(contrast == .increased ? Theme.hairlineStrong : Theme.hairline, lineWidth: 1))
    }
}

extension View {
    func card(fill: Color = Theme.surface, radius: CGFloat = Radius.m) -> some View { modifier(Card(fill: fill, radius: radius)) }
}

/// The ground of a screen: the base colour, and under the top a glow of the accent that fades out, so that a large
/// title and the first rows stand on something other than flat black.
struct DotBackground: View {
    var glow: Double = 1
    @Environment(\.colorScheme) private var scheme: ColorScheme

    var body: some View {
        ZStack(alignment: .top) {
            Theme.background
            EllipticalGradient(
                colors: [Theme.accent.opacity((scheme == .dark ? 0.20 : 0.13) * glow), Theme.accent.opacity(0)],
                center: .top, startRadiusFraction: 0, endRadiusFraction: 0.9
            )
            .frame(height: 420)
            .frame(maxWidth: .infinity)
        }
        .ignoresSafeArea()
    }
}

/// A soft mesh of blues behind a hero (the pairing screen, an empty state): static, never loud.
struct DotMesh: View {
    var body: some View {
        MeshGradient(
            width: 3, height: 3,
            points: [
                [0, 0], [0.5, 0], [1, 0],
                [0, 0.5], [0.55, 0.45], [1, 0.5],
                [0, 1], [0.5, 1], [1, 1],
            ],
            colors: [
                Theme.accent.opacity(0.30), Theme.accent.opacity(0.10), Color.indigo.opacity(0.20),
                Color.indigo.opacity(0.12), Theme.accent.opacity(0.24), Color.cyan.opacity(0.10),
                Color.clear, Theme.accent.opacity(0.08), Color.clear,
            ]
        )
        .accessibilityHidden(true)
    }
}

// MARK: - Pieces

/// A small status word in a tinted capsule: "Needs you", "Working", "Paused".
struct StatusPill: View {
    let text: String
    var symbol: String?
    var tone: Color = Theme.accent
    var pulse: Bool = false

    var body: some View {
        HStack(spacing: Space.xs) {
            if let symbol {
                Image(systemName: symbol)
                    .font(.system(size: 10, weight: .semibold))
                    .symbolEffect(.pulse, options: .repeating, isActive: pulse)
            }
            Text(text).font(.caption.weight(.semibold))
        }
        .foregroundStyle(tone)
        .padding(.horizontal, Space.s)
        .padding(.vertical, 3)
        .background(tone.opacity(0.14), in: Capsule())
        .accessibilityElement(children: .combine)
    }
}

/// An empty, error or offline state: a layered symbol over a soft mesh, a title, a line, and one button when there is a
/// way out.
struct DotEmptyState: View {
    let symbol: String
    let title: String
    let message: String
    var tone: Color = Theme.accent
    var actionTitle: String?
    var action: (() -> Void)?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    var body: some View {
        VStack(spacing: Space.m) {
            ZStack {
                DotMesh()
                    .frame(width: 220, height: 220)
                    .mask(RadialGradient(colors: [.black, .clear], center: .center, startRadius: 20, endRadius: 110))
                Circle()
                    .strokeBorder(LinearGradient(colors: [tone.opacity(0.5), tone.opacity(0.05)], startPoint: .top, endPoint: .bottom), lineWidth: 1)
                    .frame(width: 104, height: 104)
                Image(systemName: symbol)
                    .font(.system(size: 40, weight: .light))
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(tone, tone.opacity(0.45))
                    .symbolEffect(.pulse, options: .repeating.speed(0.4), isActive: !reduceMotion)
                    .frame(width: 88, height: 88)
                    .dotGlass(in: Circle(), tint: tone.opacity(0.10))
            }
            .frame(height: 150)
            Text(title).font(.title3.weight(.semibold)).foregroundStyle(Theme.text).multilineTextAlignment(.center)
            Text(message).font(.subheadline).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if let actionTitle, let action {
                Button(actionTitle, action: action).dotButton(.prominent).padding(.top, Space.xs)
            }
        }
        .padding(.horizontal, Space.xl)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
    }
}

/// A section's title: small, muted, with a count when it has one.
struct SectionLabel: View {
    let title: String
    var count: Int?
    var tone: Color = Theme.faint

    var body: some View {
        HStack(spacing: Space.xs) {
            Text(title).textCase(nil).font(.footnote.weight(.semibold)).foregroundStyle(tone)
            if let count {
                Text("\(count)").font(.footnote.weight(.semibold).monospacedDigit()).foregroundStyle(tone.opacity(0.8))
            }
        }
        .accessibilityAddTraits(.isHeader)
    }
}

// MARK: - Avatar

/// An agent's avatar: a circle with a gradient in a hue taken from its id, its initial, and a ring that says how it
/// stands: a sweep that turns while it works, a steady amber one while it waits for the owner.
struct Avatar: View {
    let id: String
    let name: String
    var size: CGFloat = 40
    var state: ThreadState = .idle

    var body: some View {
        Group {
            if id == "dot" {
                // Dot itself wears the app's own mark; the agents wear their initial in a hue of their own.
                DotGlyph(size: size)
            } else {
                let base: Double = hue(of: id)
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [Color(hue: base, saturation: 0.50, brightness: 0.78), Color(hue: base, saturation: 0.62, brightness: 0.50)],
                            startPoint: .topLeading, endPoint: .bottomTrailing)
                    )
                    .overlay(Circle().strokeBorder(Color.white.opacity(0.18), lineWidth: 0.75))
                    .overlay(
                        Text(name.prefix(1).uppercased())
                            .font(.system(size: size * 0.42, weight: .semibold, design: .rounded))
                            .foregroundStyle(.white)
                            .shadow(color: .black.opacity(0.25), radius: 1, y: 0.5)
                    )
            }
        }
        .frame(width: size, height: size)
        .overlay { StateRing(state: state).padding(-size * 0.11) }
        .accessibilityHidden(true)
    }
}

/// The ring round an avatar. It turns only while the run works, and stands still under Reduce Motion.
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
                .strokeBorder(
                    AngularGradient(colors: [Theme.accent.opacity(0), Theme.accent.opacity(0.1), Theme.accent], center: .center),
                    lineWidth: 2.5
                )
                .rotationEffect(.degrees(turning ? 360 : 0))
                .animation(turning ? .linear(duration: 1.4).repeatForever(autoreverses: false) : nil, value: turning)
                // It turns every time the chat comes back to work (a ring that left with the state starts again) and it
                // follows Reduce Motion while it turns.
                .onAppear { turning = !reduceMotion }
                .onDisappear { turning = false }
                .onChange(of: reduceMotion) { _, still in turning = !still }
        case .waiting:
            Circle().strokeBorder(Theme.warn, lineWidth: 2.5)
                .shadow(color: Theme.warn.opacity(0.45), radius: 3)
        }
    }
}

func hue(of id: String) -> Double {
    Double(id.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) % 360 }) / 360
}

/// How a chat or an agent stands.
enum ThreadState: Sendable {
    case idle, working, waiting

    var color: Color { self == .waiting ? Theme.warn : Theme.accent }
}

extension ThreadSummary {
    var state: ThreadState { isWaitingOnYou ? .waiting : isWorking ? .working : .idle }
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
