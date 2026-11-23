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
}
