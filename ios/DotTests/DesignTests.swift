import SwiftUI
import UIKit
import XCTest
@testable import Dot

/// The design tokens (item 1380): every text token holds 4.5:1 on the grounds it is drawn on, in both looks, so a
/// change of a colour that makes words hard to read fails here and not on a phone.
final class DotPaletteTests: XCTestCase {
    private let looks: [(name: String, palette: DotPalette)] = [("dark", .dark), ("light", .light)]

    func testContrastRatioMatchesTheWCAGExtremes() {
        let black: DotRGB = DotRGB(hex: 0x000000)
        let white: DotRGB = DotRGB(hex: 0xFFFFFF)
        XCTAssertEqual(black.contrast(with: white), 21, accuracy: 0.001)
        XCTAssertEqual(white.contrast(with: white), 1, accuracy: 0.001)
        XCTAssertEqual(DotRGB(hex: 0x767676).contrast(with: white), 4.54, accuracy: 0.01, "the known grey that just passes")
    }

    func testEveryTextTokenHolds45To1OnEveryGround() {
        for look in looks {
            let p: DotPalette = look.palette
            let grounds: [(String, DotRGB)] = [("background", p.background), ("surface", p.surface), ("raised", p.raised)]
            let inks: [(String, DotRGB)] = [
                ("text", p.text), ("muted", p.muted), ("faint", p.faint), ("accent", p.accent), ("link", p.link),
                ("warn", p.warn), ("bad", p.bad), ("good", p.good),
            ]
            for (inkName, ink) in inks {
                for (groundName, ground) in grounds {
                    XCTAssertGreaterThanOrEqual(
                        ink.contrast(with: ground), 4.5, "\(look.name): \(inkName) on \(groundName)")
                }
            }
        }
    }

    func testWhiteTextOnTheAccentFillHolds45To1AtBothEnds() {
        for look in looks {
            let p: DotPalette = look.palette
            XCTAssertGreaterThanOrEqual(p.onAccent.contrast(with: p.accentFillTop), 4.5, "\(look.name): the top of the fill")
            XCTAssertGreaterThanOrEqual(p.onAccent.contrast(with: p.accentFill), 4.5, "\(look.name): the bottom of the fill")
        }
    }

    func testTheTextLadderStepsDownInTheDarkAndUpInTheLight() {
        // text is the strongest ink, then muted, then faint: each a step nearer the ground.
        for look in looks {
            let p: DotPalette = look.palette
            XCTAssertGreaterThan(p.text.contrast(with: p.background), p.muted.contrast(with: p.background), look.name)
            XCTAssertGreaterThan(p.muted.contrast(with: p.background), p.faint.contrast(with: p.background), look.name)
        }
    }

    func testTheGroundsStepAwayFromTheBaseInTheDark() {
        let p: DotPalette = .dark
        XCTAssertLessThan(p.background.luminance, p.surface.luminance)
        XCTAssertLessThan(p.surface.luminance, p.raised.luminance)
    }

    func testThemeColoursFollowTheAppearance() {
        func components(_ color: Color, _ style: UIUserInterfaceStyle) -> (CGFloat, CGFloat, CGFloat) {
            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
            UIColor(color).resolvedColor(with: UITraitCollection(userInterfaceStyle: style)).getRed(&r, green: &g, blue: &b, alpha: &a)
            return (r, g, b)
        }
        let dark: (CGFloat, CGFloat, CGFloat) = components(Theme.background, .dark)
        let light: (CGFloat, CGFloat, CGFloat) = components(Theme.background, .light)
        XCTAssertEqual(Double(dark.0), DotPalette.dark.background.red, accuracy: 0.002)
        XCTAssertEqual(Double(light.0), DotPalette.light.background.red, accuracy: 0.002)
        XCTAssertLessThan(dark.0, 0.1, "near-black in the dark")
        XCTAssertGreaterThan(light.0, 0.9, "a cool white in the light")
    }

    func testALinkIsBrighterThanTheTextInTheDark() {
        let link: DotRGB = DotPalette.dark.link
        XCTAssertGreaterThan(max(link.red, link.green, link.blue), Theme.textLevel, "its brightest channel is above the text's")
        XCTAssertGreaterThan(link.blue, link.red, "it leans to the blue of the accent")
    }
}

final class ToolGlyphTests: XCTestCase {
    func testToolsGetTheSymbolOfWhatTheyDo() {
        XCTAssertEqual(ToolGlyph.symbol(for: "run_shell"), "terminal")
        XCTAssertEqual(ToolGlyph.symbol(for: "shell"), "terminal")
        XCTAssertEqual(ToolGlyph.symbol(for: "read_file"), "doc.text")
        XCTAssertEqual(ToolGlyph.symbol(for: "write_file"), "square.and.pencil")
        XCTAssertEqual(ToolGlyph.symbol(for: "delegate"), "person.2")
        XCTAssertEqual(ToolGlyph.symbol(for: "web_search"), "magnifyingglass")
        XCTAssertEqual(ToolGlyph.symbol(for: "send_email"), "envelope")
        XCTAssertEqual(ToolGlyph.symbol(for: "ask_user"), "questionmark.bubble")
    }

    func testAnUnknownToolGetsTheWrench() {
        XCTAssertEqual(ToolGlyph.symbol(for: "frobnicate"), "wrench.and.screwdriver")
        XCTAssertEqual(ToolGlyph.symbol(for: ""), "wrench.and.screwdriver")
    }

    func testALineSplitsAtItsFirstColonIntoVerbAndWhatItDid() {
        let words: (verb: String, rest: String) = ToolGlyph.split("Shell: ls -l ~/Documents")
        XCTAssertEqual(words.verb, "Shell")
        XCTAssertEqual(words.rest, "ls -l ~/Documents")
    }

    func testALineWithNoColonOrALongLeadIsAllWords() {
        XCTAssertEqual(ToolGlyph.split("Listing the folder").verb, "")
        XCTAssertEqual(ToolGlyph.split("Listing the folder").rest, "Listing the folder")
        let long: String = "A sentence that is much longer than a verb: with a colon"
        XCTAssertEqual(ToolGlyph.split(long).verb, "")
        XCTAssertEqual(ToolGlyph.split(long).rest, long)
    }

    func testOnlyTheFirstColonSplits() {
        let words: (verb: String, rest: String) = ToolGlyph.split("Shell: echo a:b")
        XCTAssertEqual(words.verb, "Shell")
        XCTAssertEqual(words.rest, "echo a:b")
    }
}
