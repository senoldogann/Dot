import SwiftUI
import UIKit
import XCTest

@testable import DotNext

/// The design system's contracts: contrast of every rendered ink, the palette the system resolves, the type rhythm,
/// the reading column, and the small pure rules the components lean on.
final class PaletteContrastTests: XCTestCase {
    func testEveryInkMeetsItsMinimumOnEverySurfaceInAllFourPalettes() {
        let failures: [ContrastPair] = ContrastAudit.failures()
        XCTAssertEqual(
            failures, [],
            failures.map { "\($0.variant) \($0.ink) on \($0.surface): \(String(format: "%.2f", $0.ratio)) < \($0.minimum)" }
                .joined(separator: "\n"))
    }

    func testTheAuditCoversEveryTextInkAndSurface() {
        let pairs: [ContrastPair] = ContrastAudit.pairs(of: .dark)
        let inks: Set<String> = Set(pairs.map(\.ink))
        let surfaces: Set<String> = Set(pairs.map(\.surface))
        XCTAssertEqual(
            inks, ["text", "secondary", "tertiary", "warn", "failure", "glyph", "action", "onAction", "ground"])
        XCTAssertEqual(surfaces, ["ground", "card", "nested", "sheet", "glassFallback", "action", "warnFill", "warn"])
    }

    func testACardIsNeverDarkerThanItsGroundInTheLightLooks() {
        for variant in [PaletteVariant.light, .lightIncreased] {
            let palette: Palette = Palette.of(variant)
            XCTAssertGreaterThanOrEqual(Contrast.luminance(palette.card), Contrast.luminance(palette.ground), "\(variant)")
        }
    }

    func testTheSurfacesStepUpwardInTheDarkLooks() {
        for variant in [PaletteVariant.dark, .darkIncreased] {
            let palette: Palette = Palette.of(variant)
            XCTAssertLessThan(Contrast.luminance(palette.ground), Contrast.luminance(palette.card), "\(variant)")
            XCTAssertLessThan(Contrast.luminance(palette.card), Contrast.luminance(palette.nested), "\(variant)")
        }
    }

    func testTheFamilyIsNeutralExceptAmberAndRed() {
        for variant in PaletteVariant.allCases {
            let palette: Palette = Palette.of(variant)
            let neutrals: [RGB] = [
                palette.ground, palette.card, palette.nested, palette.sheet, palette.glassFallback, palette.text,
                palette.secondary, palette.tertiary, palette.glyph, palette.action, palette.onAction,
            ]
            for color in neutrals {
                let spread: Double = max(color.red, color.green, color.blue) - min(color.red, color.green, color.blue)
                XCTAssertLessThanOrEqual(spread, 3.0 / 255, "\(variant) \(color)")
            }
        }
    }

    @MainActor
    func testTheResolvedColoursAreThePalettesForEveryTraitCombination() {
        let cases: [(UIUserInterfaceStyle, UIAccessibilityContrast, PaletteVariant)] = [
            (.light, .normal, .light), (.dark, .normal, .dark), (.light, .high, .lightIncreased),
            (.dark, .high, .darkIncreased),
        ]
        for (style, contrast, variant) in cases {
            let traits: UITraitCollection = UITraitCollection { mutable in
                mutable.userInterfaceStyle = style
                mutable.accessibilityContrast = contrast
            }
            let resolved: UIColor = UIColor.dynamic(\.tertiary).resolvedColor(with: traits)
            var red: CGFloat = 0
            var green: CGFloat = 0
            var blue: CGFloat = 0
            var alpha: CGFloat = 0
            XCTAssertTrue(resolved.getRed(&red, green: &green, blue: &blue, alpha: &alpha))
            let expected: RGB = Palette.of(variant).tertiary
            XCTAssertEqual(Double(red), expected.red, accuracy: 0.002, "\(variant)")
            XCTAssertEqual(Double(green), expected.green, accuracy: 0.002, "\(variant)")
            XCTAssertEqual(Double(blue), expected.blue, accuracy: 0.002, "\(variant)")
        }
    }

