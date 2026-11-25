import XCTest

@testable import DotEngine

/// Settings' search with synonyms (PLAN §13) and the connection diagnosis by hop (research G2 #5).
final class SettingsTests: XCTestCase {
    private let entries: [SettingsEntry] = [
        SettingsEntry(id: "rules", title: "Kurallar", path: "Ajanlar", synonyms: ["approvals", "permissions", "onaylar", "izinler"]),
        SettingsEntry(id: "appearance", title: "Görünüm", path: "", synonyms: ["dark mode", "theme", "karanlık"]),
        SettingsEntry(id: "models", title: "Modeller", path: "Ajanlar", synonyms: ["llm", "review model"]),
        SettingsEntry(id: "review", title: "Code review", path: "Ajanlar › Modeller", synonyms: ["pre-push"]),
    ]

    func testASynonymFindsThePageInEitherLanguage() {
        XCTAssertEqual(searchSettings("approvals", in: entries).map(\.id), ["rules"])
        XCTAssertEqual(searchSettings("onay", in: entries).map(\.id), ["rules"])
        XCTAssertEqual(searchSettings("KARANLIK", in: entries).map(\.id), ["appearance"], "case and the dotless i are folded")
        XCTAssertEqual(searchSettings("gorunum", in: entries).map(\.id), ["appearance"], "accents are folded")
    }

    func testATitleMatchRanksAboveASynonymMatchAndEveryWordMustMatch() {
        XCTAssertEqual(searchSettings("review", in: entries).map(\.id), ["review", "models"])
        XCTAssertEqual(searchSettings("review model", in: entries).map(\.id), ["models", "review"], "neither title holds both words: the order given")
        XCTAssertEqual(searchSettings("dark xyz", in: entries), [])
        XCTAssertEqual(searchSettings("   ", in: entries), [], "an empty query finds nothing")
    }

    private func probe(_ link: LinkState, _ health: ProbeAnswer, _ status: ProbeAnswer, _ proof: ProofOutcome) -> Diagnosis {
        diagnose(DiagnosisProbe(link: link, health: health, status: status, proof: proof))
    }

    func testEachHopIsNamedByWhatTheProbesSaw() {
        XCTAssertEqual(probe(.down, .notRun, .notRun, .notRun).firstFailure?.0, .phone)
        let dns: Diagnosis = probe(.up, .transport(code: -1003, millis: 40), .notRun, .notRun)
        XCTAssertEqual(dns.firstFailure?.0, .tailnet)
        XCTAssertEqual(dns.firstFailure?.1, .nameNotFound)
        XCTAssertEqual(dns.hops.map(\.1), [.passed, .failed(.nameNotFound), .unknown, .unknown, .unknown])
        XCTAssertEqual(probe(.up, .transport(code: -1001, millis: 10_000), .notRun, .notRun).firstFailure?.1, .macSilent)
        XCTAssertEqual(probe(.up, .transport(code: -1004, millis: 30), .notRun, .notRun).firstFailure?.1, .daemonNotListening)
        XCTAssertEqual(probe(.up, .transport(code: -1202, millis: 30), .notRun, .notRun).firstFailure?.1, .insecure)
        XCTAssertEqual(probe(.up, .answered(code: 502, millis: 30), .notRun, .notRun).firstFailure?.0, .daemon)
        XCTAssertEqual(probe(.up, .answered(code: 200, millis: 30), .answered(code: 401, millis: 30), .notRun).firstFailure?.1, .keyRefused(401))
        XCTAssertEqual(probe(.up, .answered(code: 200, millis: 30), .answered(code: 200, millis: 30), .mismatch).firstFailure?.1, .proofMismatch)
    }

    func testAllHopsPassWhenTheKeyIsTakenAndProven() {
        let whole: Diagnosis = probe(.up, .answered(code: 200, millis: 30), .answered(code: 200, millis: 40), .proven)
        XCTAssertNil(whole.firstFailure)
        XCTAssertEqual(whole.hops.map(\.0), DiagnosisHop.allCases)
        XCTAssertNil(probe(.up, .answered(code: 200, millis: 30), .answered(code: 200, millis: 40), .unsupported).firstFailure, "an old daemon without a proof route still passes")
    }

    func testATraceIdIsTenCharacters() {
        XCTAssertEqual(traceId(random: 0).count, 10)
        XCTAssertEqual(traceId(random: UInt64.max).count, 10)
    }

    func testTheProofIsTheDaemonsOwnHMAC() {
        // The values come from the daemon's own computation (`server/guard.py`, Python hmac) for the same inputs.
        let nonce: String = String(repeating: "ab", count: 32)
        XCTAssertEqual(
            expectedProof(key: "phone.0123456789ab." + String(repeating: "A", count: 43), nonce: nonce),
            "87c8d5c7fc5c0d5788da0138722678d5c20580463201e572be5d90a7a7f83fc0")
        XCTAssertEqual(expectedProof(key: "maintoken", nonce: nonce), "241b3f08b8dcfdd6b5de0fca31a4c17d79890c2c13c4fa62ba645821e0ed1dc3")
        XCTAssertEqual(phoneKeyId("phone.0123456789ab." + String(repeating: "A", count: 43)), "0123456789ab")
        XCTAssertNil(phoneKeyId("maintoken"))
        XCTAssertEqual(proofNonce().count, 64)
    }

