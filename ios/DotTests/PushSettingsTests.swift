import XCTest
@testable import Dot

/// What the Notifications switches show and do (item 1441): no clock and no network, only the rules.
final class PushSwitchesTests: XCTestCase {
    private let held: PushSettings = PushSettings(needsYou: true, ready: false, done: true)

    func testAPhoneThatNeverSetThemShowsAllThreeOnAndDimmedUntilItIsRead() {
        let switches: PushSwitches = PushSwitches()
        XCTAssertEqual(switches.shown, .all, "a phone that never set them is told all three: the daemon says so")
        XCTAssertFalse(switches.isLive)
        XCTAssertFalse(switches.canFlip, "nothing is known yet: a flip could not be kept or undone")
        XCTAssertEqual(switches.offlineLine, "Dot can't be reached, so these switches are not read yet.")
    }

    func testWhatThePhoneKnewIsShownDimmedUntilTheDaemonAnswers() {
        var switches: PushSwitches = PushSwitches(known: held)
        XCTAssertEqual(switches.shown, held)
        XCTAssertFalse(switches.isLive, "the last known state is shown dimmed")
        XCTAssertFalse(switches.canFlip)
        XCTAssertEqual(switches.offlineLine, "Dot can't be reached. These are the switches as the phone last knew them.")
        switches.loaded(held)
        XCTAssertTrue(switches.isLive)
        XCTAssertTrue(switches.canFlip)
        XCTAssertNil(switches.offlineLine)
    }

    func testAReadShowsWhatTheDaemonHolds() {
        var switches: PushSwitches = PushSwitches(known: .all)
        switches.loaded(held)
        XCTAssertEqual(switches.shown, held)
        XCTAssertEqual(switches.confirmed, held)
    }

