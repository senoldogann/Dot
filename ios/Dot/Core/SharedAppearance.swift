import SwiftUI
import UIKit

/// A daemon-resolved sRGB palette. Keep decoding independent of the phone's current color scheme.
struct SharedAppearance: Codable, Equatable, Sendable {
    struct Palette: Codable, Equatable, Sendable {
        let canvas: String
        let surface: String
        let raised: String
        let text: String
        let muted: String
        let accent: String
    }

    struct Theme: Codable, Equatable, Sendable {
        let light: Palette
        let dark: Palette
        let artwork: String?
    }

    let revision: Int
    let active: Theme?

    struct Artwork: Decodable, Sendable {
        let jpeg: Data
    }

    func palette(in scheme: ColorScheme) -> Palette? {
        scheme == .dark ? active?.dark : active?.light
    }
}

extension SharedAppearance.Palette {
    func color(_ key: KeyPath<Self, String>, fallback: Color) -> Color {
        let hex: String = self[keyPath: key]
        guard hex.count == 7, hex.first == "#", let value: UInt32 = UInt32(hex.dropFirst(), radix: 16) else { return fallback }
        return Color(DotRGB(hex: value))
    }
}

struct CachedAppearance: Codable, Equatable, Sendable {
    let selection: SharedAppearance
    let artwork: Data?
}

struct SharedAppearanceBackground: View {
    let appearance: CachedAppearance?
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        if let palette: SharedAppearance.Palette = appearance?.selection.palette(in: scheme) {
            let canvas: Color = palette.color(\.canvas, fallback: Theme.background)
            GeometryReader { geometry in
                ZStack {
                    canvas
                    if let picture: Image = picture {
                        picture.resizable().scaledToFill()
                            .frame(width: geometry.size.width, height: geometry.size.height)
                            .clipped()
                        LinearGradient(
                            colors: [canvas.opacity(0.94), canvas.opacity(0.84)],
                            startPoint: .topLeading, endPoint: .bottomTrailing)
                    }
                }
            }
            .background(canvas)
            .ignoresSafeArea()
            .accessibilityHidden(true)
        } else {
            Theme.background.ignoresSafeArea()
        }
    }

    private var picture: Image? {
        if appearance?.selection.active?.artwork == "quiet-forest" { return Image("QuietForest") }
        if let data: Data = appearance?.artwork, let image: UIImage = UIImage(data: data) { return Image(uiImage: image) }
        return nil
    }
}

extension EnvironmentValues {
    @Entry var sharedAppearancePalette: SharedAppearance.Palette?
}
