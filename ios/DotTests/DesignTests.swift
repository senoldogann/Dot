import SwiftUI
import UIKit
import XCTest
@testable import Dot

/// The design tokens (items 1380 and 1641): every text token holds 4.5:1 on the grounds it is drawn on, in both looks, so a
/// change of a colour that makes words hard to read fails here and not on a phone; the family is neutral (no accent hue), the
/// primary action is the ink itself, and a card is never darker than the ground it lies on.
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
            let inks: [(String, DotRGB)] = [("text", p.text), ("muted", p.muted), ("faint", p.faint), ("warn", p.warn), ("bad", p.bad)]
            for (inkName, ink) in inks {
                for (groundName, ground) in grounds {
                    XCTAssertGreaterThanOrEqual(
                        ink.contrast(with: ground), 4.5, "\(look.name): \(inkName) on \(groundName)")
                }
            }
        }
    }

    /// Glyphs (a chevron, a state mark, a quiet icon) need 3:1; every ink the app draws a glyph in holds more on every ground.
    func testEveryGlyphInkHolds3To1OnEveryGround() {
        for look in looks {
            let p: DotPalette = look.palette
            for (inkName, ink) in [("muted", p.muted), ("faint", p.faint), ("warn", p.warn), ("bad", p.bad)] {
                for (groundName, ground) in [("background", p.background), ("surface", p.surface), ("raised", p.raised)] {
                    XCTAssertGreaterThanOrEqual(ink.contrast(with: ground), 3, "\(look.name): \(inkName) glyph on \(groundName)")
                }
            }
        }
    }

    func testTheActionsLabelHolds45To1OnItsFill() {
        for look in looks {
            let p: DotPalette = look.palette
            XCTAssertGreaterThanOrEqual(p.onAction.contrast(with: p.action), 4.5, "\(look.name): the label of the primary action")
        }
    }

    func testTheAmberFillHoldsTheTextAndItsOwnInk() {
        for look in looks {
            let p: DotPalette = look.palette
            XCTAssertGreaterThanOrEqual(p.text.contrast(with: p.warnSurface), 4.5, "\(look.name): text on the amber fill")
            XCTAssertGreaterThanOrEqual(p.warn.contrast(with: p.warnSurface), 4.5, "\(look.name): the amber ink on the amber fill")
        }
    }

    /// The swipe actions of a row are drawn by the system with a white label.
    func testTheSwipeFillsHoldTheirWhiteLabel() {
        let white: DotRGB = DotRGB(hex: 0xFFFFFF)
        XCTAssertGreaterThanOrEqual(white.contrast(with: Theme.swipeNeutralRGB), 4.5)
        XCTAssertGreaterThanOrEqual(white.contrast(with: Theme.swipeDestructiveRGB), 4.5)
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

    /// The Queued sheet's bug: a card darker than what it lies on. In both looks the surface is at least as light as the ground.
    func testACardIsNeverDarkerThanItsGround() {
        for look in looks {
            XCTAssertGreaterThanOrEqual(look.palette.surface.luminance, look.palette.background.luminance, look.name)
        }
    }

    /// One neutral family: the grounds, the ink and the action are greys (their channels are within 3/255 of each other). Colour
    /// is only the amber of what waits on the owner and the muted red of what failed, and each of those leans to red.
    func testTheFamilyIsNeutralAndOnlyAmberAndRedAreColours() {
        for look in looks {
            let p: DotPalette = look.palette
            let neutral: [(String, DotRGB)] = [
                ("background", p.background), ("surface", p.surface), ("raised", p.raised), ("text", p.text), ("muted", p.muted),
                ("faint", p.faint), ("action", p.action), ("onAction", p.onAction),
            ]
            for (name, colour) in neutral { XCTAssertLessThanOrEqual(colour.spread, 3.0 / 255, "\(look.name): \(name) is a grey") }
            for (name, colour) in [("warn", p.warn), ("warnSurface", p.warnSurface), ("bad", p.bad)] {
                XCTAssertGreaterThan(colour.red, colour.blue, "\(look.name): \(name) is warm, never blue")
            }
        }
    }

    /// The accent is not a hue: the primary action is the text colour, and its label is the ground.
    func testThePrimaryActionIsTheInkOnTheGround() {
        for look in looks {
            XCTAssertEqual(look.palette.action, look.palette.text, look.name)
            XCTAssertEqual(look.palette.onAction.luminance, look.palette.background.luminance, accuracy: 0.002, look.name)
        }
    }

    private func components(_ color: Color, _ style: UIUserInterfaceStyle) -> (CGFloat, CGFloat, CGFloat) {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        UIColor(color).resolvedColor(with: UITraitCollection(userInterfaceStyle: style)).getRed(&r, green: &g, blue: &b, alpha: &a)
        return (r, g, b)
    }

    func testThemeColoursFollowTheAppearance() {
        let dark: (CGFloat, CGFloat, CGFloat) = components(Theme.background, .dark)
        let light: (CGFloat, CGFloat, CGFloat) = components(Theme.background, .light)
        XCTAssertEqual(Double(dark.0), DotPalette.dark.background.red, accuracy: 0.002)
        XCTAssertEqual(Double(light.0), DotPalette.light.background.red, accuracy: 0.002)
        XCTAssertLessThan(dark.0, 0.1, "near-black in the dark")
        XCTAssertGreaterThan(light.0, 0.9, "a light grey in the light")
        let action: (CGFloat, CGFloat, CGFloat) = components(Theme.action, .dark)
        XCTAssertEqual(Double(action.0), DotPalette.dark.text.red, accuracy: 0.002, "the primary action is the ink")
    }

    /// The `AccentColor` asset is the system tint of every control that sets none (a switch, a text cursor, an alert's button): it
    /// is the ink, in both looks, so that no control falls back to a blue.
    func testTheAccentColorAssetIsTheInkInBothLooks() throws {
        for (style, palette) in [(UIUserInterfaceStyle.dark, DotPalette.dark), (UIUserInterfaceStyle.light, DotPalette.light)] {
            let traits: UITraitCollection = UITraitCollection(userInterfaceStyle: style)
            let accent: UIColor = try XCTUnwrap(UIColor(named: "AccentColor", in: Bundle(for: NativeStore.self), compatibleWith: traits))
            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
            accent.resolvedColor(with: traits).getRed(&r, green: &g, blue: &b, alpha: &a)
            XCTAssertEqual(Double(r), palette.text.red, accuracy: 0.004, "\(style.rawValue)")
            XCTAssertEqual(Double(g), palette.text.green, accuracy: 0.004, "\(style.rawValue)")
            XCTAssertEqual(Double(b), palette.text.blue, accuracy: 0.004, "\(style.rawValue)")
        }
    }
}

