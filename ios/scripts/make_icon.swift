// Draws Dot's placeholder app icon: a yellow circle with two dark oval eyes on a white square, 1024 x 1024.
//
// Usage: swift scripts/make_icon.swift Dot/Assets.xcassets/AppIcon.appiconset/icon-1024.png
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let arguments: [String] = CommandLine.arguments
guard arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: make_icon.swift <output.png>\n".utf8))
    exit(2)
}
let outputURL: URL = URL(fileURLWithPath: arguments[1])
let side: Int = 1024
let space: CGColorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
guard
    let context: CGContext = CGContext(
        data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0, space: space,
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
else {
    FileHandle.standardError.write(Data("cannot create a bitmap context\n".utf8))
    exit(1)
}
let size: CGFloat = CGFloat(side)

// An app icon has no transparency: a white ground, iOS rounds the corners itself.
context.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
context.fill(CGRect(x: 0, y: 0, width: size, height: size))

let inset: CGFloat = size * 0.1
context.setFillColor(CGColor(srgbRed: 1.0, green: 0.78, blue: 0.22, alpha: 1))
context.fillEllipse(in: CGRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset))

let eyeWidth: CGFloat = size * 0.09
let eyeHeight: CGFloat = size * 0.16
let eyeY: CGFloat = size * 0.5
context.setFillColor(CGColor(srgbRed: 0.12, green: 0.11, blue: 0.10, alpha: 1))
for centerX: CGFloat in [size * 0.39, size * 0.61] {
    context.fillEllipse(in: CGRect(x: centerX - eyeWidth / 2, y: eyeY, width: eyeWidth, height: eyeHeight))
}

guard
    let image: CGImage = context.makeImage(),
    let destination: CGImageDestination = CGImageDestinationCreateWithURL(
        outputURL as CFURL, UTType.png.identifier as CFString, 1, nil)
else {
    FileHandle.standardError.write(Data("cannot create \(outputURL.path)\n".utf8))
    exit(1)
}
CGImageDestinationAddImage(destination, image, nil)
guard CGImageDestinationFinalize(destination) else {
    FileHandle.standardError.write(Data("cannot write \(outputURL.path)\n".utf8))
    exit(1)
}
