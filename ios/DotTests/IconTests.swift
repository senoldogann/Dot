import SwiftUI
import UIKit
import XCTest
@testable import Dot

/// The app's icons (item 1461): Lucide's shapes as the vectors of `Icons.xcassets`, drawn as templates by `DotIcon`. The
/// catalog is written by `scripts/export_ios_icons.py` from the web app's `icons.js` (its own test is
/// `tests/unit/test_ios_icons.py`); what is tested here is what only the phone can say: that the system's renderer reads each
/// SVG, and draws it in the colour the text round it has.
final class IconTests: XCTestCase {
    func testEveryIconOfTheListHasAnImageInTheCatalog() {
        XCTAssertGreaterThanOrEqual(IconName.allCases.count, 40)
        for name in IconName.allCases {
            XCTAssertNotNil(UIImage(named: name.rawValue), "\(name.rawValue) has no image: run scripts/export_ios_icons.py")
        }
    }

    func testTheNamesAreTheCatalogsAndNoneIsTwice() {
        XCTAssertEqual(Set(IconName.allCases.map(\.rawValue)).count, IconName.allCases.count)
        XCTAssertEqual(IconName(rawValue: "bot"), .bot)
        XCTAssertNil(IconName(rawValue: "repeat"), "Swift reserves it: the loop mark is `loop`")
    }

    /// The system's renderer takes the SVGs as the exporter writes them (a black stroke, no `currentColor`): each icon draws
    /// something, and all of what it draws is in the colour it was given (the system's red), not black.
    @MainActor
    func testEveryIconDrawsInTheColourItIsGiven() throws {
        for name in IconName.allCases {
            let renderer: ImageRenderer<AnyView> = ImageRenderer(content: AnyView(DotIcon(name, size: 48).foregroundStyle(Color.red)))
            renderer.scale = 2
            let image: CGImage = try XCTUnwrap(renderer.uiImage?.cgImage, "\(name.rawValue) did not render")
            let (drawn, red, sample): (Int, Int, String) = Self.count(image)
            XCTAssertGreaterThan(drawn, 40, "\(name.rawValue) drew nothing: the renderer may not read its SVG")
            XCTAssertEqual(red, drawn, "\(name.rawValue) is not all in the colour it was given (first drawn pixel: \(sample))")
        }
    }

    /// The pixels that are drawn at all (opaque enough), and how many of them are red.
    private static func count(_ image: CGImage) -> (drawn: Int, red: Int, sample: String) {
        let width: Int = image.width, height: Int = image.height
        var bytes: [UInt8] = [UInt8](repeating: 0, count: width * height * 4)
        let space: CGColorSpace = CGColorSpaceCreateDeviceRGB()
        bytes.withUnsafeMutableBytes { raw in
            guard let context: CGContext = CGContext(
                data: raw.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4, space: space,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
            else { return }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        }
        var drawn: Int = 0, red: Int = 0
        var sample: String = "none"
        for pixel in 0..<(width * height) {
            let r: Int = Int(bytes[pixel * 4]), g: Int = Int(bytes[pixel * 4 + 1]), b: Int = Int(bytes[pixel * 4 + 2]), a: Int = Int(bytes[pixel * 4 + 3])
            guard a > 128 else { continue }
            if drawn == 0 { sample = "r\(r) g\(g) b\(b) a\(a)" }
            drawn += 1
            // Premultiplied: a pixel of the system's red has its red as high as its alpha and little green or blue; a black one
            // (the SVG's own stroke, the tint not taken) has none of the red.
            if Double(r) >= 0.85 * Double(a), Double(g) <= 0.4 * Double(a), Double(b) <= 0.4 * Double(a) { red += 1 }
        }
        return (drawn, red, sample)
    }

    func testTheLicenceOfTheIconsIsInTheApp() throws {
        let url: URL = try XCTUnwrap(Bundle.main.url(forResource: "LICENSE-Lucide", withExtension: "txt"), "the licence is not a resource of the app")
        let text: String = try String(contentsOf: url, encoding: .utf8)
        XCTAssertTrue(text.contains("ISC License"))
        XCTAssertTrue(text.contains("Lucide"))
        XCTAssertTrue(text.contains("The MIT License"), "the icons Lucide took from Feather are under Feather's licence")
    }
}