final class ToolGlyphTests: XCTestCase {
    /// Dot's own tools wear the icon the web app gives them (`toolIconName` of icons.js), so a run reads the same on the
    /// phone and on the Mac.
    func testDotsToolsGetTheIconOfTheWebApp() {
        let web: [(String, IconName)] = [
            ("shell", .terminal), ("terminal_send", .terminal), ("read_file", .eye), ("write_file", .file), ("edit_file", .file),
            ("find_files", .search), ("search_files", .search), ("list_dir", .list), ("web_search", .globe), ("web_fetch", .globe),
            ("browser_click", .globe), ("desktop_act", .monitor), ("apple_script", .monitor), ("desktop_clipboard_read", .copy),
            ("memory_save", .database), ("schedule_create", .calendar), ("delegate", .branch), ("update_plan", .list),
            ("ask_user", .help), ("ask_secret", .key), ("notify", .bell), ("use_tools", .layers), ("skill", .puzzle),
            ("mcp__tavily__tavily_search", .plug),
        ]
        for (tool, icon) in web { XCTAssertEqual(ToolGlyph.icon(for: tool), icon, tool) }
    }

    /// A name that is another harness's is read by its words.
    func testAnotherHarnessesToolsAreReadByTheirWords() {
        XCTAssertEqual(ToolGlyph.icon(for: "run_shell"), .terminal)
        XCTAssertEqual(ToolGlyph.icon(for: "Bash"), .terminal)
        XCTAssertEqual(ToolGlyph.icon(for: "send_email"), .mail)
        XCTAssertEqual(ToolGlyph.icon(for: "message_agent"), .users, "an agent's name is not a mail's")
        XCTAssertEqual(ToolGlyph.icon(for: "NotebookEdit"), .squarePen)
    }

    func testAnUnknownToolGetsTheWrench() {
        XCTAssertEqual(ToolGlyph.icon(for: "frobnicate"), .wrench)
        XCTAssertEqual(ToolGlyph.icon(for: ""), .wrench)
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
