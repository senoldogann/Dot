import DotEngine
import SwiftUI
import UIKit

// The work log of a chat (wave N2, plan §4.1): one-line tool rows, the fold of a finished stretch, the plan as a
// checklist, the helpers as one group, the asks left in place, how a run ended, and Dot's notes.

extension EnvironmentValues {
    /// Told when the owner opens or closes a row of the work log (a step, a thought, a fold): the transcript stops
    /// following new words, so that the row they act on stays where it is. Left to follow, the end of the page chased the
    /// growing row with a scroll on every frame of its animation, and the animation stuttered.
    @Entry var disclosureToggled: @MainActor () -> Void = {}
}

/// What the transcript stacks: one item, or consecutive steps (tool calls and the model's thinking) drawn as one tight
/// block (the reference's 28 pt pitch, §19: no gap between the rows of a stretch of work; item 1769: a thought is a row
/// of the stretch, not a block of its own).
enum TranscriptBlock: Identifiable {
    case item(TranscriptItem)
    case steps([TranscriptItem])

    var id: String {
        switch self {
        case .item(let item): item.id
        case .steps(let rows): "steps:" + (rows.first?.id ?? "")
        }
    }

    static func of(_ items: [TranscriptItem]) -> [TranscriptBlock] {
        var blocks: [TranscriptBlock] = []
        for item in items {
            if isStep(item) {
                if case .steps(let run)? = blocks.last {
                    blocks[blocks.count - 1] = .steps(run + [item])
                } else {
                    blocks.append(.steps([item]))
                }
            } else {
                blocks.append(.item(item))
            }
        }
        return blocks
    }

    private static func isStep(_ item: TranscriptItem) -> Bool {
        switch item {
        case .tool, .thought: true
        default: false
        }
    }
}

/// One line of a stretch of steps: a step, or consecutive tool calls said as one "Ran 3 commands and read 2 files" (items
/// 1769 and 1909, after another coding agent's work log).
enum StepGroup: Identifiable, Equatable {
    case row(TranscriptItem)
    case tools([ToolEntry])

    var id: String {
        switch self {
        case .row(let item): item.id
        case .tools(let entries): "tools:" + (entries.first?.id ?? "")
        }
    }

    /// The lines of `rows`: two or more consecutive tool calls that all ended well become one line; a call at work or
    /// one that failed stands alone, so neither is hidden in a fold. Pure.
    static func of(_ rows: [TranscriptItem]) -> [StepGroup] {
        var groups: [StepGroup] = []
        var run: [ToolEntry] = []
        func flush() {
            if run.count >= 2 { groups.append(.tools(run)) } else { groups.append(contentsOf: run.map { .row(.tool($0)) }) }
            run = []
        }
        for row in rows {
            if case .tool(let entry) = row, entry.state == .done {
                run.append(entry)
            } else {
                flush()
                groups.append(.row(row))
            }
        }
        flush()
        return groups
    }
}

/// A stretch of steps, no gap between them.
struct StepRun: View {
    let rows: [TranscriptItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(StepGroup.of(rows)) { group in
                switch group {
                case .tools(let entries): ToolGroupRow(entries: entries).id(group.id)
                case .row(.tool(let entry)): WorkToolRow(entry: entry).id(entry.id)
                case .row(.thought(let id, let text, let live)): ThoughtRow(text: text, live: live).id(id)
                case .row: EmptyView()
                }
            }
        }
    }
}

