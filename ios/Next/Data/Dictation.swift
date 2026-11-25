import AVFoundation
import Foundation
import Observation
import Speech
import UIKit
import os

/// Moved from the old app (`ios/Dot/Data/Dictation.swift`). Voice dictation for the composer's microphone: it listens, turns the speech into words on the phone itself
/// (`requiresOnDeviceRecognition`), and hands the words heard so far to the box as they come. It asks for the two
/// permissions when the owner first presses the button, never at launch, and every way it can fail ends as a plain
/// line (`DictationState.failed`), never a crash or a silent button.
@MainActor
@Observable
final class Dictation {
    private(set) var state: DictationState = .idle
    /// The words heard so far in this go (a go ends at Stop, a silence the recognizer ends, or a failure).
    private(set) var heard: String = ""
    /// The gate that stopped the last start, to offer Settings when a permission is the reason.
    private(set) var blocked: DictationGate?

    @ObservationIgnored private var engine: AVAudioEngine?
    @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var task: SFSpeechRecognitionTask?
    @ObservationIgnored private var recognizer: SFSpeechRecognizer?
    @ObservationIgnored private var onHeard: ((String) -> Void)?
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "dictation")

    /// Starts listening; `onHeard` is told the words so far, each time they change. A second call while it listens stops it.
    func toggle(onHeard: @escaping (String) -> Void) {
        if state.isActive {
            stop()
            return
        }
        self.onHeard = onHeard
        heard = ""
        blocked = nil
        state = .starting
        Task { await begin() }
    }

    func stop() {
        guard engine != nil || task != nil || state.isActive else { return }
        finish(failure: nil)
    }

    func clearFailure() {
        if case .failed = state { state = .idle }
        blocked = nil
    }

    // MARK: Starting

    private func begin() async {
        let recognizer: SFSpeechRecognizer? = SFSpeechRecognizer(locale: DictationLanguage.current(.standard).locale(app: Locale.current))
        self.recognizer = recognizer
        var gate: DictationGate = currentGate(recognizer)
        if gate == .needsSpeechPermission {
            _ = await Self.askSpeech()
            gate = currentGate(recognizer)
        }
        if gate == .needsMicrophonePermission {
            _ = await Self.askMicrophone()
            gate = currentGate(recognizer)
        }
        guard state == .starting else { return }
        guard gate == .ready, let recognizer else {
            blocked = gate
            fail(gate.failureWords ?? DictationGate.recognizerUnavailable.failureWords ?? String(localized: "Dictation is not available now. Type the message instead.", comment: "Composer: dictation cannot start now"))
            return
        }
        do {
            try listen(with: recognizer)
        } catch DictationError.noMicrophone {
            fail(String(localized: "There is no microphone to listen to here. Type the message instead.", comment: "Composer: dictation found no microphone"))
        } catch {
            logger.error("dictation could not start: \(String(describing: error), privacy: .public)")
            fail(String(localized: "Dictation could not start. Type the message instead.", comment: "Composer: dictation failed to start"))
        }
    }

    private func currentGate(_ recognizer: SFSpeechRecognizer?) -> DictationGate {
        DictationGate.decide(
            speech: Self.speechState(), microphone: Self.microphoneState(),
            recognizerAvailable: recognizer?.isAvailable ?? false, onDevice: recognizer?.supportsOnDeviceRecognition ?? false)
    }

    private static func speechState() -> PermissionState {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: .granted
        case .notDetermined: .undetermined
        case .denied, .restricted: .denied
        @unknown default: .denied
        }
    }

    private static func microphoneState() -> PermissionState {
        switch AVAudioApplication.shared.recordPermission {
        case .granted: .granted
        case .undetermined: .undetermined
        case .denied: .denied
        @unknown default: .denied
        }
    }

    // Nonisolated: the system calls these back on a queue of its own, and a closure formed in a main-actor context would
    // stop the app there with an assertion on the actor (Swift 6).
    private nonisolated static func askSpeech() async -> Bool {
        await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            SFSpeechRecognizer.requestAuthorization { @Sendable status in continuation.resume(returning: status == .authorized) }
        }
    }

    private nonisolated static func askMicrophone() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    // MARK: Listening

    private func listen(with recognizer: SFSpeechRecognizer) throws {
        let session: AVAudioSession = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: .duckOthers)
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let request: SFSpeechAudioBufferRecognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // On this phone only: no audio is sent anywhere.
        request.requiresOnDeviceRecognition = true
        self.request = request

        let engine: AVAudioEngine = AVAudioEngine()
        let input: AVAudioInputNode = engine.inputNode
        let format: AVAudioFormat = input.outputFormat(forBus: 0)
        // No microphone (the simulator has none by default) reports a format with no sample rate.
        guard format.sampleRate > 0, format.channelCount > 0 else { throw DictationError.noMicrophone }
        Self.installTap(on: input, format: format, sink: BufferSink(request))
        engine.prepare()
        try engine.start()
        self.engine = engine

        task = Self.recognize(with: recognizer, request: request) { @Sendable [weak self] words, final, failed in
            Task { @MainActor in self?.took(words: words, final: final, failed: failed) }
        }
        state = .listening
    }

    /// The tap and the recognizer's handler are formed here, outside the main actor, since the audio thread and the
    /// recognizer's queue call them.
    private nonisolated static func installTap(on input: AVAudioInputNode, format: AVAudioFormat, sink: BufferSink) {
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in sink.append(buffer) }
    }

    private nonisolated static func recognize(
        with recognizer: SFSpeechRecognizer, request: SFSpeechAudioBufferRecognitionRequest,
        deliver: @escaping @Sendable (String?, Bool, Bool) -> Void
    ) -> SFSpeechRecognitionTask {
        recognizer.recognitionTask(with: request) { result, error in
            deliver(result?.bestTranscription.formattedString, result?.isFinal ?? false, error != nil)
        }
    }

    private func took(words: String?, final: Bool, failed: Bool) {
        guard state == .listening || state == .starting else { return }
        if let words, words != heard {
            heard = words
            onHeard?(words)
        }
        if final {
            finish(failure: nil)
        } else if failed {
            // The recognizer ends a go with an error when it hears nothing for a while: what was heard stays in the box.
            finish(failure: heard.isEmpty ? String(localized: "Nothing was heard. Try again.", comment: "Composer: dictation heard nothing") : nil)
        }
    }

    private func fail(_ words: String) {
        finish(failure: words)
    }

    /// Ends a go: the tap, the engine and the task go, the audio session is given back, and the state says why.
    private func finish(failure: String?) {
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        request?.endAudio()
        task?.cancel()
        engine = nil
        request = nil
        task = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        state = failure.map { DictationState.failed($0) } ?? .idle
    }

    enum DictationError: Error { case noMicrophone }
}

/// Hands the microphone's buffers to the recognizer from the audio thread, which is not the main actor's.
private final class BufferSink: @unchecked Sendable {
    private let request: SFSpeechAudioBufferRecognitionRequest

    init(_ request: SFSpeechAudioBufferRecognitionRequest) { self.request = request }

    func append(_ buffer: AVAudioPCMBuffer) { request.append(buffer) }
}
