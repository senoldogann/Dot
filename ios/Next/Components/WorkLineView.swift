import DotEngine
import SwiftUI

// The one live line under the last message while a run works and the steps are hidden (item 1769; the web's "What it
// is doing", workstatus.js): the words the web says, in the same English and Turkish, each from an event.

/// How much of what a call is about the line says, so that it stays one line on a phone (the web's DETAIL_CHARS).
private let detailCharacters: Int = 48

/// The line's words for what the run does now; `nil` while words are being written in sight (the line steps aside).
func workLineWords(_ now: WorkNow) -> String? {
    switch now {
    case .queued: String(localized: "Waiting to start", comment: "Work line: a queued run has not started")
    case .typing: String(localized: "Typing", comment: "Work line: the run waits for its model to answer")
    case .thinking: String(localized: "Thinking", comment: "Capsule: the run's model is thinking")
    case .tool(let step): callPhrase(step)
    case .handoff(let agent):
        agent.isEmpty
            ? String(localized: "Handing a part to a subagent", comment: "Work line: work handed to a helper")
            : String(localized: "Waiting for \(agent)", comment: "Work line: work handed to an agent; the argument is its name")
    case .told(let words): middleCut(words, 90)
    case .writing: nil
    case .waitingForYou: String(localized: "Waiting for you", comment: "Work line: an approval or a question waits for the owner")
    case .stopping: String(localized: "Stopping", comment: "Work line: Stop was pressed and the run has not ended")
    }
}

/// A call in the line's words, with what it is about ("Running swift test", "Reading README.md").
func callPhrase(_ step: HelperStep) -> String {
    let tool: String = step.tool
    let about: String = step.target.trimmingCharacters(in: .whitespacesAndNewlines)
    let short: (String) -> String = { middleCut($0, detailCharacters) }
    if tool == "web_search" {
        return about.isEmpty
            ? String(localized: "Searching the web", comment: "Work line: a web search")
            : String(localized: "Searching the web for “\(short(about))”", comment: "Work line: a web search; the argument is what is searched")
    }
    if tool == "web_fetch" || tool == "web_extract" {
        return about.isEmpty
            ? String(localized: "Reading a page", comment: "Work line: a web page read")
            : String(localized: "Reading \(short(URL(string: about)?.host() ?? about))", comment: "Work line: a file or page read; the argument is its name")
    }
    if tool.hasPrefix("browser_") { return String(localized: "Working in the browser", comment: "Work line: a browser step") }
    if tool.hasPrefix("desktop_") { return String(localized: "Working on the Mac", comment: "Work line: a step on the Mac's screen") }
    if tool.hasPrefix("schedule_") { return String(localized: "Setting up a schedule", comment: "Work line: a schedule step") }
    if tool.hasPrefix("memory_") || tool == "recall" { return String(localized: "Checking its notes", comment: "Work line: a step on Dot's notes") }
    switch ToolKind.of(tool) {
    case .command:
        let line: String = about.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        return line.isEmpty
            ? String(localized: "Running a command", comment: "Work line: a command")
            : String(localized: "Running \(short(line))", comment: "Tool row: a command at work; the argument is the command")
    case .read:
        return about.isEmpty
            ? String(localized: "Reading a file", comment: "Work line: a file read")
            : String(localized: "Reading \(short(lastPart(about)))", comment: "Work line: a file or page read; the argument is its name")
    case .edit:
        return about.isEmpty
            ? String(localized: "Writing a file", comment: "Work line: a file written")
            : String(localized: "Writing \(short(lastPart(about)))", comment: "Work line: a file written; the argument is its name")
    case .screen: return String(localized: "Working on the Mac", comment: "Work line: a step on the Mac's screen")
    case .web: return String(localized: "Reading a page", comment: "Work line: a web page read")
    default: return about.isEmpty ? (dotToolWords(tool) ?? toolName(tool)) : "\(dotToolWords(tool) ?? toolName(tool)): \(short(about))"
    }
}

