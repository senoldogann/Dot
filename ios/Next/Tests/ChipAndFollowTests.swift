import XCTest

@testable import DotNext

/// The pure rules behind the composer's expanded model chip (item 1991) and the chat's follow of new words.
final class ChipAndFollowTests: XCTestCase {
    func testAModelNameLosesAProviderNameInFrontOfIt() {
        XCTAssertEqual(modelNameWithoutProvider("Anthropic · Opus 5.5", provider: "anthropic"), "Opus 5.5")
        XCTAssertEqual(modelNameWithoutProvider("Claude Code: Opus", provider: "claude_code"), "Opus")
        XCTAssertEqual(modelNameWithoutProvider("DeepSeek V4.1 Flash", provider: "opencode_go"), "DeepSeek V4.1 Flash")
        XCTAssertEqual(modelNameWithoutProvider("Opus 5.5", provider: "anthropic"), "Opus 5.5")
        XCTAssertEqual(modelNameWithoutProvider("Anthropic", provider: "anthropic"), "Anthropic", "a label that is only the provider is kept")
        XCTAssertEqual(modelNameWithoutProvider("Anthropicus 3", provider: "anthropic"), "Anthropicus 3", "a longer word is not the provider")
        XCTAssertEqual(modelNameWithoutProvider("Mistral Large", provider: "mistral"), "Mistral Large", "a model named like its provider keeps its name")
        XCTAssertEqual(modelNameWithoutProvider("Mistral-Large", provider: "mistral"), "Mistral-Large", "a bare hyphen inside a name is no separator")
        XCTAssertEqual(modelNameWithoutProvider("Mistral–Large", provider: "mistral"), "Mistral–Large", "nor is a bare dash")
        XCTAssertEqual(modelNameWithoutProvider("Anthropic: Opus 5.5", provider: "anthropic"), "Opus 5.5")
        XCTAssertEqual(modelNameWithoutProvider("Anthropic - Opus 5.5", provider: "anthropic"), "Opus 5.5", "a dash with a space on both sides separates")
        XCTAssertEqual(modelNameWithoutProvider("Anthropic/Opus 5.5", provider: "anthropic"), "Opus 5.5")
    }

    func testTheChipsGreyWordsAreTheLevelAndFast() {
        // Said in the language the app runs in: the level's own word, then "Fast".
        let fast: String = String(localized: "Fast", comment: "The composer's model chip: fast mode is on")
        XCTAssertEqual(chipDetail(effort: "medium", fast: true), effortTitle("medium") + " " + fast)
        XCTAssertEqual(chipDetail(effort: "high", fast: false), effortTitle("high"))
        XCTAssertEqual(chipDetail(effort: nil, fast: true), fast)
        XCTAssertNil(chipDetail(effort: nil, fast: false))
    }

    func testTheFollowStopsOnTheOwnersScrollAwayFromTheEndEvenWhenTheEndMovedFirst() {
        XCTAssertTrue(followsAfterScroll(following: false, atEnd: true, ownerScrolls: false), "back at the end it follows again")
        XCTAssertTrue(followsAfterScroll(following: true, atEnd: false, ownerScrolls: false), "a height that moved by itself stops nothing")
        XCTAssertFalse(followsAfterScroll(following: true, atEnd: false, ownerScrolls: true), "the owner's scroll stops it")
        XCTAssertFalse(followsAfterScroll(following: false, atEnd: false, ownerScrolls: false), "away from the end it stays stopped")
    }
}
