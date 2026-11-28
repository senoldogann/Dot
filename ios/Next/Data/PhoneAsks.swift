import CoreLocation
import DotEngine
import EventKit
import Foundation
import os

/// Does Dot's asks on this iPhone (item 2051): reads its calendars and adds an event through EventKit, adds a reminder,
/// and says where it is through Core Location (when in use). Each permission is asked by iOS the first time an ask needs
/// it, never at launch; a refusal is answered as one, with words the run reads. The engine's `PhoneAskLoop` brings the
/// asks while the app is in front.
actor PhoneAsks: PhoneAskHandler {
    private let store: EKEventStore = EKEventStore()
    /// The longest a look for the phone's place waits for a fix (and the owner's answer to the permission).
    private static let locationWait: Double = 30
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "asks")

    func answer(_ ask: PhoneAsk) async -> PhoneAnswer {
        switch ask {
        case .calendarEvents(let events): await readEvents(events)
        case .calendarAdd(let add): await addEvent(add)
        case .reminderAdd(let add): await addReminder(add)
        case .location: await locate()
        }
    }

    // MARK: Permissions

    /// The phone's full access to `type`, asked of the owner the first time; a refusal when it is not given.
    private func access(_ type: EKEntityType) async -> PhoneAnswer? {
        let what: String = type == .event ? "the calendars" : "Reminders"
        switch EKEventStore.authorizationStatus(for: type) {
        case .fullAccess:
            return nil
        case .restricted:
            return .refused(.restricted, message: "This iPhone's restrictions keep Dot Next from \(what).")
        case .denied, .writeOnly:
            return .refused(.denied, message: "The owner has not allowed Dot Next full access to \(what).")
        case .notDetermined:
            do {
                let given: Bool = type == .event ? try await store.requestFullAccessToEvents() : try await store.requestFullAccessToReminders()
                return given ? nil : .refused(.denied, message: "The owner did not allow Dot Next access to \(what).")
            } catch {
                logger.error("access request failed type=\(type.rawValue, privacy: .public) error=\(String(describing: error), privacy: .public)")
                return .refused(.failed, message: "iOS could not ask for access to \(what): \(error.localizedDescription)")
            }
        @unknown default:
            return .refused(.failed, message: "iOS answered an access state this app does not know.")
        }
    }

    // MARK: Calendars

    private func readEvents(_ ask: CalendarEventsAsk) async -> PhoneAnswer {
        if let refused: PhoneAnswer = await access(.event) { return refused }
        guard let start: Date = PhoneTimes.moment(ask.start), let end: Date = PhoneTimes.moment(ask.end), end > start else {
            return .refused(.failed, message: "The time asked for is not one: \(ask.start) to \(ask.end).")
        }
        let words: String = ask.query.trimmingCharacters(in: .whitespacesAndNewlines)
        let found: [EKEvent] = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: nil))
            .filter { event in
                words.isEmpty || [event.title, event.location, event.notes].contains { ($0 ?? "").localizedStandardContains(words) }
            }
            .sorted { $0.startDate < $1.startDate }
        let limit: Int = max(1, min(ask.limit, PhoneAskRules.mostEvents))
        return .events(found.prefix(limit).map(Self.phoneEvent), more: found.count > limit)
    }

    private static func phoneEvent(_ event: EKEvent) -> PhoneEvent {
        let calendar: Calendar = Calendar.current
        let start: String
        let end: String
        if event.isAllDay {
            start = PhoneTimes.wireDay(event.startDate, calendar: calendar)
            // EventKit ends an all-day event on its last day; the contract ends it on the day after.
            let after: Date = calendar.date(byAdding: .day, value: 1, to: event.endDate) ?? event.endDate
            end = PhoneTimes.wireDay(after, calendar: calendar)
        } else {
            start = PhoneTimes.wire(event.startDate, zone: event.timeZone ?? calendar.timeZone)
            end = PhoneTimes.wire(event.endDate, zone: event.timeZone ?? calendar.timeZone)
        }
        return PhoneEvent(
            id: event.eventIdentifier ?? event.calendarItemIdentifier, title: event.title ?? "", start: start, end: end, allDay: event.isAllDay,
            location: event.location ?? "", notes: event.notes ?? "", calendar: event.calendar?.title ?? "", organizer: event.organizer?.name ?? "")
    }

    private func addEvent(_ ask: CalendarAddAsk) async -> PhoneAnswer {
        if let refused: PhoneAnswer = await access(.event) { return refused }
        let calendar: Calendar = Calendar.current
        let start: Date
        let end: Date
        if ask.allDay {
            guard let first: DateComponents = PhoneTimes.day(ask.start), let after: DateComponents = PhoneTimes.day(ask.end),
                let begins: Date = calendar.date(from: first), let ends: Date = calendar.date(from: after), ends > begins
            else { return .refused(.failed, message: "The days asked for are not ones: \(ask.start) to \(ask.end).") }
            start = begins
            // The last moment of the last day: EventKit's all-day event ends on its last day.
            end = ends.addingTimeInterval(-1)
        } else {
            guard let begins: Date = PhoneTimes.moment(ask.start), let ends: Date = PhoneTimes.moment(ask.end), ends > begins else {
                return .refused(.failed, message: "The time asked for is not one: \(ask.start) to \(ask.end).")
            }
            start = begins
            end = ends
        }
        let target: EKCalendar?
        if ask.calendar.isEmpty {
            target = store.defaultCalendarForNewEvents
        } else {
            target = store.calendars(for: .event).first { $0.title.localizedCaseInsensitiveCompare(ask.calendar) == .orderedSame && $0.allowsContentModifications }
        }
        guard let target else {
            let names: String = store.calendars(for: .event).filter(\.allowsContentModifications).map(\.title).joined(separator: ", ")
            return .refused(.failed, message: ask.calendar.isEmpty ? "This iPhone has no calendar for new events." : "No calendar named “\(ask.calendar)” takes events; those that do: \(names).")
        }
        let event: EKEvent = EKEvent(eventStore: store)
        event.title = ask.title
        event.startDate = start
        event.endDate = end
        event.isAllDay = ask.allDay
        event.location = ask.location.isEmpty ? nil : ask.location
        event.notes = ask.notes.isEmpty ? nil : ask.notes
        event.calendar = target
        do {
            try store.save(event, span: .thisEvent, commit: true)
        } catch {
            logger.error("event not saved error=\(String(describing: error), privacy: .public)")
            return .refused(.failed, message: "The event was not saved: \(error.localizedDescription)")
        }
        return .created(id: event.eventIdentifier ?? event.calendarItemIdentifier, calendar: target.title)
    }

    // MARK: Reminders

    private func addReminder(_ ask: ReminderAddAsk) async -> PhoneAnswer {
        if let refused: PhoneAnswer = await access(.reminder) { return refused }
        let target: EKCalendar?
        if ask.list.isEmpty {
            target = store.defaultCalendarForNewReminders()
        } else {
            target = store.calendars(for: .reminder).first { $0.title.localizedCaseInsensitiveCompare(ask.list) == .orderedSame && $0.allowsContentModifications }
        }
        guard let target else {
            let names: String = store.calendars(for: .reminder).filter(\.allowsContentModifications).map(\.title).joined(separator: ", ")
            return .refused(.failed, message: ask.list.isEmpty ? "This iPhone has no list for new reminders." : "No list named “\(ask.list)”; the lists: \(names).")
        }
        let reminder: EKReminder = EKReminder(eventStore: store)
        reminder.title = ask.title
        reminder.notes = ask.notes.isEmpty ? nil : ask.notes
        reminder.calendar = target
        if let due: String = ask.due {
            let calendar: Calendar = Calendar.current
            if var day: DateComponents = PhoneTimes.day(due) {
                day.calendar = calendar
                reminder.dueDateComponents = day
            } else if let moment: Date = PhoneTimes.moment(due) {
                var parts: DateComponents = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: moment)
                parts.calendar = calendar
                reminder.dueDateComponents = parts
                // A due moment alerts at it, as a reminder the owner sets with a time does.
                reminder.addAlarm(EKAlarm(absoluteDate: moment))
            } else {
                return .refused(.failed, message: "The due time is not one: \(due).")
            }
        }
        do {
            try store.save(reminder, commit: true)
        } catch {
            logger.error("reminder not saved error=\(String(describing: error), privacy: .public)")
            return .refused(.failed, message: "The reminder was not saved: \(error.localizedDescription)")
        }
        return .created(id: reminder.calendarItemIdentifier, calendar: target.title)
    }

    // MARK: Location

    private func locate() async -> PhoneAnswer {
        await withTaskGroup(of: PhoneAnswer.self) { group in
            group.addTask { await Self.firstFix() }
            group.addTask {
                do {
                    try await Task.sleep(for: .seconds(Self.locationWait))
                } catch {
                    // The fix came first and this wait was cancelled: its answer is never read.
                    return .refused(.failed, message: "cancelled")
                }
                return .refused(.failed, message: "No location fix in \(Int(Self.locationWait)) s (indoors, or the permission question is still open).")
            }
            let first: PhoneAnswer = await group.next() ?? .refused(.failed, message: "No location fix.")
            group.cancelAll()
            return first
        }
    }

    /// The first fix of the phone's place, holding a when-in-use session so iOS asks the owner the first time.
    private static func firstFix() async -> PhoneAnswer {
        let session: CLServiceSession = CLServiceSession(authorization: .whenInUse)
        defer { session.invalidate() }
        do {
            for try await update in CLLocationUpdate.liveUpdates() {
                if update.authorizationRestricted {
                    return .refused(.restricted, message: "This iPhone's restrictions keep Dot Next from its location.")
                }
                if update.authorizationDenied || update.authorizationDeniedGlobally {
                    return .refused(.denied, message: "The owner has not allowed Dot Next to use the location while it is open.")
                }
                if let location: CLLocation = update.location, location.horizontalAccuracy >= 0 {
                    return .location(
                        latitude: location.coordinate.latitude, longitude: location.coordinate.longitude, accuracyM: location.horizontalAccuracy,
                        at: PhoneTimes.wire(location.timestamp, zone: .current))
                }
            }
        } catch {
            return .refused(.failed, message: "Core Location failed: \(error.localizedDescription)")
        }
        return .refused(.failed, message: "Core Location ended with no fix.")
    }
}
