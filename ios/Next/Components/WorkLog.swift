import DotEngine
import SwiftUI
import UIKit

// The work log of a chat (wave N2, plan §4.1): one-line tool rows, the fold of a finished stretch, the plan as a
// checklist, the helpers as one group, the asks left in place, how a run ended, and Dot's notes.

/// What the transcript stacks: one item, or consecutive tool calls drawn as one tight block (the reference's 28 pt
/// pitch, §19: no gap between the rows of a stretch of work).
enum TranscriptBlock: Identifiable {
    case item(TranscriptItem)
    case tools([ToolEntry])

    var id: String {
        switch self {
        case .item(let item): item.id
        case .tools(let entries): "tools:" + (entries.first?.id ?? "")
        }
    }

    static func of(_ items: [TranscriptItem]) -> [TranscriptBlock] {
        var blocks: [TranscriptBlock] = []
        for item in items {
            if case .tool(let entry) = item {
                if case .tools(let run)? = blocks.last {
                    blocks[blocks.count - 1] = .tools(run + [entry])
                } else {
                    blocks.append(.tools([entry]))
                }
            } else {
                blocks.append(.item(item))
            }
        }
        return blocks
    }
}

/// A stretch of tool calls, no gap between them.
struct ToolRun: View {
    let entries: [ToolEntry]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(entries, id: \.id) { entry in WorkToolRow(entry: entry).id(entry.id) }
        }
    }
}

