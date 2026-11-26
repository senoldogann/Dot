import Foundation

// The Mac's screen as the daemon sends it (`GET /api/screen/live`, `dotagent/desktop/live.py`): H.264 frames, each a
// 13-byte header (kind, the length after it, its capture time in microseconds; big-endian) and its payload in AVCC form
// (NAL units, each after its 4-byte length). A key frame's payload starts with the decoder's configuration (the `avcC`
// record, after its 2-byte length). The same reading as the web's `livestream.js`. Pure.

/// The kind of a frame of the live stream.
public enum LiveFrameKind: UInt8, Sendable, Equatable {
    case key = 1
    case delta = 2
}

/// One frame of the live stream.
public struct LiveFrame: Sendable, Equatable {
    public let kind: LiveFrameKind
    /// When the Mac captured it, in microseconds of the capture's own clock.
    public let micros: UInt64
    public let payload: Data

    public init(kind: LiveFrameKind, micros: UInt64, payload: Data) {
        self.kind = kind
        self.micros = micros
        self.payload = payload
    }
}

/// A frame the stream sent that is not of a kind it knows: the stream is not the daemon's, or is broken.
public struct LiveStreamBroken: Error, Equatable, Sendable {
    public let kind: UInt8
    public let length: UInt32
}

let liveHeaderBytes: Int = 13

/// The frames whole in `bytes`, and what is left over (the start of a frame still coming). Throws on a kind the stream
/// does not have, so a broken stream ends rather than being read as garbage.
public func splitLiveFrames(_ bytes: Data) throws(LiveStreamBroken) -> (frames: [LiveFrame], rest: Data) {
    var frames: [LiveFrame] = []
    var at: Int = bytes.startIndex
    while bytes.endIndex - at >= liveHeaderBytes {
        let kindByte: UInt8 = bytes[at]
        let length: UInt32 = bigEndian(bytes, at: at + 1, count: 4)
        guard let kind: LiveFrameKind = LiveFrameKind(rawValue: kindByte) else { throw LiveStreamBroken(kind: kindByte, length: length) }
        let start: Int = at + liveHeaderBytes
        guard bytes.endIndex - start >= Int(length) else { break }
        let micros: UInt64 = bigEndian(bytes, at: at + 5, count: 8)
        frames.append(LiveFrame(kind: kind, micros: micros, payload: Data(bytes[start..<start + Int(length)])))
        at = start + Int(length)
    }
    return (frames, Data(bytes[at...]))
}

/// The decoder's configuration and the frame of a key frame's payload; `nil` when the payload is shorter than it says.
public func splitKeyPayload(_ payload: Data) -> (avcc: Data, frame: Data)? {
    guard payload.count >= 2 else { return nil }
    let start: Int = payload.startIndex
    let length: Int = Int(bigEndian(payload, at: start, count: 2) as UInt16)
    guard payload.count >= 2 + length else { return nil }
    return (Data(payload[start + 2..<start + 2 + length]), Data(payload[(start + 2 + length)...]))
}

/// The parameter sets of an `avcC` record (ISO/IEC 14496-15 §5.2.4.1): the SPS and PPS a decoder is made from, and the
/// size of the length before each NAL unit of the frames.
public struct AVCConfiguration: Sendable, Equatable {
    public let sequenceSets: [Data]
    public let pictureSets: [Data]
    public let nalLengthSize: Int

    public init(sequenceSets: [Data], pictureSets: [Data], nalLengthSize: Int) {
        self.sequenceSets = sequenceSets
        self.pictureSets = pictureSets
        self.nalLengthSize = nalLengthSize
    }
}

/// The `avcC` record read; `nil` when it is cut short or names no parameter sets.
public func avcConfiguration(_ record: Data) -> AVCConfiguration? {
    let bytes: [UInt8] = Array(record)
    guard bytes.count >= 7, bytes[0] == 1 else { return nil }
    let nalLengthSize: Int = Int(bytes[4] & 0x03) + 1
    // The SPS count's byte is the sixth; the sets follow it.
    var at: Int = 6
    func sets(count: Int) -> [Data]? {
        var found: [Data] = []
        for _ in 0..<count {
            guard at + 2 <= bytes.count else { return nil }
            let length: Int = Int(bytes[at]) << 8 | Int(bytes[at + 1])
            at += 2
            guard at + length <= bytes.count else { return nil }
            found.append(Data(bytes[at..<at + length]))
            at += length
        }
        return found
    }
    let sequenceCount: Int = Int(bytes[5] & 0x1F)
    guard let sequence: [Data] = sets(count: sequenceCount), at < bytes.count else { return nil }
    let pictureCount: Int = Int(bytes[at])
    at += 1
    guard let picture: [Data] = sets(count: pictureCount), !sequence.isEmpty, !picture.isEmpty else { return nil }
    return AVCConfiguration(sequenceSets: sequence, pictureSets: picture, nalLengthSize: nalLengthSize)
}

private func bigEndian<T: FixedWidthInteger>(_ bytes: Data, at: Int, count: Int) -> T {
    var value: T = 0
    for index in at..<at + count { value = value << 8 | T(bytes[index]) }
    return value
}
