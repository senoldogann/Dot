import DotEngine
import XCTest

@testable import DotNext

/// The chat's settings sheet (PLAN §24) and Settings › Voice (PLAN §25), their pure rules: which models show where and in
/// what order, what a pick of a model does to the level and the speed, and which microphone voice input takes.
final class ChatSettingsAndVoiceTests: XCTestCase {
    private func providers() throws -> [ProviderModels] {
        let option: (String, String, String) -> String = { ref, label, extra in
            #"{"ref":"\#(ref)","label":"\#(label)","efforts":["low","medium","high"],"default_effort":"medium","context_length":1000\#(extra)}"#
        }
        let json: String = "["
            + #"{"provider":"claude_code","models":[\#(option("claude_code:sonnet", "Sonnet 5.5", #","tools":true"#)),\#(option("claude_code:opus", "Opus 5.5", #","tools":true,"fast_mode":true"#))],"error":null},"#
            + #"{"provider":"ollama","models":[\#(option("ollama:gemma", "gemma4", #","tools":true,"free":true"#)),\#(option("ollama:embed", "embed", #","tools":false"#))],"error":null},"#
            + #"{"provider":"openai","models":[],"error":"no key"}"#
            + "]"
        return try JSONDecoder().decode([ProviderModels].self, from: Data(json.utf8))
    }

    func testGroupsLeaveOutFailedProvidersAndToollessModelsAndFloatFavourites() throws {
        let marks: ModelMarks = ModelMarks(favourites: ["claude_code:opus"], hidden: [])
        let groups: [ModelGroup] = modelGroups(try providers(), marks: marks, defaultRef: "claude_code:sonnet", chosenRef: nil, showsHidden: false, query: "", filter: .all)
        XCTAssertEqual(groups.map(\.provider), ["claude_code", "ollama"], "a provider that failed to list is left out")
        XCTAssertEqual(groups[0].rows.map(\.option.ref), ["claude_code:opus", "claude_code:sonnet"], "the favourite floats up")
        XCTAssertEqual(groups[0].rows.map(\.isDefault), [false, true])
        XCTAssertEqual(groups[1].rows.map(\.option.ref), ["ollama:gemma"], "a model without tools cannot run a task")
    }

    func testHiddenModelsShowOnlyWhileManagedOrChosenAndTheSearchAndFilterNarrow() throws {
        let marks: ModelMarks = ModelMarks(favourites: [], hidden: ["claude_code:sonnet"])
        let all: [ProviderModels] = try providers()
        XCTAssertEqual(modelGroups(all, marks: marks, defaultRef: nil, chosenRef: nil, showsHidden: false, query: "", filter: .all)[0].rows.map(\.option.ref), ["claude_code:opus"])
        XCTAssertEqual(modelGroups(all, marks: marks, defaultRef: nil, chosenRef: nil, showsHidden: true, query: "", filter: .all)[0].rows.map(\.isHidden), [true, false], "the daemon's order, the hidden one dimmed")
        XCTAssertEqual(modelGroups(all, marks: marks, defaultRef: nil, chosenRef: "claude_code:sonnet", showsHidden: false, query: "", filter: .all)[0].rows.count, 2, "the chat's own model stays")
        XCTAssertEqual(modelGroups(all, marks: marks, defaultRef: nil, chosenRef: nil, showsHidden: false, query: "OPUS 5", filter: .all).flatMap(\.rows).map(\.option.ref), ["claude_code:opus"])
        XCTAssertEqual(modelGroups(all, marks: marks, defaultRef: nil, chosenRef: nil, showsHidden: false, query: "", filter: .free).flatMap(\.rows).map(\.option.ref), ["ollama:gemma"])
        XCTAssertEqual(modelGroups(all, marks: ModelMarks(favourites: ["ollama:gemma"], hidden: []), defaultRef: nil, chosenRef: nil, showsHidden: false, query: "", filter: .favourites).map(\.provider), ["ollama"])
    }

