import DotEngine
import XCTest

@testable import DotNext

/// Settings' pure parts (wave N3): the clock's formats, the web view's origin lock and sign-in address, a schedule's
/// line, the tokens' words, the diagnosis report (never the key), and the search index.
final class SettingsWordsTests: XCTestCase {
    private let english: Locale = Locale(identifier: "en_US")

    func testTheClockReadsInTheChosenFormat() throws {
        var calendar: Calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let date: Date = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 11, day: 24, hour: 14, minute: 5)))
        XCTAssertEqual(clockWords(date, format: .twentyFour, locale: english), "14:05")
        XCTAssertTrue(clockWords(date, format: .twelve, locale: english).hasPrefix("2:05"), clockWords(date, format: .twelve, locale: english))
        XCTAssertTrue(clockWords(date, format: .twelve, locale: english).contains("PM"))
    }

    func testOnlyTheMacsOwnOriginLoadsInTheWebView() throws {
        let origin: URL = try XCTUnwrap(URL(string: "https://mac.tail0.ts.net"))
        XCTAssertEqual(decideWebNavigation(url: try XCTUnwrap(URL(string: "https://mac.tail0.ts.net/#/settings/vault")), origin: origin), .allow)
        XCTAssertEqual(decideWebNavigation(url: try XCTUnwrap(URL(string: "https://evil.example/")), origin: origin), .openExternally)
        XCTAssertEqual(decideWebNavigation(url: try XCTUnwrap(URL(string: "http://mac.tail0.ts.net/")), origin: origin), .openExternally, "not the same scheme")
        XCTAssertEqual(decideWebNavigation(url: try XCTUnwrap(URL(string: "javascript:alert(1)")), origin: origin), .block)
        let loopback: URL = try XCTUnwrap(URL(string: "http://127.0.0.1:18765"))
        XCTAssertEqual(decideWebNavigation(url: try XCTUnwrap(URL(string: "http://127.0.0.1:18765/")), origin: loopback), .allow)
        XCTAssertEqual(decideWebNavigation(url: try XCTUnwrap(URL(string: "http://127.0.0.1:9999/")), origin: loopback), .openExternally, "another port")
    }

    func testThePageGetsTheKeyOnTheMacsOriginOnly() throws {
        let origin: URL = try XCTUnwrap(URL(string: "https://mac.tail0.ts.net"))
        let key: String = "phone.0123456789ab." + String(repeating: "A", count: 43)
        XCTAssertEqual(webAddress(origin: origin, route: "/settings/vault").absoluteString, "https://mac.tail0.ts.net/#/settings/vault")
        XCTAssertNil(webAddress(origin: origin, route: "/watch").query(), "the key is never in the address")
        let script: String = keyScript(origin: origin, key: key)
        XCTAssertTrue(script.hasPrefix(#"if (location.origin === "https:\/\/mac.tail0.ts.net")"#) || script.hasPrefix(#"if (location.origin === "https://mac.tail0.ts.net")"#), script)
        XCTAssertTrue(script.contains(#"localStorage.setItem("dot.token", "\#(key)")"#))
        XCTAssertFalse(keyScript(origin: origin, key: "x\"); alert(1); (\"").contains(#"alert(1); (""#), "a quote in a key cannot end the string")
    }

    func testAScheduleSaysPausedFailedOrItsNextRun() {
        let now: Date = Date(timeIntervalSince1970: 1_800_000_000)
        func schedule(enabled: Bool, status: String, failed: Int) -> ScheduleView {
            ScheduleView(
                record: ScheduleRecord(
                    id: "s", name: "Brief", cron: "0 8 * * *", runAt: nil, goal: "g", mode: "task", enabled: enabled, lastRunAt: now.timeIntervalSince1970 - 3600,
                    nextRunAt: now.timeIntervalSince1970 + 3600, lastStatus: status, options: ScheduleOptions(model: nil, effort: nil, approvals: nil, goalMode: nil, timezone: nil),
                    runsFailedInRow: failed),
                when: "Every day at 08:00", nextLocal: nil, lastLocal: nil, runAtInput: nil, zone: "Europe/Helsinki", agent: nil)
        }
        let app: Bundle = Bundle(for: AppBundleAnchor.self)
        func word(_ key: String.LocalizationValue) -> String { String(localized: key, bundle: app) }
        let paused: String = scheduleLine(schedule(enabled: false, status: "done", failed: 0), now: now, locale: english)
        XCTAssertTrue(paused.contains(word("Paused")), paused)
        XCTAssertTrue(paused.contains("1 hour ago"), "the last run, relative to the given now: \(paused)")
        XCTAssertFalse(paused.contains("in 1 hour"), "a paused one has no next run")
        let failed: String = scheduleLine(schedule(enabled: true, status: "failed", failed: 3), now: now, locale: english)
        XCTAssertTrue(failed.contains(word("Failed \(3) times in a row")), failed)
        let next: String = scheduleLine(schedule(enabled: true, status: "done", failed: 0), now: now, locale: english)
        XCTAssertTrue(next.hasPrefix(word("Every day at \("08:00")") + " · ") && next.contains("in 1 hour"), next)
    }

    func testARhythmReadsAsTheWebSaysIt() {
        let app: Bundle = Bundle(for: AppBundleAnchor.self)
        func word(_ key: String.LocalizationValue) -> String { String(localized: key, bundle: app) }
        XCTAssertEqual(rhythmWords(cron: "30 7 * * *", runAt: nil, locale: english), word("Every day at \("07:30")"))
        XCTAssertEqual(rhythmWords(cron: "0 */2 * * *", runAt: nil, locale: english), word("Every \(2) hours"))
        XCTAssertEqual(rhythmWords(cron: "*/15 * * * *", runAt: nil, locale: english), word("Every \(15) minutes"))
        XCTAssertEqual(rhythmWords(cron: "30 9 * * 1-5", runAt: nil, locale: english), word("Weekdays at \("09:30")"))
        XCTAssertEqual(rhythmWords(cron: "0 9 * * 1", runAt: nil, locale: english), word("Every \("Monday") at \("09:00")"))
        XCTAssertEqual(rhythmWords(cron: "30 18 * * 5,1,3", runAt: nil, locale: english), word("\("Mon, Wed, Fri") at \("18:30")"))
        XCTAssertEqual(rhythmWords(cron: "0 8,20 * * *", runAt: nil, locale: english), word("Every day at \("08:00, 20:00")"))
        XCTAssertNil(rhythmWords(cron: "0 9 1 * *", runAt: nil, locale: english), "a day of the month has no words: the daemon's stand")
        XCTAssertNil(rhythmWords(cron: nil, runAt: nil, locale: english))
    }

    func testACapThatDoesNotFitIsNoCap() {
        XCTAssertEqual(capTokens("300"), 300_000_000)
        XCTAssertNil(capTokens("9999999999999999"), "13+ digits overflow: no Save, no crash")
        XCTAssertNil(capTokens("-1"))
        XCTAssertNil(capTokens("abc"))
    }

    func testTokensReadAsTheWebSaysThem() {
        XCTAssertEqual(tokenWords(500_000_000, locale: english), "500M")
        XCTAssertEqual(tokenWords(4_000_000_000, locale: english), "4,000M")
        XCTAssertEqual(tokenWords(2_300_000, locale: english), "2.3M")
        XCTAssertEqual(tokenWords(45_000, locale: english), "45k")
    }

    func testTheReportNamesTheFailingHopAndNeverTheKey() {
        let probe: DiagnosisProbe = DiagnosisProbe(link: .up, health: .transport(code: -1003, millis: 30), status: .notRun, proof: .notRun)
        let run: DiagnosisRun = DiagnosisRun(probe: probe, diagnosis: diagnose(probe), trace: "ABCDEFGHIJ", at: Date(timeIntervalSince1970: 0))
        let report: String = diagnosisReport(run, host: "mac.tail0.ts.net", appVersion: "0.1 (1)", system: "iOS 27.2")
        XCTAssertTrue(report.contains("trace: ABCDEFGHIJ"))
        XCTAssertTrue(report.contains("- tailnet: FAILED"))
        XCTAssertTrue(report.contains("- key: not reached"))
        XCTAssertFalse(report.contains("phone."), "no key in the report")
    }

    func testTheSearchIndexFindsRulesByApprovalsAndMaintenanceByResume() {
        let index: [(SettingsEntry, SettingsPage)] = settingsIndex()
        let ids: (String) -> [String] = { query in searchSettings(query, in: index.map(\.0)).map(\.id) }
        XCTAssertEqual(ids("approvals").first, "rules")
        XCTAssertEqual(ids("resume"), ["maintenance"])
        XCTAssertEqual(ids("imessage"), [], "the Mac's pages have no row on the phone (PLAN §26)")
        XCTAssertTrue(ids("budget").contains("caps"))
        XCTAssertEqual(Set(index.map(\.0.id)).count, index.count, "every entry has its own id")
    }
}
