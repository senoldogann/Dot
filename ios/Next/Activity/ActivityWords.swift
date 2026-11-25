import DotEngine
import Foundation

// The words of the Activity tab (item 1826), pure functions of what the daemon said, so they are tested alone
// (`ActivityWordsTests`). They say what the web's `awaywords.js`, `briefcard.js`, `checkincard.js`,
// `initiativecard.js` and `watcherspanel.js` say.

/// How a run of Dot's own ended, or that it works: the word and its tone.
enum RunOutcome: Equatable, Sendable {
    case done
    case failed
    case stopped
    case working

    init(status: String) {
        switch status {
        case "done": self = .done
        case "failed": self = .failed
        case "cancelled": self = .stopped
        default: self = .working
        }
    }

    var word: String {
        switch self {
        case .done: String(localized: "Done", comment: "Status word: the run finished")
        case .failed: String(localized: "Failed", comment: "Status word: the run failed")
        case .stopped: String(localized: "Stopped", comment: "Activity: a run of Dot's own that was stopped")
        case .working: String(localized: "Working", comment: "Status word: a run is at work")
        }
    }

    var symbol: String {
        switch self {
        case .done: "checkmark.circle"
        case .failed: "xmark.circle"
        case .stopped: "stop.circle"
        case .working: "circle.dotted"
        }
    }
}

/// Who made the decisions and how many said no: "2 without asking, 1 by the reviewer; 1 denied".
func decidedByWords(yolo: Int, reviewer: Int, denied: Int) -> String {
    var who: [String] = []
    if yolo > 0 { who.append(String(localized: "\(yolo) without asking", comment: "Away card: decisions the mode that asks only to pay or send let through")) }
    if reviewer > 0 { who.append(String(localized: "\(reviewer) by the reviewer", comment: "Away card: decisions the review model made")) }
    let joined: String = who.joined(separator: ", ")
    guard denied > 0 else { return joined }
    return String(localized: "\(joined); \(denied) denied", comment: "Away card: who decided, then how many of the decisions said no")
}

/// What Dot did on its own while the owner was away, in one line; empty when nothing happened (the web's `awayLine`).
func awayLine(_ view: AwayView) -> String {
    var parts: [String] = []
    let own: Int = view.done + view.failed + view.cancelled + view.working
    if own > 0 {
        var how: [String] = []
        if view.done > 0 { how.append(String(localized: "\(view.done) done", comment: "Away card: tasks that ended done")) }
        if view.failed > 0 { how.append(String(localized: "\(view.failed) failed", comment: "Away card: tasks that failed")) }
        if view.cancelled > 0 { how.append(String(localized: "\(view.cancelled) stopped", comment: "Away card: tasks that were stopped")) }
        if view.working > 0 { how.append(String(localized: "\(view.working) working", comment: "Away card: tasks still at work")) }
        parts.append(String(localized: "\(own) tasks: \(how.joined(separator: ", "))", comment: "Away card: how many tasks Dot did on its own, then how they went"))
    }
    if view.campaignSessions > 0 {
        parts.append(String(localized: "\(view.campaignSessions) campaign sessions", comment: "Away card: sessions of campaigns that ran"))
    }
    let decisions: AwayDecisions = view.decisions
    let yolo: Int = decisions.yoloApproved + decisions.yoloDenied
    let reviewer: Int = decisions.reviewerApproved + decisions.reviewerDenied
    if yolo + reviewer > 0 {
        let by: String = decidedByWords(yolo: yolo, reviewer: reviewer, denied: decisions.yoloDenied + decisions.reviewerDenied)
        parts.append(String(localized: "\(yolo + reviewer) decisions made for you (\(by))", comment: "Away card: approvals answered for the owner, then who answered them"))
    }
    if view.drafts > 0 {
        parts.append(String(localized: "\(view.drafts) drafts wait for you", comment: "Away card: drafts the runs wrote that wait for the owner"))
    }
    return parts.joined(separator: " · ")
}

