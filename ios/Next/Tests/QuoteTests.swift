import XCTest

@testable import DotNext

/// What a quote does to a message's words (item 1972): the same rules as the web's `quoteInto`, so that a quote sent from
/// either shows the same in both.
final class QuoteTests: XCTestCase {
    func testAQuoteStartsTheSentWordsWithEachLineBehindAnAngleBracket() {
        XCTAssertEqual(quotedWords("and you?", quote: "One\nTwo"), "> One\n> Two\n\nand you?")
        XCTAssertEqual(quotedWords("plain", quote: nil), "plain")
    }

    func testASentMessageSplitsBackIntoItsQuoteAndItsWords() {
        let (quote, rest): (String?, String) = splitQuote("> One\n> Two\n\nand you?")
        XCTAssertEqual(quote, "One\nTwo")
        XCTAssertEqual(rest, "and you?")
        let (none, whole): (String?, String) = splitQuote("no quote > here")
        XCTAssertNil(none)
        XCTAssertEqual(whole, "no quote > here")
    }

    func testAQuoteKeepsTheFirstWordsFoldsBlankRunsAndIsCutWithAnEllipsis() {
        XCTAssertEqual(quoteOf("a\n\n\n\nb"), "a\n\nb")
        XCTAssertNil(quoteOf("  \n "))
        let cut: String? = quoteOf(String(repeating: "x", count: 400))
        XCTAssertEqual(cut?.count, quoteLimit)
        XCTAssertEqual(cut?.last, "…")
        XCTAssertEqual(quoteChipText("  First   line here\nsecond"), "First line here")
    }

    func testASwipePullsOnlyFromAwayFromTheEdgeAndMoreAcrossThanDown() {
        XCTAssertEqual(swipePull(startX: 10, width: 160, height: 0), 0, "the screen's edge is the back gesture's")
        XCTAssertEqual(swipePull(startX: 100, width: 40, height: 60), 0, "a scroll is not a swipe")
        XCTAssertEqual(swipePull(startX: 100, width: -50, height: 0), 0, "only to the right")
        XCTAssertLessThan(swipePull(startX: 100, width: 30, height: 0), swipeThreshold)
        XCTAssertGreaterThanOrEqual(swipePull(startX: 100, width: 160, height: 10), swipeThreshold)
        XCTAssertLessThan(swipePull(startX: 100, width: 2000, height: 0), swipeLimit, "a rubber band never passes its limit")
    }
}