/// A call the run told as one of Dot's short messages (a `narration`, item 1976): a page or a site by its name ("Looking at
/// gistsmith.com"), any other call as the live line says it.
func narrationWords(_ step: HelperStep) -> String {
    let about: String = step.target.trimmingCharacters(in: .whitespacesAndNewlines)
    guard about.contains("://"), let host = URL(string: about)?.host() else { return callPhrase(step) }
    let site: String = host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    return String(localized: "Looking at \(middleCut(site, detailCharacters))", comment: "Chat message of Dot's: a page it looks at; the argument is its site")
}

/// Dot's first words in a chat (a `first_words` event, item 2018): the fast model's line, or the words said at once when it
/// has none.
func firstWordsText(_ text: String) -> String {
    text.isEmpty ? String(localized: "Looking into it…", comment: "Chat message of Dot's, said the moment a task starts, before it has done anything") : text
}

/// How long a run has worked, as the live line says it beside its words ("12 s", "3 min").
func workedFor(_ seconds: TimeInterval) -> String {
    let whole: Int = max(0, Int(seconds))
    return whole < 60
        ? String(localized: "\(whole) s", comment: "Work line: how long the run has worked, in seconds")
        : String(localized: "\(whole / 60) min", comment: "Work line: how long the run has worked, in whole minutes")
}

/// Who works and since when, for the live line of "Show steps" off (item 1976): the bot's face in the mark's place and
/// the time beside the words ("· 3 min"), as a chat with a person shows them.
struct WorkLineSpeaker: Equatable {
    let id: String
    let look: AgentLook?
    let since: Date
}

/// The name a path ends with ("README.md" of "~/work/README.md").
private func lastPart(_ path: String) -> String {
    let trimmed: String = path.hasSuffix("/") ? String(path.dropLast()) : path
    return trimmed.split(separator: "/").last.map(String.init) ?? trimmed
}

/// The mark beside the line's words: the kind of work in SF Symbols; `nil` while the model is typing (the dots).
func workLineSymbol(_ now: WorkNow) -> String? {
    switch now {
    case .typing, .writing: nil
    case .queued: "clock"
    case .thinking, .told: "brain"
    case .handoff: "person.2"
    case .waitingForYou: "hand.raised"
    case .stopping: "stop.circle"
    case .tool(let step):
        step.tool == "web_search" ? "magnifyingglass" : step.tool.hasPrefix("memory_") ? "note.text" : toolSymbol(ToolKind.of(step.tool))
    }
}

/// Whether the line shimmers: only while the run works on its own; never while it waits on the owner or for its stop,
/// which may last hours with the chat on screen (item 1932). Pure.
func workLineMoves(_ now: WorkNow) -> Bool {
    switch now {
    case .waitingForYou, .stopping: false
    default: true
    }
}

