import SwiftUI

/// The app's look: near-black surfaces, hairlines at 8 % white, one calm blue, an 8 pt radius, and 10/11 pt metadata.
/// Dark only: the surfaces are part of the design, not a setting.
enum Theme {
    static let background: Color = Color(red: 0.039, green: 0.039, blue: 0.043)
    static let surface: Color = Color(red: 0.067, green: 0.067, blue: 0.075)
    static let raised: Color = Color(red: 0.094, green: 0.094, blue: 0.106)
    static let hairline: Color = Color.white.opacity(0.08)
    static let text: Color = Color.white.opacity(0.88)  // `textLevel`
    static let muted: Color = Color.white.opacity(0.58)
    static let faint: Color = Color.white.opacity(0.38)
    static let accent: Color = Color(red: 0.31, green: 0.55, blue: 0.96)
    static let warn: Color = Color(red: 0.93, green: 0.70, blue: 0.28)
    static let bad: Color = Color(red: 0.94, green: 0.40, blue: 0.40)
    static let good: Color = Color(red: 0.35, green: 0.78, blue: 0.55)

    /// A link: the text's own ink tilted toward the accent, a shade brighter than the words round it (its brightest
    /// channel is full, the text's is 88 %).
    static let linkComponents: (red: Double, green: Double, blue: Double) = (0.70, 0.83, 1.0)
    static let link: Color = Color(red: linkComponents.red, green: linkComponents.green, blue: linkComponents.blue)
    static let textLevel: Double = 0.88

    static let radius: CGFloat = 8
    static let radiusSmall: CGFloat = 4

    static let body: Font = .system(size: 15)
    static let small: Font = .system(size: 13)
    static let meta: Font = .system(size: 11)
    static let tiny: Font = .system(size: 10)
    static let mono: Font = .system(size: 12.5, design: .monospaced)
}

/// A surface with a hairline border and the 8 pt radius.
struct Card: ViewModifier {
    var fill: Color = Theme.surface

    func body(content: Content) -> some View {
        content
            .background(fill, in: RoundedRectangle(cornerRadius: Theme.radius))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
    }
}

extension View {
    func card(fill: Color = Theme.surface) -> some View { modifier(Card(fill: fill)) }
}

/// A flat text button of the app: small, hairline, no fill.
struct QuietButtonStyle: ButtonStyle {
    var tint: Color = Theme.text
    var filled: Bool = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(filled ? Color.black.opacity(0.9) : tint)
            .padding(.horizontal, 12)
            .frame(minHeight: 32)
            .background(filled ? tint : Color.clear, in: RoundedRectangle(cornerRadius: Theme.radius))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(filled ? Color.clear : Theme.hairline, lineWidth: 1))
            .opacity(configuration.isPressed ? 0.6 : 1)
    }
}

/// An agent's avatar: a circle in a hue taken from its id, its initial, and the state dot.
struct Avatar: View {
    let id: String
    let name: String
    var size: CGFloat = 40
    var state: ThreadState = .idle

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            Circle()
                .fill(Color(hue: hue(of: id), saturation: 0.45, brightness: 0.55))
                .overlay(Text(name.prefix(1).uppercased()).font(.system(size: size * 0.42, weight: .semibold)).foregroundStyle(.white))
                .frame(width: size, height: size)
            if state != .idle {
                Circle().fill(state.color).frame(width: 11, height: 11)
                    .overlay(Circle().strokeBorder(Theme.background, lineWidth: 2))
            }
        }
        .accessibilityHidden(true)
    }
}

func hue(of id: String) -> Double {
    Double(id.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) % 360 }) / 360
}

/// How a chat or an agent stands: shown as a dot.
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
