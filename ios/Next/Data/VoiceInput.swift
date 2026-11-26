import AVFoundation
import DotEngine
import Foundation
import Observation
import Speech
import os

/// Voice typing in the composer (PLAN §24, owner-refs/ref-voice-recording.png): the microphone turns the composer into a
/// recorder (✕, a live waveform, a running clock, ✓); ✓ writes the recording down and hands the words to the field.
/// Where it is written down is Settings › Voice: this phone's own recognizer (Speech, on the device, Turkish or English
/// by the dictation language) or the paired Mac's (`/api/voice/streams`, the web's way since item 626).
///
/// What makes it hold up on a real phone: each go has a fresh audio engine, recognizer and request, and gives the audio
/// session back at its end (the web's recognizer went deaf after one go; this one starts clean each time); a long
/// dictation goes on across the recognizer's own pauses (a segment that ends is kept and a new one begins); a call or
/// Siri (an interruption), the app leaving the screen or the Mac's five minutes end the go with the words heard so far,
/// never losing them; a microphone plugged in or out mid-go (the engine's configuration change) is taken up again.
@MainActor
@Observable
final class VoiceInput {
    enum Phase: Equatable {
        case idle
        case starting
        case recording
        /// ✓ was pressed: the words are being written down.
        case transcribing
        case failed(String)

        var isActive: Bool { self == .starting || self == .recording || self == .transcribing }
    }

    private(set) var phase: Phase = .idle
    /// The newest levels of the microphone, 0 to 1, oldest first (the waveform).
    private(set) var levels: [Float] = []
    private(set) var startedAt: Date?
    /// The words this phone's recognizer heard so far (nothing for the Mac's, which writes down at the end).
    private(set) var heard: String = ""
    /// The gate that stopped the last start, to offer Settings when a permission is the reason.
    private(set) var blocked: DictationGate?
    /// Why the last go ended before ✓ (a call took the microphone), said once over the composer.
    private(set) var notice: String?

    static let levelCount: Int = 48

