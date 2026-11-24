import SwiftUI
import UIKit

extension PaletteVariant {
    /// The palette a trait collection draws with: its appearance and its Increase Contrast setting.
    static func of(_ traits: UITraitCollection) -> PaletteVariant {
        let increased: Bool = traits.accessibilityContrast == .high
        switch (traits.userInterfaceStyle == .dark, increased) {
        case (true, true): return .darkIncreased
        case (true, false): return .dark
        case (false, true): return .lightIncreased
        case (false, false): return .light
        }
    }
}

extension UIColor {
    convenience init(rgb: RGB) {
        self.init(red: rgb.red, green: rgb.green, blue: rgb.blue, alpha: 1)
    }

    /// A colour that follows the appearance and Increase Contrast, picked from the palette by `role`.
    static func dynamic(_ role: @escaping @Sendable (Palette) -> RGB) -> UIColor {
        UIColor { traits in UIColor(rgb: role(Palette.of(PaletteVariant.of(traits)))) }
    }
}

/// The palette as SwiftUI colours. Every view draws with these and nothing else (no `Color.blue`, no system tints).
enum Tone {
    static let ground: Color = Color(uiColor: .dynamic(\.ground))
    static let card: Color = Color(uiColor: .dynamic(\.card))
    static let nested: Color = Color(uiColor: .dynamic(\.nested))
    static let sheet: Color = Color(uiColor: .dynamic(\.sheet))
    static let glassFallback: Color = Color(uiColor: .dynamic(\.glassFallback))
    static let text: Color = Color(uiColor: .dynamic(\.text))
    static let secondary: Color = Color(uiColor: .dynamic(\.secondary))
    static let tertiary: Color = Color(uiColor: .dynamic(\.tertiary))
    static let glyph: Color = Color(uiColor: .dynamic(\.glyph))
    static let action: Color = Color(uiColor: .dynamic(\.action))
    static let onAction: Color = Color(uiColor: .dynamic(\.onAction))
    static let warn: Color = Color(uiColor: .dynamic(\.warn))
    static let warnFill: Color = Color(uiColor: .dynamic(\.warnFill))
    static let failure: Color = Color(uiColor: .dynamic(\.failure))
    static let separator: Color = Color(
        uiColor: UIColor { traits in
            let palette: Palette = Palette.of(PaletteVariant.of(traits))
            return UIColor(rgb: palette.text).withAlphaComponent(palette.separatorOpacity)
        })
}

/// Spacing on the reference's 3.5 pt rhythm, with the gutters the plan fixes (16 on a phone page, 14 in grouped lists).
enum Space {
    static let hair: CGFloat = 1
    static let xxs: CGFloat = 3.5
    static let xs: CGFloat = 7
    static let s: CGFloat = 10.5
    static let m: CGFloat = 14
    static let l: CGFloat = 17.5
    static let xl: CGFloat = 21
    static let xxl: CGFloat = 28
    /// The page's side margin on a phone.
    static let gutter: CGFloat = 16
    /// The inset of grouped lists and the gap between their sections.
    static let groupedGutter: CGFloat = 14
}

/// Corner radii, all drawn `.continuous`.
enum Radius {
    /// An inner element: a code block, a notice, a nested field.
    static let inner: CGFloat = 12
    /// The owner's bubble, an ask card.
    static let bubble: CGFloat = 20
    /// A grouped section, a card.
    static let section: CGFloat = 24
    /// The composer.
    static let composer: CGFloat = 27
}

/// Sizes that are a contract: the 44 pt hit floor and the visual pitches of the reference's density (review S4).
enum Metric {
    static let hit: CGFloat = 44
    static let row: CGFloat = 49
    static let rowWithDescription: CGFloat = 63
    static let toolRow: CGFloat = 28
    static let iconSlot: CGFloat = 22
    static let sendDisc: CGFloat = 30
    static let floatingControl: CGFloat = 38.5
    static let bubbleFraction: CGFloat = 0.85
    /// A collapsed tool's expanded detail scrolls past this height.
    static let toolDetailMax: CGFloat = 210
}

/// The continuous rounded rectangle of a radius token.
func rounded(_ radius: CGFloat) -> RoundedRectangle {
    RoundedRectangle(cornerRadius: radius, style: .continuous)
}
