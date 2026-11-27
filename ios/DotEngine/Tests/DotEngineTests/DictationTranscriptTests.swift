import Foundation
import XCTest

@testable import DotEngine

/// A dictation keeps every word across the speaker's pauses (item 2048). The results below are the ones
/// `SFSpeechRecognizer` gave on macOS 26 for a `say` recording with a three-second pause (on-device, partial results):
/// the pause closes the utterance with a non-final result that carries metadata, and the next words start from empty.
final class DictationTranscriptTests: XCTestCase {
    private func feed(_ results: [(String, Bool)]) -> DictationTranscript {
        results.reduce(DictationTranscript.empty) { $0.taking(words: $1.0, closesUtterance: $1.1) }
    }

    func testAPauseInsideOneTaskKeepsTheWordsBeforeIt() {
        let heard: DictationTranscript = feed([
            ("I", false), ("I would like", false), ("I would like to book a table for two people tomorrow evening", false),
            ("I would like to book a table for two people tomorrow evening", true),
            ("And", false), ("And please make it near the window", false),
        ])
        XCTAssertEqual(heard.text, "I would like to book a table for two people tomorrow evening And please make it near the window")
        XCTAssertEqual(heard.live, "And please make it near the window")
        let done: DictationTranscript = heard.taking(words: "And please make it near the window if possible", closesUtterance: true)
        XCTAssertEqual(done.closed, ["I would like to book a table for two people tomorrow evening", "And please make it near the window if possible"])
        XCTAssertEqual(done.live, "")
    }

    func testTurkishWithTwoPausesKeepsAllThree() {
        let heard: DictationTranscript = feed([
            ("Yarın akşam", false), ("Yarın akşam iki kişilik bir masa ayırtmak istiyorum", true),
            ("Mümkünse", false), ("Mümkünse pencere kenarında olsun", true),
            ("Bir de", false), ("Bir de doğum günü pastası lazım", true),
        ])
        XCTAssertEqual(heard.text, "Yarın akşam iki kişilik bir masa ayırtmak istiyorum Mümkünse pencere kenarında olsun Bir de doğum günü pastası lazım")
    }

    func testAnEmptyFinalAfterAClosedUtteranceAddsNothingAndRepeatsNothing() {
        // A pause then ✓: the task's final result has no words.
        let heard: DictationTranscript = feed([("Hello there", false), ("Hello there", true), ("", true)])
        XCTAssertEqual(heard.closed, ["Hello there"])
        XCTAssertEqual(heard.text, "Hello there")
    }

    func testWordsSaidAgainAfterAPauseAreKeptTwice() {
        // The same words in the next utterance are the owner's own, said again: a closed utterance is never compared.
        let heard: DictationTranscript = feed([("No", true), ("No", false), ("No", true)])
        XCTAssertEqual(heard.closed, ["No", "No"])
        XCTAssertEqual(heard.text, "No No")
    }

    func testARevisionReplacesOnlyTheLiveUtterance() {
        let heard: DictationTranscript = feed([("First part", true), ("I scream", false), ("Ice cream", false)])
        XCTAssertEqual(heard.text, "First part Ice cream")
    }

    func testATaskThatEndsKeepsItsLiveWordsAndTheNextTaskAddsToThem() {
        // A silence error or a new microphone ends the task: its words stay, and a new task's words come after them.
        let ended: DictationTranscript = feed([("One two", false)]).closingLive()
        let next: DictationTranscript = ended.taking(words: "three", closesUtterance: false)
        XCTAssertEqual(next.text, "One two three")
        XCTAssertEqual(ended.closingLive(), ended, "closing with nothing live changes nothing")
    }

    func testAnEmptyPartialKeepsTheLiveWords() {
        XCTAssertEqual(feed([("Keep me", false), ("  ", false)]).text, "Keep me")
    }
}