    @ObservationIgnored private var engine: AVAudioEngine?
    @ObservationIgnored private var tap: VoiceTap?
    @ObservationIgnored private var recognizer: SFSpeechRecognizer?
    @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var task: SFSpeechRecognitionTask?
    /// The segments the recognizer finished in this go, and the one it is writing.
    @ObservationIgnored private var segments: [String] = []
    @ObservationIgnored private var current: String = ""
    @ObservationIgnored private var feed: MacVoiceFeed?
    @ObservationIgnored private var deliver: ((String) -> Void)?
    @ObservationIgnored private var learned: ((MicPort) -> Void)?
    @ObservationIgnored private var finalWait: CheckedContinuation<Void, Never>?
    @ObservationIgnored private var observers: [NSObjectProtocol] = []
    @ObservationIgnored private var limit: Task<Void, Never>?
    /// Segments begun again after the recognizer heard no speech, with no word since: past `quietRestarts` the go ends.
    @ObservationIgnored private var quietRuns: Int = 0
    static let quietRestarts: Int = 3
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "voice")

    /// What a go needs: where it is written down (the Mac's API for `.mac`), its language, the preferred order of the
    /// microphones; `deliver` takes the words at the end, `learned` the microphone it used (Settings › Voice names it).
    struct Start {
        let on: TranscribeOn
        let mac: DaemonAPI?
        let locale: Locale
        let order: [MicSlot]
        let deliver: (String) -> Void
        let learned: (MicPort) -> Void
    }

    func start(_ start: Start) {
        guard !phase.isActive else { return }
        deliver = start.deliver
        learned = start.learned
        heard = ""
        segments = []
        current = ""
        levels = []
        blocked = nil
        notice = nil
        phase = .starting
        Task { await begin(start) }
    }

    /// ✓: the recording is written down and handed to the field.
    func confirm() {
        guard phase == .recording || phase == .starting else { return }
        Task { await finish(keep: true, why: nil) }
    }

    /// ✕: nothing is written down.
    func cancel() {
        guard phase == .starting || phase == .recording else { return }
        Task { await finish(keep: false, why: nil) }
    }

    /// The app left the screen: the go ends with what was heard.
    func leftTheScreen() {
        guard phase == .recording || phase == .starting else { return }
        Task { await finish(keep: true, why: String(localized: "Recording stopped when Dot left the screen; what was heard is in the box.", comment: "Composer: voice stopped as the app went to the background")) }
    }

    func clearFailure() {
        if case .failed = phase { phase = .idle }
        notice = nil
        blocked = nil
    }

    // MARK: Starting

    private func begin(_ start: Start) async {
        let onDevice: Bool = start.on == .device
        let recognizer: SFSpeechRecognizer? = onDevice ? SFSpeechRecognizer(locale: start.locale) : nil
        var gate: DictationGate = currentGate(recognizer, onDevice: onDevice)
        if gate == .needsSpeechPermission {
            _ = await Self.askSpeech()
            gate = currentGate(recognizer, onDevice: onDevice)
        }
        if gate == .needsMicrophonePermission {
            _ = await AVAudioApplication.requestRecordPermission()
            gate = currentGate(recognizer, onDevice: onDevice)
        }
        guard phase == .starting else { return }
        guard gate == .ready else {
            blocked = gate
            fail(gate.failureWords ?? DictationGate.recognizerUnavailable.failureWords ?? "")
            return
        }
        if !onDevice {
            guard let api = start.mac else {
                fail(String(localized: "Dot is not paired with a Mac. Choose This device in Settings › Voice.", comment: "Composer: voice set to the Mac without a paired Mac"))
                return
            }
            // A stream that cannot be opened or fed ends the go at once, said with the Mac's words (not only at ✓).
            feed = MacVoiceFeed(api: api, language: languageTag(start.locale)) { @Sendable [weak self] in
                Task { @MainActor in await self?.finish(keep: true, why: nil) }
            }
        }
        self.recognizer = recognizer
        do {
            try openSession(order: start.order)
            try openEngine()
            if let recognizer { beginSegment(recognizer) }
        } catch VoiceError.noMicrophone {
            fail(String(localized: "There is no microphone to listen to here. Type the message instead.", comment: "Composer: dictation found no microphone"))
            return
        } catch {
            logger.error("voice could not start error=\(String(describing: error), privacy: .public)")
            fail(String(localized: "Dictation could not start. Type the message instead.", comment: "Composer: dictation failed to start"))
            return
        }
        observe()
        startedAt = Date()
        phase = .recording
        if !onDevice {
            limit = Task { [weak self] in
                try? await Task.sleep(for: .seconds(macRecordingLimit))
                guard !Task.isCancelled else { return }
                await self?.finish(keep: true, why: String(localized: "The Mac writes down five minutes at most; what was said is in the box.", comment: "Composer: the Mac's recording limit was reached"))
            }
        }
    }

    private func currentGate(_ recognizer: SFSpeechRecognizer?, onDevice: Bool) -> DictationGate {
        let microphone: PermissionState =
            switch AVAudioApplication.shared.recordPermission {
            case .granted: .granted
            case .undetermined: .undetermined
            default: .denied
            }
        guard onDevice else {
            return DictationGate.decide(speech: .granted, microphone: microphone, recognizerAvailable: true, onDevice: true)
        }
        let speech: PermissionState =
            switch SFSpeechRecognizer.authorizationStatus() {
            case .authorized: .granted
            case .notDetermined: .undetermined
            default: .denied
            }
        return DictationGate.decide(
            speech: speech, microphone: microphone, recognizerAvailable: recognizer?.isAvailable ?? false, onDevice: recognizer?.supportsOnDeviceRecognition ?? false)
    }

    private nonisolated static func askSpeech() async -> Bool {
        await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            SFSpeechRecognizer.requestAuthorization { @Sendable status in continuation.resume(returning: status == .authorized) }
        }
    }

    /// The session: recording, Bluetooth headsets allowed, other audio ducked; the first connected microphone of the
    /// owner's order preferred, and learned by name when it is a wired or Bluetooth one.
    private func openSession(order: [MicSlot]) throws {
        let session: AVAudioSession = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .measurement, options: [.duckOthers, .allowBluetoothHFP, .defaultToSpeaker])
        let ports: [AVAudioSessionPortDescription] = session.availableInputs ?? []
        if let wanted: MicPort = preferredMic(ports.compactMap(Self.port), order: order), let port = ports.first(where: { $0.uid == wanted.uid }) {
            try session.setPreferredInput(port)
        }
        try session.setActive(true, options: .notifyOthersOnDeactivation)
        if let used = session.currentRoute.inputs.first.flatMap(Self.port) { learned?(used) }
    }

    static func port(_ description: AVAudioSessionPortDescription) -> MicPort? {
        let kind: MicKind? =
            switch description.portType {
            case .builtInMic: .builtIn
            case .headsetMic, .usbAudio, .lineIn: .wired
            case .bluetoothHFP, .bluetoothLE: .bluetooth
            case .carAudio: .carPlay
            default: nil
            }
        return kind.map { MicPort(uid: description.uid, name: description.portName, kind: $0) }
    }

    /// A fresh engine with the tap on the microphone: levels for the waveform, the buffers for the recognizer or the
    /// Mac's 16 kHz pieces.
    private func openEngine() throws {
        let engine: AVAudioEngine = AVAudioEngine()
        let input: AVAudioInputNode = engine.inputNode
        let format: AVAudioFormat = input.outputFormat(forBus: 0)
        // No microphone (the simulator without one) reports a format with no sample rate.
        guard format.sampleRate > 0, format.channelCount > 0 else { throw VoiceError.noMicrophone }
        let tap: VoiceTap = VoiceTap(
            format: format, request: request, feed: feed,
            level: { @Sendable [weak self] level in Task { @MainActor in self?.took(level: level) } })
        self.tap = tap
        Self.install(tap, on: input, format: format)
        engine.prepare()
        try engine.start()
        self.engine = engine
    }

    private nonisolated static func install(_ tap: VoiceTap, on input: AVAudioInputNode, format: AVAudioFormat) {
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in tap.take(buffer) }
    }

    // MARK: The recognizer's segments

    /// A new request and task on the same microphone: at the start, and each time the recognizer ends a segment while
    /// the owner still speaks.
    private func beginSegment(_ recognizer: SFSpeechRecognizer) {
        let request: SFSpeechAudioBufferRecognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // On this phone only: no audio is sent anywhere.
        request.requiresOnDeviceRecognition = true
        request.addsPunctuation = true
        request.taskHint = .dictation
        self.request = request
        tap?.request = request
        current = ""
        let segment: ObjectIdentifier = ObjectIdentifier(request)
        task = Self.recognize(with: recognizer, request: request) { @Sendable [weak self] words, final, error in
            Task { @MainActor in self?.took(words: words, final: final, error: error, segment: segment) }
        }
    }

    private nonisolated static func recognize(
        with recognizer: SFSpeechRecognizer, request: SFSpeechAudioBufferRecognitionRequest,
        deliver: @escaping @Sendable (String?, Bool, RecognizerError?) -> Void
    ) -> SFSpeechRecognitionTask {
        recognizer.recognitionTask(with: request) { result, error in
            deliver(result?.bestTranscription.formattedString, result?.isFinal ?? false, error.map(RecognizerError.init))
        }
    }

    private func took(words: String?, final: Bool, error: RecognizerError?, segment: ObjectIdentifier) {
        // A late answer of a segment already closed.
        guard let request, ObjectIdentifier(request) == segment else { return }
        if let words, !words.isEmpty {
            current = words
            quietRuns = 0
        }
        heard = (segments + [current]).filter { !$0.isEmpty }.joined(separator: " ")
        guard final || error != nil else { return }
        if !current.isEmpty { segments.append(current) }
        current = ""
        if phase == .transcribing {
            finalWait?.resume()
            finalWait = nil
            return
        }
        guard phase == .recording, let recognizer else { return }
        if let error {
            logger.warning("the recognizer stopped domain=\(error.domain, privacy: .public) code=\(error.code) detail=\(error.detail, privacy: .public)")
        }
        switch voiceSegmentNext(final: final, error: error, quietRuns: quietRuns, limit: Self.quietRestarts) {
        case .goOn:
            // A pause the recognizer closed a segment on, or a silence: the go goes on.
            if error != nil { quietRuns += 1 }
            beginSegment(recognizer)
        case .stopQuiet:
            Task { await finish(keep: true, why: String(localized: "Dictation stopped: nothing more was heard. What was heard is in the box.", comment: "Composer: voice typing ended after several silences")) }
        case .stopFailed:
            let detail: String = error?.detail ?? ""
            Task { await finish(keep: true, why: String(localized: "Dictation stopped: \(detail). What was heard is in the box.", comment: "Composer: the phone's recognizer failed; the argument is its words")) }
        }
    }

    private func took(level: Float) {
        guard phase == .recording else { return }
        levels.append(level)
        if levels.count > Self.levelCount { levels.removeFirst(levels.count - Self.levelCount) }
    }

    // MARK: Interruptions

    private func observe() {
        let center: NotificationCenter = NotificationCenter.default
        observers = [
            center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
                let began: Bool = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt).flatMap(AVAudioSession.InterruptionType.init(rawValue:)) == .began
                guard began else { return }
                Task { @MainActor in
                    await self?.finish(keep: true, why: String(localized: "Recording stopped: something else took the microphone (a call, Siri). What was heard is in the box.", comment: "Composer: an interruption ended voice typing"))
                }
            },
            center.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.microphoneChanged() }
            },
            center.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in await self?.finish(keep: true, why: nil) }
            },
        ]
    }

    /// A microphone was plugged in or taken away: the engine stopped itself; the tap goes on the new input, and this
    /// phone's recognizer closes its segment and starts another in the new format.
    private func microphoneChanged() {
        guard phase == .recording, let engine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        self.engine = nil
        do {
            if let recognizer {
                request?.endAudio()
                if !current.isEmpty { segments.append(current) }
                current = ""
                self.request = nil
                tap?.request = nil
                try openEngine()
                beginSegment(recognizer)
            } else {
                try openEngine()
            }
            logger.info("voice moved to another microphone")
        } catch {
            logger.error("voice lost the microphone error=\(String(describing: error), privacy: .public)")
            Task { await finish(keep: true, why: String(localized: "The microphone went away; what was heard is in the box.", comment: "Composer: the microphone was disconnected while recording")) }
        }
    }

    // MARK: Ending

    /// Ends a go: the microphone stops; with `keep` the words are written down and handed over (this phone's final
    /// segment, or the Mac's answer), else dropped; the session is given back. `why` is said over the composer.
    private func finish(keep: Bool, why: String?) async {
        // Once ✓ is being written down, a second end (a late interruption, ✕) waits for it rather than cut it.
        guard phase == .starting || phase == .recording else { return }
        limit?.cancel()
        limit = nil
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers = []
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        tap = nil
        phase = keep ? .transcribing : .idle
        var words: String?
        var failure: String?
        if keep {
            if let request {
                request.endAudio()
                // The recognizer's last answer for what was said, a moment after the end of the audio.
                await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                    finalWait = continuation
                    Task { @MainActor [weak self] in
                        try? await Task.sleep(for: .seconds(2))
                        self?.finalWait?.resume()
                        self?.finalWait = nil
                    }
                }
                if !current.isEmpty { segments.append(current) }
                words = segments.joined(separator: " ")
            } else if let feed {
                do {
                    words = try await feed.end()
                } catch {
                    logger.error("the Mac could not write the recording down error=\(String(describing: error), privacy: .public)")
                    failure = String(localized: "Your Mac could not write it down: \(macWords(error))", comment: "Composer: the Mac's dictation failed; the argument is why")
                }
            }
        } else {
            feed?.cancel()
        }
        task?.cancel()
        task = nil
        request = nil
        recognizer = nil
        feed = nil
        segments = []
        current = ""
        startedAt = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        let spoken: String = (words ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !spoken.isEmpty { deliver?(spoken) }
        deliver = nil
        notice = why
        if let failure {
            phase = .failed(failure)
        } else if keep, spoken.isEmpty, why == nil {
            phase = .failed(String(localized: "Nothing was heard. Try again.", comment: "Composer: dictation heard nothing"))
        } else {
            phase = .idle
        }
    }

    private func fail(_ words: String) {
        feed?.cancel()
        feed = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        phase = .failed(words)
    }

    private func macWords(_ error: Error) -> String {
        if let request = error as? DaemonRequestError, !request.detail.isEmpty { return request.detail }
        return String(describing: error)
    }

    enum VoiceError: Error { case noMicrophone }
}

