import SwiftUI
import UIKit

/// The type roles (plan §3): the system font at the reference's sizes, each scaled with Dynamic Type relative to the
/// system text style it stands for, with an explicit line height (body 16/23).
enum TypeRole: String, CaseIterable, Sendable {
    case micro
    case caption
    case label
    case footnote
    case body
    case headline
    case title
    case largeTitle
    case code

    /// Size and line height at the default text size (Large).
    var points: (size: CGFloat, lineHeight: CGFloat) {
        switch self {
        case .micro: (11, 14)
        case .caption: (12, 16)
        case .label: (13, 17)
        case .footnote: (14, 19)
        case .body: (16, 23)
        case .headline: (18, 23)
        case .title: (21, 28)
        case .largeTitle: (26, 32)
        case .code: (13, 19)
        }
    }

    var weight: Font.Weight {
        switch self {
        case .headline, .title: .semibold
        case .largeTitle: .bold
        default: .regular
        }
    }

    var design: Font.Design {
        self == .code ? .monospaced : .default
    }

    /// The system text style this role scales with.
    var textStyle: Font.TextStyle {
        switch self {
        case .micro: .caption2
        case .caption: .caption
        case .label: .footnote
        case .footnote: .subheadline
        case .body: .callout
        case .headline: .headline
        case .title: .title3
        case .largeTitle: .title
        case .code: .footnote
        }
    }

    var uiTextStyle: UIFont.TextStyle {
        switch self {
        case .micro: .caption2
        case .caption: .caption1
        case .label: .footnote
        case .footnote: .subheadline
        case .body: .callout
        case .headline: .headline
        case .title: .title3
        case .largeTitle: .title1
        case .code: .footnote
        }
    }
}

/// The extra leading that turns a font's natural line height into the role's line height, scaled together.
/// `naturalLineHeight` is the font's own (`UIFont.lineHeight`); never negative.
func lineSpacing(roleSize: CGFloat, roleLineHeight: CGFloat, scaledSize: CGFloat, naturalLineHeight: CGFloat) -> CGFloat {
    let scale: CGFloat = scaledSize / roleSize
    return max(0, roleLineHeight * scale - naturalLineHeight)
}

/// Applies a role: font, weight, design and line height, all following Dynamic Type.
struct TypeRoleModifier: ViewModifier {
    let role: TypeRole
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    func body(content: Content) -> some View {
        let metrics: UIFontMetrics = UIFontMetrics(forTextStyle: role.uiTextStyle)
        let traits: UITraitCollection = UITraitCollection(
            preferredContentSizeCategory: UIContentSizeCategory(dynamicTypeSize))
        let size: CGFloat = metrics.scaledValue(for: role.points.size, compatibleWith: traits)
        let uiFont: UIFont =
            role == .code
            ? UIFont.monospacedSystemFont(ofSize: size, weight: .regular)
            : UIFont.systemFont(ofSize: size, weight: role.weight.uiWeight)
        return content
            .font(.system(size: size, weight: role.weight, design: role.design))
            .lineSpacing(
                lineSpacing(
                    roleSize: role.points.size, roleLineHeight: role.points.lineHeight, scaledSize: size,
                    naturalLineHeight: uiFont.lineHeight))
    }
}

extension View {
    func textRole(_ role: TypeRole) -> some View { modifier(TypeRoleModifier(role: role)) }

    /// Clocks and counts: digits of one width, so a ticking time does not jitter.
    func tabularNumbers() -> some View { monospacedDigit() }
}

extension Font.Weight {
    var uiWeight: UIFont.Weight {
        switch self {
        case .semibold: .semibold
        case .bold: .bold
        case .medium: .medium
        default: .regular
        }
    }
}

extension UIContentSizeCategory {
    init(_ size: DynamicTypeSize) {
        switch size {
        case .xSmall: self = .extraSmall
        case .small: self = .small
        case .medium: self = .medium
        case .large: self = .large
        case .xLarge: self = .extraLarge
        case .xxLarge: self = .extraExtraLarge
        case .xxxLarge: self = .extraExtraExtraLarge
        case .accessibility1: self = .accessibilityMedium
        case .accessibility2: self = .accessibilityLarge
        case .accessibility3: self = .accessibilityExtraLarge
        case .accessibility4: self = .accessibilityExtraExtraLarge
        case .accessibility5: self = .accessibilityExtraExtraExtraLarge
        @unknown default: self = .large
        }
    }
}

/// The reading column on a wide screen (review S9): the width that holds about `charactersPerLine` characters of the
/// body font, measured from the font itself rather than guessed.
enum ProseColumn {
    /// A sample of ordinary English prose, so the average glyph width is that of real text.
    static let sample: String =
        "Dot read the three files you named, ran the tests twice and found the failing case in the parser; "
        + "it now waits for your word before it pushes the branch and opens the pull request for review."

    static func averageCharacterWidth(font: UIFont) -> CGFloat {
        let width: CGFloat = (sample as NSString).size(withAttributes: [.font: font]).width
        return width / CGFloat(sample.count)
    }

    static func width(charactersPerLine: Int, font: UIFont) -> CGFloat {
        (averageCharacterWidth(font: font) * CGFloat(charactersPerLine)).rounded(.up)
    }

    /// The body font at a Dynamic Type size.
    static func bodyFont(_ size: DynamicTypeSize) -> UIFont {
        let traits: UITraitCollection = UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(size))
        let points: CGFloat = UIFontMetrics(forTextStyle: TypeRole.body.uiTextStyle).scaledValue(
            for: TypeRole.body.points.size, compatibleWith: traits)
        return UIFont.systemFont(ofSize: points)
    }

    /// The target: 68 characters, the middle of the 60-75 band.
    static let target: Int = 68
}
