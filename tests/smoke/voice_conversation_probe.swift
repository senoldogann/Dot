// Manual speech pipeline probe. It receives only a paced synthetic fixture and writes measurements as JSON lines.

import AVFoundation
import Foundation
import Speech

private let rate: Int32 = 16_000
private let frameBytes = Int(rate) * 2 / 50

private enum ProbeError: Error {
    case usage
    case invalidFixture
    case unavailableLocale(String)
    case unsupportedFormat
    case conversionFailed
    case noSpeechSignal
}

private enum Phase: String, Encodable {
    case firstUse = "first-use"
    case prepared
}

private struct Measurement: Encodable {
    let phase: Phase
    let tail_ms: Int
    let text: String
    let prepare_ms: Double
    let transcript_ms: Double
    let first_signal_ms: Double
    let end_to_signal_ms: Double
}

private func milliseconds(_ duration: Duration) -> Double {
    let parts = duration.components
    return Double(parts.seconds) * 1_000 + Double(parts.attoseconds) / 1e15
}

private func audioBuffer(_ bytes: Data, in format: AVAudioFormat, using converter: AVAudioConverter?) throws
    -> AVAudioPCMBuffer
{
    let frames = AVAudioFrameCount(bytes.count / 2)
    guard let input = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
        let samples = input.int16ChannelData
    else { throw ProbeError.unsupportedFormat }
    input.frameLength = frames
    _ = bytes.withUnsafeBytes { raw in
        raw.copyBytes(to: UnsafeMutableRawBufferPointer(start: samples[0], count: bytes.count))
    }
    guard let converter else { return input }
    let wanted = converter.outputFormat
    let capacity = AVAudioFrameCount((Double(frames) * wanted.sampleRate / format.sampleRate).rounded(.up)) + 32
    guard let output = AVAudioPCMBuffer(pcmFormat: wanted, frameCapacity: capacity) else {
        throw ProbeError.unsupportedFormat
    }
    var sent = false
    var error: NSError?
    let status = converter.convert(to: output, error: &error) { _, state in
        if sent {
            state.pointee = .noDataNow
            return nil
        }
        sent = true
        state.pointee = .haveData
        return input
    }
    if let error { throw error }
    guard status != .error, output.frameLength > 0 else { throw ProbeError.conversionFailed }
    return output
}

private func speechSignal(in buffer: AVAudioPCMBuffer) -> Bool {
    let frames = Int(buffer.frameLength)
    if let samples = buffer.int16ChannelData {
        return (0..<frames).contains { abs(Int(samples[0][$0])) >= 64 }
    }
    if let samples = buffer.floatChannelData {
        return (0..<frames).contains { abs(samples[0][$0]) >= 1.0 / 512 }
    }
    return false
}

@MainActor
private func firstSignal(synthesizer: AVSpeechSynthesizer, voice: AVSpeechSynthesisVoice, reply: String) async throws
    -> ContinuousClock.Instant
{
    let utterance = AVSpeechUtterance(string: reply)
    utterance.voice = voice
    let (signals, stream) = AsyncStream<ContinuousClock.Instant>.makeStream(bufferingPolicy: .bufferingOldest(1))
    synthesizer.write(utterance) { buffer in
        guard let pcm = buffer as? AVAudioPCMBuffer, pcm.frameLength > 0 else {
            stream.finish()
            return
        }
        if speechSignal(in: pcm) {
            stream.yield(.now)
        }
    }
    var first: ContinuousClock.Instant?
    // Drain through the end of the utterance before reusing the synthesizer.
    for await signal in signals where first == nil { first = signal }
    guard let first else { throw ProbeError.noSpeechSignal }
    return first
}

