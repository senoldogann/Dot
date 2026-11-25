import Foundation
import XCTest

@testable import DotEngine

/// The Connect apps sheet's catalogue (item 1861): the daemon's answers, in the shapes of `server/views.py`, decoded and
/// read the way the web's `catalogue.js` reads them, with what a phone may start.
final class ConnectCatalogTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    private func sources(servers: String, vault: String, accounts: String) throws -> ConnectSources {
        let way: (String, String) -> String = { kind, ref in #"{"kind":"\#(kind)","ref":"\#(ref)","how":"h","data":"d","does":"does"}"# }
        let listing: (String, String, String, [String]) -> String = { id, name, section, ways in
            #"{"id":"\#(id)","name":"\#(name)","section":"\#(section)","does":"\#(name) does things.","logo":"\#(id).svg","glyph":null,"ways":[\#(ways.joined(separator: ","))]}"#
        }
        let plugins: String =
            #"{"plugins":["#
            + [
                listing("google", "Google", "popular", [way("web", "google")]),
                listing("notion", "Notion", "popular", [way("mcp", "notion"), way("web", "notion")]),
                listing("github", "GitHub", "popular", [way("mcp", "github"), way("web", "github")]),
                listing("mail", "Mail", "popular", [way("mac", "mail")]),
                listing("microsoft-365", "Microsoft 365", "popular", [way("web", "microsoft")]),
                listing("1password", "1Password", "passwords", [way("vault", "1password")]),
                listing("sentry", "Sentry", "code", [way("mcp", "sentry")]),
                listing("things", "Things", "mac", [way("mac", "things")]),
                listing("linear", "Linear", "popular", [way("mcp", "linear")]),
            ].joined(separator: ",") + "]}"
        let app: (String, String, String?, Bool) -> String = { id, state, step, waiting in
            #"{"id":"\#(id)","kind":"mac","name":"\#(id)","does":"x","state":"\#(state)","step":\#(step.map { "\"\($0)\"" } ?? "null"),"waiting":\#(waiting)}"#
        }
        let connections: String =
            #"{"apps":["# + [app("mail", "not_asked", "ask", false), app("things", "not_installed", nil, false), app("google", "signed_out", "sign_in", false),
                app("notion", "signed_out", "sign_in", false), app("github", "signed_in", nil, false), app("microsoft", "signed_out", "sign_in", false)]
            .joined(separator: ",") + #"],"browser_mode":"own","headless":false,"runner":"Dot"}"#
        return ConnectSources(
            plugins: try decode(PluginsView.self, plugins).plugins, connections: try decode(ConnectionsView.self, connections),
            servers: try decode(McpServersView.self, servers), vault: try decode(VaultStateView.self, vault), providers: try decode([AccountProviderView].self, accounts))
    }

    private let noServers: String = #"{"servers":[],"services":[{"id":"notion","name":"Notion","added":false},{"id":"sentry","name":"Sentry","added":false},{"id":"linear","name":"Linear","added":true}]}"#
    private let noVault: String = #"{"cli":"/opt/homebrew/bin/op","app":true,"connected":false,"vaults":[],"error":null}"#
    private let noAccounts: String = #"[{"id":"google","name":"Google","configured":true,"client_names":[],"accounts":[]}]"#

    private func app(_ apps: [ConnectApp], _ id: String) throws -> ConnectApp { try XCTUnwrap(apps.first { $0.id == id }, id) }

    func testWhatAPhoneMayStartAndWhatFinishesOnTheMac() throws {
        let apps: [ConnectApp] = connectApps(try sources(servers: noServers, vault: noVault, accounts: noAccounts))
        XCTAssertEqual(apps.map(\.id), ["google", "notion", "github", "mail", "microsoft-365", "1password", "sentry", "things", "linear"], "the daemon's order")
        XCTAssertEqual(try app(apps, "mail").action, .stepOnMac(app: "mail"), "macOS asks on the Mac; the phone starts the question")
        XCTAssertEqual(try app(apps, "google").action, .stepOnMac(app: "google"), "the sign-in page opens in Dot's browser on the Mac")
        XCTAssertEqual(try app(apps, "notion").action, .onMacOnly, "adding a connected app is refused to a phone (item 678), before its web way")
        XCTAssertEqual(try app(apps, "sentry").action, .onMacOnly)
        XCTAssertEqual(try app(apps, "1password").action, .onMacOnly, "1Password's token is taken on the Mac alone")
        XCTAssertEqual(try app(apps, "github").action, .connected, "its web way is signed in")
        XCTAssertEqual(try app(apps, "things").action, .nothing, "not installed: no step to take")
        XCTAssertEqual(try app(apps, "linear").action, .nothing, "its name is taken by a server of the config: nothing to add")
        XCTAssertTrue(apps.allSatisfy { $0.removals.isEmpty }, "nothing connected here has a Disconnect")
    }

    func testConnectedWaysAccountsAndWhatDisconnectTakes() throws {
        let servers: String =
            #"{"servers":[{"name":"notion","origin":"connected","address":"https://mcp.notion.com/mcp","service":"notion","state":"ready","tools":["search"],"error":null,"link":null,"signs_in":true,"mark":null,"picks_tools":true,"plugin":null},"#
            + #"{"name":"sentry","origin":"connected","address":"https://mcp.sentry.dev/mcp","service":"sentry","state":"signing_in","tools":[],"error":null,"link":"https://sentry.io/oauth","signs_in":true,"mark":null,"picks_tools":false,"plugin":null}],"#
            + #""services":[]}"#
        let vault: String = #"{"cli":"/opt/homebrew/bin/op","app":true,"connected":true,"vaults":[{"id":"v1","name":"Shared with Dot","logins":12}],"error":null}"#
        let accounts: String =
            #"[{"id":"google","name":"Google","configured":true,"client_names":[],"accounts":[{"address":"owner@example.com","connected_at":1.0}]},{"id":"microsoft","name":"Microsoft","configured":false,"client_names":[],"accounts":[]}]"#
        let apps: [ConnectApp] = connectApps(try sources(servers: servers, vault: vault, accounts: accounts))
        let notion: ConnectApp = try app(apps, "notion")
        XCTAssertEqual(notion.action, .connected)
        XCTAssertEqual(notion.removals, [.server("notion")])
        XCTAssertEqual(try app(apps, "sentry").action, .waiting, "Dot waits for the sign-in")
        let password: ConnectApp = try app(apps, "1password")
        XCTAssertEqual(password.action, .connected)
        XCTAssertEqual(password.accounts, ["Shared with Dot"])
        XCTAssertEqual(password.removals, [.vault])
        let google: ConnectApp = try app(apps, "google")
        XCTAssertEqual(google.action, .connected, "an official sign-in connects it")
        XCTAssertEqual(google.accounts, ["owner@example.com"])
        XCTAssertEqual(google.removals, [.account(provider: "google", address: "owner@example.com")])
        XCTAssertEqual(try app(apps, "microsoft-365").action, .stepOnMac(app: "microsoft"), "no account: its web way's step")
    }

    func testSectionsFilterAndSearch() throws {
        let apps: [ConnectApp] = connectApps(try sources(servers: noServers, vault: noVault, accounts: noAccounts))
        XCTAssertEqual(connectSections(apps), ["popular", "passwords", "code", "mac"], "in the order they first stand")
        XCTAssertEqual(filterConnectApps(apps, section: "code", onlyConnected: false, query: "").map(\.id), ["sentry"])
        XCTAssertEqual(filterConnectApps(apps, section: nil, onlyConnected: true, query: "").map(\.id), ["github"])
        XCTAssertEqual(filterConnectApps(apps, section: nil, onlyConnected: false, query: "GIT").map(\.id), ["github"], "case is folded")
        XCTAssertEqual(filterConnectApps(apps, section: nil, onlyConnected: false, query: "microsoft does").map(\.id), ["microsoft-365"], "every word, name or words")
        XCTAssertEqual(filterConnectApps(apps, section: "code", onlyConnected: false, query: "git"), [])
        let skills: [PaletteSkill] = try decode([PaletteSkill].self, #"[{"name":"pdf-forms","description":"Fill PDF forms."},{"name":"deploy","description":"Ship the site."}]"#)
        XCTAssertEqual(filterSkills(skills, query: "pdf").map(\.name), ["pdf-forms"])
        XCTAssertEqual(filterSkills(skills, query: "ship").map(\.name), ["deploy"])
        XCTAssertEqual(filterSkills(skills, query: " "), [], "skills are listed only while searching")
    }
}
