import Foundation

// The Mac's dictation (`routes_voice.py`, items 626 and 969): the phone records, streams the sound to the Mac in
// numbered pieces of 16 kHz mono 16-bit samples, and the Mac's own on-device model writes it down at the tick. Settings ›
// Voice › "Transcribe on" picks it over this phone's recognizer (PLAN §25).

/// `GET /api/voice`: whether the Mac can write a recording down, and why not in its words.
public struct VoiceAbility: Codable, Sendable, Hashable {
    public let transcribe: Bool
    public let why: String?

    public init(transcribe: Bool, why: String?) {
        self.transcribe = transcribe
        self.why = why
    }
}

/// The name of a stream the Mac opened.
public struct VoiceStream: Codable, Sendable, Hashable {
    public let id: String

    public init(id: String) { self.id = id }
}

/// The words a stream was written down as; empty when nothing was heard.
public struct VoiceTranscript: Codable, Sendable, Hashable {
    public let text: String

    public init(text: String) { self.text = text }
}

private struct VoiceOpenBody: Encodable { let language: String }

private struct VoicePieceBody: Encodable {
    let piece: Int
    let dataB64: String

    enum CodingKeys: String, CodingKey {
        case piece
        case dataB64 = "data_b64"
    }
}

/// The sound a piece may carry at most: the daemon's `MAX_PIECE_BYTES` is larger; half a second (16 000 bytes) is
/// what the phone sends, so a piece is never refused for its size.
public let voicePieceBytes: Int = 16_000

/// Splits the samples gathered so far into the pieces to send now and the rest to keep for the next ones (pure). A
/// piece is `size` bytes; at the end (`flush`) the rest goes too, when it holds any sound. 16-bit samples: the size and
/// the rest stay even.
public func voicePieces(_ pending: Data, size: Int, flush: Bool) -> (pieces: [Data], rest: Data) {
    var pieces: [Data] = []
    var start: Int = pending.startIndex
    while pending.endIndex - start >= size {
        pieces.append(pending.subdata(in: start..<(start + size)))
        start += size
    }
    let rest: Data = pending.subdata(in: start..<pending.endIndex)
    if flush {
        let even: Int = rest.count - rest.count % 2
        if even > 0 { pieces.append(rest.prefix(even)) }
        return (pieces, Data())
    }
    return (pieces, rest)
}

extension DaemonAPI {
    public func voiceAbility() async throws(DaemonRequestError) -> VoiceAbility { try await get("/api/voice", query: []) }

    /// Opens a stream in `language` (a BCP 47 tag, `tr-TR`); the Mac starts its program while the microphone opens.
    public func openVoiceStream(language: String) async throws(DaemonRequestError) -> VoiceStream {
        try await send("POST", "/api/voice/streams", body: Self.json(VoiceOpenBody(language: language)))
    }

    /// The stream's next piece of sound, numbered from 0 in the order it was recorded.
    public func feedVoice(stream: String, piece: Int, samples: Data) async throws(DaemonRequestError) {
        try await sendVoid(
            "POST", "/api/voice/streams/\(stream)/audio", body: Self.json(VoicePieceBody(piece: piece, dataB64: samples.base64EncodedString())))
    }

    /// The owner's tick: the words of the stream.
    public func endVoiceStream(_ stream: String) async throws(DaemonRequestError) -> VoiceTranscript {
        try await send("POST", "/api/voice/streams/\(stream)/end", body: nil)
    }

    /// The owner's cross: nothing is written down.
    public func dropVoiceStream(_ stream: String) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/voice/streams/\(stream)", body: nil)
    }
}
