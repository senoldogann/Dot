import SwiftUI

/// How a helper run stands, as the agents sheet says it (plan §18): ink for work, amber when it waits on the owner,
/// red for a failure, quiet for the ends.
enum HelperState: Equatable, Sendable {
    case working
    case waitingOnYou
    case stopping
    case done
    case failed
    case stopped

    var word: String {
        switch self {
        case .working: String(localized: "Working", comment: "Status word: a run is at work")
        case .waitingOnYou:
            String(localized: "Waiting on you", comment: "Status word: a chat waits for the owner's approval or answer")
        case .stopping: String(localized: "Stopping…", comment: "Helper state: the owner pressed Stop, the helper has not ended yet")
        case .done: String(localized: "Done", comment: "Status word: the run finished")
        case .failed: String(localized: "Failed", comment: "Status word: the run failed")
        case .stopped: String(localized: "Stopped", comment: "Helper state: the helper ended because the owner stopped it")
        }
    }

    var tone: StatusTone {
        switch self {
        case .working, .stopping: .neutral
        case .waitingOnYou: .warn
        case .failed: .failure
        case .done, .stopped: .quiet
        }
    }

    /// Whether the run still goes (its clock runs and it can be stopped).
    var isLive: Bool { self == .working || self == .waitingOnYou }
}

/// A helper run as the agents sheet lists it.
struct HelperRun: Identifiable, Equatable, Sendable {
    let id: String
    let title: String
    let state: HelperState
    let startedAt: Date
    let endedAt: Date?
    /// "provider · model · effort", e.g. "Claude · Opus 5.5 · Medium": the daemon's words.
    let engine: String
}

/// The elapsed time of a run in the locale's short units ("41 min 15 s", "41 dk 15 sn"); hours appear past 60 minutes.
func elapsedWords(from start: Date, to end: Date) -> String {
    let seconds: Int = max(0, Int(end.timeIntervalSince(start)))
    let units: Set<Duration.UnitsFormatStyle.Unit> = seconds >= 3600 ? [.hours, .minutes] : [.minutes, .seconds]
    return Duration.seconds(seconds).formatted(.units(allowed: units, width: .abbreviated))
}

/// One helper in the agents sheet (owner's reference, plan §18): a status dot and the state word after the title, a
/// live tabular clock, the engine on the second line, a chevron to the helper's chat, and a round 44 pt Stop with a red
/// glyph on the nested fill. At the accessibility sizes the clock stands on its own line.
struct HelperRow: View {
    let run: HelperRun
    let open: () -> Void
    let stop: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        HStack(spacing: Space.s) {
            Button(action: open) {
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Circle().fill(run.state.tone.color).frame(width: 7, height: 7)
                        .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        titleLine
                        Text(run.engine).textRole(.label).foregroundStyle(Tone.tertiary)
                        if dynamicTypeSize.isAccessibilitySize { clock }
                    }
                    Spacer(minLength: Space.xs)
                    if !dynamicTypeSize.isAccessibilitySize { clock }
                    Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
                }
                .padding(.vertical, Space.s)
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel([run.title, run.state.word, run.engine].joined(separator: ", "))
            .accessibilityAddTraits(.isButton)
            .accessibilityHint(String(localized: "Opens the helper's chat", comment: "VoiceOver hint of a helper row"))
            if run.state.isLive {
                Button(action: stop) {
                    Image(systemName: "stop.fill")
                        .font(.system(.callout, weight: .semibold))
                        .foregroundStyle(Tone.failure)
                        .frame(width: Metric.hit, height: Metric.hit)
                        .background(Tone.nested, in: Circle())
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(String(localized: "Stop \(run.title)", comment: "VoiceOver: the Stop button of a helper row; the argument is the helper's title"))
            }
        }
    }

    /// "title · state", the state in its tone, wrapping as one text.
    private var titleLine: some View {
        var title: AttributedString = AttributedString(run.title)
        title.foregroundColor = Tone.text
        var dot: AttributedString = AttributedString(" · ")
        dot.foregroundColor = Tone.tertiary
        var state: AttributedString = AttributedString(run.state.word)
        state.foregroundColor = run.state.tone.color
        return Text(title + dot + state).textRole(.footnote).fontWeight(.medium)
    }

    private var clock: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(elapsedWords(from: run.startedAt, to: run.endedAt ?? context.date))
                .textRole(.label)
                .foregroundStyle(Tone.tertiary)
                .tabularNumbers()
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
        }
        .accessibilityHidden(true)
    }
}

