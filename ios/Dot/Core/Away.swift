import Foundation

/// A run Dot did on its own while the owner was away (`GET /api/away`, items 1356 and 1424): what a schedule, a campaign, a
/// watcher, Dot's own initiative, an agent's work or a command that ended in the background did.
struct AwayRun: Codable, Equatable, Hashable, Sendable, Identifiable {
    let runId: String
    /// The name of the campaign or the schedule, else of the chat, else the first line of the goal.
    let title: String
    /// What started it: `schedule`, `campaign`, `background`, `team`, `watcher`, `initiative`…
    let source: String
    /// `done`, `failed`, `cancelled`, or a state of a run that works (`queued`, `running`, `blocked`, `ending`).
    let status: String
    let steps: Int
    let createdAt: Double
    let finishedAt: Double?
    /// The first line of its answer, secrets masked (200 characters at most).
    let answer: String?

    var id: String { runId }

    enum CodingKeys: String, CodingKey {
        case title, source, status, steps, answer
        case runId = "run_id"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
    }

    init(runId: String, title: String, source: String, status: String, steps: Int = 0, createdAt: Double, finishedAt: Double? = nil, answer: String? = nil) {
        self.runId = runId
        self.title = title
        self.source = source
        self.status = status
        self.steps = steps
        self.createdAt = createdAt
        self.finishedAt = finishedAt
        self.answer = answer
    }

    /// A key the daemon leaves out (a field a later daemon adds, or one an older one lacks) reads as none, not as a failure
    /// of the whole card.
    init(from decoder: any Decoder) throws {
        let container: KeyedDecodingContainer<CodingKeys> = try decoder.container(keyedBy: CodingKeys.self)
        runId = try container.decode(String.self, forKey: .runId)
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
        source = try container.decodeIfPresent(String.self, forKey: .source) ?? ""
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "done"
        steps = try container.decodeIfPresent(Int.self, forKey: .steps) ?? 0
        createdAt = try container.decodeIfPresent(Double.self, forKey: .createdAt) ?? 0
        finishedAt = try container.decodeIfPresent(Double.self, forKey: .finishedAt)
        answer = try container.decodeIfPresent(String.self, forKey: .answer)
    }
}

/// What Dot did on its own since a moment (`GET /api/away?since=`): the counts of every such run, the newest fifty, and the
/// approvals Dot answered for the owner in them. `until` is when it was read, the mark that dismissing the card sets.
struct AwayView: Codable, Equatable, Sendable {
    var since: Double
    var until: Double
    var done: Int
    var failed: Int
    var cancelled: Int
    var working: Int
    var campaignSessions: Int
    var runs: [AwayRun]
    /// How many approvals the review model and `yolo` answered for the owner in those runs (a yes or a no).
    var decided: Int

    enum CodingKeys: String, CodingKey {
        case since, until, done, failed, cancelled, working, runs, decisions
        case campaignSessions = "campaign_sessions"
    }

    init(
        since: Double, until: Double, done: Int = 0, failed: Int = 0, cancelled: Int = 0, working: Int = 0, campaignSessions: Int = 0,
        runs: [AwayRun] = [], decided: Int = 0
    ) {
        self.since = since
        self.until = until
        self.done = done
        self.failed = failed
        self.cancelled = cancelled
        self.working = working
        self.campaignSessions = campaignSessions
        self.runs = runs
        self.decided = decided
    }

    init(from decoder: any Decoder) throws {
        let container: KeyedDecodingContainer<CodingKeys> = try decoder.container(keyedBy: CodingKeys.self)
        since = try container.decode(Double.self, forKey: .since)
        until = try container.decode(Double.self, forKey: .until)
        done = try container.decodeIfPresent(Int.self, forKey: .done) ?? 0
        failed = try container.decodeIfPresent(Int.self, forKey: .failed) ?? 0
        cancelled = try container.decodeIfPresent(Int.self, forKey: .cancelled) ?? 0
        working = try container.decodeIfPresent(Int.self, forKey: .working) ?? 0
        campaignSessions = try container.decodeIfPresent(Int.self, forKey: .campaignSessions) ?? 0
        runs = try container.decodeIfPresent([Maybe].self, forKey: .runs)?.compactMap(\.run) ?? []
        decided = (try container.decodeIfPresent(Decisions.self, forKey: .decisions))?.total ?? 0
    }