/// Consecutive tool calls that ended well as one quiet line in plain words, "Ran 3 commands and read 2 files", with the
/// terminal's symbol when all are commands and a hammer otherwise; a tap opens each of them as its own row (item 1909).
struct ToolGroupRow: View {
    let entries: [ToolEntry]
    @State private var open: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.locale) private var locale: Locale
    @Environment(\.disclosureToggled) private var disclosureToggled: @MainActor () -> Void

    var body: some View {
        let words: String = toolGroupWords(entries, locale: locale)
        VStack(alignment: .leading, spacing: 0) {
            Button {
                disclosureToggled()
                open.toggle()
            } label: {
                HStack(spacing: Space.xs) {
                    Symbol(name: entries.allSatisfy { $0.kind == .command } ? toolSymbol(.command) : "hammer", context: .tool)
                        .foregroundStyle(Tone.secondary).frame(width: Metric.iconSlot)
                    Text(words).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(1)
                    Spacer(minLength: Space.xs)
                    Symbol(name: "chevron.down", context: .status).foregroundStyle(Tone.glyph).rotationEffect(.degrees(open ? 180 : 0))
                }
                .frame(minHeight: Metric.toolRow)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .haptic(.selection, trigger: open)
            .accessibilityLabel(words)
            .accessibilityIdentifier("tools.group")
            .accessibilityValue(open ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure"))
            .accessibilityHint(open ? String(localized: "Hides the steps", comment: "VoiceOver hint of an open work-log group") : String(localized: "Shows the steps", comment: "VoiceOver hint of a closed work-log group"))
            if open {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(entries, id: \.id) { entry in WorkToolRow(entry: entry) }
                }
                .padding(.leading, Space.xs)
                .transition(.opacity)
            }
        }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: open)
    }
}

/// A tool call as one quiet 28 pt line (§19): a shimmer while it runs, a red ✕ when it failed; a tap opens what it was
/// asked and what it said (monospaced, scrolling past 210 pt) with its screenshots; a long press copies both.
struct WorkToolRow: View {
    let entry: ToolEntry
    @AppStorage(PrefKey.terminalFontSize) private var terminalSize: Double = 0
    @State private var open: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    @Environment(\.disclosureToggled) private var disclosureToggled: @MainActor () -> Void

    private var detail: String {
        [entry.args, entry.output].filter { !$0.isEmpty }.joined(separator: "\n\n")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                disclosureToggled()
                open.toggle()
            } label: {
                HStack(spacing: Space.xs) {
                    line
                    Spacer(minLength: Space.xs)
                    if entry.state == .failed {
                        Symbol(name: "xmark", context: .status).foregroundStyle(Tone.failure)
                    }
                    if !detail.isEmpty || !entry.images.isEmpty {
                        Symbol(name: "chevron.down", context: .status).foregroundStyle(Tone.glyph)
                            .rotationEffect(.degrees(open ? 180 : 0))
                    }
                }
                .frame(minHeight: Metric.toolRow)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .haptic(.selection, trigger: open)
            .contextMenu {
                Button(String(localized: "Copy", comment: "Context menu of a tool call: copies its line and detail"), systemImage: "doc.on.doc") {
                    UIPasteboard.general.string = [toolLine(entry), detail].filter { !$0.isEmpty }.joined(separator: "\n")
                }
            }
            .modifier(ToggleMeter(open: open, kind: OutputBox.isLong(detail) ? "long" : "short"))
            .accessibilityLabel(accessibilityWords)
            .accessibilityValue(open ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure"))
            if !entry.images.isEmpty, !open {
                screenshots.padding(.leading, Metric.iconSlot + Space.xs).padding(.bottom, Space.xxs)
            }
            if open {
                VStack(alignment: .leading, spacing: Space.xs) {
                    if !detail.isEmpty {
                        OutputBox(text: detail, role: .code, points: terminalSize, ink: Tone.secondary, anchorsAtEnd: false)
                    }
                    if !entry.images.isEmpty { screenshots }
                }
                .padding(.leading, Metric.iconSlot + Space.xs)
                .padding(.bottom, Space.xs)
                .transition(.opacity)
            }
        }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: open)
    }

    @ViewBuilder private var line: some View {
        let content: some View = HStack(spacing: Space.xs) {
            Group {
                if isDotTool(entry) { DotMark() } else { Symbol(name: toolSymbol(entry.kind), context: .tool) }
            }
            .frame(width: Metric.iconSlot)
            Text(toolLine(entry)).textRole(.footnote).lineLimit(dynamicTypeSize.isAccessibilitySize ? 4 : 1)
                // A path keeps its file name at the end; words keep their start.
                .truncationMode(entry.kind == .read || entry.kind == .edit || entry.kind == .web ? .middle : .tail)
        }
        if entry.state == .running { content.modifier(Shimmer()) } else { content.foregroundStyle(Tone.secondary) }
    }

    private var screenshots: some View {
        ScrollView(.horizontal) {
            HStack(spacing: Space.xs) {
                ForEach(entry.images, id: \.self) { name in DaemonImageThumb(name: name) }
            }
        }
        .scrollIndicators(.hidden)
    }

    private var accessibilityWords: String {
        let words: String = toolLine(entry)
        switch entry.state {
        case .running: return String(localized: "\(words), running", comment: "VoiceOver: a tool call at work; the argument is its line")
        case .done: return words
        case .failed: return String(localized: "\(words), failed", comment: "VoiceOver: a tool call that failed; the argument is its line")
        }
    }
}