@MainActor
private func measure(
    pcm: Data, locale: Locale, tailMs: Int, phase: Phase, synthesizer: AVSpeechSynthesizer,
    voice: AVSpeechSynthesisVoice, reply: String
) async throws -> Measurement {
    let transcriber = DictationTranscriber(
        locale: locale, contentHints: [], transcriptionOptions: [.punctuation],
        reportingOptions: [], attributeOptions: []
    )
    // Asset installation is outside the measured turn, as it would be in a prepared call.
    if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
        try await request.downloadAndInstall()
    }
    guard let source = AVAudioFormat(
        commonFormat: .pcmFormatInt16, sampleRate: Double(rate), channels: 1, interleaved: true),
        let wanted = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber])
    else { throw ProbeError.unsupportedFormat }
    let converter = source == wanted ? nil : AVAudioConverter(from: source, to: wanted)
    if source != wanted && converter == nil { throw ProbeError.unsupportedFormat }

    let analyzer = SpeechAnalyzer(
        modules: [transcriber], options: .init(priority: .userInitiated, modelRetention: .processLifetime))
    let preparing = ContinuousClock.now
    if phase == .prepared { try await analyzer.prepareToAnalyze(in: wanted) }
    let prepareMs = milliseconds(preparing.duration(to: .now))

    let (inputs, feed) = AsyncStream<AnalyzerInput>.makeStream()
    defer { feed.finish() }
    async let words = transcriber.results.reduce(into: [String]()) { parts, result in
        if result.isFinal { parts.append(String(result.text.characters)) }
    }
    try await analyzer.start(inputSequence: inputs)

    let started = ContinuousClock.now
    let frameOffsets = stride(from: 0, to: pcm.count, by: frameBytes)
    for offset in frameOffsets {
        let end = min(offset + frameBytes, pcm.count)
        let buffer = try audioBuffer(pcm[offset..<end], in: source, using: converter)
        feed.yield(AnalyzerInput(buffer: buffer))
        try await Task.sleep(until: started.advanced(by: .seconds(Double(end / 2) / Double(rate))))
    }

    let speechEnded = ContinuousClock.now
    try await Task.sleep(for: .milliseconds(250))
    let tailBytes = Int(rate) * tailMs * 2 / 1_000
    if tailBytes > 0 {
        let tail = try audioBuffer(Data(count: tailBytes), in: source, using: converter)
        feed.yield(AnalyzerInput(buffer: tail))
    }

    let finalizing = ContinuousClock.now
    // End this turn's input explicitly. Timed finalization stalled on the last sample of the open sequence.
    feed.finish()
    try await analyzer.finalizeAndFinishThroughEndOfInput()
    let text = try await words.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    let transcriptMs = milliseconds(finalizing.duration(to: .now))

    // A fixed-delay connector fixture supplies a short response without sending audio or text to a model.
    try await Task.sleep(for: .milliseconds(330))
    let speaking = ContinuousClock.now
    let signal = try await firstSignal(synthesizer: synthesizer, voice: voice, reply: reply)
    return Measurement(
        phase: phase, tail_ms: tailMs, text: text, prepare_ms: prepareMs,
        transcript_ms: transcriptMs, first_signal_ms: milliseconds(speaking.duration(to: signal)),
        end_to_signal_ms: milliseconds(speechEnded.duration(to: signal))
    )
}

@main
private struct VoiceConversationProbe {
    @MainActor
    static func main() async throws {
        let arguments = CommandLine.arguments
        guard arguments.count == 3 else { throw ProbeError.usage }
        let language = arguments[1]
        guard let locale = await DictationTranscriber.supportedLocale(equivalentTo: Locale(identifier: language)),
            let voice = AVSpeechSynthesisVoice(language: language)
        else { throw ProbeError.unavailableLocale(language) }
        let pcm = try Data(contentsOf: URL(fileURLWithPath: arguments[2]))
        guard !pcm.isEmpty && pcm.count.isMultiple(of: 2) else { throw ProbeError.invalidFixture }
        let synthesizer = AVSpeechSynthesizer()
        let reply = language == "tr-TR" ? "Peki, şimdi bakıyorum." : "Okay, I'll check now."
        let encoder = JSONEncoder()
        let cases: [(Phase, Int)] = [(.firstUse, 800)]
            + (0..<3).flatMap { _ in [0, 200, 800].map { (Phase.prepared, $0) } }
        for (phase, tailMs) in cases {
            let result = try await measure(
                pcm: pcm, locale: locale, tailMs: tailMs, phase: phase, synthesizer: synthesizer,
                voice: voice, reply: reply
            )
            print(String(decoding: try encoder.encode(result), as: UTF8.self))
        }
    }
}
