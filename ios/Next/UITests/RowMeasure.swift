import UIKit
import XCTest

/// Measures the rows of a screen from its screenshot (the design bar of SETTINGS-NATIVE.md: measure, do not eyeball).
/// A row is an element as wide as a grouped section's card; its mark, its chevron and a switch's track are found as
/// the pixels that differ from the card under them. Every number is in points.
struct RowMeasure {
    struct Pixels {
        let width: Int
        let height: Int
        let bytes: [UInt8]
        let scale: CGFloat

        init(_ image: UIImage, pointsWide: CGFloat) {
            let cg: CGImage = image.cgImage!
            width = cg.width
            height = cg.height
            scale = CGFloat(cg.width) / pointsWide
            var raw: [UInt8] = [UInt8](repeating: 0, count: width * height * 4)
            let context: CGContext = CGContext(
                data: &raw, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
            context.draw(cg, in: CGRect(x: 0, y: 0, width: width, height: height))
            bytes = raw
        }

        func pixel(_ x: Int, _ y: Int) -> (Int, Int, Int) {
            let at: Int = (y * width + x) * 4
            return (Int(bytes[at]), Int(bytes[at + 1]), Int(bytes[at + 2]))
        }

        /// The box, in points, of the pixels of `rect` (points) that differ from `ground` by more than a little.
        func ink(in rect: CGRect, ground: (Int, Int, Int)) -> CGRect? {
            let x0: Int = max(0, Int(rect.minX * scale)), x1: Int = min(width - 1, Int(rect.maxX * scale))
            let y0: Int = max(0, Int(rect.minY * scale)), y1: Int = min(height - 1, Int(rect.maxY * scale))
            guard x0 < x1, y0 < y1 else { return nil }
            var minX: Int = Int.max, minY: Int = Int.max, maxX: Int = -1, maxY: Int = -1
            for y in y0...y1 {
                for x in x0...x1 {
                    let (r, g, b): (Int, Int, Int) = pixel(x, y)
                    if abs(r - ground.0) + abs(g - ground.1) + abs(b - ground.2) > 60 {
                        minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
                    }
                }
            }
            guard maxX >= 0 else { return nil }
            return CGRect(x: CGFloat(minX) / scale, y: CGFloat(minY) / scale, width: CGFloat(maxX - minX + 1) / scale, height: CGFloat(maxY - minY + 1) / scale)
        }
    }

    /// The inset of a section's card from the screen's edges, and of a row's mark and trailing control from the card's
    /// (the app's `Space.gutter` and `Space.groupedGutter`); the mark's slot (`Metric.iconSlot`).
    static let cardInset: CGFloat = 16
    static let rowInset: CGFloat = 14
    static let iconSlot: CGFloat = 22
    static let tolerance: CGFloat = 1

    /// Measures every full-width row and switch of `app` on screen now; returns one line a row, and fails the test on
    /// a difference over a point.
    static func check(_ app: XCUIApplication, screen name: String, file: StaticString = #filePath, line: UInt = #line) -> [String] {
        let screenWidth: CGFloat = app.windows.firstMatch.frame.width
        let screenHeight: CGFloat = app.windows.firstMatch.frame.height
        let pixels: Pixels = Pixels(app.screenshot().image, pointsWide: screenWidth)
        var lines: [String] = []
        // The bars over and under the scrolled content (navigation, tab bar) are glass: rows under them are not measured.
        let visible: CGRect = CGRect(x: 0, y: 110, width: screenWidth, height: screenHeight - 110 - 100)
        let rows: [XCUIElement] = app.buttons.allElementsBoundByIndex.filter { element in
            let frame: CGRect = element.frame
            return abs(frame.width - (screenWidth - 2 * cardInset)) <= tolerance && visible.contains(frame)
        }
        for row in rows {
            let frame: CGRect = row.frame
            let label: String = String(row.label.prefix(28))
            let ground: (Int, Int, Int) = pixels.pixel(Int((frame.minX + 4) * pixels.scale), Int(frame.midY * pixels.scale))
            let left: CGFloat = frame.minX
            let right: CGFloat = screenWidth - frame.maxX
            XCTAssertLessThanOrEqual(abs(left - right), tolerance, "\(name) \(label): card insets \(left) / \(right)", file: file, line: line)
            XCTAssertGreaterThanOrEqual(frame.height, 44, "\(name) \(label): row under 44 pt", file: file, line: line)
            let slot: CGRect = CGRect(x: frame.minX + rowInset, y: frame.minY + 2, width: iconSlot, height: frame.height - 4)
            var parts: [String] = ["\(name) · \(label): h \(fmt(frame.height)), insets \(fmt(left))/\(fmt(right))"]
            if let mark = pixels.ink(in: slot, ground: ground) {
                let dy: CGFloat = mark.midY - frame.midY
                let dx: CGFloat = mark.midX - slot.midX
                parts.append("mark Δy \(fmt(dy)) Δx \(fmt(dx))")
                XCTAssertLessThanOrEqual(abs(dy), tolerance, "\(name) \(label): mark off the row's centre by \(dy)", file: file, line: line)
                XCTAssertLessThanOrEqual(abs(dx), tolerance, "\(name) \(label): mark off its slot's centre by \(dx)", file: file, line: line)
            }
            let tail: CGRect = CGRect(x: frame.maxX - rowInset - 14, y: frame.minY + 2, width: 14, height: frame.height - 4)
            if let chevron = pixels.ink(in: tail, ground: ground), chevron.width < 12 {
                let dy: CGFloat = chevron.midY - frame.midY
                let inset: CGFloat = frame.maxX - chevron.maxX
                parts.append("chevron Δy \(fmt(dy)) right \(fmt(inset))")
                XCTAssertLessThanOrEqual(abs(dy), tolerance, "\(name) \(label): chevron off the row's centre by \(dy)", file: file, line: line)
                XCTAssertLessThanOrEqual(abs(inset - rowInset), tolerance, "\(name) \(label): chevron's right inset \(inset)", file: file, line: line)
            }
            lines.append(parts.joined(separator: ", "))
        }
        for toggle in app.switches.allElementsBoundByIndex where visible.contains(toggle.frame) {
            let frame: CGRect = toggle.frame
            let label: String = String(toggle.label.prefix(28))
            let cardRight: CGFloat = screenWidth - cardInset
            let ground: (Int, Int, Int) = pixels.pixel(Int((cardRight - 3) * pixels.scale), Int(frame.midY * pixels.scale))
            let area: CGRect = CGRect(x: cardRight - rowInset - 60, y: frame.midY - 20, width: 60, height: 40)
            guard let track = pixels.ink(in: area, ground: ground) else { continue }
            let inset: CGFloat = cardRight - track.maxX
            let dy: CGFloat = track.midY - frame.midY
            lines.append("\(name) · switch \(label): right \(fmt(inset)), Δy \(fmt(dy))")
            XCTAssertLessThanOrEqual(abs(inset - rowInset), tolerance, "\(name) \(label): switch's right inset \(inset)", file: file, line: line)
        }
        return lines
    }

    private static func fmt(_ value: CGFloat) -> String { String(format: "%.1f", value) }
}