/// "Every day at 07:30" or "Weekdays at 07:30".
func daysAtWords(days: String, time: String) -> String {
    days == "weekdays"
        ? String(localized: "Weekdays at \(time)", comment: "Schedule rhythm; the argument is one or more clock times")
        : String(localized: "Every day at \(time)", comment: "Schedule rhythm; the argument is one or more clock times")
}

/// The morning brief's line: when it is written, or that it is off; a schedule changed to times the card cannot show
/// is said so, not given the card's time.
func briefLine(_ view: BriefView) -> String {
    guard view.enabled else { return String(localized: "Off", comment: "VoiceOver value of a switch that is off") }
    if view.custom { return String(localized: "On, at times set on your Mac", comment: "Morning brief: its schedule runs at times the phone cannot show") }
    return daysAtWords(days: view.days, time: view.time)
}

/// The evening check-in's line.
func checkinLine(_ view: CheckinView) -> String {
    guard view.enabled else {
        return String(localized: "Off: what got done, what waits for you and the next step", comment: "Evening check-in: what it writes, while it is off")
    }
    return daysAtWords(days: view.days, time: view.time)
}

/// Dot's own initiative in a few words: "Off", "On · nothing to do", "On · 2 to do".
func initiativeLine(_ view: InitiativeView) -> String {
    guard view.enabled else { return String(localized: "Off", comment: "VoiceOver value of a switch that is off") }
    let waiting: Int = view.waiting
    return waiting == 0
        ? String(localized: "On · nothing to do", comment: "Own initiative: on, with nothing on its list")
        : String(localized: "On · \(waiting) to do", comment: "Own initiative: on, with things on its list")
}

/// The approval mode Dot's own runs start in, as the card says it.
func initiativeModeWords(_ approvals: String) -> String {
    approvals == "yolo"
        ? String(localized: "Its tasks ask you only before a message, a post or a release.", comment: "Own initiative: the mode its tasks start in is yolo")
        : String(localized: "Its tasks start in the \(approvalWords(approvals)) approval mode.", comment: "Own initiative: the approval mode its tasks start in")
}

/// A thing on Dot's list: its state's word and mark.
func todoStateWords(_ status: String) -> (word: String, symbol: String) {
    switch status {
    case "doing": (String(localized: "Doing now", comment: "Own initiative: a thing being done"), "play.circle")
    case "done": (String(localized: "Done", comment: "Status word: the run finished"), "checkmark.circle")
    case "dropped": (String(localized: "Let go", comment: "Own initiative: a thing taken off the list"), "xmark.circle")
    default: (String(localized: "To do", comment: "Own initiative: a thing to do"), "circle")
    }
}

/// What a thing's row says under its title: its state, its tries, why, and what came of it.
func todoLine(_ todo: InitiativeTodo) -> String {
    var parts: [String] = [todoStateWords(todo.status).word]
    if todo.attempts > 0 && todo.status != "done" {
        parts.append(String(localized: "\(todo.attempts) tries", comment: "Own initiative: how many times Dot tried a thing"))
    }
    if !todo.why.isEmpty { parts.append(todo.why) }
    if !todo.note.isEmpty { parts.append(todo.note) }
    return parts.joined(separator: " · ")
}

/// A watcher's kind: its word and mark.
func watcherKindWords(_ kind: String) -> (word: String, symbol: String) {
    switch kind {
    case "calendar": (String(localized: "Calendar", comment: "Watcher kind: an event about to start"), "calendar")
    case "github": (String(localized: "GitHub", comment: "Watcher kind: GitHub notifications"), "arrow.triangle.branch")
    default: (String(localized: "New mail", comment: "Watcher kind: new mail in a signed-in account"), "envelope")
    }
}

