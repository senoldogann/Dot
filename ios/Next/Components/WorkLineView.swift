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

/// The live line: the mark (or the three typing dots), the words shimmering with "…", a soft fade when the words change.
/// It is a button: a tap shows the chat's steps, a second folds them back.
struct WorkLineView: View {
    let now: WorkNow
    let stepsShown: Bool
    let toggle: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    var body: some View {
        if let words = workLineWords(now) {
            Button(action: toggle) {
                HStack(spacing: Space.xs) {
                    Group {
                        if let symbol = workLineSymbol(now) {
                            Symbol(name: symbol, context: .tool).foregroundStyle(Tone.tertiary)
                        } else {
                            TypingDots()
                        }
                    }
                    .frame(width: Metric.iconSlot)
                    Text(words + "…").textRole(.footnote).lineLimit(1).modifier(Shimmer())
                        .id(words)
                        .transition(.opacity)
                    Spacer(minLength: 0)
                }
                .frame(minHeight: Metric.toolRow)
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