    func testAReadThatDoesNotGetThroughKeepsTheLastSwitchesAndDimsThem() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        switches.loadFailed()
        XCTAssertEqual(switches.shown, held)
        XCTAssertFalse(switches.isLive)
        XCTAssertNil(switches.flip(.done, to: false), "a switch that cannot be kept does not move")
        XCTAssertEqual(switches.shown, held)
    }

    func testAFlipShowsAtOnceAndIsWrittenAsAllThreeTogether() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        let body: PushSettings? = switches.flip(.ready, to: true)
        XCTAssertEqual(body, PushSettings(needsYou: true, ready: true, done: true))
        XCTAssertEqual(switches.shown.ready, true, "optimistic: it shows before the daemon answers")
        XCTAssertEqual(switches.confirmed, held, "and is not the daemon's word yet")
        XCTAssertNil(switches.saved(PushSettings(needsYou: true, ready: true, done: true)))
        XCTAssertEqual(switches.confirmed, PushSettings(needsYou: true, ready: true, done: true))
        XCTAssertNil(switches.sending)
    }

    func testAFlipToWhatTheSwitchAlreadyShowsWritesNothing() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        XCTAssertNil(switches.flip(.needsYou, to: true))
        XCTAssertNil(switches.sending)
    }

    func testAnotherFlipWhileAWriteIsOnItsWayWaitsAndIsFollowedByOneThatCarriesBoth() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        let first: PushSettings? = switches.flip(.ready, to: true)
        XCTAssertNotNil(first)
        XCTAssertNil(switches.flip(.done, to: false), "one write at a time: a second would race the first")
        XCTAssertEqual(switches.shown, PushSettings(needsYou: true, ready: true, done: false))
        let next: PushSettings? = switches.saved(first ?? .all)
        XCTAssertEqual(next, PushSettings(needsYou: true, ready: true, done: false), "the follow-up carries the latest of all three")
        XCTAssertNil(switches.saved(PushSettings(needsYou: true, ready: true, done: false)))
        XCTAssertEqual(switches.shown, PushSettings(needsYou: true, ready: true, done: false))
    }

    func testAFlipBackWhileAWriteIsOnItsWayEndsWhereTheOwnerLeftIt() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        let first: PushSettings? = switches.flip(.ready, to: true)
        XCTAssertNil(switches.flip(.ready, to: false))
        XCTAssertEqual(switches.saved(first ?? .all), held, "the follow-up undoes it: the owner's last word stands")
        XCTAssertNil(switches.saved(held))
        XCTAssertEqual(switches.shown, held)
    }

    func testAWriteTheDaemonDoesNotTakeGoesBackToWhatItHoldsWithAPlainLine() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        _ = switches.flip(.done, to: false)
        XCTAssertEqual(switches.shown.done, false)
        switches.failed(PushSwitches.words(forStatus: nil))
        XCTAssertEqual(switches.shown, held, "rolled back to what the daemon holds")
        XCTAssertEqual(switches.failure, "Dot could not be reached, so the switches are back as they were.")
        XCTAssertNil(switches.sending)
        XCTAssertNotNil(switches.flip(.done, to: false), "and the owner may try again")
        XCTAssertNil(switches.failure, "a new flip clears the line")
    }

    func testAFailedFollowUpGoesBackToTheLastThingTheDaemonKept() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        let first: PushSettings? = switches.flip(.ready, to: true)
        _ = switches.flip(.done, to: false)
        let second: PushSettings? = switches.saved(first ?? .all)
        XCTAssertNotNil(second)
        switches.failed(PushSwitches.words(forStatus: 500))
        XCTAssertEqual(switches.shown, PushSettings(needsYou: true, ready: true, done: true), "back to the first write, which was kept")
    }

    func testAReadThatArrivesWhileAWriteIsOnItsWayDoesNotUndoTheFlip() {
        var switches: PushSwitches = PushSwitches(known: held)
        switches.loaded(held)
        _ = switches.flip(.ready, to: true)
        switches.loaded(held)
        XCTAssertEqual(switches.shown.ready, true, "the owner's newest word stands until the daemon answers it")
        XCTAssertEqual(switches.confirmed, held)
    }

    func testEachWayAWriteFailsHasItsOwnPlainWords() {
        XCTAssertEqual(PushSwitches.words(forStatus: 401), "Dot did not accept this phone's key. Forget the key in Settings and pair again.")
        XCTAssertEqual(PushSwitches.words(forStatus: 404), "Dot does not know this phone any more. Forget the key in Settings and pair again.")
        XCTAssertEqual(PushSwitches.words(forStatus: 422), "Dot could not keep that, so the switches are back as they were.")
        XCTAssertEqual(PushSwitches.words(forStatus: nil), "Dot could not be reached, so the switches are back as they were.")
    }

    func testEachSwitchSaysInOneLineWhatItMeansInTheAppsVoice() {
        XCTAssertEqual(PushKind.allCases.map(\.title), ["Needs you", "Ready", "Done"])
        let jargon: [String] = ["push", "apns", "daemon", "token", "lease", "api", "payload", "endpoint", "mcp", "run "]
        for kind in PushKind.allCases {
            XCTAssertFalse(kind.words.isEmpty)
            XCTAssertFalse(kind.words.contains("\n"), "one line")
            for word in jargon {
                XCTAssertFalse(kind.words.lowercased().contains(word), "\(kind.title): no engine words (\(word))")
            }
        }
    }

    func testTheSwitchesAreTheThreeKeysTheDaemonTakes() throws {
        let encoder: JSONEncoder = JSONEncoder()
        encoder.outputFormatting = .sortedKeys
        XCTAssertEqual(
            String(decoding: try encoder.encode(held), as: UTF8.self), #"{"done":true,"needs_you":true,"ready":false}"#)
        let read: PushSettings = try JSONDecoder().decode(PushSettings.self, from: Data(#"{"needs_you": false, "ready": true, "done": true}"#.utf8))
        XCTAssertEqual(read, PushSettings(needsYou: false, ready: true, done: true))
        var changed: PushSettings = .all
        changed[.ready] = false
        XCTAssertEqual(changed, PushSettings(needsYou: true, ready: false, done: true))
    }
}
