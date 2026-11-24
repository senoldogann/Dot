import Foundation

/// What the capsule over the composer says (item 1390). A good agent app's phone shows ONE floating capsule above its composer,
/// its segments divided by hairlines, and each segment opens its own detail; nothing is stacked. Dot's segments are the
/// run's status, its helpers, the loops of the chat and the queue, each drawn only when it has something to say, so a
/// chat at rest has no capsule at all. This is the pure part: which segments there are and what they say.
struct DockSummary: Equatable, Sendable {
    enum Kind: String, Sendable, CaseIterable {
        case status, helpers, loops, queue
    }

    enum Tone: Sendable {
        case plain, accent, warn
    }

    struct Segment: Equatable, Sendable, Identifiable {
        let kind: Kind
        let symbol: String
        /// The long form, and the short one (a count or a word) for a width that cannot hold the long one.
        let words: String
        let short: String
        let tone: Tone
        /// A run that works counts its time from here (the view draws the clock, one tick a second).
        var since: Double?
        /// What follows in a quieter shade: what the first helper does, "Reconnecting…".
        var detail: String?

        var id: Kind { kind }
    }

    let segments: [Segment]

    var isEmpty: Bool { segments.isEmpty }

    /// The run waits on the owner: the capsule takes a warm tint, the one thing on the screen that does.
    var asksTheOwner: Bool { segments.contains { $0.kind == .status && $0.tone == .warn && $0.symbol == "bell.fill" } }

    func segment(_ kind: Kind) -> Segment? { segments.first { $0.kind == kind } }

    /// The capsule for a chat: its run's line, the state of its stream, its queue and its schedules.
    static func make(line: RunLine, connection: FeedState, queue: QueueView, automations: [ThreadAutomation]) -> DockSummary {
        var segments: [Segment] = []
        var reconnecting: Segment?
        if case .reconnecting(_, let unauthorized) = connection {
            reconnecting = Segment(
                kind: .status, symbol: unauthorized ? "key" : "arrow.triangle.2.circlepath",
                words: unauthorized ? "Key refused · pair again" : "Reconnecting…",
                short: unauthorized ? "Key" : "…", tone: unauthorized ? .warn : .plain)
        }
        switch line {
        case .idle:
            if let reconnecting { segments.append(reconnecting) }
        case .working(let since):
            segments.append(
                Segment(
                    kind: .status, symbol: reconnecting == nil ? "waveform" : "wifi.exclamationmark", words: "Working",
                    short: "Working", tone: .accent, since: since,
                    detail: reconnecting.map { _ in "Reconnecting…" }))
        case .waitingOnYou(let count):
            segments.append(
                Segment(
                    kind: .status, symbol: "bell.fill", words: count > 1 ? "Waiting on you · \(count)" : "Waiting on you",
                    short: count > 1 ? "You · \(count)" : "You", tone: .warn))
        case .waitingOnSubagents(let count, let now):
            segments.append(
                Segment(
                    kind: .status, symbol: "circle.dashed", words: "Waiting on \(count) \(helperWord(count))",
                    short: "\(count) \(helperWord(count))", tone: .accent, detail: now))
        case .helpersInBackground(let count):
            if let reconnecting { segments.append(reconnecting) }
            segments.append(
                Segment(kind: .helpers, symbol: "person.2", words: "\(count) \(helperWord(count))", short: "\(count)", tone: .plain))
        }
        let loops: [ThreadAutomation] = automations.filter { $0.loop && $0.schedule.record.enabled }
        if !loops.isEmpty {
            let one: String? = loops.count == 1 ? rhythm(loops[0].schedule.when) : nil
            segments.append(
                Segment(
                    kind: .loops, symbol: "arrow.triangle.2.circlepath", words: one ?? "\(loops.count) loops",
                    short: "\(loops.count)", tone: .plain))
        }
        if !queue.messages.isEmpty {
            let count: Int = queue.messages.count
            segments.append(
                Segment(
                    kind: .queue, symbol: queue.held ? "pause.circle" : "list.number",
                    words: queue.held ? "Queued · paused" : "\(count) queued", short: "\(count)", tone: queue.held ? .warn : .plain))
        }
        return DockSummary(segments: segments)
    }

    static func helperWord(_ count: Int) -> String { count == 1 ? "helper" : "helpers" }

    /// "every 10 minutes" as a capsule has room for it: "every 10 min"; a rhythm too long for that is just "1 loop".
    static func rhythm(_ when: String) -> String {
        var words: String = when.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for (long, short) in [(" minutes", " min"), (" minute", " min"), (" hours", " h"), (" hour", " h"), (" seconds", " s")] {
            words = words.replacingOccurrences(of: long, with: short)
        }
        return words.isEmpty || words.count > 18 ? "1 loop" : words
    }
}