/// A watcher's line under its name: its kind, how often it looks, what it does, and how its last look went.
func watcherLine(_ view: WatcherView, now: Date, locale: Locale) -> String {
    let relative: RelativeDateTimeFormatter = RelativeDateTimeFormatter()
    relative.locale = locale
    relative.unitsStyle = .short
    var parts: [String] = [watcherKindWords(view.kind).word]
    if !view.account.isEmpty { parts.append(view.account) }
    parts.append(
        view.kind == "calendar"
            ? String(localized: "\(view.minutes) minutes before an event", comment: "Watcher: how long before an event a calendar watcher tells")
            : String(localized: "Looks every \(view.minutes) minutes", comment: "Watcher: how often it looks"))
    if view.action == "run" { parts.append(String(localized: "Starts a task", comment: "Watcher: it starts a task of the owner's goal for what came in")) }
    if let looked = view.lookedAt {
        parts.append(
            String(
                localized: "Looked \(relative.localizedString(for: Date(timeIntervalSince1970: looked), relativeTo: now))",
                comment: "Watcher: when it last looked (relative, e.g. '5 min ago')"))
    } else if view.error == nil {
        parts.append(String(localized: "Has not looked yet", comment: "Watcher: it never looked"))
    }
    if view.told > 0 { parts.append(String(localized: "\(view.told) told this hour", comment: "Watcher: tells in the last hour")) }
    return parts.joined(separator: " · ")
}

/// A row of the activity list: its mark and the line under its title, by its kind and section.
func activityLine(_ row: ActivityRow, now: Date, locale: Locale) -> String {
    let clock: String = Date(timeIntervalSince1970: row.since).formatted(.dateTime.hour().minute().locale(locale))
    switch row.kind {
    case "approval":
        return String(localized: "Waits for your yes", comment: "Activity: an approval a task waits for")
    case "question":
        return String(localized: "Waits for your answer", comment: "Activity: a question a task waits for")
    case "takeover":
        return String(localized: "You have its browser", comment: "Activity: a task whose browser the owner took over")
    case "schedule" where row.section == "due":
        return String(localized: "At \(clock)", comment: "Activity: when a schedule fires later today")
    case "campaign" where row.section == "due":
        return String(localized: "Next session at \(clock)", comment: "Activity: when a campaign's next session starts")
    case "lesson":
        return String(localized: "A lesson", comment: "Activity: a lesson Dot wrote today")
    case "journal":
        return String(localized: "The journal", comment: "Activity: the journal note Dot wrote today")
    case "reflection":
        return row.status == "failed"
            ? String(localized: "The reflection failed", comment: "Activity: today's reflection failed")
            : String(localized: "The reflection", comment: "Activity: today's reflection")
    default:
        break
    }
    var parts: [String] = []
    if row.section == "working" {
        switch row.kind {
        case "delegated": parts.append(String(localized: "A helper of another task", comment: "Activity: a run another run delegated"))
        case "campaign": parts.append(String(localized: "A session of a campaign", comment: "Activity: a run that is a campaign's session"))
        default: parts.append(String(localized: "Working", comment: "Status word: a run is at work"))
        }
    } else {
        parts.append(RunOutcome(status: row.status).word)
        if let minutes = row.minutes {
            parts.append(durationWords(Int(max(1, (minutes * 60).rounded())), locale: locale))
        }
    }
    if let steps = row.steps, steps > 0 { parts.append(String(localized: "\(steps) steps", comment: "Activity: how many steps a task took")) }
    if row.section != "working" { parts.append(clock) }
    return parts.joined(separator: " · ")
}

/// The mark of a row of the activity list.
func activitySymbol(_ row: ActivityRow) -> String {
    switch row.kind {
    case "approval": "hand.raised"
    case "question": "questionmark.bubble"
    case "takeover": "hand.point.up.left"
    case "schedule": "clock"
    case "campaign": "flag"
    case "delegated": "person.2"
    case "lesson": "lightbulb"
    case "journal": "book.closed"
    case "reflection": "sparkles"
    default: row.section == "working" ? "circle.dotted" : RunOutcome(status: row.status).symbol
    }
}

/// The run a row opens: its own, or the one its link names (a lesson opens the run it came from); `nil` for a row that
/// opens no chat (a schedule due, the journal, the reflection).
func runOpened(by row: ActivityRow) -> String? {
    if let run = row.runId { return run }
    let prefix: String = "#/runs/"
    guard row.href.hasPrefix(prefix) else { return nil }
    let run: String = String(row.href.dropFirst(prefix.count))
    return run.isEmpty ? nil : run
}

