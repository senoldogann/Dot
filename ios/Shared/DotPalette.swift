import Foundation

/// An sRGB colour as plain numbers, so that the design's contrast can be measured by a test and the Live Activity
/// (which does not link the app's `Theme`) draws with the same values.
struct DotRGB: Equatable, Sendable {
    let red: Double
    let green: Double
    let blue: Double

    init(hex: UInt32) {
        red = Double((hex >> 16) & 0xFF) / 255
        green = Double((hex >> 8) & 0xFF) / 255
        blue = Double(hex & 0xFF) / 255
    }

    /// WCAG 2 relative luminance.
    var luminance: Double {
        func linear(_ channel: Double) -> Double {
            channel <= 0.03928 ? channel / 12.92 : pow((channel + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    }

    /// WCAG 2 contrast ratio, 1 to 21, whichever colour is the lighter.
    func contrast(with other: DotRGB) -> Double {
        let lighter: Double = max(luminance, other.luminance)
        let darker: Double = min(luminance, other.luminance)
        return (lighter + 0.05) / (darker + 0.05)
    }

    /// How far the colour is from a grey: the spread of its channels, 0 (a neutral) to 1.
    var spread: Double { max(red, green, blue) - min(red, green, blue) }
}

/// The colour tokens of the iPhone app, in its two looks (items 1640 to 1647, the owner's order of 2026-11-24: a professional,
/// calm, adult look). ONE neutral graphite family, taken from the web app's Graphite theme (`static/js/appearance/palettes-more.js`):
/// a ground, a surface one step above it, a raised step; the ink in three tiers; the primary action is the ink itself (a light
/// pill with a dark label on the dark ground, a dark pill with a light label on the light one). There is no accent hue. Colour
/// is for two states only: amber for what waits on the owner and a muted red for what failed. A card is never darker than the
/// ground it lies on. Every text token holds a contrast of 4.5:1 against the grounds it is drawn on (`DotPaletteTests`).
struct DotPalette: Sendable {
    /// The app's ground, and of every sheet.
    let background: DotRGB
    /// A card, a row group: one step above the ground.
    let surface: DotRGB
    /// A fill a step off the surface: the owner's message, an avatar's disc, a field, a code block.
    let raised: DotRGB
    /// The ink in three tiers: the words, the secondary words, the quiet tier (a footer, a time, a placeholder).
    let text: DotRGB
    let muted: DotRGB
    let faint: DotRGB
    /// The primary action: the ink as a fill, and the ground as the label on it.
    let action: DotRGB
    let onAction: DotRGB
    /// Waiting on the owner: an amber ink, and the one amber fill (the capsule over the composer's "Waiting on you").
    let warn: DotRGB
    let warnSurface: DotRGB
    /// A failure: a muted red ink for a glyph and its words, never a fill.
    let bad: DotRGB

    static let dark: DotPalette = DotPalette(
        background: DotRGB(hex: 0x121212), surface: DotRGB(hex: 0x1F1F1F), raised: DotRGB(hex: 0x2C2C2E),
        text: DotRGB(hex: 0xF5F5F5), muted: DotRGB(hex: 0xB0B0B0), faint: DotRGB(hex: 0x949494),
        action: DotRGB(hex: 0xF5F5F5), onAction: DotRGB(hex: 0x121212),
        warn: DotRGB(hex: 0xF2BC4E), warnSurface: DotRGB(hex: 0x3A2D10), bad: DotRGB(hex: 0xEE8480))

    /// The light look is not the dark one inverted: a light grey ground with white cards above it (a card is never darker than
    /// its ground), the web's Graphite greys one step across.
    static let light: DotPalette = DotPalette(
        background: DotRGB(hex: 0xF2F2F2), surface: DotRGB(hex: 0xFFFFFF), raised: DotRGB(hex: 0xE9E9EB),
        text: DotRGB(hex: 0x0D0D0D), muted: DotRGB(hex: 0x4A4A4A), faint: DotRGB(hex: 0x666666),
        action: DotRGB(hex: 0x0D0D0D), onAction: DotRGB(hex: 0xFFFFFF),
        warn: DotRGB(hex: 0x7A4500), warnSurface: DotRGB(hex: 0xF6E8CC), bad: DotRGB(hex: 0xB42318))
}
