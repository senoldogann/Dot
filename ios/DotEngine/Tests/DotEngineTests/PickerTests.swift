import XCTest

@testable import DotEngine

/// The composer's pickers (PLAN §22): the trigger at the start of a word, the sections in their order and filtered as
/// the owner types, the picks a message carries, the commands the composer does itself.
final class PickerTests: XCTestCase {
    private let catalog: ComposerCatalog = ComposerCatalog(
        skills: [PickerItem(kind: .skill, token: "/pdf", name: "pdf", detail: "Read and make PDFs")],
        agents: [PickerItem(kind: .agent, token: "@Researcher", name: "Researcher", detail: "Finds sources")],
        teams: [PickerItem(kind: .team, token: "@Launch", name: "Launch", detail: "")],
        chats: [PickerItem(kind: .chat, token: "@\"Dentist\"", name: "Dentist", detail: "")],
        accounts: [PickerItem(kind: .account, token: "@me@example.com", name: "me@example.com", detail: "Google · mail")],
        files: [PickerItem(kind: .file, token: "#~/site/ci.yml", name: "~/site/ci.yml", detail: "+3 −1")])
    private let commands: [PickerItem] = [
        PickerItem(kind: .command, token: "/btw", name: "/btw", detail: ""), PickerItem(kind: .command, token: "/loop", name: "/loop", detail: ""),
    ]

    func testATriggerOpensAtTheStartOfAWordOnly() {
        XCTAssertEqual(pickerTrigger(in: "/"), .slash(""))
        XCTAssertEqual(pickerTrigger(in: "look at #ci"), .hash("ci"))
        XCTAssertEqual(pickerTrigger(in: "ask\n@res"), .at("res"))
        XCTAssertNil(pickerTrigger(in: "me@example"), "an address is no mention")
        XCTAssertNil(pickerTrigger(in: "done "), "a finished word closes the panel")
    }

    func testSectionsComeInTheirOrderAndFilterAsTheOwnerTypes() {
        XCTAssertEqual(pickerSections(.slash(""), commands: commands, catalog: catalog, limit: 5).map(\.kind), [.command, .skill])
        XCTAssertEqual(pickerSections(.at(""), commands: commands, catalog: catalog, limit: 5).map(\.kind), [.agent, .team, .chat, .account])
        XCTAssertEqual(pickerSections(.slash("lo"), commands: commands, catalog: catalog, limit: 5).flatMap(\.items).map(\.token), ["/loop"])
        XCTAssertEqual(pickerSections(.hash("ci"), commands: commands, catalog: catalog, limit: 5).flatMap(\.items).map(\.name), ["~/site/ci.yml"])
        XCTAssertEqual(pickerSections(.slash(""), commands: commands, catalog: catalog, limit: 1).first?.items.count, 1)
    }

    func testAMessageCarriesItsPicksAndLeavesTheirTokens() {
        let (text, picks): (String, MessagePicks) = messagePicks("/plan summarise /pdf the report for @me@example.com", catalog: catalog)
        XCTAssertEqual(text, "summarise the report for")
        XCTAssertEqual(picks, MessagePicks(skills: ["pdf"], accounts: ["me@example.com"], planFirst: true))
        XCTAssertEqual(messagePicks("@Researcher look", catalog: catalog).text, "@Researcher look", "a mention of an agent stays in the words")
    }

    func testCommandsTheComposerDoesItself() {
        XCTAssertEqual(composerCommand("/btw is it slow?"), .aside("is it slow?"))
        XCTAssertEqual(composerCommand("/side why"), .aside("why"))
        XCTAssertEqual(composerCommand("/loop 30m check the build"), .loop(every: "30m", text: "check the build"))
        XCTAssertEqual(composerCommand("/loop soon"), .loopHow)
        XCTAssertEqual(composerCommand("/stop"), .stop)
        XCTAssertEqual(composerCommand("/model"), .model)
        XCTAssertEqual(composerCommand("/btwx hi"), .message)
        XCTAssertEqual(composerCommand("plain words"), .message)
    }
}