    func testARefusalSaysItsWordsAndWhetherItWasForAPhone() {
        let refused: DaemonRequestError = .status(
            path: "/api/budget", code: 403, body: #"{"message":"a phone cannot raise a cap: do it on your Mac","context":{"phone":true}}"#)
        XCTAssertEqual(refused.detail, "a phone cannot raise a cap: do it on your Mac")
        XCTAssertTrue(refused.refusedToAPhone)
        XCTAssertFalse(DaemonRequestError.status(path: "/x", code: 403, body: #"{"message":"no","context":{}}"#).refusedToAPhone)
        XCTAssertEqual(DaemonRequestError.status(path: "/x", code: 404, body: #"{"detail":"Not Found"}"#).detail, "Not Found")
    }

    func testAScheduleEditWritesEveryKeyNullsIncluded() throws {
        let edit: ScheduleEdit = ScheduleEdit(name: "n", goal: "g", cron: "0 9 * * *", at: nil, mode: "task", model: nil, effort: nil, approvals: nil, goalMode: nil, timezone: nil)
        let object: [String: Any] = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(edit)) as? [String: Any])
        XCTAssertEqual(Set(object.keys), ["name", "goal", "cron", "at", "mode", "model", "effort", "approvals", "goal_mode", "timezone"])
        XCTAssertTrue(object["at"] is NSNull)
    }
}

final class SettingsFailureTests: XCTestCase {
    func testAFailureIsReadIntoWhatThePageSays() {
        XCTAssertEqual(settingsFailure(.transport(path: "/api/x", underlying: URLError(.timedOut))), .unreachable)
        XCTAssertEqual(settingsFailure(.status(path: "/api/x", code: 401, body: #"{"message":"a valid bearer token is required"}"#)), .keyRefused)
        XCTAssertEqual(
            settingsFailure(.status(path: "/api/budget", code: 403, body: #"{"message":"a phone cannot raise a cap: do it on your Mac","context":{"phone":true}}"#)),
            .notForAPhone("a phone cannot raise a cap: do it on your Mac"))
        XCTAssertEqual(settingsFailure(.status(path: "/api/budget", code: 404, body: #"{"detail":"Not Found"}"#)), .daemonTooOld, "an unknown route")
        XCTAssertEqual(settingsFailure(.status(path: "/api/memories/x", code: 404, body: #"{"message":"no note x","context":{}}"#)), .other("no note x"), "a missing item is not an old daemon")
        XCTAssertEqual(settingsFailure(.status(path: "/api/x", code: 500, body: "")), .other("HTTP 500"))
    }
}

final class PairingQRTests: XCTestCase {
    func testTheMacsQRFillsTheAddressAndNothingElseDoes() {
        XCTAssertEqual(addressFromQR("https://macbook-air.tail0a1b2.ts.net/")?.name, "macbook-air.tail0a1b2.ts.net")
        XCTAssertNil(addressFromQR("https://tailscale.com/download"), "the Tailscale download code is no Mac")
        XCTAssertNil(addressFromQR("WIFI:S:home;T:WPA;P:secret;;"))
        XCTAssertNil(addressFromQR("http://macbook-air.tail0a1b2.ts.net/"))
    }
}

final class PairingLinkTests: XCTestCase {
    func testThePairLinkGivesTheAddressAndTheCode() {
        let start: PairingStart? = pairingStart("dotnext://pair?host=https%3A%2F%2Fmac.tail0a1b2.ts.net&code=7KQ2M9XD")
        XCTAssertEqual(start, .link(host: DaemonHost(name: "mac.tail0a1b2.ts.net", origin: URL(string: "https://mac.tail0a1b2.ts.net")!), code: "7KQ2M9XD"))
        XCTAssertEqual(pairingStart("https://mac.tail0a1b2.ts.net/"), .address(DaemonHost(name: "mac.tail0a1b2.ts.net", origin: URL(string: "https://mac.tail0a1b2.ts.net")!)))
    }

    func testAStrayLinkStartsNothing() {
        XCTAssertNil(pairingStart("dotnext://pair?host=https://evil.example&code=7KQ2M9XD"), "not a tailnet address")
        XCTAssertNil(pairingStart("dotnext://pair?host=http://mac.tail0a1b2.ts.net&code=7KQ2M9XD"), "not https")
        XCTAssertNil(pairingStart("dotnext://pair?host=https://mac.tail0a1b2.ts.net&code=123"), "not 8 characters")
        XCTAssertNil(pairingStart("dotnext://chat/abc"))
        XCTAssertNil(pairingStart("dotnext://pair/extra?host=https://mac.tail0a1b2.ts.net&code=7KQ2M9XD"))
    }
}