    func testTheSearchFindsAProviderByNameAndFoldsTurkishLetters() throws {
        let all: [ProviderModels] = try providers()
        let none: ModelMarks = ModelMarks(favourites: [], hidden: [])
        XCTAssertEqual(modelGroups(all, marks: none, defaultRef: nil, chosenRef: nil, showsHidden: false, query: "claude code", filter: .all).map(\.provider), ["claude_code"])
        XCTAssertEqual(modelGroups(all, marks: none, defaultRef: nil, chosenRef: nil, showsHidden: false, query: "GEMMA", filter: .all).flatMap(\.rows).map(\.option.ref), ["ollama:gemma"])
        XCTAssertEqual(modelGroups(all, marks: none, defaultRef: nil, chosenRef: nil, showsHidden: false, query: "ÖPÜS", filter: .all).flatMap(\.rows).map(\.option.ref), ["claude_code:opus"], "accents folded")
    }

    func testTheCurrentAndRecentModelsComeFirstOnceEach() throws {
        let all: [ProviderModels] = try providers()
        let none: ModelMarks = ModelMarks(favourites: [], hidden: [])
        let rows: [ModelRow] = recentRows(
            all, recent: ["ollama:gemma", "claude_code:opus", "gone:model", "ollama:embed"], chosenRef: "claude_code:opus", defaultRef: nil, marks: none,
            showsHidden: false, limit: 4)
        XCTAssertEqual(rows.map(\.option.ref), ["claude_code:opus", "ollama:gemma"], "the chosen first, each once; a model gone or without tools left out")
        let hiding: ModelMarks = ModelMarks(favourites: [], hidden: ["ollama:gemma", "claude_code:opus"])
        let hidden: [ModelRow] = recentRows(
            all, recent: ["ollama:gemma"], chosenRef: "claude_code:opus", defaultRef: nil, marks: hiding, showsHidden: false, limit: 4)
        XCTAssertEqual(hidden.map(\.option.ref), ["claude_code:opus"], "a hidden model leaves the recent ones, the chosen one stays")
        XCTAssertEqual(
            recentRows(all, recent: ["ollama:gemma"], chosenRef: nil, defaultRef: nil, marks: hiding, showsHidden: true, limit: 4).map(\.isHidden), [true],
            "while hidden models are managed it shows, marked")
        XCTAssertEqual(failedProviders(all).map(\.provider), ["openai"])
        XCTAssertEqual(failedProviders(all).map(\.why), ["no key"])
        XCTAssertEqual(RecentModels.adding("a", to: ["b", "a", "c"]), ["a", "b", "c"])
    }

    func testPickingAModelKeepsALevelItHasAndDropsFastModeItLacks() throws {
        let models: [ModelOption] = try providers().flatMap(\.models)
        let opus: ModelOption = try XCTUnwrap(models.first { $0.ref == "claude_code:opus" })
        let sonnet: ModelOption = try XCTUnwrap(models.first { $0.ref == "claude_code:sonnet" })
        let fastHigh: ChatChoices = ChatChoices.daemons.with(model: opus.ref, effort: "high").with(speed: "fast")
        let moved: ChatChoices = picking(sonnet, in: fastHigh)
        XCTAssertEqual(moved.model, "claude_code:sonnet")
        XCTAssertEqual(moved.effort, "high", "a level the model has is kept")
        XCTAssertEqual(moved.speed, "standard", "no fast mode on a model without it")
        XCTAssertEqual(picking(opus, in: ChatChoices.daemons.with(model: nil, effort: "max")).effort, "medium", "a level it lacks becomes its default")
        XCTAssertEqual(picking(opus, in: fastHigh).speed, "fast")
    }

    func testTheMarksToggle() {
        let none: ModelMarks = ModelMarks(favourites: [], hidden: [])
        XCTAssertEqual(none.togglingFavourite("a").favourites, ["a"])
        XCTAssertEqual(none.togglingFavourite("a").togglingFavourite("a").favourites, [])
        XCTAssertEqual(none.togglingHidden("b").hidden, ["b"])
    }

    // MARK: Voice

    private let built: MicPort = MicPort(uid: "Built-In", name: "iPhone Microphone", kind: .builtIn)
    private let car: MicPort = MicPort(uid: "BT-CAR", name: "Toyota Touch&Go", kind: .bluetooth)
    private let pods: MicPort = MicPort(uid: "BT-PODS", name: "AirPods", kind: .bluetooth)
    private let usb: MicPort = MicPort(uid: "USB-1", name: "USB Mic", kind: .wired)