/// A screenshot a tool returned, loaded over the phone's key; a tap shows it whole.
struct DaemonImageThumb: View {
    let name: String
    @Environment(AppModel.self) private var model: AppModel
    @State private var image: UIImage?
    @State private var whole: Bool = false

    var body: some View {
        Button {
            whole = image != nil
        } label: {
            Group {
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    Tone.nested.overlay { ProgressView().controlSize(.small) }
                }
            }
            .frame(width: 120, height: 76)
            .clipShape(rounded(Radius.inner))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(String(localized: "Screenshot", comment: "VoiceOver: a screenshot a tool step returned"))
        .task(id: name) { image = await model.image(named: name) }
        .sheet(isPresented: $whole) {
            if let image {
                ZoomableImage(image: image, label: String(localized: "Screenshot", comment: "VoiceOver: a screenshot a tool step returned"))
                    .ignoresSafeArea(edges: .bottom)
                    .inspectorSheet(title: String(localized: "Screenshot", comment: "VoiceOver: a screenshot a tool step returned"), close: { whole = false })
            }
        }
    }
}

/// A finished stretch of work as one 44 pt line (plan §4.1): "Worked for 2 dk 14 sn · read 3 files, ran 2 commands";
/// a stretch with a failed step is drawn open.
struct WorkFoldView: View {
    let fold: WorkFold
    let row: (TranscriptItem) -> AnyView
    @State private var open: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.locale) private var locale: Locale
    @Environment(\.disclosureToggled) private var disclosureToggled: @MainActor () -> Void

    init(fold: WorkFold, row: @escaping (TranscriptItem) -> AnyView) {
        self.fold = fold
        self.row = row
        _open = State(initialValue: fold.startsOpen)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Button {
                disclosureToggled()
                open.toggle()
            } label: {
                HStack(spacing: Space.xs) {
                    Text(foldWords(fold, locale: locale)).textRole(.footnote).foregroundStyle(Tone.secondary).tabularNumbers()
                        .multilineTextAlignment(.leading)
                    Symbol(name: "chevron.right", context: .status).foregroundStyle(Tone.glyph).rotationEffect(.degrees(open ? 90 : 0))
                    Spacer(minLength: 0)
                }
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .haptic(.selection, trigger: open)
            .accessibilityLabel(foldWords(fold, locale: locale))
            .accessibilityValue(open ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure"))
            .accessibilityHint(open ? String(localized: "Hides the steps", comment: "VoiceOver hint of an open work-log group") : String(localized: "Shows the steps", comment: "VoiceOver hint of a closed work-log group"))
            if open {
                VStack(alignment: .leading, spacing: Space.xs) {
                    ForEach(TranscriptBlock.of(fold.items)) { block in
                        switch block {
                        case .item(let item): row(item)
                        case .steps(let rows): StepRun(rows: rows)
                        }
                    }
                }
                .padding(.leading, Space.xs)
                .transition(.opacity)
            }
        }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: open)
    }
}

/// How far a run's plan got, for the plan chip beside the working capsule (item 1880).
struct PlanProgress: Equatable, Sendable {
    let done: Int
    let total: Int

    init(steps: [PlanStep]) {
        done = steps.filter { $0.status == "done" }.count
        total = steps.count
    }

    init(done: Int, total: Int) {
        self.done = done
        self.total = total
    }
}

/// The run's plan as a checklist: its header line and one row per step.
struct PlanSteps: View {
    let steps: [PlanStep]

