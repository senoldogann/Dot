// Draws DotNext's app icon (owner's decision D12, 2026-11-24): a FLAT graphite tile with a light disc and two dark
// eyes, Dot's own face; no gradient, no glow, no hue. 1024 x 1024 in the three looks iOS asks for: default, dark and
// tinted (a grey image the system tints). iOS rounds the corners itself.
//
// Usage: swift ios/Next/scripts/make_icon.swift ios/Next/Resources/Assets.xcassets/AppIcon.appiconset
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let arguments: [String] = CommandLine.arguments
guard arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: make_icon.swift <appiconset folder>\n".utf8))
    exit(2)
}
let folder: URL = URL(fileURLWithPath: arguments[1])
let side: Int = 1024
let size: CGFloat = CGFloat(side)
guard let space: CGColorSpace = CGColorSpace(name: CGColorSpace.sRGB) else {
    FileHandle.standardError.write(Data("no sRGB colour space\n".utf8))
    exit(1)
}

/// The three colours of a look: the tile, the disc, the eyes.
struct Look {
    let file: String
    let tile: CGFloat
    let disc: CGFloat
    let eyes: CGFloat
}

let looks: [Look] = [
    Look(file: "icon-1024.png", tile: 0x2A / 255, disc: 0xF2 / 255, eyes: 0x1C / 255),
    Look(file: "icon-1024-dark.png", tile: 0x12 / 255, disc: 0xD6 / 255, eyes: 0x12 / 255),
    Look(file: "icon-1024-tinted.png", tile: 0x00 / 255, disc: 0xFF / 255, eyes: 0x00 / 255),
]

func grey(_ level: CGFloat) -> CGColor { CGColor(srgbRed: level, green: level, blue: level, alpha: 1) }

func draw(_ look: Look) -> CGImage? {
    guard
        let context: CGContext = CGContext(
            data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0, space: space,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { return nil }
    context.setFillColor(grey(look.tile))
    context.fill(CGRect(x: 0, y: 0, width: size, height: size))
    // The disc: the same proportions as the in-app face (`DotFace`), centred.
    let diameter: CGFloat = size * 0.62
    let disc: CGRect = CGRect(x: (size - diameter) / 2, y: (size - diameter) / 2, width: diameter, height: diameter)
    context.setFillColor(grey(look.disc))
    context.fillEllipse(in: disc)
    // Two eyes: capsules 0.11 x 0.22 of the disc, 0.15 apart, a little above the centre (CoreGraphics' y goes up).
    let eyeWidth: CGFloat = diameter * 0.11
    let eyeHeight: CGFloat = diameter * 0.22
    let gap: CGFloat = diameter * 0.15
    let centreY: CGFloat = size / 2 + diameter * 0.02
    context.setFillColor(grey(look.eyes))
    for direction in [-1.0, 1.0] as [CGFloat] {
        let centreX: CGFloat = size / 2 + direction * (gap / 2 + eyeWidth / 2)
        let eye: CGRect = CGRect(x: centreX - eyeWidth / 2, y: centreY - eyeHeight / 2, width: eyeWidth, height: eyeHeight)
        context.addPath(CGPath(roundedRect: eye, cornerWidth: eyeWidth / 2, cornerHeight: eyeWidth / 2, transform: nil))
        context.fillPath()
    }
    return context.makeImage()
}

for look in looks {
    guard let image: CGImage = draw(look) else {
        FileHandle.standardError.write(Data("could not draw \(look.file)\n".utf8))
        exit(1)
    }
    let url: URL = folder.appendingPathComponent(look.file)
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        FileHandle.standardError.write(Data("could not write \(url.path)\n".utf8))
        exit(1)
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        FileHandle.standardError.write(Data("could not finish \(url.path)\n".utf8))
        exit(1)
    }
    print("wrote \(url.path)")
}
