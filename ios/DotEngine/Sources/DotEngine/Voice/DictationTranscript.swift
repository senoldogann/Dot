import Foundation

/// What one dictation heard so far (item 2048): the utterances the recognizer closed, in order, and the one it is still
/// writing. A pause never takes words away: the recognizer's words for the live utterance only replace that utterance,
/// and a closed one is never changed again.
///
/// Why it is needed: since iOS 18 (and on macOS 26, measured), Apple's `SFSpeechRecognizer` closes an utterance when
/// the speaker pauses, inside the same task and with no final result: the closing result carries
/// `speechRecognitionMetadata`, and the next results hold only the words said after the pause. Taking each result as the
/// whole text replaced everything said before the pause.
public struct DictationTranscript: Equatable, Sendable {
    /// The utterances the recognizer closed, oldest first.
    public let closed: [String]
    /// The words of the utterance being written now ("" between two).
    public let live: String

    public init(closed: [String], live: String) {
        self.closed = closed
        self.live = live
    }

    public static let empty: DictationTranscript = DictationTranscript(closed: [], live: "")

    /// Everything heard, one space apart.
    public var text: String {
        (closed + [live]).filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// The transcript after a result of the recognizer: `words` are its words for the live utterance (an empty result
    /// keeps what it had), and `closesUtterance` says the result ends that utterance (a final result, or one with
    /// metadata), so the next words start a new one.
    public func taking(words: String, closesUtterance: Bool) -> DictationTranscript {
        let said: String = words.trimmingCharacters(in: .whitespacesAndNewlines)
        let next: DictationTranscript = said.isEmpty ? self : DictationTranscript(closed: closed, live: said)
        return closesUtterance ? next.closingLive() : next
    }

    /// The live utterance kept as closed: its recognition task ended (an error, a new microphone) or the dictation is
    /// over.
    public func closingLive() -> DictationTranscript {
        live.isEmpty ? self : DictationTranscript(closed: closed + [live], live: "")
    }
}