    var body: some View {
        let done: Int = steps.filter { $0.status == "done" }.count
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(String(localized: "Plan · \(done) of \(steps.count) done", comment: "Plan card header; the arguments are the steps done and all steps"))
                .textRole(.label).fontWeight(.semibold).foregroundStyle(Tone.secondary).tabularNumbers()
            ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Image(systemName: glyph(step.status)).font(.system(.footnote, weight: .medium))
                        .foregroundStyle(step.status == "failed" ? Tone.failure : step.status == "done" ? Tone.text : Tone.tertiary)
                        .accessibilityHidden(true)
                    Text(step.text).textRole(.footnote)
                        .foregroundStyle(step.status == "done" || step.status == "skipped" ? Tone.secondary : Tone.text)
                        .strikethrough(step.status == "skipped")
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(Text(verbatim: "\(step.text), \(statusWords(step.status))"))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func glyph(_ status: String) -> String {
        switch status {
        case "done": "checkmark.circle.fill"
        case "in_progress": "circle.dotted"
        case "failed": "xmark.circle"
        case "skipped": "minus.circle"
        default: "circle"
        }
    }

    private func statusWords(_ status: String) -> String {
        switch status {
        case "done": String(localized: "done", comment: "VoiceOver: a plan step's state")
        case "in_progress": String(localized: "in progress", comment: "VoiceOver: a plan step's state")
        case "failed": String(localized: "failed", comment: "VoiceOver: a plan step's state")
        case "skipped": String(localized: "skipped", comment: "VoiceOver: a plan step's state")
        default: String(localized: "to do", comment: "VoiceOver: a plan step's state")
        }
    }
}

/// An ask left where it was asked: a quiet line with what was decided and by whom (amber while it waits).
struct AskAnchorRow: View {
    let anchor: AskAnchor

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
            Image(systemName: glyph).font(.system(.footnote, weight: .medium)).foregroundStyle(anchor.status == "pending" ? Tone.warn : Tone.tertiary)
                .frame(width: Metric.iconSlot).accessibilityHidden(true)
            Text(anchorWords(anchor)).textRole(.footnote).foregroundStyle(anchor.status == "pending" ? Tone.warn : Tone.secondary).lineLimit(3)
        }
        .frame(minHeight: Metric.toolRow)
        .accessibilityElement(children: .combine)
    }

    private var glyph: String {
        switch anchor.severity {
        case "question": "questionmark.bubble"
        case "secret": "key"
        case "handoff": "hand.raised"
        default: "checkmark.shield"
        }
    }
}

/// Dot's note (a helper's report among them): Dot's own row, its face and the words, not the owner's bubble.
struct DotNoteRow: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: Space.xs) {
            DotFace(size: 18).padding(.top, 2)
            Text(text).textRole(.footnote).foregroundStyle(Tone.secondary).textSelection(.enabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(String(localized: "Dot's note: \(text)", comment: "VoiceOver: a note of Dot's in the chat; the argument is its words"))
    }
}

/// The footer of a run's final answer (plan §24): copy, the reactions on it and the menu that adds one (docs/runs.md), and
/// the time the answer came. Quiet ink glyphs of one weight, close together; drawn under the final answer alone.
struct ReactionsBar: View {
    let at: Date
    let on: Set<String>
    let toggle: (ReactionKind) -> Void
    let copy: () -> Void

