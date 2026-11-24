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
}

/// The colour tokens of the iPhone app, in its two looks. Dark is the signature one: near-black ground, raised
/// surfaces a step lighter, one calm blue. Light keeps the same roles on a cool white. Every text token holds a
/// contrast of 4.5:1 against the grounds it is drawn on (`DotPaletteTests`).
struct DotPalette: Sendable {
    let background: DotRGB
    let surface: DotRGB
    let raised: DotRGB
    let text: DotRGB
    let muted: DotRGB
    let faint: DotRGB
    /// The accent as ink (a symbol, a word) on the grounds.
    let accent: DotRGB
    /// The accent as a fill that white text sits on: its top and its bottom (a very slight gradient).
    let accentFillTop: DotRGB
    let accentFill: DotRGB
    let onAccent: DotRGB
    let link: DotRGB
    let warn: DotRGB
    let bad: DotRGB
    let good: DotRGB

    static let dark: DotPalette = DotPalette(
        background: DotRGB(hex: 0x0A0A0B), surface: DotRGB(hex: 0x121214), raised: DotRGB(hex: 0x1B1B1F),
        text: DotRGB(hex: 0xEDEDEF), muted: DotRGB(hex: 0xA6A6AF), faint: DotRGB(hex: 0x8E8E98),
        accent: DotRGB(hex: 0x6AA4FF), accentFillTop: DotRGB(hex: 0x3070E4), accentFill: DotRGB(hex: 0x2762D8),
        onAccent: DotRGB(hex: 0xFFFFFF), link: DotRGB(hex: 0xB8D6FF), warn: DotRGB(hex: 0xF2BC4E),
        bad: DotRGB(hex: 0xFF7070), good: DotRGB(hex: 0x5FCB92))

    static let light: DotPalette = DotPalette(
        background: DotRGB(hex: 0xF3F3F6), surface: DotRGB(hex: 0xFFFFFF), raised: DotRGB(hex: 0xE9E9EE),
        text: DotRGB(hex: 0x131316), muted: DotRGB(hex: 0x4F4F59), faint: DotRGB(hex: 0x666670),
        accent: DotRGB(hex: 0x2559C9), accentFillTop: DotRGB(hex: 0x3070E4), accentFill: DotRGB(hex: 0x2762D8),
        onAccent: DotRGB(hex: 0xFFFFFF), link: DotRGB(hex: 0x1B4FBF), warn: DotRGB(hex: 0x8F5200),
        bad: DotRGB(hex: 0xBA2626), good: DotRGB(hex: 0x1B7447))
}