    func testContrastMatchesKnownWCAGValues() {
        XCTAssertEqual(Contrast.ratio(RGB(hex: 0x000000), RGB(hex: 0xFFFFFF)), 21, accuracy: 0.001)
        XCTAssertEqual(Contrast.ratio(RGB(hex: 0x777777), RGB(hex: 0xFFFFFF)), 4.48, accuracy: 0.01)
        XCTAssertEqual(Contrast.ratio(RGB(hex: 0xFFFFFF), RGB(hex: 0xFCFCFC)), 1.03, accuracy: 0.01)
    }
}

final class TypographyTests: XCTestCase {
    func testLineSpacingTurnsTheNaturalLineIntoTheRolesLineAndScalesWithIt() {
        XCTAssertEqual(lineSpacing(roleSize: 16, roleLineHeight: 23, scaledSize: 16, naturalLineHeight: 19.1), 3.9, accuracy: 0.001)
        XCTAssertEqual(lineSpacing(roleSize: 16, roleLineHeight: 23, scaledSize: 32, naturalLineHeight: 38.2), 7.8, accuracy: 0.001)
        XCTAssertEqual(lineSpacing(roleSize: 13, roleLineHeight: 14, scaledSize: 13, naturalLineHeight: 16), 0)
    }

    func testTheBodyRoleIs16On23() {
        XCTAssertEqual(TypeRole.body.points.size, 16)
        XCTAssertEqual(TypeRole.body.points.lineHeight, 23)
        XCTAssertEqual(TypeRole.code.design, .monospaced)
    }

    /// The reading column holds 60 to 75 characters per line of real prose, counted by TextKit's own line breaking.
    func testTheProseColumnHolds60To75CharactersPerLine() {
        for size in [DynamicTypeSize.large, .xxxLarge, .xSmall] {
            let font: UIFont = ProseColumn.bodyFont(size)
            let width: CGFloat = ProseColumn.width(charactersPerLine: ProseColumn.target, font: font)
            let perLine: Double = averageCharactersPerLine(text: longProse, font: font, width: width)
            XCTAssertGreaterThanOrEqual(perLine, 60, "\(size)")
            XCTAssertLessThanOrEqual(perLine, 75, "\(size)")
        }
    }

    private var longProse: String {
        String(
            repeating: "Dot opened the booking page, compared three free slots and kept the one on Thursday afternoon, "
                + "then stopped before paying the deposit because a payment always waits for your word. ", count: 12)
    }

    private func averageCharactersPerLine(text: String, font: UIFont, width: CGFloat) -> Double {
        let storage: NSTextStorage = NSTextStorage(string: text, attributes: [.font: font])
        let layout: NSLayoutManager = NSLayoutManager()
        let container: NSTextContainer = NSTextContainer(size: CGSize(width: width, height: .greatestFiniteMagnitude))
        container.lineFragmentPadding = 0
        layout.addTextContainer(container)
        storage.addLayoutManager(layout)
        var lines: Int = 0
        var index: Int = 0
        let glyphs: Int = layout.numberOfGlyphs
        while index < glyphs {
            var range: NSRange = NSRange()
            layout.lineFragmentRect(forGlyphAt: index, effectiveRange: &range)
            index = NSMaxRange(range)
            lines += 1
        }
        return Double(text.count) / Double(max(lines, 1))
    }
}

final class ComponentRuleTests: XCTestCase {
    func testTheShownStatusFollowsThePlansPriority() {
        XCTAssertEqual(ChatStatus.shown(of: [.done, .working, .waitingOnYou]), .waitingOnYou)
        XCTAssertEqual(ChatStatus.shown(of: [.failed, .starting]), .starting)
        XCTAssertEqual(ChatStatus.shown(of: [.done, .failed]), .failed)
        XCTAssertEqual(ChatStatus.shown(of: [.question, .working]), .question)
        XCTAssertNil(ChatStatus.shown(of: []))
    }