/// A draft's title: its subject, else the first line of its words.
func draftTitle(_ draft: DraftView) -> String {
    if !draft.subject.isEmpty { return draft.subject }
    return draft.body.split(separator: "\n", omittingEmptySubsequences: true).first.map(String.init) ?? ""
}

/// A draft's line: to whom, by which app, and whether its last send failed.
func draftLine(_ draft: DraftView) -> String {
    let app: String =
        draft.channel == "message"
        ? String(localized: "Message", comment: "Draft: an iMessage") : String(localized: "Mail", comment: "Draft: an email")
    let to: String = String(localized: "To \(draft.to.joined(separator: ", "))", comment: "Draft: its recipients")
    var parts: [String] = [app, to]
    if draft.status == "failed" { parts.append(String(localized: "Send failed", comment: "Draft: its last send failed")) }
    return parts.joined(separator: " · ")
}

/// A proposal of Self-improvement: its state's word ("New" or "Tried") and who made it.
func proposalLine(_ version: HarnessVersion, outside: Bool, refused: Bool, locale: Locale) -> String {
    var parts: [String] = [
        version.state == "probed"
            ? String(localized: "Tried", comment: "Self-improvement: a version the nightly loop tried")
            : String(localized: "New", comment: "Self-improvement: a version nobody tried yet")
    ]
    switch version.madeBy {
    case "owner": parts.append(String(localized: "Written by you", comment: "Self-improvement: a version the owner wrote"))
    case "composer": parts.append(String(localized: "Combined by Dot", comment: "Self-improvement: a version Dot combined from others"))
    default: parts.append(String(localized: "Proposed by Dot", comment: "Self-improvement: a version Dot proposed"))
    }
    parts.append(Date(timeIntervalSince1970: version.createdAt).formatted(.dateTime.day().month(.abbreviated).locale(locale)))
    if outside { parts.append(String(localized: "From outside content", comment: "Self-improvement: a line may come from content others can write")) }
    if refused { parts.append(String(localized: "Refused", comment: "Self-improvement: the checks refuse the version's text")) }
    return parts.joined(separator: " · ")
}

/// The points of a text of a version: a line a point, without the dash it starts with.
func points(of text: String) -> [String] {
    text.split(separator: "\n").map { line in
        var point: Substring = line.drop { $0 == " " || $0 == "\t" }
        if let first = point.first, "-*•".contains(first), point.dropFirst().first == " " { point = point.dropFirst(2) }
        return point.trimmingCharacters(in: .whitespaces)
    }
    .filter { !$0.isEmpty }
}

/// What one agent did of `view`: its runs and the rows its face wears (an agent's profile shows only its own work).
func scoped(_ view: ActivityView, to agent: String) -> ActivityView {
    func own(_ rows: [ActivityRow]) -> [ActivityRow] { rows.filter { $0.actorId == agent } }
    return ActivityView(
        waiting: own(view.waiting), working: own(view.working), endedDelegated: own(view.endedDelegated), recent: own(view.recent),
        learnedToday: own(view.learnedToday), due: own(view.due), generatedAt: view.generatedAt)
}

/// "While you were away" of one agent: its runs among the newest the daemon listed, counted again; the decisions and
/// the drafts, which the daemon counts for all of Dot, are left out.
func scoped(_ view: AwayView, to agent: String) -> AwayView {
    let runs: [AwayRun] = view.runs.filter { $0.agentId == agent }
    let outcomes: [RunOutcome] = runs.map { RunOutcome(status: $0.status) }
    return AwayView(
        since: view.since, until: view.until, done: outcomes.filter { $0 == .done }.count, failed: outcomes.filter { $0 == .failed }.count,
        cancelled: outcomes.filter { $0 == .stopped }.count, working: outcomes.filter { $0 == .working }.count,
        campaignSessions: runs.filter(\.campaign).count, runs: runs,
        decisions: AwayDecisions(reviewerApproved: 0, reviewerDenied: 0, yoloApproved: 0, yoloDenied: 0), decisionRuns: [], drafts: 0)
}
