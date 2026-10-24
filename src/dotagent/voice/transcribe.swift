// The program that writes a recording down (item 626): `transcribe <audio file> <language>` prints one line of JSON on
// the standard output, `{"text": "..."}` for the words, or `{"error": "<code>", "detail": "..."}`, and exits 0 for
// words and 1 for an error. The codes are `unsupported-language` (the Mac has no dictation of the language),
// `no-audio` (the file holds no sound, or none this Mac can read) and `failed` (anything else). The words go nowhere
// else: no file, no log, no standard error.
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
        guard arguments.count == 3 else {
            fail("failed", "usage: transcribe <audio file> <language>")
        }
        let url = URL(fileURLWithPath: arguments[1])
        guard let locale = await DictationTranscriber.supportedLocale(equivalentTo: Locale(identifier: arguments[2])) else {
            fail("unsupported-language", "this Mac has no dictation of \(arguments[2])")
        }
        let file: AVAudioFile
        do {
            file = try AVAudioFile(forReading: url)
        } catch {
            fail("no-audio", "the recording cannot be read as sound: \(error.localizedDescription)")
        }
        if file.length == 0 {
            fail("no-audio", "the recording holds no sound")
        }
        let transcriber = DictationTranscriber(
            locale: locale, contentHints: [], transcriptionOptions: [.punctuation], reportingOptions: [],
            attributeOptions: []
        )
        do {
            if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                try await request.downloadAndInstall()
            }
            let analyzer = SpeechAnalyzer(modules: [transcriber])
            async let words = transcriber.results.reduce(into: [String]()) { all, result in
                if result.isFinal {
                    all.append(String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines))
                }
            }
            if let last = try await analyzer.analyzeSequence(from: file) {
                try await analyzer.finalizeAndFinish(through: last)
            } else {
                await analyzer.cancelAndFinishNow()
            }
            let text = try await words.filter { !$0.isEmpty }.joined(separator: " ")
            finish(["text": text], code: 0)
        } catch {
            fail("failed", error.localizedDescription)
        }
    }
}