/// What still works for a chat after its run ended (plan §18): helpers released by a wait, background commands, loops.
enum BackgroundKind: Equatable, Sendable {
    case subagent
    case command
    case loop

    var word: String {
        switch self {
        case .subagent: String(localized: "Subagent", comment: "Background work kind: a helper agent")
        case .command: String(localized: "Command", comment: "Background work kind: a shell command still running")
        case .loop: String(localized: "Loop", comment: "Background work kind: a repeating task of the chat")
        }
    }

    var symbol: String {
        switch self {
        case .subagent: "person.2"
        case .command: "terminal"
        case .loop: "arrow.triangle.2.circlepath"
        }
    }
}

struct BackgroundWork: Identifiable, Equatable, Sendable {
    let id: String
    let title: String
    let kind: BackgroundKind
}

/// One item of the background work sheet: icon, title, kind, chevron.
struct BackgroundWorkRow: View {
    let work: BackgroundWork
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            HStack(spacing: Space.s) {
                Symbol(name: work.kind.symbol, context: .row).foregroundStyle(Tone.secondary).frame(width: Metric.iconSlot)
                VStack(alignment: .leading, spacing: 2) {
                    Text(work.title).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
                    Text(work.kind.word).textRole(.label).foregroundStyle(Tone.tertiary)
                }
                Spacer(minLength: Space.xs)
                Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
            }
            .padding(.vertical, Space.s)
            .frame(minHeight: Metric.hit)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([work.title, work.kind.word].joined(separator: ", "))
        .accessibilityAddTraits(.isButton)
    }
}

/// The agents sheet: the helpers of a chat, newest first, each stoppable; a Stop shows "Stopping…" at once.
struct AgentsSheet: View {
    @State var runs: [HelperRun]
    let close: () -> Void

    var body: some View {
        List {
            ForEach(runs) { run in
                HelperRow(run: run, open: {}, stop: { stop(run) })
                    .listRowBackground(Color.clear)
                    .listRowSeparatorTint(Tone.separator)
                    .listRowInsets(EdgeInsets(top: 0, leading: Space.l, bottom: 0, trailing: Space.l))
            }
        }
        .listStyle(.plain)
        .inspectorSheet(title: String(localized: "Agents", comment: "Title of the sheet of a chat's helpers"), close: close)
    }

    private func stop(_ run: HelperRun) {
        guard let index = runs.firstIndex(of: run) else { return }
        runs[index] = HelperRun(
            id: run.id, title: run.title, state: .stopping, startedAt: run.startedAt, endedAt: nil, engine: run.engine)
    }
}

/// The background work sheet of a chat.
struct BackgroundWorkSheet: View {
    let items: [BackgroundWork]
    let close: () -> Void

    var body: some View {
        List {
            ForEach(items) { work in
                BackgroundWorkRow(work: work, open: {})
                    .listRowBackground(Color.clear)
                    .listRowSeparatorTint(Tone.separator)
                    .listRowInsets(EdgeInsets(top: 0, leading: Space.l, bottom: 0, trailing: Space.l))
            }
        }
        .listStyle(.plain)
        .inspectorSheet(
            title: String(localized: "Background work", comment: "Title of the sheet of what still works for a chat"),
            close: close)
    }
}
