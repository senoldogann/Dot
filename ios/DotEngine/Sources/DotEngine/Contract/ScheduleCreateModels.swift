import Foundation

/// When a new schedule runs, as the phone's form offers it: the web form's presets (every hour, daily, weekdays, one
/// weekday), a single time, or a cron form typed by hand.
public enum ScheduleRhythm: Sendable, Hashable {
    case hourly
    case daily(hour: Int, minute: Int)
    case weekdays(hour: Int, minute: Int)
    /// `weekday` is cron's: 0 Sunday … 6 Saturday.
    case weekly(weekday: Int, hour: Int, minute: Int)
    /// A single run at an exact moment.
    case once(Date)
    /// Five fields typed by hand; the daemon checks them (and refuses one too frequent).
    case custom(String)

    /// The cron form the daemon takes, `nil` for a single run (pure).
    public var cron: String? {
        switch self {
        case .hourly: "0 * * * *"
        case .daily(let hour, let minute): "\(minute) \(hour) * * *"
        case .weekdays(let hour, let minute): "\(minute) \(hour) * * 1-5"
        case .weekly(let weekday, let hour, let minute): "\(minute) \(hour) * * \(weekday)"
        case .once: nil
        case .custom(let text): text.split(separator: " ", omittingEmptySubsequences: true).joined(separator: " ")
        }
    }

    /// The daemon's `at` for a single run: ISO 8601 with the phone's offset, so the moment is the one the owner picked
    /// whatever the Mac's zone (the daemon reads a time without an offset in the Mac's zone); `nil` for a repeat (pure).
    public func at(in zone: TimeZone) -> String? {
        guard case .once(let date) = self else { return nil }
        let formatter: ISO8601DateFormatter = ISO8601DateFormatter()
        formatter.timeZone = zone
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}

/// The body of `POST /api/schedules` (`ScheduleCreateRequest`): a key left `nil` is left out, and the daemon takes the
/// configured choice (or the agent's) when the schedule fires.
public struct ScheduleCreate: Codable, Sendable, Hashable {
    public let name: String
    public let goal: String
    public let cron: String?
    public let at: String?
    /// `task` or `research`.
    public let mode: String
    public let model: String?
    public let effort: String?
    public let approvals: String?
    public let goalMode: Bool?
    public let agentId: String?

    public init(name: String, goal: String, rhythm: ScheduleRhythm, zone: TimeZone, mode: String, model: String?, effort: String?, approvals: String?, goalMode: Bool?, agentId: String?) {
        self.name = name
        self.goal = goal
        self.cron = rhythm.cron
        self.at = rhythm.at(in: zone)
        self.mode = mode
        self.model = model
        self.effort = effort
        self.approvals = approvals
        self.goalMode = goalMode
        self.agentId = agentId
    }

    enum CodingKeys: String, CodingKey {
        case name, goal, cron, at, mode, model, effort, approvals
        case goalMode = "goal_mode"
        case agentId = "agent_id"
    }
}

/// `GET /api/schedules/{id}/firings`: a run the schedule started, at its time or by hand, and the title of its chat.
public struct ScheduleFiring: Codable, Sendable, Hashable, Identifiable {
    public let run: RunRecord
    public let chatTitle: String

    public var id: String { run.id }

    public init(run: RunRecord, chatTitle: String) {
        self.run = run
        self.chatTitle = chatTitle
    }

    enum CodingKeys: String, CodingKey {
        case run
        case chatTitle = "chat_title"
    }
}
