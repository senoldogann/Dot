import Foundation

/// An sRGB colour as plain numbers, so that contrast is measured by a pure function and a test, never by eye.
struct RGB: Equatable, Hashable, Sendable {
    let red: Double
    let green: Double
    let blue: Double

    init(hex: UInt32) {
        red = Double((hex >> 16) & 0xFF) / 255
        green = Double((hex >> 8) & 0xFF) / 255
        blue = Double(hex & 0xFF) / 255
    }
}

/// WCAG 2 contrast, the one rule every ink of the app is held to.
enum Contrast {
    /// Relative luminance of an sRGB colour (WCAG 2.x).
    static func luminance(_ color: RGB) -> Double {
        func linear(_ channel: Double) -> Double {
            channel <= 0.04045 ? channel / 12.92 : pow((channel + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(color.red) + 0.7152 * linear(color.green) + 0.0722 * linear(color.blue)
    }

    /// Contrast ratio from 1 to 21, whichever colour is the lighter.
    static func ratio(_ first: RGB, _ second: RGB) -> Double {
        let a: Double = luminance(first)
        let b: Double = luminance(second)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }
}

/// The two appearances and the Increase Contrast setting: the four palettes the app draws with.
enum PaletteVariant: String, CaseIterable, Sendable {
    case light
    case dark
    case lightIncreased
    case darkIncreased
}

/// The colour tokens of DotNext (plan §3, §13 S3, owner's decisions §15): one graphite family started from the old
/// app's `DotPalette` (the web's Graphite theme), light and dark, each with an Increase Contrast step.
///
/// - Surfaces: `ground` (pages), `card` (a standalone card AND a grouped section: one step for both, review S3),
///   `nested` (a fill inside a card and the pressed row: the owner's bubble, a code block, a chip), `sheet` (a
///   sheet's own ground), `glassFallback` (the opaque fill floating chrome uses under Reduce Transparency).
///   No surface has a stroke.
/// - Ink: `text`, `secondary`, `tertiary` (every one ≥ 4.5:1 on every surface), `glyph` (chevrons and decorative
///   symbols, ≥ 3:1). The primary action is the ink itself (`action` with `onAction` on it): there is no accent hue.
/// - Colour means two things only: `warn` (amber) = waits on you, `failure` (red) = failed.
struct Palette: Equatable, Sendable {
    let ground: RGB
    let card: RGB
    let nested: RGB
    let sheet: RGB
    let glassFallback: RGB
    let text: RGB
    let secondary: RGB
    let tertiary: RGB
    let glyph: RGB
    let action: RGB
    let onAction: RGB
    let warn: RGB
    let warnFill: RGB
    let failure: RGB
    /// The opacity of `text` for a hairline in a dense list (≤ 6 %, plan §3); the only line the app draws.
    let separatorOpacity: Double

    static func of(_ variant: PaletteVariant) -> Palette {
        switch variant {
        case .light: light
        case .dark: dark
        case .lightIncreased: lightIncreased
        case .darkIncreased: darkIncreased
        }
    }

    static let dark: Palette = Palette(
        ground: RGB(hex: 0x121212), card: RGB(hex: 0x1F1F1F), nested: RGB(hex: 0x2C2C2E),
        sheet: RGB(hex: 0x181818), glassFallback: RGB(hex: 0x262626),
        text: RGB(hex: 0xF5F5F5), secondary: RGB(hex: 0xB0B0B0), tertiary: RGB(hex: 0x989898),
        glyph: RGB(hex: 0x808080), action: RGB(hex: 0xF5F5F5), onAction: RGB(hex: 0x121212),
        warn: RGB(hex: 0xF2BC4E), warnFill: RGB(hex: 0x3A2D10), failure: RGB(hex: 0xEE8480),
        separatorOpacity: 0.06)

    /// Not the dark look inverted: a light grey ground with white cards above it, so a card is never darker than its
    /// ground and the groups read as tonal panels (review S3: white on the reference's light ground is 1.03:1).
    static let light: Palette = Palette(
        ground: RGB(hex: 0xF2F2F2), card: RGB(hex: 0xFFFFFF), nested: RGB(hex: 0xE9E9EB),
        sheet: RGB(hex: 0xF2F2F2), glassFallback: RGB(hex: 0xFAFAFA),
        text: RGB(hex: 0x0D0D0D), secondary: RGB(hex: 0x4A4A4A), tertiary: RGB(hex: 0x636363),
        glyph: RGB(hex: 0x7A7A7A), action: RGB(hex: 0x0D0D0D), onAction: RGB(hex: 0xFFFFFF),
        warn: RGB(hex: 0x7A4500), warnFill: RGB(hex: 0xF6E8CC), failure: RGB(hex: 0xB42318),
        separatorOpacity: 0.06)

    /// Increase Contrast: the quiet tiers move toward the words, the surfaces spread a little further apart.
    static let darkIncreased: Palette = Palette(
        ground: RGB(hex: 0x000000), card: RGB(hex: 0x1C1C1C), nested: RGB(hex: 0x333336),
        sheet: RGB(hex: 0x111111), glassFallback: RGB(hex: 0x262626),
        text: RGB(hex: 0xFFFFFF), secondary: RGB(hex: 0xD0D0D0), tertiary: RGB(hex: 0xB8B8B8),
        glyph: RGB(hex: 0x9C9C9C), action: RGB(hex: 0xFFFFFF), onAction: RGB(hex: 0x000000),
        warn: RGB(hex: 0xFFC961), warnFill: RGB(hex: 0x3A2D10), failure: RGB(hex: 0xFF9A96),
        separatorOpacity: 0.14)

    static let lightIncreased: Palette = Palette(
        ground: RGB(hex: 0xEBEBEB), card: RGB(hex: 0xFFFFFF), nested: RGB(hex: 0xDEDEE0),
        sheet: RGB(hex: 0xEBEBEB), glassFallback: RGB(hex: 0xFFFFFF),
        text: RGB(hex: 0x000000), secondary: RGB(hex: 0x2E2E2E), tertiary: RGB(hex: 0x454545),
        glyph: RGB(hex: 0x5C5C5C), action: RGB(hex: 0x000000), onAction: RGB(hex: 0xFFFFFF),
        warn: RGB(hex: 0x5E3500), warnFill: RGB(hex: 0xF6E8CC), failure: RGB(hex: 0x8F1B12),
        separatorOpacity: 0.14)
}

/// One measured pair: an ink over the surface it may be drawn on.
struct ContrastPair: Equatable, Sendable {
    let variant: PaletteVariant
    let ink: String
    let surface: String
    let ratio: Double
    let minimum: Double

    var passes: Bool { ratio >= minimum }
}

/// The rendered combinations of a palette (review S3, "test rendered combinations"): every text ink on every surface,
/// every glyph ink on every surface, the action's label on the action, amber ink on the amber fill, and the action as a
/// glyph (send disc, switch track) on the surfaces it sits on.
enum ContrastAudit {
    static let textMinimum: Double = 4.5
    static let glyphMinimum: Double = 3.0

    static func pairs(of variant: PaletteVariant) -> [ContrastPair] {
        let palette: Palette = Palette.of(variant)
        let surfaces: [(String, RGB)] = [
            ("ground", palette.ground), ("card", palette.card), ("nested", palette.nested),
            ("sheet", palette.sheet), ("glassFallback", palette.glassFallback),
        ]
        let texts: [(String, RGB)] = [
            ("text", palette.text), ("secondary", palette.secondary), ("tertiary", palette.tertiary),
            ("warn", palette.warn), ("failure", palette.failure),
        ]
        let glyphs: [(String, RGB)] = [("glyph", palette.glyph), ("action", palette.action)]
        var result: [ContrastPair] = []
        for (surfaceName, surface) in surfaces {
            for (inkName, ink) in texts {
                result.append(pair(variant, inkName, ink, surfaceName, surface, textMinimum))
            }
            for (inkName, ink) in glyphs {
                result.append(pair(variant, inkName, ink, surfaceName, surface, glyphMinimum))
            }
        }
        result.append(pair(variant, "onAction", palette.onAction, "action", palette.action, textMinimum))
        result.append(pair(variant, "warn", palette.warn, "warnFill", palette.warnFill, textMinimum))
        // The tab badge: the ground's colour on the amber.
        result.append(pair(variant, "ground", palette.ground, "warn", palette.warn, textMinimum))
        return result
    }

    /// Every pair that fails its minimum, over all four palettes. Empty is the contract.
    static func failures() -> [ContrastPair] {
        PaletteVariant.allCases.flatMap(pairs(of:)).filter { !$0.passes }
    }

    private static func pair(
        _ variant: PaletteVariant, _ inkName: String, _ ink: RGB, _ surfaceName: String, _ surface: RGB,
        _ minimum: Double
    ) -> ContrastPair {
        ContrastPair(
            variant: variant, ink: inkName, surface: surfaceName, ratio: Contrast.ratio(ink, surface), minimum: minimum)
    }
}
