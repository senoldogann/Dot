import Foundation

// What Dot asks of this phone and what the phone answers (item 2051, `docs/phone.md` "Dot's asks to the phone"):
// `GET /api/phone/asks/next` gives the next ask, `POST /api/phone/asks/{id}` takes its answer. The daemon's models are
// `dotagent.phone.asks`.

/// The events of the phone's calendars that overlap `start` to `end` (ISO 8601 moments with their offset), those with
/// `query` in their title, place or notes when it is not empty, at most `limit`.
public struct CalendarEventsAsk: Codable, Sendable, Equatable {
    public let id: String
    public let start: String
    public let end: String
    public let query: String
    public let limit: Int
}

/// An event to add to the default calendar for new events, or to the one named `calendar`. Timed: `start` and `end`
/// are ISO 8601 moments. All day: they are days (`2026-11-28`), `end` the day after its last.
public struct CalendarAddAsk: Codable, Sendable, Equatable {
    public let id: String
    public let title: String
    public let start: String
    public let end: String
    public let allDay: Bool
    public let location: String
    public let notes: String
    public let calendar: String

    enum CodingKeys: String, CodingKey {
        case id, title, start, end, location, notes, calendar
        case allDay = "all_day"
    }
}

/// A reminder to add to the default list, or to the one named `list`; `due` is a moment, a day, or `nil`.
public struct ReminderAddAsk: Codable, Sendable, Equatable {
    public let id: String
    public let title: String
    public let due: String?
    public let notes: String
    public let list: String
}

/// One ask of the daemon's, by its `kind`.
public enum PhoneAsk: Decodable, Sendable, Equatable {
    case calendarEvents(CalendarEventsAsk)
    case calendarAdd(CalendarAddAsk)
    case reminderAdd(ReminderAddAsk)
    case location(id: String)

    public var id: String {
        switch self {
        case .calendarEvents(let ask): ask.id
        case .calendarAdd(let ask): ask.id
        case .reminderAdd(let ask): ask.id
        case .location(let id): id
        }
    }

    private enum Keys: String, CodingKey { case id, kind }

    public init(from decoder: any Decoder) throws {
        let keyed: KeyedDecodingContainer<Keys> = try decoder.container(keyedBy: Keys.self)
        let kind: String = try keyed.decode(String.self, forKey: .kind)
        switch kind {
        case "calendar_events": self = .calendarEvents(try CalendarEventsAsk(from: decoder))
        case "calendar_add": self = .calendarAdd(try CalendarAddAsk(from: decoder))
        case "reminder_add": self = .reminderAdd(try ReminderAddAsk(from: decoder))
        case "location": self = .location(id: try keyed.decode(String.self, forKey: .id))
        default:
            throw DecodingError.dataCorruptedError(forKey: .kind, in: keyed, debugDescription: "an ask of a kind this app does not know: \(kind)")
        }
    }
}

/// An event as the phone read it: timed with ISO 8601 moments, or all day with days, `end` the day after its last.
public struct PhoneEvent: Codable, Sendable, Equatable {
    public let id: String
    public let title: String
    public let start: String
    public let end: String
    public let allDay: Bool
    public let location: String
    public let notes: String
    public let calendar: String
    public let organizer: String

    public init(id: String, title: String, start: String, end: String, allDay: Bool, location: String, notes: String, calendar: String, organizer: String) {
        self.id = String(id.prefix(PhoneAskRules.idChars))
        self.title = String(title.prefix(PhoneAskRules.textChars))
        self.start = start
        self.end = end
        self.allDay = allDay
        self.location = String(location.prefix(PhoneAskRules.textChars))
        self.notes = String(notes.prefix(PhoneAskRules.textChars))
        self.calendar = String(calendar.prefix(PhoneAskRules.idChars))
        self.organizer = String(organizer.prefix(PhoneAskRules.idChars))
    }

