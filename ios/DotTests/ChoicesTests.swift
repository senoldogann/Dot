import XCTest
@testable import Dot

final class ChoicesTests: XCTestCase {
    private func provider() -> [ProviderModels] {
        [ProviderModels(
            provider: "openai",
            models: [
                ModelOption(ref: "openai:big", label: "Big", tools: true, vision: true, free: false, efforts: ["low", "medium", "high", "xhigh"], defaultEffort: "medium"),
                ModelOption(ref: "openai:plain", label: "Plain", tools: true, vision: false, free: false, efforts: ["on"], defaultEffort: nil),
                ModelOption(ref: "openai:notools", label: "No tools", tools: false, vision: false, free: false, efforts: nil, defaultEffort: nil),
            ],
            error: nil),
            ProviderModels(provider: "empty", models: [], error: "no key")]
    }

    func testEffortLevelsAreWords() {
        XCTAssertEqual(effortWords("xhigh"), "Extra high")
        XCTAssertEqual(effortWords("low"), "Low")
        XCTAssertEqual(effortWords("off"), "Off")
    }

    func testAModelWithFewerThanTwoLevelsOffersNoChoice() {
        let options: [ProviderModels] = provider()
        XCTAssertEqual(options.option("openai:big")?.pickableEfforts, ["low", "medium", "high", "xhigh"])
        XCTAssertEqual(options.option("openai:plain")?.pickableEfforts, [])
        XCTAssertEqual(options.option("openai:notools")?.pickableEfforts, [])
        XCTAssertNil(options.option("nope:x"))
        XCTAssertNil(options.option(nil))
    }

    func testThePickerOffersOnlyModelsThatUseToolsAndProvidersThatHaveSome() {
        let pickable: [ProviderModels] = provider().pickable
        XCTAssertEqual(pickable.map(\.provider), ["openai"])
        XCTAssertEqual(pickable[0].models.map(\.ref), ["openai:big", "openai:plain"])
    }

    func testPickingAModelDropsALevelItDoesNotOfferAndFastMode() {
        let chat: ChatChoices = ChatChoices(mode: "task", model: "openai:big", effort: "xhigh", speed: "fast", approvals: "ask", goalMode: true)
        let plain: ChatChoices = chat.picking(model: "openai:plain", among: provider())
        XCTAssertEqual(plain.model, "openai:plain")
        XCTAssertNil(plain.effort, "a level the new model does not offer is not asked for")
        XCTAssertEqual(plain.speed, "standard", "fast mode is only on some models")
        XCTAssertEqual(plain.mode, "task")
        XCTAssertEqual(plain.approvals, "ask", "the chat's other choices stay")
        XCTAssertEqual(plain.goalMode, true)
        // A level the new model has stays.
        let keeps: ChatChoices = ChatChoices(mode: "task", model: nil, effort: "high", speed: "standard", approvals: nil, goalMode: nil)
            .picking(model: "openai:big", among: provider())
        XCTAssertEqual(keeps.effort, "high")
        // Back to the default model.
        XCTAssertNil(chat.picking(model: nil, among: provider()).model)
    }

    func testAFreshChatTakesTheDaemonsOwnEverywhere() {
        let fresh: ChatChoices = ChatChoices.fresh.picking(effort: "low")
        XCTAssertEqual(fresh.mode, "auto")
        XCTAssertNil(fresh.model)
        XCTAssertEqual(fresh.effort, "low")
        XCTAssertEqual(ChatChoices.fresh.picking(effort: "low").picking(effort: nil).effort, nil)
    }

    func testTheEffortLineSaysTheModelsDefaultWhenNoneIsPicked() {
        let chat: ChatChoices = ChatChoices(mode: "auto", model: nil, effort: nil, speed: "standard", approvals: nil, goalMode: nil)
        XCTAssertEqual(chat.effortLine(among: provider(), main: "openai:big"), "Medium (default)")
        XCTAssertEqual(chat.picking(effort: "high").effortLine(among: provider(), main: "openai:big"), "High")
        XCTAssertEqual(chat.picking(model: "openai:plain", among: provider()).effortLine(among: provider(), main: "openai:big"), "Default")
    }

    func testTheDaemonsRunDetailCarriesTheChoicesAndAnOlderOneDoesNot() throws {
        let run: String = #""run":{"id":"run_1","thread_id":"thr_1","goal":"g","mode":"task","model":"m","status":"done","created_at":1,"finished_at":2,"result":"r","steps":1,"parent_run_id":null,"source":"ui","agent_id":null,"follows_reaction":null},"title":"T","children":[],"active":false,"questions":[]"#
        let with: RunDetail = try JSONDecoder().decode(
            RunDetail.self,
            from: Data(#"{\#(run),"choices":{"mode":"chat","model":"ollama:m","effort":"low","speed":"standard","approvals":null,"goal_mode":null}}"#.utf8))
        XCTAssertEqual(with.choices?.mode, "chat")
        XCTAssertEqual(with.choices?.model, "ollama:m")
        XCTAssertEqual(with.choices?.effort, "low")
        let none: RunDetail = try JSONDecoder().decode(RunDetail.self, from: Data(#"{\#(run),"choices":null}"#.utf8))
        XCTAssertNil(none.choices)
        let absent: RunDetail = try JSONDecoder().decode(RunDetail.self, from: Data("{\(run)}".utf8))
        XCTAssertNil(absent.choices)
    }

    func testModelOptionsReadTheirLevels() throws {
        let option: ModelOption = try JSONDecoder().decode(
            ModelOption.self,
            from: Data(#"{"ref":"a:b","label":"B","vision":true,"tools":true,"thinking":true,"efforts":["low","high"],"default_effort":"high","free":false,"learns":false,"fast_mode":false}"#.utf8))
        XCTAssertEqual(option.efforts, ["low", "high"])
        XCTAssertEqual(option.defaultEffort, "high")
        let bare: ModelOption = try JSONDecoder().decode(
            ModelOption.self, from: Data(#"{"ref":"a:b","label":"B","vision":true,"tools":true,"free":false}"#.utf8))
        XCTAssertNil(bare.efforts)
        XCTAssertEqual(bare.pickableEfforts, [])
    }

    func testAThreadRowIsMadeFromARunsPageForAChatTheListLacks() throws {
        let detail: RunDetail = RunDetail(
            run: makeRun("run_7", thread: "thr_7", goal: "x", status: "running", created: 50), title: "Plan", children: [], active: true,
            questions: [], choices: nil)
        let row: ThreadSummary = ThreadSummary.opened(from: detail)
        XCTAssertEqual(row.id, "thr_7")
        XCTAssertEqual(row.title, "Plan")
        XCTAssertEqual(row.latestRunId, "run_7")
        XCTAssertTrue(row.isWorking)
    }
}