/// A tool call as one quiet 28 pt line (§19): a shimmer while it runs, a red ✕ when it failed; a tap opens what it was
/// asked and what it said (monospaced, scrolling past 210 pt) with its screenshots; a long press copies both.
struct WorkToolRow: View {
    let entry: ToolEntry
    @State private var open: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    private var detail: String {
        [entry.args, entry.output].filter { !$0.isEmpty }.joined(separator: "\n\n")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
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
            .accessibilityLabel(accessibilityWords)
            .accessibilityValue(open ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure"))
            if !entry.images.isEmpty, !open {
                screenshots.padding(.leading, Metric.iconSlot + Space.xs).padding(.bottom, Space.xxs)
            }
            if open {
                VStack(alignment: .leading, spacing: Space.xs) {
                    if !detail.isEmpty {
                        ScrollView {
                            Text(detail).textRole(.code).foregroundStyle(Tone.secondary).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading).padding(Space.s)
                        }
                        .frame(maxHeight: Metric.toolDetailMax)
                        .fixedSize(horizontal: false, vertical: true)
                        .background(Tone.nested, in: rounded(Radius.inner))
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
            Symbol(name: toolSymbol(entry.kind), context: .tool).frame(width: Metric.iconSlot)
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
                ScrollView([.horizontal, .vertical]) { Image(uiImage: image).resizable().scaledToFit().frame(minWidth: 320) }
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

    init(fold: WorkFold, row: @escaping (TranscriptItem) -> AnyView) {
        self.fold = fold
        self.row = row
        _open = State(initialValue: fold.hasFailure)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Button {
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
                        case .tools(let entries): ToolRun(entries: entries)
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

/// The run's plan as a checklist card.
struct PlanCard: View {
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
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Tone.card, in: rounded(Radius.bubble))
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

/// How a run ended other than done: the cause in one line and Try again (plan §4.1 States).
struct RunEndedCard: View {
    let status: String
    let detail: String
    let retry: (() -> Void)?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            Image(systemName: status == "failed" ? "exclamationmark.triangle" : "stop.circle")
                .foregroundStyle(status == "failed" ? Tone.failure : Tone.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: Space.xs) {
                Text(title).textRole(.footnote).fontWeight(.medium).foregroundStyle(status == "failed" ? Tone.failure : Tone.text)
                if !detail.isEmpty { Text(detail).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(3) }
                if let retry {
                    Button(String(localized: "Try again", comment: "Under a refused message: sends it again"), action: retry)
                        .buttonStyle(SecondaryButtonStyle())
                }
            }
            Spacer(minLength: 0)
        }
        .padding(Space.m)
        .background(Tone.nested, in: rounded(Radius.bubble))
        .accessibilityElement(children: .combine)
    }

    private var title: String {
        switch status {
        case "failed": String(localized: "The run failed", comment: "Notice at the end of a failed run")
        case "cancelled": String(localized: "The run was stopped", comment: "Notice at the end of a stopped run")
        default: String(localized: "The run ended", comment: "Notice at the end of a run that ended another way")
        }
    }
}

/// The Claude plan's usage limit stopped the run: a neutral card with when it can go on (R-c #9).
struct UsageLimitCard: View {
    let resetsAt: Double?
    let resume: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(String(localized: "The Claude plan's usage limit was reached", comment: "Usage limit card title"))
                .textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
            Text(line).textRole(.footnote).foregroundStyle(Tone.secondary)
            Button(String(localized: "Resume now", comment: "Usage limit card: starts the work again now"), action: resume).buttonStyle(SecondaryButtonStyle())
        }
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Tone.nested, in: rounded(Radius.bubble))
        .accessibilityElement(children: .combine)
    }

    private var line: String {
        guard let resetsAt else { return String(localized: "It resets within a few hours.", comment: "Usage limit card when the plan did not say when") }
        let clock: String = Date(timeIntervalSince1970: resetsAt).formatted(date: .omitted, time: .shortened)
        return String(localized: "It resets at \(clock).", comment: "Usage limit card; the argument is the clock time")
    }
}

/// The helpers a run started, each with its current step (plan §18, R-e #2): who, what it does now, its state.
struct HelperGroupCard: View {
    let helpers: [HelperView]
    let open: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let working: Int = helpers.filter { helperState($0.status).isLive }.count
        Button(action: open) {
            VStack(alignment: .leading, spacing: Space.xs) {
                HStack(spacing: Space.xs) {
                    if !dynamicTypeSize.isAccessibilitySize {
                        Symbol(name: "person.2", context: .tool).foregroundStyle(Tone.secondary).frame(width: Metric.iconSlot)
                    }
                    Text(String(localized: "\(helpers.count) helpers", comment: "Helper group header, first part; the argument is how many helpers the run started") + " · " + String(localized: "\(working) working", comment: "Helper group header, second part; the argument is how many still work"))
                        .textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary).tabularNumbers()
                    Spacer(minLength: 0)
                    Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
                }
                ForEach(helpers, id: \.runId) { helper in
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        Circle().fill(helperState(helper.status).tone.color).frame(width: 7, height: 7).frame(width: Metric.iconSlot)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(helper.shortTitle).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(1)
                            Text(stepWords(helper)).textRole(.label).foregroundStyle(Tone.tertiary).lineLimit(2)
                        }
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel([helper.shortTitle, helperState(helper.status).word, stepWords(helper)].joined(separator: ", "))
                }
            }
            .padding(Space.m)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Tone.card, in: rounded(Radius.bubble))
            .contentShape(rounded(Radius.bubble))
        }
        .buttonStyle(.plain)
        .accessibilityHint(String(localized: "Opens the helpers", comment: "VoiceOver hint of the helper group in a chat"))
    }

    private func stepWords(_ helper: HelperView) -> String {
        guard helperState(helper.status).isLive else { return helperState(helper.status).word }
        guard let step = helper.step else { return String(localized: "Starting", comment: "Status word: a run is about to start") }
        let entry: ToolEntry = ToolEntry(
            id: "", tool: step.tool, kind: ToolKind.of(step.tool), target: step.target, args: "", output: "", state: .running, images: [],
            startedAt: step.at, endedAt: nil)
        return toolLine(entry)
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

/// The model's thinking of a step (Show all, Hide successful, Hide all; never in Zen): "Thinking" with its last lines
/// while it is written, "Thought" folded once the step is stored; a tap opens the whole of it.
struct ThoughtRow: View {
    let text: String
    let live: Bool
    @State private var open: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            Button { open.toggle() } label: {
                HStack(spacing: Space.xs) {
                    Symbol(name: "brain", context: .tool).foregroundStyle(Tone.tertiary)
                    if live {
                        Text(String(localized: "Thinking", comment: "Capsule: the run's model is thinking")).textRole(.label).modifier(Shimmer())
                    } else {
                        Text(String(localized: "Thought", comment: "Work log: a step's thinking, folded")).textRole(.label).foregroundStyle(Tone.secondary)
                    }
                    Spacer(minLength: 0)
                    Symbol(name: open ? "chevron.up" : "chevron.down", context: .chevron).foregroundStyle(Tone.tertiary)
                }
                .frame(minHeight: Metric.toolRow)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(String(localized: "Shows the whole thinking", comment: "VoiceOver hint of a thinking row"))
            if open || live {
                Text(text)
                    .textRole(.label)
                    .foregroundStyle(Tone.tertiary)
                    .lineLimit(open ? nil : 3)
                    .truncationMode(.head)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
        }
    }
}
