// The program that writes down what the owner says (items 626, 969): `transcribe <language>` reads the sound on its
// standard input as it comes, 16 kHz mono 16-bit little-endian samples with no header, writes it down meanwhile, and
// once that input ends prints one line of JSON on the standard output, `{"text": "..."}` for the words, or
// `{"error": "<code>", "detail": "..."}`, and exits 0 for words and 1 for an error. The codes are
// `unsupported-language` (the Mac has no dictation of the language) and `failed` (anything else). The words go nowhere
// else: no file, no log, no standard error.
//
// The input is read from the first moment, whatever the model is doing: the sound waits in memory while the model is
// made ready (and, the first time a language is used, downloaded), so the daemon's writes never wait on a full pipe.
// What was said is written down as it comes, so the answer is there a moment after the input ends: measured on this
// Mac, 0.04 s to 0.08 s after 12 s of Turkish fed in real time.
//
// It writes with macOS 26's own dictation model, on this Mac (`DictationTranscriber`, 54 languages, Turkish among
// them, where `SpeechTranscriber` has ten and no Turkish). That needs no permission: it asks for no Speech Recognition
// access, and the voice does not leave the Mac. The one thing that does is the language's model, which macOS downloads
// from Apple the first time a language is used (`AssetInventory`) and keeps. Results come as final text only, with
// punctuation where the language has it (English does, Turkish does not): the short-dictation preset and its hint give
// only volatile results, so they are left out.
//
// The daemon compiles this file with `swiftc -parse-as-library -O` on first use (dotagent/voice/helper.py).

import AVFoundation
import Foundation
import Speech

/// One line of JSON on the standard output, and the end of the program: 0 for words, 1 for an error.
func finish(_ reply: [String: String], code: Int32) -> Never {
    let line = (try? JSONSerialization.data(withJSONObject: reply, options: [.withoutEscapingSlashes]))
        ?? Data(#"{"error":"failed","detail":"the answer could not be written"}"#.utf8)
    FileHandle.standardOutput.write(line)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(code)
}

func fail(_ code: String, _ detail: String) -> Never {
    finish(["error": code, "detail": detail], code: 1)
}

@main
struct Transcribe {
    static func main() async {
        let arguments = CommandLine.arguments
        guard arguments.count == 2 else {
            fail("failed", "usage: transcribe <language>, with the sound on the standard input")
        }
        guard let locale = await DictationTranscriber.supportedLocale(equivalentTo: Locale(identifier: arguments[1])) else {
            fail("unsupported-language", "this Mac has no dictation of \(arguments[1])")
        }
        let transcriber = DictationTranscriber(
            locale: locale, contentHints: [], transcriptionOptions: [.punctuation], reportingOptions: [],
            attributeOptions: []
        )
        guard let given = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)
        else {
            fail("failed", "the format of the input could not be made")
        }
        let sound = readInput()
        do {
            if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                try await request.downloadAndInstall()
            }
            guard let wanted = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
                fail("failed", "the dictation model takes no sound this Mac can give it")
            }
            let converter: AVAudioConverter? = wanted == given ? nil : AVAudioConverter(from: given, to: wanted)
            if wanted != given && converter == nil {
                fail("failed", "the sound cannot be converted for the dictation model")
            }
            let (inputs, feed) = AsyncStream<AnalyzerInput>.makeStream()
            let analyzer = SpeechAnalyzer(modules: [transcriber])
            async let words = transcriber.results.reduce(into: [String]()) { all, result in
                if result.isFinal {
                    all.append(String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines))
                }
            }
            try await analyzer.start(inputSequence: inputs)
            var heard = false
            var left = Data()
            for await read in sound {
                left.append(read)
                let whole = left.count - left.count % 2
                if whole == 0 {
                    continue
                }
                let block = Data(left.prefix(whole))
                left = Data(left.dropFirst(whole))
                guard let buffer = pcm(block, given), let ready = converted(buffer, wanted, converter) else {
                    continue
                }
                heard = true
                feed.yield(AnalyzerInput(buffer: ready))
            }
            feed.finish()
            if heard {
                try await analyzer.finalizeAndFinishThroughEndOfInput()
            } else {
                await analyzer.cancelAndFinishNow()
            }
            let text = try await words.filter { !$0.isEmpty }.joined(separator: " ")
            finish(["text": text], code: 0)
        } catch {
            fail("failed", error.localizedDescription)
        }
    }

    /// The standard input as it comes, read to its end on a thread of its own from the moment this is called: what
    /// is read waits in the stream (which keeps every element) until it is taken.
    static func readInput() -> AsyncStream<Data> {
        let (sound, feed) = AsyncStream<Data>.makeStream(bufferingPolicy: .unbounded)
        Thread.detachNewThread {
            while true {
                let read = FileHandle.standardInput.availableData
                if read.isEmpty {
                    break
                }
                feed.yield(read)
            }
            feed.finish()
        }
        return sound
    }

    /// `bytes` of 16-bit samples as a buffer of `format`.
    static func pcm(_ bytes: Data, _ format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(bytes.count / 2)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames), let samples = buffer.int16ChannelData
        else {
            return nil
        }
        buffer.frameLength = frames
        bytes.withUnsafeBytes { raw in
            if let base = raw.baseAddress {
                memcpy(samples[0], base, Int(frames) * 2)
            }
        }
        return buffer
    }

    /// `buffer` in the format the model wants: as it is, or through `converter`.
    static func converted(_ buffer: AVAudioPCMBuffer, _ wanted: AVAudioFormat, _ converter: AVAudioConverter?)
        -> AVAudioPCMBuffer?
    {
        guard let converter else {
            return buffer
        }
        let ratio = wanted.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 1
        guard let out = AVAudioPCMBuffer(pcmFormat: wanted, frameCapacity: capacity) else {
            return nil
        }
        var given = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, state in
            if given {
                state.pointee = .noDataNow
                return nil
            }
            given = true
            state.pointee = .haveData
            return buffer
        }
        return status == .error || out.frameLength == 0 ? nil : out
    }
}
