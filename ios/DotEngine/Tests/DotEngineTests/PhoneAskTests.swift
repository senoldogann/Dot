import XCTest

@testable import DotEngine

/// Item 2051: Dot's asks to the phone as the daemon writes them, the phone's answers as the daemon reads them
/// (`dotagent.phone.asks`), the times of both, and the loop that takes an ask and answers it.
final class PhoneAskTests: XCTestCase {
    private func decoded(_ json: String) throws -> PhoneAsk { try JSONDecoder().decode(PhoneAsk.self, from: Data(json.utf8)) }

    private func encoded(_ answer: PhoneAnswer) throws -> [String: JSONValue] {
        try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(answer))
    }

    func testEveryKindOfAskReadsAsTheDaemonWritesIt() throws {
        XCTAssertEqual(
            try decoded(#"{"id":"ask_1","kind":"calendar_events","start":"2026-11-28T00:00:00+03:00","end":"2026-11-29T00:00:00+03:00","query":"","limit":50}"#),
            .calendarEvents(CalendarEventsAsk(id: "ask_1", start: "2026-11-28T00:00:00+03:00", end: "2026-11-29T00:00:00+03:00", query: "", limit: 50)))
        XCTAssertEqual(
            try decoded(#"{"id":"ask_2","kind":"calendar_add","title":"Dentist","start":"2026-11-28","end":"2026-11-29","all_day":true,"location":"","notes":"","calendar":""}"#),
            .calendarAdd(CalendarAddAsk(id: "ask_2", title: "Dentist", start: "2026-11-28", end: "2026-11-29", allDay: true, location: "", notes: "", calendar: "")))
        XCTAssertEqual(
            try decoded(#"{"id":"ask_3","kind":"reminder_add","title":"Call Ada","due":null,"notes":"","list":""}"#),
            .reminderAdd(ReminderAddAsk(id: "ask_3", title: "Call Ada", due: nil, notes: "", list: "")))
        XCTAssertEqual(try decoded(#"{"id":"ask_4","kind":"location"}"#), .location(id: "ask_4"))
        XCTAssertEqual(try decoded(#"{"id":"ask_4","kind":"location"}"#).id, "ask_4")
        XCTAssertThrowsError(try decoded(#"{"id":"ask_5","kind":"contacts"}"#), "a kind this app does not know is not guessed at")
    }

    func testEveryAnswerIsWrittenAsTheDaemonReadsIt() throws {
        let event: PhoneEvent = PhoneEvent(
            id: "E1", title: "Lunch", start: "2026-11-28T12:00:00+03:00", end: "2026-11-28T13:00:00+03:00", allDay: false,
            location: "", notes: String(repeating: "n", count: 5000), calendar: "Home", organizer: "")
        let events: [String: JSONValue] = try encoded(.events([event], more: false))
        XCTAssertEqual(Set(events.keys), ["kind", "events", "more"])
        XCTAssertEqual(events["kind"]?.string, "events")
        guard case .array(let listed)? = events["events"], case .object(let first)? = listed.first else { return XCTFail("no events") }
        XCTAssertEqual(Set(first.keys), ["id", "title", "start", "end", "all_day", "location", "notes", "calendar", "organizer"])
        XCTAssertEqual(first["notes"]?.string?.count, PhoneAskRules.textChars, "the daemon's limit is kept here")

        let many: [String: JSONValue] = try encoded(.events(Array(repeating: event, count: 120), more: false))
        guard case .array(let cut)? = many["events"] else { return XCTFail("no events") }
        XCTAssertEqual(cut.count, PhoneAskRules.mostEvents)
        XCTAssertEqual(many["more"], .bool(true), "what was cut is said to be more")

        XCTAssertEqual(try encoded(.created(id: "EK-1", calendar: "Home")), ["kind": .string("created"), "id": .string("EK-1"), "calendar": .string("Home")])
        let here: [String: JSONValue] = try encoded(.location(latitude: 41, longitude: 29, accuracyM: 12, at: "2026-11-28T11:00:00Z"))
        XCTAssertEqual(Set(here.keys), ["kind", "latitude", "longitude", "accuracy_m", "at"])
        XCTAssertEqual(try encoded(.refused(.denied, message: "No.")), ["kind": .string("refused"), "reason": .string("denied"), "message": .string("No.")])
    }

    func testTimesAreMomentsWithAnOffsetOrDays() throws {
        let moment: Date = try XCTUnwrap(PhoneTimes.moment("2026-11-28T14:00:00+03:00"))
        XCTAssertEqual(moment.timeIntervalSince1970, 1_795_863_600)
        XCTAssertNil(PhoneTimes.moment("2026-11-28"), "a day is not a moment")
        XCTAssertEqual(PhoneTimes.day("2026-11-28"), DateComponents(year: 2026, month: 11, day: 28))
        XCTAssertNil(PhoneTimes.day("2026-11-28T14:00:00Z"))
        XCTAssertNil(PhoneTimes.day("2026-13-01"))
        let istanbul: TimeZone = try XCTUnwrap(TimeZone(identifier: "Europe/Istanbul"))
        XCTAssertEqual(PhoneTimes.wire(moment, zone: istanbul), "2026-11-28T14:00:00+03:00")
        var calendar: Calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = istanbul
        XCTAssertEqual(PhoneTimes.wireDay(moment, calendar: calendar), "2026-11-28")
    }

    private struct Handler: PhoneAskHandler {
        func answer(_ ask: PhoneAsk) async -> PhoneAnswer {
            switch ask {
            case .location: .location(latitude: 41, longitude: 29, accuracyM: 12, at: "2026-11-28T11:00:00Z")
            default: .refused(.failed, message: "not in this test")
            }
        }
    }

    func testTheLoopTakesAnAskAndAnswersIt() async throws {
        let daemon: FakeDaemon = FakeDaemon(version: "1.0")
        daemon.queueAsk(#"{"id":"ask_9","kind":"location"}"#)
        let loop: PhoneAskLoop = PhoneAskLoop(api: daemon.api(), handler: Handler(), clock: SystemClock())
        await loop.start()
        let began: Date = Date()
        while daemon.phoneAnswers["ask_9"] == nil, Date().timeIntervalSince(began) < 5 { try await Task.sleep(for: .milliseconds(10)) }
        await loop.stop()
        let sent: Data = try XCTUnwrap(daemon.phoneAnswers["ask_9"])
        let body: [String: [String: JSONValue]] = try JSONDecoder().decode([String: [String: JSONValue]].self, from: sent)
        XCTAssertEqual(body["answer"]?["kind"], .string("location"))
        XCTAssertEqual(body["answer"]?["latitude"]?.double ?? Double(body["answer"]?["latitude"]?.int ?? 0), 41)
        let running: Bool = await loop.running
        XCTAssertFalse(running)
    }

    func testAnOlderDaemonStopsTheLoop() async throws {
        let daemon: FakeDaemon = FakeDaemon(version: "1.0")
        daemon.old = true
        let loop: PhoneAskLoop = PhoneAskLoop(api: daemon.api(), handler: Handler(), clock: SystemClock())
        await loop.start()
        let began: Date = Date()
        while await loop.running, Date().timeIntervalSince(began) < 5 { try await Task.sleep(for: .milliseconds(10)) }
        let running: Bool = await loop.running
        XCTAssertFalse(running, "a daemon from before item 2051 answers 404, and the phone stops asking")
    }
}
