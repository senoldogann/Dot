import DotEngine
import XCTest

@testable import DotNext

/// The chat's settings sheet (PLAN §24), its pure rules: which models show where and in what order, and what a pick of
/// a model does to the level and the speed.
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
}
