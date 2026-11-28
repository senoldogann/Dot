import XCTest

@testable import DotEngine

/// Settings › AI services (item 2054): the keys decode from what a throwaway daemon's `/api/keys` gave, with and
/// without a last check (a daemon from before the item sends none), and the state, the removal and the field's check
/// follow the daemon's rules.
final class KeyTests: XCTestCase {
    private let listed: String =
        #"{"env_file":"/tmp/dot/.env","keys":[{"provider":"openai","name":"OPENAI_API_KEY","source":null,"saved":false,"tail":"","check":null},{"provider":"openrouter","name":"OPENROUTER_API_KEY","source":"env_file","saved":true,"tail":"7c38","check":{"ok":false,"detail":"401: the key was refused","at":1795600000.5}},{"provider":"anthropic","name":"ANTHROPIC_API_KEY","source":"environment","saved":true,"tail":"Ab12"},{"provider":"google","name":"GOOGLE_API_KEY","source":"keychain","saved":false,"tail":"zz99","check":{"ok":true,"detail":"google api answered","at":1795600000.0}}]}"#

    func testTheDaemonsAnswerDecodesWithAndWithoutACheck() throws {
        let keys: [KeyView] = try JSONDecoder().decode(KeysView.self, from: Data(listed.utf8)).keys
        XCTAssertEqual(keys.map(\.state), [.missing, .failing, .set, .set])
        XCTAssertEqual(keys.map(\.removable), [false, true, true, false], "only a key in Dot's .env is the phone's to remove")
        XCTAssertNil(keys[2].check, "a daemon from before item 2054 sends no check")
        XCTAssertTrue(keys[2].shadowed, "a key Dot was started with comes before one saved here")
        XCTAssertEqual(keys[1].source, .envFile)
        let result: KeyCheckResult = try JSONDecoder().decode(KeyCheckResult.self, from: Data(#"{"name":"openrouter api","ok":true,"required":true,"detail":"fine"}"#.utf8))
        XCTAssertEqual(keys[1].checked(result, at: 5).state, .set)
        XCTAssertEqual(keys[1].checked(result, at: 5).check?.at, 5)
    }

    func testTheFieldSendsOnlyAKeyTheDaemonTakes() {
        XCTAssertTrue(isSendableKey("  sk-or-0123456789abcdef\n"), "the ends of a paste are trimmed")
        XCTAssertEqual(trimmedKey("  sk-1 \n"), "sk-1")
        XCTAssertFalse(isSendableKey(""))
        XCTAssertFalse(isSendableKey("   "))
        XCTAssertFalse(isSendableKey("sk with space"))
        XCTAssertFalse(isSendableKey("sk-ğ"))
        XCTAssertFalse(isSendableKey(String(repeating: "a", count: 1001)))
        XCTAssertTrue(isSendableKey(String(repeating: "a", count: 1000)))
    }
}