/// The live line: the mark (or the three typing dots), the words shimmering with "…", a soft fade when the words change.
/// It is a button: a tap shows the chat's steps, a second folds them back.
struct WorkLineView: View {
    let now: WorkNow
    let stepsShown: Bool
    /// The bot and the time of "Show steps" off (item 1976), `nil` beside the steps.
    let speaker: WorkLineSpeaker?
    let toggle: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    var body: some View {
        if let words = workLineWords(now) {
            Button(action: toggle) {
                HStack(spacing: Space.xs) {
                    Group {
                        if let speaker {
                            BotFace(id: speaker.id, look: speaker.look, size: Metric.iconSlot)
                        } else if let symbol = workLineSymbol(now) {
                            Symbol(name: symbol, context: .tool).foregroundStyle(Tone.tertiary)
                        } else {
                            TypingDots()
                        }
                    }
                    .frame(width: Metric.iconSlot)
                    Group {
                        if workLineMoves(now) {
                            Text(words + "…").textRole(.footnote).lineLimit(1).modifier(Shimmer())
                        } else {
                            // A state that waits on the owner or for a stop is still: no shimmer redrawing it forever
                            // (item 1932: the asks scene held the phone at 100 % CPU on this line).
                            Text(words).textRole(.footnote).lineLimit(1).foregroundStyle(Tone.secondary)
                        }
                    }
                        .id(words)
                        .transition(.opacity)
                    if let speaker, workLineMoves(now) {
                        // Once a second, the time only: the words keep their shimmer and fade.
                        TimelineView(.periodic(from: speaker.since, by: 1)) { context in
                            Text(verbatim: "· " + workedFor(context.date.timeIntervalSince(speaker.since)))
                                .textRole(.footnote).monospacedDigit().foregroundStyle(Tone.tertiary).fixedSize()
                        }
                        .accessibilityHidden(true)
                    }
                    Spacer(minLength: 0)
                }
                // A button alone on its line: the 44 pt tap target (the design bar), not a tool row's 28 pt pitch.
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .animation(motionAnimation(.message, reduceMotion: reduceMotion), value: words)
            .haptic(.selection, trigger: stepsShown)
            .accessibilityLabel(words)
            .accessibilityHint(
                stepsShown
                    ? String(localized: "Hides the steps", comment: "VoiceOver hint of an open work-log group")
                    : String(localized: "Shows the steps", comment: "VoiceOver hint of a closed work-log group"))
            .accessibilityIdentifier("work.line")
        }
    }
}

/// Three dots that rise in turn, the model typing (item 1091); still under Reduce Motion.
struct TypingDots: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @ScaledMetric(relativeTo: .footnote) private var dot: CGFloat = 4

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { timeline in
            let time: Double = timeline.date.timeIntervalSinceReferenceDate
            HStack(spacing: dot * 0.6) {
                ForEach(0..<3, id: \.self) { index in
                    let phase: Double = (time * 1.6 - Double(index) * 0.18).truncatingRemainder(dividingBy: 1)
                    let lift: Double = reduceMotion ? 0 : max(0, sin(phase * .pi * 2)) * Double(dot) * 0.6
                    Circle().fill(Tone.tertiary).frame(width: dot, height: dot).offset(y: -lift)
                }
            }
        }
        .accessibilityHidden(true)
    }
}

/// The waiting line's words, "Waiting · <on what>", while the run's step waits on something (a wait or a watch); `nil`
/// for any other step (item 1909, after another coding agent's work log). A wait on helpers is the pill's "Waiting on 2
/// helpers".
func waitingWords(_ step: HelperStep) -> String? {
    guard ToolKind.of(step.tool) == .wait else { return nil }
    let line: String = step.line.trimmingCharacters(in: .whitespacesAndNewlines)
    let what: String = line.isEmpty ? callPhrase(step) : middleCut(line, 90)
    return String(localized: "Waiting · \(what)", comment: "Waiting line: the run waits on something; the argument says on what")
}

/// One quiet line under the stream while the run waits on something: the terminal's prompt, "Waiting · Watching pull
/// request #554" and Details, a plain text button that opens the run inspector. Stop is the pill's: one Stop on the
/// screen, where the run's state already is (item 1909).
struct WaitingLine: View {
    let words: String
    let details: () -> Void

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
            Symbol(name: "apple.terminal", context: .tool).foregroundStyle(Tone.tertiary).frame(width: Metric.iconSlot)
                .accessibilityHidden(true)
            Text(words).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(2)
                .accessibilityIdentifier("waiting.line")
            Button(String(localized: "Details", comment: "Waiting line: opens the run inspector"), action: details)
                .buttonStyle(PlainRowButtonStyle())
                .accessibilityIdentifier("waiting.details")
            Spacer(minLength: 0)
        }
        .frame(minHeight: Metric.hit)
    }
}

/// The line under the owner's message from the moment it is sent until its run's first event (item 1919): the typing
/// dots and "Starting…", shimmering, as the work line draws; it is replaced in place by the run's own line.
struct StartingLine: View {
    var body: some View {
        HStack(spacing: Space.xs) {
            TypingDots().frame(width: Metric.iconSlot)
            Text(ChatStatus.starting.word + "…").textRole(.footnote).lineLimit(1).modifier(Shimmer())
            Spacer(minLength: 0)
        }
        .frame(minHeight: Metric.hit)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(ChatStatus.starting.word)
        .accessibilityIdentifier("work.starting")
    }
}