    func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(since, forKey: .since)
        try container.encode(until, forKey: .until)
        try container.encode(done, forKey: .done)
        try container.encode(failed, forKey: .failed)
        try container.encode(cancelled, forKey: .cancelled)
        try container.encode(working, forKey: .working)
        try container.encode(campaignSessions, forKey: .campaignSessions)
        try container.encode(runs, forKey: .runs)
        try container.encode(Decisions(total: decided), forKey: .decisions)
    }

    /// A run that does not read is left out of the card, not the whole card (the counts still say how many).
    private struct Maybe: Decodable {
        let run: AwayRun?
        init(from decoder: any Decoder) throws { run = try? AwayRun(from: decoder) }
    }

    /// The daemon's four counts of approvals it answered, added; written back as one so that what the phone keeps reads again.
    private struct Decisions: Codable {
        var total: Int

        enum CodingKeys: String, CodingKey {
            case total
            case reviewerApproved = "reviewer_approved"
            case reviewerDenied = "reviewer_denied"
            case yoloApproved = "yolo_approved"
            case yoloDenied = "yolo_denied"
        }

        init(total: Int) { self.total = total }

        init(from decoder: any Decoder) throws {
            let container: KeyedDecodingContainer<CodingKeys> = try decoder.container(keyedBy: CodingKeys.self)
            let named: Int =
                (try container.decodeIfPresent(Int.self, forKey: .reviewerApproved) ?? 0)
                + (try container.decodeIfPresent(Int.self, forKey: .reviewerDenied) ?? 0)
                + (try container.decodeIfPresent(Int.self, forKey: .yoloApproved) ?? 0)
                + (try container.decodeIfPresent(Int.self, forKey: .yoloDenied) ?? 0)
            total = try container.decodeIfPresent(Int.self, forKey: .total) ?? named
        }

        func encode(to encoder: any Encoder) throws {
            var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
            try container.encode(total, forKey: .total)
        }
    }
}

/// The moment the owner last looked, which Dot keeps for every device (`GET` and `POST /api/away/read`, item 1356).
struct AwayMark: Codable, Equatable, Sendable {
    /// Seconds since the epoch; `nil` before any device marked it.
    var readAt: Double?

    enum CodingKeys: String, CodingKey { case readAt = "read_at" }
}

/// The card "While you were away" at the top of the chat list: what is said and what each row opens. A pure view of one
/// `AwayView`: no screen, no clock.
struct AwayCard: Equatable, Sendable {
    struct Row: Equatable, Sendable, Identifiable {
        let run: AwayRun
        /// "Done", "Failed", "Stopped", "At work", "Waiting for you".
        let status: String
        /// The words of the title and, when it has one, of the answer's first line.
        let title: String
        let detail: String?
        var id: String { run.id }
    }

    /// "Dot did 5 things on its own".
    let headline: String
    /// "3 done · 1 failed · 1 at work".
    let counts: String
    /// The few newest runs, newest first.
    let rows: [Row]
    /// How many runs the card does not list.
    let more: Int
    /// "Dot answered 2 approvals for you", when it did.
    let decisions: String?
    /// The mark that dismissing the card sets (`POST /api/away/read`).
    let until: Double
    /// When the card's news begins.
    let since: Double
}

enum AwayRules {
    /// How many runs the card lists.
    static let shown: Int = 3

    /// The card for what Dot did, or `nil` when it did nothing the owner has not seen (no card is better than an empty one).
    static func card(from view: AwayView) -> AwayCard? {
        let total: Int = view.done + view.failed + view.cancelled + view.working
        guard total > 0 || !view.runs.isEmpty else { return nil }
        // The newest first, by the moment each one ended (or began, while it works).
        let newest: [AwayRun] = view.runs.sorted { moment($0) > moment($1) }
        let count: Int = max(total, view.runs.count)
        return AwayCard(
            headline: "Dot did \(count) \(count == 1 ? "thing" : "things") on its own",
            counts: counts(of: view),
            rows: newest.prefix(shown).map(row(of:)),
            more: max(0, count - min(shown, newest.count)),
            decisions: view.decided > 0 ? "Dot answered \(view.decided) \(view.decided == 1 ? "approval" : "approvals") for you" : nil,
            until: view.until, since: view.since)
    }

    /// "3 done · 1 failed · 1 at work", the parts that are not none, in the order the owner reads them.
    static func counts(of view: AwayView) -> String {
        var parts: [String] = []
        if view.done > 0 { parts.append("\(view.done) done") }
        if view.failed > 0 { parts.append("\(view.failed) failed") }
        if view.cancelled > 0 { parts.append("\(view.cancelled) stopped") }
        if view.working > 0 { parts.append("\(view.working) at work") }
        return parts.joined(separator: " · ")
    }

    static func row(of run: AwayRun) -> AwayCard.Row {
        let line: String = run.answer.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) } ?? ""
        return AwayCard.Row(
            run: run, status: status(of: run.status), title: run.title.isEmpty ? "A task" : run.title, detail: line.isEmpty ? nil : line)
    }

    /// The status of a run in the owner's words.
    static func status(of status: String) -> String {
        switch status {
        case "done": "Done"
        case "failed": "Failed"
        case "cancelled": "Stopped"
        case "blocked": "Waiting for you"
        case "queued", "running", "ending": "At work"
        default: status.capitalizedFirst
        }
    }

    /// When a run last changed: its end, or its start while it works.
    static func moment(_ run: AwayRun) -> Double { run.finishedAt ?? run.createdAt }
}