/// What the microphone's tap does on the audio thread: a level per buffer, the buffer to this phone's recognizer, or the
/// buffer as 16 kHz mono 16-bit samples to the Mac's stream.
final class VoiceTap: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var heldRequest: SFSpeechAudioBufferRecognitionRequest?
    private let feed: MacVoiceFeed?
    private let converter: AVAudioConverter?
    private let target: AVAudioFormat?
    private let level: @Sendable (Float) -> Void

    init(format: AVAudioFormat, request: SFSpeechAudioBufferRecognitionRequest?, feed: MacVoiceFeed?, level: @escaping @Sendable (Float) -> Void) {
        heldRequest = request
        self.feed = feed
        self.level = level
        let target: AVAudioFormat? = feed == nil ? nil : AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)
        self.target = target
        converter = target.flatMap { AVAudioConverter(from: format, to: $0) }
    }

    var request: SFSpeechAudioBufferRecognitionRequest? {
        get { lock.withLock { heldRequest } }
        set { lock.withLock { heldRequest = newValue } }
    }

    func take(_ buffer: AVAudioPCMBuffer) {
        level(micLevel(rms: Self.rms(buffer)))
        request?.append(buffer)
        if let feed, let samples = convert(buffer) { feed.append(samples) }
    }

    private static func rms(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let channel = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return 0 }
        var sum: Float = 0
        for index in 0..<Int(buffer.frameLength) { sum += channel[index] * channel[index] }
        return (sum / Float(buffer.frameLength)).squareRoot()
    }

    private func convert(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let converter, let target else { return nil }
        let capacity: AVAudioFrameCount = AVAudioFrameCount(Double(buffer.frameLength) * target.sampleRate / buffer.format.sampleRate) + 16
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return nil }
        let given: GivenOnce = GivenOnce()
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if given.done {
                status.pointee = .noDataNow
                return nil
            }
            given.done = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let samples = out.int16ChannelData?[0], out.frameLength > 0 else { return nil }
        return Data(bytes: samples, count: Int(out.frameLength) * 2)
    }

    private final class GivenOnce: @unchecked Sendable { var done: Bool = false }
}

