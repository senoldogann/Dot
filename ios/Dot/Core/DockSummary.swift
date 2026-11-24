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
        let icon: IconName
        /// The long form, and the short one (a count or a word) for a width that cannot hold the long one.
        let words: String
        let short: String
        let tone: Tone
        /// A run that works counts its time from here (the view draws the clock, one tick a second).
        var since: Double?
        /// What follows in a quieter shade: what the first helper does, "Reconnecting…".
        var detail: String?
        /// What it says is only the last thing read, for the phone has no link or Dot cannot be reached: it is drawn dimmed
        /// (the helpers' count, item 1466) and VoiceOver says "last known".
        var dimmed: Bool = false

        var id: Kind { kind }
    }

    let segments: [Segment]

    var isEmpty: Bool { segments.isEmpty }

    /// The run waits on the owner: the capsule takes a warm tint, the one thing on the screen that does.
    var asksTheOwner: Bool { segments.contains { $0.kind == .status && $0.tone == .warn && $0.icon == .bell } }

    func segment(_ kind: Kind) -> Segment? { segments.first { $0.kind == kind } }

    /// The capsule for a chat: its run's line, the state of its stream, its queue and its schedules. `offline` is the phone's
    /// link being down: the helpers' count, which only a read of the daemon changes (item 1466), stays as last read and dims.
    static func make(
        line: RunLine, connection: FeedState, queue: QueueView, automations: [ThreadAutomation], offline: Bool = false
    ) -> DockSummary {
        var segments: [Segment] = []
        var reconnecting: Segment?
        if case .reconnecting(_, let unauthorized) = connection {
            reconnecting = Segment(
                kind: .status, icon: unauthorized ? .key : .refresh,
                words: unauthorized ? "Key refused · pair again" : "Reconnecting…",
                short: unauthorized ? "Key" : "…", tone: unauthorized ? .warn : .plain)
        }
        switch line {
        case .idle:
            if let reconnecting { segments.append(reconnecting) }
        case .working(let since):
            segments.append(
                Segment(
                    kind: .status, icon: reconnecting == nil ? .audioWaveform : .wifiOff, words: "Working",
                    short: "Working", tone: .accent, since: since,
                    detail: reconnecting.map { _ in "Reconnecting…" }))
        case .waitingOnYou(let count):
            segments.append(
                Segment(
                    kind: .status, icon: .bell, words: count > 1 ? "Waiting on you · \(count)" : "Waiting on you",
                    short: count > 1 ? "You · \(count)" : "You", tone: .warn))
        case .waitingOnSubagents(let count, let now):
            segments.append(
                Segment(
                    kind: .status, icon: .circleDashed, words: "Waiting on \(count) \(helperWord(count))",
                    short: "\(count) \(helperWord(count))", tone: .accent, detail: now))
        case .helpersInBackground(let count):
            if let reconnecting { segments.append(reconnecting) }
            // Helpers at work are the accent; a count that was only the last one read is the quiet shade, dimmed.
            let lastKnown: Bool = HelperWatch.isLastKnown(online: !offline, reconnecting: reconnecting != nil)
            segments.append(
                Segment(
                    kind: .helpers, icon: .users, words: "\(count) \(helperWord(count))", short: "\(count)",
                    tone: lastKnown ? .plain : .accent, dimmed: lastKnown))
        }
        let loops: [ThreadAutomation] = automations.filter { $0.loop && $0.schedule.record.enabled }
        if !loops.isEmpty {
            let one: String? = loops.count == 1 ? rhythm(loops[0].schedule.when) : nil
            segments.append(
                Segment(
                    kind: .loops, icon: .loop, words: one ?? "\(loops.count) loops",
                    short: "\(loops.count)", tone: .plain))
        }
        if !queue.messages.isEmpty {
            let count: Int = queue.messages.count
            segments.append(
                Segment(
                    kind: .queue, icon: queue.held ? .circlePause : .listOrdered,
                    words: queue.held ? "Queued · paused" : "\(count) queued", short: "\(count)", tone: queue.held ? .warn : .plain))
        }
        return DockSummary(segments: segments)
    }

    static func helperWord(_ count: Int) -> String { count == 1 ? "helper" : "helpers" }

    /// "every 10 minutes" as a capsule has room for it: "every 10 min"; a rhythm too long for that is just "1 loop". A cron
    /// expression the daemon did not put into words ("cron 0 */2 * * *") is read when it is a plain step ("every 2 h").
    static func rhythm(_ when: String) -> String {
        var words: String = when.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if words.hasPrefix("cron ") {
            let fields: [Substring] = words.dropFirst(5).split(separator: " ")
            if fields.count == 5, fields[2...].allSatisfy({ $0 == "*" }) {
                if fields[0].hasPrefix("*/"), fields[1] == "*", let every: Int = Int(fields[0].dropFirst(2)), every > 0 { return "every \(every) min" }
                if fields[0] == "0", fields[1].hasPrefix("*/"), let every: Int = Int(fields[1].dropFirst(2)), every > 0 { return "every \(every) h" }
            }
            return "1 loop"
        }
        for (long, short) in [(" minutes", " min"), (" minute", " min"), (" hours", " h"), (" hour", " h"), (" seconds", " s")] {
            words = words.replacingOccurrences(of: long, with: short)
        }
        return words.isEmpty || words.count > 18 ? "1 loop" : words
    }
}
