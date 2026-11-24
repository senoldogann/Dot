import XCTest
@testable import Dot

/// While the app is in front of the owner and shows a chat, an alert about that chat is not drawn (item 1446): the app and the
/// daemon's lease agree.
final class ForegroundQuietTests: XCTestCase {
    private let chat: Set<String> = ["run_1", "run_2", "run_helper"]

    func testAnAlertAboutTheChatOnTheScreenIsNotDrawn() {
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: chat, alertRunId: "run_2"), .quiet)
    }

    func testAnAlertAboutAHelperOfTheChatOnTheScreenIsNotDrawnEither() {
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: chat, alertRunId: "run_helper"), .quiet)
    }

    func testAnAlertAboutAnotherChatIsABanner() {
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: chat, alertRunId: "run_other"), .banner)
    }

    func testWithNoChatOnTheScreenEveryAlertIsABanner() {
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: nil, alertRunId: "run_1"), .banner, "the list is on the screen")
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: [], alertRunId: "run_1"), .banner)
    }

    func testAnAppThatIsNotActiveDrawsEverything() {
        XCTAssertEqual(
            ForegroundQuiet.presentation(appIsActive: false, runIdsOnScreen: chat, alertRunId: "run_1"), .banner,
            "a system sheet or the app switcher is over the chat: the owner may not be reading it")
    }

    func testAnAlertAboutNoRunIsNotKnownToBeAboutTheChatAndIsDrawn() {
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: chat, alertRunId: nil), .banner)
    }

    /// The run an alert is about is the one its route names: `#/runs/<id>` for a run, other routes for what is about none.
    func testTheRunAnAlertIsAboutIsTheOneItsRouteNames() {
        let onScreen: Set<String> = ["run_abc-1"]
        func present(_ route: String) -> AlertPresentation {
            ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: onScreen, alertRunId: runId(inRoute: route))
        }
        XCTAssertEqual(present("#/runs/run_abc-1"), .quiet)
        XCTAssertEqual(present("#/runs/run_other"), .banner)
        for route in ["#/campaigns/cmp_1", "#/settings/schedules", "#/settings/memory", "#/runs", "#/runs/run_abc-1/extra"] {
            XCTAssertEqual(present(route), .banner, route)
        }
    }

    func testWhatTheLeaseHoldsBackAndWhatTheAppHoldsBackAreOneThing() {
        // The daemon keeps the end of a task from a phone whose app is in front; the app keeps the alert of the chat it shows.
        // An ask (an approval, a question) is pushed whatever the lease says: it is the chat's own card, so the app draws no banner
        // over it, while another chat's ask is one.
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: chat, alertRunId: "run_1"), .quiet)
        XCTAssertEqual(ForegroundQuiet.presentation(appIsActive: true, runIdsOnScreen: chat, alertRunId: "run_elsewhere"), .banner)
    }
}