    func testTheFirstConnectedMicrophoneOfTheOrderIsTaken() {
        XCTAssertEqual(preferredMic([built, pods], order: MicSlot.initial), pods, "Other Bluetooth is above the built-in one")
        XCTAssertEqual(preferredMic([built, pods, usb], order: MicSlot.initial), usb, "Other wired is first")
        XCTAssertEqual(preferredMic([built], order: MicSlot.initial), built)
        let builtFirst: [MicSlot] = [.builtIn, .otherWired, .otherBluetooth, .carPlay]
        XCTAssertEqual(preferredMic([built, pods], order: builtFirst), built, "the owner's order wins")
        XCTAssertNil(preferredMic([], order: MicSlot.initial))
    }

    func testAMicrophoneIsLearnedByNameAboveItsKindAndANamedOneIsNotOther() {
        let learned: [MicSlot] = learning(car, into: MicSlot.initial)
        XCTAssertEqual(learned, [.otherWired, .named(uid: "BT-CAR", name: "Toyota Touch&Go", kind: .bluetooth), .otherBluetooth, .builtIn, .carPlay])
        XCTAssertEqual(learning(car, into: learned), learned, "learned once")
        XCTAssertEqual(learning(built, into: MicSlot.initial), MicSlot.initial, "the built-in one has its own place")
        // The car below the built-in microphone: AirPods (Other Bluetooth) are still taken first, the car is not.
        let carLast: [MicSlot] = [.otherWired, .otherBluetooth, .builtIn, .named(uid: "BT-CAR", name: "Toyota Touch&Go", kind: .bluetooth), .carPlay]
        XCTAssertEqual(preferredMic([built, car], order: carLast), built, "a named microphone is not one of Other")
        XCTAssertEqual(preferredMic([built, car, pods], order: carLast), pods)
        XCTAssertEqual(forgetting("BT-CAR", in: carLast), MicSlot.initial)
    }

    func testTheOrderIsKeptAcrossLaunches() throws {
        let defaults: UserDefaults = try XCTUnwrap(UserDefaults(suiteName: "voice-test-\(UUID().uuidString)"))
        XCTAssertEqual(MicSlot.read(defaults), MicSlot.initial)
        let order: [MicSlot] = learning(usb, into: [.builtIn, .otherWired, .otherBluetooth, .carPlay])
        MicSlot.write(order, defaults)
        XCTAssertEqual(MicSlot.read(defaults), order)
        XCTAssertEqual(TranscribeOn.current(defaults), .device, "this phone until the owner picks the Mac")
    }

    func testARecognizerErrorIsNotAPause() {
        let silence: RecognizerError = RecognizerError(domain: "kAFAssistantErrorDomain", code: 1110, detail: "No speech detected")
        let broken: RecognizerError = RecognizerError(domain: "kAFAssistantErrorDomain", code: 1101, detail: "Local speech recognition failed")
        XCTAssertEqual(voiceSegmentNext(final: true, error: nil, quietRuns: 0, limit: 3), .goOn, "a pause goes on")
        XCTAssertEqual(voiceSegmentNext(final: false, error: silence, quietRuns: 2, limit: 3), .goOn, "a silence goes on a few times")
        XCTAssertEqual(voiceSegmentNext(final: false, error: silence, quietRuns: 3, limit: 3), .stopQuiet, "then the go ends")
        XCTAssertEqual(voiceSegmentNext(final: false, error: broken, quietRuns: 0, limit: 3), .stopFailed, "a failure ends it at once")
    }

    func testTheClockTheLanguageTagAndTheLevels() {
        XCTAssertEqual(recordingClock(7), "0:07")
        XCTAssertEqual(recordingClock(102), "1:42")
        XCTAssertEqual(languageTag(Locale(identifier: "tr-TR")), "tr-TR")
        XCTAssertEqual(languageTag(Locale(identifier: "tr")), "tr-TR")
        XCTAssertEqual(languageTag(Locale(identifier: "en-US")), "en-US")
        XCTAssertEqual(micLevel(rms: 0), 0)
        XCTAssertEqual(micLevel(rms: 1), 1)
        XCTAssertEqual(micLevel(rms: 0.001), 0, accuracy: 0.05, "-60 dB is silence")
    }

    func testTheWordsHeardGoAfterWhatWasTyped() {
        XCTAssertEqual(DictationText.merged(base: "", heard: " merhaba "), "merhaba")
        XCTAssertEqual(DictationText.merged(base: "Hi ", heard: "there"), "Hi there")
        XCTAssertEqual(DictationText.merged(base: "list:\n", heard: "milk"), "list:\nmilk")
    }
}