    enum CodingKeys: String, CodingKey {
        case id, title, start, end, location, notes, calendar, organizer
        case allDay = "all_day"
    }
}

/// Why the phone did not do an ask: the owner said no to the permission (or took it back), the phone's restrictions
/// forbid it, or it tried and could not.
public enum PhoneRefusal: String, Codable, Sendable {
    case denied
    case restricted
    case failed
}

/// The phone's answer to an ask.
public enum PhoneAnswer: Encodable, Sendable, Equatable {
    case events([PhoneEvent], more: Bool)
    case created(id: String, calendar: String)
    case location(latitude: Double, longitude: Double, accuracyM: Double, at: String)
    case refused(PhoneRefusal, message: String)

    private enum Keys: String, CodingKey {
        case kind, events, more, id, calendar, latitude, longitude, at, reason, message
        case accuracyM = "accuracy_m"
    }

    public func encode(to encoder: any Encoder) throws {
        var keyed: KeyedEncodingContainer<Keys> = encoder.container(keyedBy: Keys.self)
        switch self {
        case .events(let events, let more):
            try keyed.encode("events", forKey: .kind)
            try keyed.encode(Array(events.prefix(PhoneAskRules.mostEvents)), forKey: .events)
            try keyed.encode(more || events.count > PhoneAskRules.mostEvents, forKey: .more)
        case .created(let id, let calendar):
            try keyed.encode("created", forKey: .kind)
            try keyed.encode(String(id.prefix(PhoneAskRules.idChars)), forKey: .id)
            try keyed.encode(String(calendar.prefix(PhoneAskRules.idChars)), forKey: .calendar)
        case .location(let latitude, let longitude, let accuracyM, let at):
            try keyed.encode("location", forKey: .kind)
            try keyed.encode(latitude, forKey: .latitude)
            try keyed.encode(longitude, forKey: .longitude)
            try keyed.encode(max(0, accuracyM), forKey: .accuracyM)
            try keyed.encode(at, forKey: .at)
        case .refused(let reason, let message):
            try keyed.encode("refused", forKey: .kind)
            try keyed.encode(reason, forKey: .reason)
            try keyed.encode(String(message.prefix(PhoneAskRules.messageChars)), forKey: .message)
        }
    }
}

/// The body of `POST /api/phone/asks/{id}`.
struct PhoneAnswerBody: Encodable {
    let answer: PhoneAnswer
}

/// The daemon's limits on an answer (`dotagent.phone.asks`): what is longer is cut here rather than refused there.
public enum PhoneAskRules {
    public static let mostEvents: Int = 100
    public static let textChars: Int = 4000
    public static let idChars: Int = 500
    public static let messageChars: Int = 1000
}

/// The times of an ask and of an answer as the daemon writes them: a moment is ISO 8601 with its offset, a day is
/// `yyyy-MM-dd` in the phone's own calendar. Pure.
public enum PhoneTimes {
    /// The moment `text` names, or `nil` when it is not one (a day is not).
    public static func moment(_ text: String) -> Date? {
        let formatter: ISO8601DateFormatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        if let found: Date = formatter.date(from: text) { return found }
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: text)
    }

    /// The day `text` names (`2026-11-28`), as its year, month and day, or `nil`.
    public static func day(_ text: String) -> DateComponents? {
        let parts: [Int] = text.split(separator: "-").compactMap { Int($0) }
        guard text.count == 10, parts.count == 3, (1...12).contains(parts[1]), (1...31).contains(parts[2]) else { return nil }
        return DateComponents(year: parts[0], month: parts[1], day: parts[2])
    }

    /// `date` as a moment with the offset of `zone`.
    public static func wire(_ date: Date, zone: TimeZone) -> String {
        let formatter: ISO8601DateFormatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = zone
        return formatter.string(from: date)
    }

    /// The day `date` falls on in `calendar`, as `yyyy-MM-dd`.
    public static func wireDay(_ date: Date, calendar: Calendar) -> String {
        let parts: DateComponents = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}
