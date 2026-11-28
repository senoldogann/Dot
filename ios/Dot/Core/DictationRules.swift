import Foundation

// Voice dictation on the composer's microphone: Apple's Speech framework, on this phone only (no audio leaves it). These are
// its rules, pure: what each permission and the recognizer allow, the words of each way it can fail, and how the words
// heard join what the owner had already typed. `Dictation` (Data/) does the listening.

/// A permission as dictation needs to know it.
enum PermissionState: Equatable, Sendable {
    case undetermined
    case granted
    case denied
}

/// Whether dictation may start, or what stands in the way.
enum DictationGate: Equatable, Sendable {
    case ready
    /// Asking for the speech permission first (the system's own prompt).
    case needsSpeechPermission
    case needsMicrophonePermission
    case speechDenied
    case microphoneDenied
    /// No recognizer for the phone's language, or it is not available now (the simulator, a model not downloaded yet).
    case recognizerUnavailable
    /// The language has a recognizer but not one that runs on the phone; a recognizer that sends audio away is not used.
    case notOnDevice

    static func decide(
        speech: PermissionState, microphone: PermissionState, recognizerAvailable: Bool, onDevice: Bool
    ) -> DictationGate {
        if speech == .denied { return .speechDenied }
        if microphone == .denied { return .microphoneDenied }
        if speech == .undetermined { return .needsSpeechPermission }
        if microphone == .undetermined { return .needsMicrophonePermission }
        if !recognizerAvailable { return .recognizerUnavailable }
        if !onDevice { return .notOnDevice }
        return .ready
    }

    /// What the composer says when dictation cannot start, or `nil` when it can (or only a prompt is next).
    var failureWords: String? {
        switch self {
        case .ready, .needsSpeechPermission, .needsMicrophonePermission: nil
        case .speechDenied: "Speech recognition is off for Dot. Turn it on in Settings › Dot › Speech Recognition."
        case .microphoneDenied: "The microphone is off for Dot. Turn it on in Settings › Dot › Microphone."
        case .recognizerUnavailable: "Dictation is not available now. Type the message instead."
        case .notOnDevice: "Dictation on this phone is not available for your language. Type the message instead."
        }
    }

    /// Whether the way to fix it is the app's own page in Settings.
    var opensSettings: Bool { self == .speechDenied || self == .microphoneDenied }
}

/// Where dictation stands, for the button and the line over the box.
enum DictationState: Equatable, Sendable {
    case idle
    case starting
    case listening
    case failed(String)

    var isActive: Bool { self == .starting || self == .listening }
}

enum DictationText {
    /// What the box holds while the owner speaks: what they had typed, then the words heard so far, one space apart.
    static func merged(base: String, heard: String) -> String {
        let spoken: String = heard.trimmingCharacters(in: .whitespacesAndNewlines)
        if spoken.isEmpty { return base }
        let typed: String = base.trimmingCharacters(in: .whitespaces)
        if typed.isEmpty { return spoken }
        // What the owner typed keeps its own line breaks; only a trailing space is not doubled.
        let separator: String = base.hasSuffix("\n") ? "" : " "
        return base.trimmingCharacters(in: CharacterSet(charactersIn: " ")) + separator + spoken
    }

    /// What the box should hold now, or `nil` when the owner changed it since dictation last wrote to it (what it wrote, or
    /// what the box held when it began): dictation then stops and keeps the owner's typing, it never writes over it.
    static func update(current: String, lastWritten: String?, base: String, heard: String) -> String? {
        guard current == (lastWritten ?? base) else { return nil }
        return merged(base: base, heard: heard)
    }
}

/// What one go of dictation heard so far (item 2050, DotNext's `DictationTranscript` of item 2048, here as the old app
/// does not link DotEngine): the utterances the recognizer closed, in order, and the one it is still writing. Since iOS
/// 18 the recognizer closes an utterance when the speaker pauses, inside the same task and with no final result (the
/// closing result carries `speechRecognitionMetadata`), and the next results hold only the words said after the pause:
/// a result replaces only the live utterance, so a pause never takes words away.
struct DictationTranscript: Equatable, Sendable {
    /// The utterances the recognizer closed, oldest first.
    let closed: [String]
    /// The words of the utterance being written now ("" between two).
    let live: String

    static let empty: DictationTranscript = DictationTranscript(closed: [], live: "")

    /// Everything heard, one space apart.
    var text: String {
        (closed + [live]).filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// The transcript after a result: `words` replace the live utterance (an empty result keeps it), and
    /// `closesUtterance` (a final result, or one with metadata) closes it, so the next words start a new one.
    func taking(words: String, closesUtterance: Bool) -> DictationTranscript {
        let said: String = words.trimmingCharacters(in: .whitespacesAndNewlines)
        let next: DictationTranscript = said.isEmpty ? self : DictationTranscript(closed: closed, live: said)
        guard closesUtterance, !next.live.isEmpty else { return next }
        return DictationTranscript(closed: next.closed + [next.live], live: "")
    }
}

/// What a go of dictation last wrote to the box.
final class DictationWritten {
    var text: String?
}