    var body: some View {
        HStack(spacing: Space.xs) {
            Button(action: copy) { glyph("square.on.square") }
                .buttonStyle(.plain)
                .accessibilityLabel(String(localized: "Copy the answer", comment: "VoiceOver: copies Dot's answer"))
            ForEach(ReactionKind.allCases.filter { on.contains($0.rawValue) }) { reaction in
                Button { toggle(reaction) } label: {
                    Text(reaction.emoji).font(.footnote).padding(.horizontal, Space.xs).frame(minHeight: Metric.toolRow)
                        .background(Tone.nested, in: Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(String(localized: "Remove reaction: \(reaction.meaning)", comment: "VoiceOver: takes a reaction off; the argument is its meaning"))
            }
            Menu {
                ForEach(ReactionKind.allCases) { reaction in
                    Button { toggle(reaction) } label: { Text(verbatim: "\(reaction.emoji)  \(reaction.meaning)") }
                }
            } label: {
                glyph("face.smiling")
            }
            .accessibilityLabel(String(localized: "React", comment: "VoiceOver: the menu of reactions under an answer"))
            Text(at.formatted(date: .omitted, time: .shortened)).textRole(.label).foregroundStyle(Tone.tertiary).tabularNumbers()
            Spacer(minLength: 0)
        }
    }

    private func glyph(_ name: String) -> some View {
        Image(systemName: name)
            .font(.system(.footnote, weight: .regular))
            .symbolRenderingMode(.monochrome)
            .foregroundStyle(Tone.tertiary)
            .frame(width: 28, height: Metric.toolRow)
            .contentShape(Rectangle())
    }
}

/// The model's thinking of a step, a row of the stretch of work (items 1769 and 1880): one folded line in every state,
/// "Thinking" with its shimmer while it is written, its first sentence in the quiet ink once the step is stored; a tap
/// opens the whole of it in a box that scrolls in itself past 210 pt, the transcript staying where it is.
struct ThoughtRow: View {
    let text: String
    let live: Bool
    @State private var open: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.disclosureToggled) private var disclosureToggled: @MainActor () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                disclosureToggled()
                open.toggle()
            } label: {
                HStack(spacing: Space.xs) {
                    Symbol(name: "brain", context: .tool).foregroundStyle(Tone.tertiary).frame(width: Metric.iconSlot)
                    if live {
                        Text(String(localized: "Thinking", comment: "Capsule: the run's model is thinking")).textRole(.footnote).modifier(Shimmer())
                    } else {
                        // Only the word once the step is stored (item 1904, the owner): the thinking is read opened.
                        Text(String(localized: "Thought", comment: "Work log: a step's thinking, folded")).textRole(.footnote).foregroundStyle(Tone.secondary)
                    }
                    Spacer(minLength: Space.xs)
                    Symbol(name: "chevron.down", context: .status).foregroundStyle(Tone.glyph).rotationEffect(.degrees(open ? 180 : 0))
                }
                .frame(minHeight: Metric.toolRow)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .haptic(.selection, trigger: open)
            .accessibilityLabel(live ? String(localized: "Thinking", comment: "Capsule: the run's model is thinking") : String(localized: "Thought", comment: "Work log: a step's thinking, folded"))
            .accessibilityIdentifier("thought.row")
            .accessibilityValue(open ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure"))
            .accessibilityHint(String(localized: "Shows the whole thinking", comment: "VoiceOver hint of a thinking row"))
            if open {
                OutputBox(text: text, role: .footnote, points: 0, ink: Tone.secondary, anchorsAtEnd: live)
                .padding(.leading, Metric.iconSlot + Space.xs)
                .padding(.bottom, Space.xs)
                .transition(.opacity)
            }
        }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: open)
    }
}

/// Dot's mark on a row of its own tools (item 1769), as another coding agent marks its own calls with its name: Dot's face in the quiet
/// ink of the work log, the size of a tool's symbol, scaling with the text.
struct DotMark: View {
    @ScaledMetric(relativeTo: .footnote) private var size: CGFloat = 13

