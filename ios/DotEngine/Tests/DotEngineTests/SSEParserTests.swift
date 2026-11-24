import Foundation
import XCTest

@testable import DotEngine

/// Moved from the old app (`ios/DotTests/SSETests.swift`) with the parser.
final class SSEParserTests: XCTestCase {
    private let stream: String = ": opening\n\nid: 4\nevent: tool_call\ndata: {\"a\":1}\n\n: keepalive\n\nid: 5\nevent: assistant\ndata: one\ndata: two\n\n"

    private func parse(_ chunks: [Data]) -> ([SSEMessage], String?) {
        var parser: SSEParser = SSEParser()
        var all: [SSEMessage] = []
        for chunk in chunks { all += parser.feed(chunk) }
        return (all, parser.lastEventId)
    }

    func testWholeStream() {
        let (messages, last) = parse([Data(stream.utf8)])
        XCTAssertEqual(messages, [
            SSEMessage(event: "tool_call", data: "{\"a\":1}", id: "4"),
            SSEMessage(event: "assistant", data: "one\ntwo", id: "5"),
        ])
        XCTAssertEqual(last, "5")
    }

    func testCommentsAndKeepalivesAreNoMessages() {
        let (messages, _) = parse([Data(": a\n\n: b\n\n".utf8)])
        XCTAssertTrue(messages.isEmpty)
    }

    func testEveryChunkBoundaryGivesTheSameMessages() {
        let bytes: [UInt8] = Array(stream.utf8)
        let whole: [SSEMessage] = parse([Data(bytes)]).0
        for cut in 0...bytes.count {
            XCTAssertEqual(parse([Data(bytes[..<cut]), Data(bytes[cut...])]).0, whole, "cut at \(cut)")
        }
        XCTAssertEqual(parse(bytes.map { Data([$0]) }).0, whole, "one byte at a time")
    }

    func testAMultiByteCharacterCutInTwoIsReadWhole() {
        let bytes: [UInt8] = Array("data: héllo ✓\n\n".utf8)
        for cut in 0...bytes.count {
            XCTAssertEqual(parse([Data(bytes[..<cut]), Data(bytes[cut...])]).0.first?.data, "héllo ✓", "cut at \(cut)")
        }
    }

    func testCarriageReturnLineEndings() {
        let (messages, _) = parse([Data("id: 1\r\ndata: a\r\n\r\ndata: b\r\rdata: c".utf8)])
        XCTAssertEqual(messages.map(\.data), ["a", "b"])  // "c" is not finished
        // A `\r` that ends a chunk waits for a possible `\n` after it.
        XCTAssertEqual(parse([Data("data: x\r".utf8), Data("\n\r\n".utf8)]).0.map(\.data), ["x"])
    }

    func testTheLastIdSurvivesMessagesWithNone() {
        var parser: SSEParser = SSEParser()
        _ = parser.feed(Data("id: 9\ndata: a\n\n".utf8))
        _ = parser.feed(Data("data: delta\n\n".utf8))
        XCTAssertEqual(parser.lastEventId, "9", "a live delta carries no id: the resume point stays the last stored event")
    }

    func testAFieldWithNoColonAndAnUnknownFieldAreHarmless() {
        let (messages, _) = parse([Data("data\n\nretry: 3000\ndata: ok\n\n".utf8)])
        XCTAssertEqual(messages.map(\.data), ["", "ok"], "a data line with no value is an empty message, an unknown field is skipped")
    }
}
