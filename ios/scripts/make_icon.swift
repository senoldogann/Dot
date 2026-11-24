// Draws Dot's app icon (item 1380): a luminous blue dot with two dark eyes on a near-black navy ground, 1024 x 1024, in
// the three looks iOS 18 asks for (default, dark, tinted). iOS rounds the corners itself.
//
// Usage: swift scripts/make_icon.swift Dot/Assets.xcassets/AppIcon.appiconset
//   writes icon-1024.png (default), icon-1024-dark.png and icon-1024-tinted.png into that folder, and the launch
//   screen's mark (700 px; shrink it with `sips -z 360 360`) to ../LaunchMark.imageset/launch-mark.png next to it.
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
let space: CGColorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

func color(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: red, green: green, blue: blue, alpha: alpha)
}

func gradient(_ stops: [(CGFloat, CGColor)]) -> CGGradient {
    CGGradient(colorsSpace: space, colors: stops.map(\.1) as CFArray, locations: stops.map(\.0))!
}

enum Look { case standard, dark, tinted, mark }

func draw(_ look: Look) -> CGImage {
    let context: CGContext = CGContext(
        data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0, space: space,
        bitmapInfo: (look == .mark ? CGImageAlphaInfo.premultipliedLast : CGImageAlphaInfo.noneSkipLast).rawValue)!

    // Ground: near-black navy, lit from the top. (CoreGraphics' origin is the bottom left.)
    let ground: CGGradient =
        look == .standard
        ? gradient([(0, color(0.020, 0.024, 0.047)), (1, color(0.060, 0.090, 0.200))])
        : gradient([(0, color(0.012, 0.012, 0.016)), (1, color(0.030, 0.040, 0.085))])
    if look != .mark { context.drawLinearGradient(ground, start: CGPoint(x: 0, y: 0), end: CGPoint(x: 0, y: size), options: []) }

    // A glow of the accent behind the dot.
    if look != .tinted && look != .mark {
        let glow: CGGradient = gradient([(0, color(0.25, 0.48, 1.0, 0.55)), (1, color(0.25, 0.48, 1.0, 0))])
        context.drawRadialGradient(
            glow, startCenter: CGPoint(x: size * 0.5, y: size * 0.46), startRadius: 0,
            endCenter: CGPoint(x: size * 0.5, y: size * 0.46), endRadius: size * 0.58, options: [])
    }

    // The dot: a sphere lit from the top left.
    let radius: CGFloat = size * 0.31
    let center: CGPoint = CGPoint(x: size * 0.5, y: size * 0.5)
    let sphere: CGRect = CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
    context.saveGState()
    context.setShadow(offset: CGSize(width: 0, height: -size * 0.03), blur: size * 0.06, color: color(0, 0, 0, 0.55))
    context.setFillColor(color(0.2, 0.4, 0.9))
    context.fillEllipse(in: sphere)
    context.restoreGState()

    context.saveGState()
    context.addEllipse(in: sphere)
    context.clip()
    let body: CGGradient =
        look == .tinted
        ? gradient([(0, color(0.95, 0.95, 0.95)), (0.55, color(0.62, 0.62, 0.62)), (1, color(0.30, 0.30, 0.30))])
        : gradient([(0, color(0.72, 0.86, 1.0)), (0.45, color(0.25, 0.47, 0.95)), (1, color(0.09, 0.19, 0.58))])
    context.drawRadialGradient(
        body, startCenter: CGPoint(x: center.x - radius * 0.32, y: center.y + radius * 0.38), startRadius: 0,
        endCenter: CGPoint(x: center.x - radius * 0.05, y: center.y + radius * 0.05), endRadius: radius * 1.25,
        options: [.drawsAfterEndLocation])
    // A soft highlight on the upper left, and a darker lower rim.
    let highlight: CGGradient = gradient([(0, color(1, 1, 1, 0.55)), (1, color(1, 1, 1, 0))])
    context.drawRadialGradient(
        highlight, startCenter: CGPoint(x: center.x - radius * 0.38, y: center.y + radius * 0.52), startRadius: 0,
        endCenter: CGPoint(x: center.x - radius * 0.38, y: center.y + radius * 0.52), endRadius: radius * 0.55,
        options: [])
    context.restoreGState()
    context.setStrokeColor(color(1, 1, 1, 0.22))
    context.setLineWidth(size * 0.004)
    context.strokeEllipse(in: sphere.insetBy(dx: size * 0.002, dy: size * 0.002))

    // The eyes.
    let eyeWidth: CGFloat = size * 0.07
    let eyeHeight: CGFloat = size * 0.135
    let eyeY: CGFloat = center.y - eyeHeight * 0.35
    for centerX: CGFloat in [center.x - size * 0.078, center.x + size * 0.078] {
        let eye: CGRect = CGRect(x: centerX - eyeWidth / 2, y: eyeY, width: eyeWidth, height: eyeHeight)
        context.setFillColor(look == .tinted ? color(0.08, 0.08, 0.08) : color(0.03, 0.06, 0.17))
        context.addPath(CGPath(roundedRect: eye, cornerWidth: eyeWidth / 2, cornerHeight: eyeWidth / 2, transform: nil))
        context.fillPath()
    }
    return context.makeImage()!
}

func write(_ image: CGImage, _ name: String) {
    let url: URL = folder.appendingPathComponent(name)
    guard let destination: CGImageDestination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
    else {
        FileHandle.standardError.write(Data("cannot create \(url.path)\n".utf8))
        exit(1)
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        FileHandle.standardError.write(Data("cannot write \(url.path)\n".utf8))
        exit(1)
    }
}

write(draw(.standard), "icon-1024.png")
write(draw(.dark), "icon-1024-dark.png")
write(draw(.tinted), "icon-1024-tinted.png")
// The launch screen's mark: the dot alone on transparency, 700 x 700 (shrink it to 360 x 360, shown at 3x, with `sips -z 360 360`).
let launch: URL = URL(fileURLWithPath: folder.deletingLastPathComponent().path + "/LaunchMark.imageset/launch-mark.png")
if let mark: CGImage = draw(.mark).cropping(to: CGRect(x: 162, y: 162, width: 700, height: 700)) {
    try? FileManager.default.createDirectory(at: launch.deletingLastPathComponent(), withIntermediateDirectories: true)
    if let destination: CGImageDestination = CGImageDestinationCreateWithURL(launch as CFURL, UTType.png.identifier as CFString, 1, nil) {
        CGImageDestinationAddImage(destination, mark, nil)
        CGImageDestinationFinalize(destination)
    }
}