    var body: some View {
        ZStack {
            Circle().fill(Tone.tertiary)
            HStack(spacing: size * 0.15) {
                Capsule().fill(Tone.ground).frame(width: size * 0.12, height: size * 0.24)
                Capsule().fill(Tone.ground).frame(width: size * 0.12, height: size * 0.24)
            }
            .offset(y: -size * 0.02)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// A helper of the stream's group with its engine in words ("Claude Code · Opus 5.5 · High", `engineLine`).
struct HelperEntry: Equatable, Sendable {
    let helper: HelperView
    let engine: String
}

/// How many helpers of a group stand in one state.
struct HelperTally: Equatable, Sendable {
    let state: HelperState
    let count: Int
}

/// The group's states in the order its line says them: what works first, then what waits on the owner, then what
/// ended (failed before stopped before done); a state no helper is in is left out. Pure.
func helperTallies(_ states: [HelperState]) -> [HelperTally] {
    let order: [HelperState] = [.working, .stopping, .waitingOnYou, .failed, .stopped, .done]
    return order.compactMap { state in
        let count: Int = states.filter { $0 == state }.count
        return count == 0 ? nil : HelperTally(state: state, count: count)
    }
}

/// One state's count in words ("2 working", "1 done"). Pure.
func helperTallyWords(_ tally: HelperTally) -> String {
    switch tally.state {
    case .working: String(localized: "\(tally.count) working", comment: "Helpers group: how many helpers work now")
    case .stopping: String(localized: "\(tally.count) stopping", comment: "Helpers group: how many helpers are being stopped")
    case .waitingOnYou: String(localized: "\(tally.count) waiting on you", comment: "Helpers group: how many helpers wait for the owner")
    case .failed: String(localized: "\(tally.count) failed", comment: "Helpers group: how many helpers failed")
    case .stopped: String(localized: "\(tally.count) stopped", comment: "Helpers group: how many helpers the owner stopped")
    case .done: String(localized: "\(tally.count) done", comment: "Helpers group: how many helpers finished")
    }
}

/// The ink of a helper's state in the group: blue while it works, amber while it waits on the owner, red once it
/// failed, grey once it ended (item 1957). Pure.
func helperInk(_ state: HelperState) -> Color {
    switch state {
    case .working, .stopping: Tone.live
    case .waitingOnYou: Tone.warn
    case .failed: Tone.failure
    case .done, .stopped: Tone.tertiary
    }
}

/// The group's time: from its first helper's start to its last one's end, or on while one still works. Pure.
func helperGroupSpan(_ helpers: [HelperView]) -> (start: Date, end: Date?) {
    let start: Double = helpers.map(\.startedAt).min() ?? 0
    let live: Bool = helpers.contains { helperState($0.status).isLive }
    let end: Double? = live ? nil : helpers.map { $0.finishedAt ?? $0.startedAt }.max()
    return (Date(timeIntervalSince1970: start), end.map { Date(timeIntervalSince1970: $0) })
}

/// The helpers a run started, as ONE row of the stream (item 1957, after the owner's reference of another coding
/// agent's subagents): their providers' marks overlapping, "2 helpers", under it what they do in their states' inks
/// ("1 working · 1 done"), the group's time and a chevron. A tap opens a card of one row per helper, each opening its
/// own chat.
struct HelperGroupRow: View {
    let lines: [HelperEntry]
    let open: (String) -> Void
    @State private var expanded: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let large: Bool = dynamicTypeSize.isAccessibilitySize
        let states: [HelperState] = lines.map { helperState($0.helper.status) }
        let tallies: [HelperTally] = helperTallies(states)
        let span: (start: Date, end: Date?) = helperGroupSpan(lines.map(\.helper))
        let title: String = String(localized: "\(lines.count) helpers", comment: "Helpers group: how many helpers the run started")
        VStack(alignment: .leading, spacing: Space.xs) {
            Button {
                withAnimation(motionAnimation(.disclosure, reduceMotion: reduceMotion)) { expanded.toggle() }
            } label: {
                HStack(spacing: Space.s) {
                    ProviderMarks(providers: lines.map(\.helper.provider))
                    // At the accessibility sizes the words wrap whole and the clock stands under them: beside them it
                    // cut "2 helpers" to "2 hel…".
                    VStack(alignment: .leading, spacing: 1) {
                        Text(title).textRole(.footnote).foregroundStyle(Tone.text).lineLimit(large ? nil : 1)
                        Text(talliesText(tallies)).textRole(.label).lineLimit(large ? nil : 2)
                        if large { HelperClock(start: span.start, end: span.end) }
                    }
                    Spacer(minLength: Space.xs)
                    if !large { HelperClock(start: span.start, end: span.end) }
                    Symbol(name: expanded ? "chevron.up" : "chevron.down", context: .chevron).foregroundStyle(Tone.glyph)
                }
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(([title] + tallies.map(helperTallyWords)).joined(separator: ", "))
            .accessibilityValue(
                expanded
                    ? String(localized: "Expanded", comment: "VoiceOver value of an open group")
                    : String(localized: "Collapsed", comment: "VoiceOver value of a closed group"))
            .accessibilityHint(
                expanded
                    ? String(localized: "Hides the helpers", comment: "VoiceOver hint of the open helpers group")
                    : String(localized: "Shows each helper", comment: "VoiceOver hint of the closed helpers group"))
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("helpers.group")
            if expanded {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(lines, id: \.helper.runId) { line in
                        HelperCardRow(line: line, open: { open(line.helper.threadId) })
                    }
                }
                .padding(.horizontal, Space.s)
                .padding(.vertical, Space.xxs)
                .background(Tone.card, in: RoundedRectangle(cornerRadius: Radius.inner, style: .continuous))
                .transition(.opacity)
            }
        }
    }

    /// "1 working · 1 done", each count in its state's ink, the dots between in grey.
    private func talliesText(_ tallies: [HelperTally]) -> AttributedString {
        var text: AttributedString = AttributedString()
        for (index, tally) in tallies.enumerated() {
            if index > 0 {
                var dot: AttributedString = AttributedString(" · ")
                dot.foregroundColor = Tone.tertiary
                text += dot
            }
            var words: AttributedString = AttributedString(helperTallyWords(tally))
            words.foregroundColor = helperInk(tally.state)
            text += words
        }
        return text
    }
}

/// The providers' marks of a group, overlapping, at most three (the owner's reference).
struct ProviderMarks: View {
    let providers: [String]
    @ScaledMetric(relativeTo: .footnote) private var size: CGFloat = 26

    var body: some View {
        HStack(spacing: -size * 0.35) {
            ForEach(Array(providers.prefix(3).enumerated()), id: \.offset) { _, provider in
                Image(providerMarkAsset(provider)).resizable().renderingMode(.template).scaledToFit()
                    .foregroundStyle(Tone.secondary)
                    .padding(size * 0.24)
                    .frame(width: size, height: size)
                    .background(Tone.nested, in: Circle())
                    // The ground around each disc keeps the overlap legible without a stroke (plan §3: no strokes).
                    .padding(1.5)
                    .background(Tone.ground, in: Circle())
            }
        }
        .accessibilityHidden(true)
    }
}

/// A clock of the helpers' time, ticking from their start while one works (`end` nil), fixed once they all ended.
struct HelperClock: View {
    let start: Date
    let end: Date?

    var body: some View {
        // Ticks counted from the start: a schedule from `.now` began again at every drawing.
        TimelineView(.periodic(from: start, by: 1)) { context in
            Text(elapsedWords(from: start, to: end ?? context.date)).textRole(.label).foregroundStyle(Tone.tertiary)
                .tabularNumbers().lineLimit(1).fixedSize()
        }
        .accessibilityHidden(true)
    }
}

/// One helper in the group's card (the owner's reference): a dot in its state's ink, its title on one line, "· Working"
/// in that ink, its time and a chevron that opens its chat; under them, in grey, its provider's mark and its engine.
/// At the accessibility sizes the title takes three lines and the time its own.
struct HelperCardRow: View {
    let line: HelperEntry
    let open: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    @ScaledMetric(relativeTo: .caption) private var mark: CGFloat = 13

    var body: some View {
        let state: HelperState = helperState(line.helper.status)
        let start: Date = Date(timeIntervalSince1970: line.helper.startedAt)
        let end: Date? = line.helper.finishedAt.map { Date(timeIntervalSince1970: $0) }
        let large: Bool = dynamicTypeSize.isAccessibilitySize
        Button(action: open) {
            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                Circle().fill(helperInk(state)).frame(width: 7, height: 7)
                    .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                VStack(alignment: .leading, spacing: Space.xxs) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.xxs) {
                        Text(line.helper.shortTitle).textRole(.footnote).foregroundStyle(Tone.text)
                            .lineLimit(large ? 3 : 1).truncationMode(.tail)
                            .layoutPriority(0)
                        Text("· " + state.word).textRole(.footnote).foregroundStyle(helperInk(state))
                            .lineLimit(1).fixedSize().layoutPriority(1)
                    }
                    HStack(alignment: .center, spacing: Space.xxs) {
                        Image(providerMarkAsset(line.helper.provider)).resizable().renderingMode(.template).scaledToFit()
                            .frame(width: mark, height: mark).foregroundStyle(Tone.tertiary)
                        Text(line.engine).textRole(.label).foregroundStyle(Tone.tertiary).lineLimit(large ? 2 : 1)
                    }
                    if large { HelperClock(start: start, end: state.isLive ? nil : end ?? start) }
                }
                Spacer(minLength: Space.xs)
                if !large { HelperClock(start: start, end: state.isLive ? nil : end ?? start) }
                Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
            }
            .padding(.vertical, Space.s)
            .frame(minHeight: Metric.hit)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([line.helper.shortTitle, state.word, line.engine].filter { !$0.isEmpty }.joined(separator: ", "))
        .accessibilityHint(String(localized: "Opens the helper's chat", comment: "VoiceOver hint of a helper row"))
        .accessibilityAddTraits(.isButton)
        .accessibilityIdentifier("helper.row")
    }
}
