import Foundation
import XCTest

@testable import DotEngine

/// The Mac's dictation (PLAN §25, Settings › Voice › Transcribe on the Mac): a stream's pieces, its end and its drop.
final class VoiceStreamTests: XCTestCase {
    private var daemon: FakeDaemon!

    override func setUp() async throws { daemon = FakeDaemon(version: "1.0") }

    func testPiecesAreHalfSecondsAndTheEndSendsTheEvenRest() {
        let three: Data = Data(repeating: 1, count: 2 * voicePieceBytes + 7)
        let (pieces, rest): ([Data], Data) = voicePieces(three, size: voicePieceBytes, flush: false)
        XCTAssertEqual(pieces.map(\.count), [voicePieceBytes, voicePieceBytes])
        XCTAssertEqual(rest.count, 7, "kept for the next piece")
        let (last, none): ([Data], Data) = voicePieces(rest, size: voicePieceBytes, flush: true)
        XCTAssertEqual(last.map(\.count), [6], "a half sample is never sent")
        XCTAssertTrue(none.isEmpty)
        XCTAssertEqual(voicePieces(Data([1]), size: voicePieceBytes, flush: true).pieces, [], "no sound, no piece")
    }

    func testAStreamIsOpenedFedInOrderAndEnded() async throws {
        let api: DaemonAPI = daemon.api()
        let ability: VoiceAbility = try await api.voiceAbility()
        XCTAssertTrue(ability.transcribe)
        let stream: VoiceStream = try await api.openVoiceStream(language: "tr-TR")
        try await api.feedVoice(stream: stream.id, piece: 0, samples: Data(repeating: 0, count: 100))
        try await api.feedVoice(stream: stream.id, piece: 1, samples: Data(repeating: 0, count: 50))
        let words: VoiceTranscript = try await api.endVoiceStream(stream.id)
        XCTAssertEqual(words.text, "heard 150 bytes")
        let opened: [String: JSONValue] = try JSONDecoder().decode([String: JSONValue].self, from: try XCTUnwrap(daemon.bodies("POST", "/api/voice/streams").first))
        XCTAssertEqual(opened["language"]?.string, "tr-TR")
    }

    func testACancelledStreamIsDropped() async throws {
        let api: DaemonAPI = daemon.api()
        let stream: VoiceStream = try await api.openVoiceStream(language: "en-US")
        XCTAssertEqual(daemon.openVoiceStreams, 1)
        try await api.dropVoiceStream(stream.id)
        XCTAssertEqual(daemon.openVoiceStreams, 0)
    }
}