    func testOnlyWaitingIsAmberAndOnlyFailureIsRed() {
        XCTAssertEqual(ChatStatus.allCases.filter { $0.tone == .warn }, [.waitingOnYou, .question])
        XCTAssertEqual(ChatStatus.allCases.filter { $0.tone == .failure }, [.failed])
    }

    func testTheComposerIsOneRowAtRestAndSplitsWhenWritingMoreThanALine() {
        XCTAssertEqual(ComposerLayout.of(text: ""), .compact)
        XCTAssertEqual(ComposerLayout.of(text: "Short"), .compact)
        XCTAssertEqual(ComposerLayout.of(text: "two\nlines"), .expanded)
        XCTAssertEqual(ComposerLayout.of(text: String(repeating: "a", count: 33)), .expanded)
    }

    func testAModelsProviderIsTheReferencesPrefixWhenTheListDoesNotHoldIt() {
        XCTAssertEqual(providerOfModel("claude_code:opus", in: []), "claude_code")
        XCTAssertEqual(providerOfModel("default", in: []), "")
    }

    func testAStreamTickFiresOnlyForFollowedVisibleLiveOutputAndAtMostEvery320ms() {
        let start: Date = Date(timeIntervalSinceReferenceDate: 1000)
        func moment(_ offset: TimeInterval, replay: Bool) -> StreamTickGate.Moment {
            StreamTickGate.Moment(
                now: start.addingTimeInterval(offset), ticksEnabled: true, visible: true, following: true, isReplay: replay)
        }
        let first: (fires: Bool, next: StreamTickGate) = StreamTickGate(lastTick: nil).tick(moment(0, replay: false))
        XCTAssertTrue(first.fires)
        XCTAssertFalse(first.next.tick(moment(0.2, replay: false)).fires)
        XCTAssertTrue(first.next.tick(moment(0.33, replay: false)).fires)
        XCTAssertFalse(first.next.tick(moment(1, replay: true)).fires)
        let hidden: StreamTickGate.Moment = StreamTickGate.Moment(
            now: start.addingTimeInterval(2), ticksEnabled: true, visible: false, following: true, isReplay: false)
        XCTAssertFalse(first.next.tick(hidden).fires)
        let off: StreamTickGate.Moment = StreamTickGate.Moment(
            now: start.addingTimeInterval(2), ticksEnabled: false, visible: true, following: true, isReplay: false)
        XCTAssertFalse(first.next.tick(off).fires)
    }

    func testReduceMotionRemovesTheAnimationAndTheShimmerSweepsThenRests() {
        XCTAssertNil(motionAnimation(.enter, reduceMotion: true))
        XCTAssertNotNil(motionAnimation(.enter, reduceMotion: false))
        XCTAssertEqual(MotionToken.adjust.seconds, 0.12)
        XCTAssertEqual(MotionToken.exit.seconds, 0.15)
        XCTAssertEqual(shimmerPhase(at: 0, sweep: 1.35, pause: 1.45), 0)
        XCTAssertEqual(shimmerPhase(at: 0.675, sweep: 1.35, pause: 1.45), 0.5, accuracy: 0.0001)
        XCTAssertEqual(shimmerPhase(at: 2.0, sweep: 1.35, pause: 1.45), 1)
        XCTAssertEqual(shimmerPhase(at: 2.8 + 0.675, sweep: 1.35, pause: 1.45), 0.5, accuracy: 0.0001)
    }

    func testALinkInAnAnswerIsUnderlined() throws {
        let text: AttributedString = try answerText(markdown: "See [the page](https://example.invalid) now.")
        let linked: [AttributedString.Runs.Run] = text.runs.filter { $0.link != nil }
        XCTAssertEqual(linked.count, 1)
        XCTAssertEqual(linked.first?.underlineStyle, .single)
    }

    func testEverySymbolTheAppNamesExists() {
        for context in SymbolContext.allCases {
            for name in context.symbols {
                XCTAssertNotNil(UIImage(systemName: name), "\(context): \(name)")
            }
        }
    }
}
