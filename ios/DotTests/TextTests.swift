import SwiftUI
import UIKit
import XCTest
@testable import Dot

final class LinkStyleTests: XCTestCase {
    func testABareAddressBecomesAPlainLink() throws {
        let styled: AttributedString = LinkStyle.styled("See https://example.com/report for it")
        let link: AttributedString.Runs.Run = try XCTUnwrap(styled.runs.first { $0.link != nil })
        XCTAssertEqual(link.link, URL(string: "https://example.com/report"))
        XCTAssertNil(link.underlineStyle, "no underline")
        XCTAssertNil(link.backgroundColor, "no background")
        XCTAssertEqual(link.foregroundColor, Theme.link)
    }

    func testAMarkdownLinkKeepsItsWordsAndIsStyledAlike() throws {
        let styled: AttributedString = LinkStyle.styled("Read [the docs](https://example.com/docs) now")
        let link: AttributedString.Runs.Run = try XCTUnwrap(styled.runs.first { $0.link != nil })
        XCTAssertEqual(String(styled[link.range].characters), "the docs")
        XCTAssertNil(link.underlineStyle)
        XCTAssertNil(link.backgroundColor)
        XCTAssertEqual(link.foregroundColor, Theme.link)
    }

    func testTextRoundTheLinkIsLeftAlone() {
        let styled: AttributedString = LinkStyle.styled("before https://example.com after")
        let others: [AttributedString.Runs.Run] = styled.runs.filter { $0.link == nil }
        XCTAssertFalse(others.isEmpty)
        XCTAssertTrue(others.allSatisfy { $0.foregroundColor == nil && $0.backgroundColor == nil && $0.underlineStyle == nil })
    }

    func testTheLinkIsAShadeBrighterThanTheTextAndTiltedToTheAccentInTheDark() {
        // The theme's colours follow the appearance: read the link as the dark look draws it.
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        UIColor(Theme.link).resolvedColor(with: UITraitCollection(userInterfaceStyle: .dark)).getRed(&r, green: &g, blue: &b, alpha: &a)
        XCTAssertGreaterThan(max(r, g, b), CGFloat(Theme.textLevel), "its brightest channel is above the text's")
        XCTAssertGreaterThan(b, r, "it leans to the blue of the accent")
        XCTAssertNotEqual(Theme.link, Theme.text)
    }

    func testPlainWordsHaveNoLink() {
        XCTAssertNil(LinkStyle.styled("no address in here, just words").runs.first { $0.link != nil })
    }

    func testInlineCodeKeepsItsMonospace() {
        let styled: AttributedString = LinkStyle.styled("run `swift test` now")
        XCTAssertNotNil(styled.runs.first { $0.inlinePresentationIntent?.contains(.code) == true })
    }
}

final class MessageBlocksTests: XCTestCase {
    func testFencedCodeIsItsOwnBlock() {
        XCTAssertEqual(
            MessageBlocks.parse("Before\n\n```\nls -l\n```\n\nAfter"),
            [.prose("Before"), .code("ls -l"), .prose("After")])
    }

    func testAnUnclosedFenceIsCodeToTheEnd() {
        XCTAssertEqual(MessageBlocks.parse("Run:\n```\nls"), [.prose("Run:"), .code("ls")])
    }

    func testListMarksAndHeadingsBecomePlainLines() {
        XCTAssertEqual(MessageBlocks.parse("## Title\n- one\n* two"), [.prose("**Title**\n• one\n• two")])
    }
}

final class AddressReuseTests: XCTestCase {
    func testTheNativeAppKeepsTheShellsAddressRules() throws {
        let host: DaemonHost = try parseDaemonHost("https://Mac.Example.ts.net/", buildHost: nil).get()
        XCTAssertEqual(host.name, "mac.example.ts.net")
        XCTAssertEqual(host.url(path: "/api/threads").absoluteString, "https://mac.example.ts.net/api/threads")
        XCTAssertThrowsError(try parseDaemonHost("http://mac.example.ts.net", buildHost: nil).get())
        XCTAssertThrowsError(try parseDaemonHost("example.com", buildHost: nil).get())
    }

    func testRecordsReadAsTheDaemonWritesThem() throws {
        let json: String = """
            {"id":"thr_1","title":"T","source":"ui","created_at":1,"updated_at":2,"pinned_at":null,"run_count":1,
             "latest_run_id":"run_1","latest_status":"blocked","latest_mode":"task","latest_created_at":1,"unread":false,
             "actor_id":null,"last_message":"hi","last_role":"user","loop_cron":null,"loop_next_at":null,"team":null,"campaign_id":null}
            """
        let thread: ThreadSummary = try JSONDecoder().decode(ThreadSummary.self, from: Data(json.utf8))
        XCTAssertTrue(thread.isWaitingOnYou)
        let ev: EventRecord = try JSONDecoder().decode(
            EventRecord.self, from: Data(#"{"id":3,"run_id":"run_1","ts":1.5,"kind":"tool_call","data":{"call_id":"c","args":{"n":[1,2.5,null,true]}}}"#.utf8))
        XCTAssertEqual(ev.data["call_id"]?.string, "c", "keys are kept as the daemon writes them")
        XCTAssertEqual(ev.data["args"]?["n"]?.array?.count, 4)
    }
}