/// The Mac's stream of one go: opened at once, fed the pieces in the order they were recorded (one sender, so the
/// numbers never cross), ended at ✓ with the Mac's words or dropped at ✕.
final class MacVoiceFeed: Sendable {
    private let continuation: AsyncStream<Data>.Continuation
    private let sender: Task<String, Error>

    /// `failed` is told when the stream cannot be opened or fed before its end (not when it is dropped).
    init(api: DaemonAPI, language: String, failed: @escaping @Sendable () -> Void) {
        let (stream, continuation): (AsyncStream<Data>, AsyncStream<Data>.Continuation) = AsyncStream.makeStream(of: Data.self)
        self.continuation = continuation
        sender = Task {
            let opened: VoiceStream
            do {
                opened = try await api.openVoiceStream(language: language)
            } catch {
                continuation.finish()
                if !Task.isCancelled { failed() }
                throw error
            }
            do {
                var pending: Data = Data()
                var number: Int = 0
                for await chunk in stream {
                    pending.append(chunk)
                    let (pieces, rest): ([Data], Data) = voicePieces(pending, size: voicePieceBytes, flush: false)
                    for piece in pieces {
                        try await api.feedVoice(stream: opened.id, piece: number, samples: piece)
                        number += 1
                    }
                    pending = rest
                }
                try Task.checkCancellation()
                for piece in voicePieces(pending, size: voicePieceBytes, flush: true).pieces {
                    try await api.feedVoice(stream: opened.id, piece: number, samples: piece)
                    number += 1
                }
                return try await api.endVoiceStream(opened.id).text
            } catch {
                try? await api.dropVoiceStream(opened.id)
                continuation.finish()
                if !Task.isCancelled { failed() }
                throw error
            }
        }
    }

    func append(_ samples: Data) { continuation.yield(samples) }

    func end() async throws -> String {
        continuation.finish()
        return try await sender.value
    }

    func cancel() {
        sender.cancel()
        continuation.finish()
    }
}
