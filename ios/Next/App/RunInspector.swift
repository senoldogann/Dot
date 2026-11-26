import DotEngine
import QuickLook
import SwiftUI

/// The run inspector, "This chat" (plan §4.1, the owner's shots `dotnext-inspector-problem.png` and of 2026-11-26): plain
/// sections on the sheet's own ground (no card, a hairline between rows, a small header close above them), one text style
/// for every label (body, ink) and every value (body, secondary, tabular), rows of the 44 pt floor and no taller. What it
/// holds, each on the web's own route (§26):
/// - the run: state, started, took, approval mode; the model as the owner reads it (its name and
///   level on one row, "Opus 5.5 · Medium", its provider on a row of its own);
/// - the recap (`/runs/{id}/recap`) with Write a new one;
/// - the cost (`/runs/{id}/stats`): time, where it went, tokens in (with the cached share) and out, steps, against the
///   cap and what like tasks spend; Dot holds no price per model, so there is no money (the footer says so);
/// - the plan; the helpers, each opening its own chat;
/// - the changes, each file's diff in Quick Look and, for a file as the runs left it, Undo (asked first);
/// - the commands that run in the background with Stop (`DELETE /threads/{id}/terminals/{sid}`).
struct RunInspector: View {
    let thread: String
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var actions: RunActions?
    @State private var changes: RunChanges?
    @State private var recap: Loadable<RecapView> = .loading
    @State private var costs: Loadable<RunCosts> = .loading
    @State private var terminals: [TerminalView] = []
    @State private var preview: URL?
    @State private var undoing: FileChange?
    @State private var undone: Set<String> = []
    @State private var stopped: Set<String> = []
    @State private var writingRecap: Bool = false
    @AppStorage(PrefKey.showSteps) private var showStepsEverywhere: Bool = true
    @State private var failure: String?

    private var view: ThreadView? { model.chats[thread]?.view }
    private var run: RunRecord? { view.flatMap { DotEngine.ownRuns(of: $0).last } }
    private var macPlace: String { String(localized: "the chat on the web", comment: "Where on the Mac a chat's action is done") }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xs) {
                if let failure { Notice(tone: .failure, message: failure, dismiss: { self.failure = nil }) }
                GroupedSection(header: String(localized: "Messages", comment: "Chat settings section: what a chat shows"), footer: nil) {
                    ToggleRow(
                        systemImage: "list.bullet.indent", title: String(localized: "Show steps", comment: "Settings switch: a chat shows its runs' tool calls and thoughts (item 1915)"),
                        description: String(localized: "For this chat; Settings › Chat sets it for every chat.", comment: "This chat sheet: the Show steps switch is this chat's own (item 1915)"),
                        isOn: Binding(get: { model.stepsOverrides[thread] ?? showStepsEverywhere }, set: { model.stepsOverrides[thread] = $0 }))
                    .accessibilityIdentifier("inspector.showSteps")
                }
                if let run {
                    runSection(run)
                    modelSection(run)
                    recapSection(run)
                    costSection
                } else {
                    EmptyState(
                        title: String(localized: "Nothing ran yet", comment: "Inspector: the chat has no run"), systemImage: "circle.dotted",
                        detail: String(localized: "What a run did and cost shows here once one started.", comment: "Inspector empty state detail"), actionTitle: nil, action: {})
                }
                if let plan = planSteps, !plan.isEmpty {
                    InspectorSection(header: String(localized: "Plan", comment: "Inspector section: the run's plan"), footer: nil) {
                        PlanSteps(steps: plan).padding(.vertical, Space.s)
                    }
                }
                helpersSection
                changesSection
                backgroundSection
            }
            .padding(.horizontal, Space.gutter)
            .padding(.bottom, Space.m)
            .readableColumn()
        }
        .refreshable { await load() }
        .task(id: run?.id) { await load() }
        .quickLookPreview($preview)
        .confirmationDialog(
            String(localized: "Undo the change to this file?", comment: "Inspector: confirmation of a file's undo"),
            isPresented: Binding(get: { undoing != nil }, set: { if !$0 { undoing = nil } }), titleVisibility: .visible, presenting: undoing
        ) { file in
            Button(String(localized: "Undo the change", comment: "Inspector: takes a file back to before the runs changed it"), role: .destructive) { undo(file) }
        } message: { file in
            Text(
                file.created
                    ? String(localized: "The task made this file; undo deletes it.", comment: "Inspector: what the undo of a created file does")
                    : String(localized: "The file goes back to how it was before the task changed it.", comment: "Inspector: what the undo of a changed file does"))
        }
        .inspectorSheet(title: String(localized: "This chat", comment: "Title of the run inspector"), close: close)
    }

    // MARK: Sections

    private func runSection(_ run: RunRecord) -> some View {
        InspectorSection(header: String(localized: "Run", comment: "Inspector section: the newest run"), footer: nil) {
            FactRow(title: String(localized: "State", comment: "Inspector row: the run's status"), value: statusWords(run.status))
            FactRow(
                title: String(localized: "Started", comment: "Inspector row: when the run started"),
                value: Date(timeIntervalSince1970: run.createdAt).formatted(date: .abbreviated, time: .shortened))
            FactRow(
                title: String(localized: "Took", comment: "Inspector row: how long the run worked"),
                value: durationWords(Int((run.finishedAt ?? Date().timeIntervalSince1970) - run.createdAt), locale: locale))
            if let approvals = run.approvals {
                FactRow(title: String(localized: "Approval mode", comment: "Inspector row: the run's approval mode"), value: approvalWords(approvals))
            }
        }
    }

    private func modelSection(_ run: RunRecord) -> some View {
        // The chat's choices as the composer's chip and its settings read them; the run's own when none are kept.
        let choices: ChatChoices? = view?.newest?.choices
        let ref: String = choices.flatMap { effectiveModel($0, defaultRef: model.chatDefaults?.model) } ?? run.model
        let parts: ModelParts = modelParts(ref)
        let label: String = model.models.flatMap(\.models).first { $0.ref == ref }?.label ?? parts.model
        let effort: String? = choices.map(\.effort) ?? run.effort
        return InspectorSection(header: String(localized: "Model for this chat", comment: "Inspector section: the chat's model"), footer: nil) {
            // "DeepSeek V4.1 Flash · Max", and who serves it on a row of its own.
            FactRow(
                title: String(localized: "Model", comment: "Inspector row: the model and reasoning level of this chat"),
                value: [label, effort.flatMap { $0.isEmpty ? nil : effortTitle($0) }].compactMap { $0 }.joined(separator: " · "))
            if let provider = parts.provider {
                FactRow(title: String(localized: "Provider", comment: "Inspector row: who serves the model"), value: providerTitle(provider))
            }
            if let meter = contextMeter {
                FactRow(title: String(localized: "Context used", comment: "Inspector row: how full the model's context is"), value: meter.formatted(.percent.precision(.fractionLength(0))))
            }
        }
    }

    @ViewBuilder private func recapSection(_ run: RunRecord) -> some View {
        if case .loaded(let read) = recap, read.enabled {
            InspectorSection(header: String(localized: "Recap", comment: "Inspector section: the run's recap"), footer: recapFooter(read)) {
                if let written = read.recap {
                    Text(written.text).textRole(.body).foregroundStyle(Tone.text).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, Space.s)
                } else {
                    Text(String(localized: "No recap yet.", comment: "Inspector: the run has no recap"))
                        .textRole(.body).foregroundStyle(Tone.secondary).rowInsets()
                }
                RowButton(
                    title: read.recap == nil
                        ? String(localized: "Write a recap", comment: "Inspector: asks for a recap of the run now")
                        : String(localized: "Write a new recap", comment: "Inspector: asks for a fresh recap of the run"),
                    busy: writingRecap
                ) { writeRecap(run) }
                .accessibilityIdentifier("inspector.recap")
            }
        }
    }

    private func recapFooter(_ read: RecapView) -> String? {
        guard let written = read.recap else { return nil }
        return String(
            localized: "Written \(Date(timeIntervalSince1970: written.writtenAt).formatted(.relative(presentation: .named))) after \(written.steps) steps",
            comment: "Inspector: when the recap was written; first argument a relative time, second the steps it covers")
    }

    @ViewBuilder private var costSection: some View {
        switch costs {
        case .loading:
            EmptyView()
        case .failed:
            EmptyView()
        case .loaded(let cost):
            InspectorSection(
                header: String(localized: "Cost", comment: "Inspector section: what the run cost"),
                footer: String(localized: "Dot keeps no prices, so a task's cost is its time and tokens.", comment: "Inspector: why the cost shows no money")
            ) {
                FactRow(title: String(localized: "Time", comment: "Inspector cost row: the run's wall time"), value: durationWords(Int(cost.minutes * 60), locale: locale))
                if cost.stats.time.modelSeconds > 0 {
                    FactRow(title: String(localized: "Model", comment: "Inspector cost row: the seconds the model took"), value: durationWords(Int(cost.stats.time.modelSeconds), locale: locale))
                }
                if cost.toolSeconds > 0 {
                    FactRow(title: String(localized: "Actions", comment: "Inspector cost row: the seconds the tools took"), value: durationWords(Int(cost.toolSeconds), locale: locale))
                }
                if cost.waitedS >= 1 {
                    FactRow(title: String(localized: "Waited for you", comment: "Inspector cost row: the time spent waiting for the owner"), value: durationWords(Int(cost.waitedS), locale: locale))
                }
                FactRow(title: String(localized: "Steps", comment: "Inspector cost row: how many times Dot went back to the model"), value: cost.stats.steps.formatted(.number.locale(locale)))
                FactRow(title: String(localized: "Tokens in", comment: "Inspector cost row: what was sent to the model"), value: tokensIn(cost))
                FactRow(title: String(localized: "Tokens out", comment: "Inspector cost row: what the model wrote"), value: tokenWords(cost.stats.outputTokens, locale: locale))
                if let budget = cost.budget, let line = budgetWords(budget) {
                    FactRow(title: String(localized: "With helpers", comment: "Inspector cost row: the tokens of the run and its helpers against its cap"), value: line)
                }
            }
        }
    }

    @ViewBuilder private var helpersSection: some View {
        let helpers: [HelperView] = view?.helpers ?? []
        if !helpers.isEmpty {
            InspectorSection(header: String(localized: "Helpers", comment: "Inspector section: the chat's helpers"), footer: nil) {
                ForEach(helpers, id: \.runId) { helper in
                    NavigationLink {
                        ChatScreen(thread: helper.threadId, initialPanel: nil, editsFirstQueued: false)
                    } label: {
                        HStack(spacing: Space.s) {
                            Text(helper.shortTitle).textRole(.body).foregroundStyle(Tone.text).lineLimit(1)
                            Spacer(minLength: Space.s)
                            Text(helperState(helper.status).word).textRole(.body).foregroundStyle(Tone.secondary)
                            Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
                        }
                        .rowInsets()
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(RowPressStyle())
                    .accessibilityHint(String(localized: "Opens the helper's chat", comment: "VoiceOver hint of a helper row"))
                }
            }
        }
    }

    @ViewBuilder private var changesSection: some View {
        let files: [FileChange] = changes?.files ?? []
        InspectorSection(header: String(localized: "Changes", comment: "Inspector section: what the runs changed"), footer: unrecoverableWords) {
            if files.isEmpty {
                Text(String(localized: "No file changed yet.", comment: "Inspector: no changes")).textRole(.body).foregroundStyle(Tone.secondary).rowInsets()
            }
            ForEach(files, id: \.path) { file in
                HStack(spacing: Space.s) {
                    Button { preview = diffFile(file) } label: {
                        HStack(spacing: Space.s) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text((file.path as NSString).lastPathComponent).textRole(.body).foregroundStyle(Tone.text).lineLimit(1)
                                Text(fileLine(file)).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(1).truncationMode(.head)
                            }
                            Spacer(minLength: Space.s)
                            Text(verbatim: "+\(file.added) −\(file.removed)").textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint(String(localized: "Shows the change", comment: "VoiceOver hint of a changed file row"))
                    if undone.contains(file.path) {
                        Text(String(localized: "Undone", comment: "Inspector: a file's change was taken back")).textRole(.body).foregroundStyle(Tone.secondary)
                    } else if canUndo(file) {
                        Button(String(localized: "Undo", comment: "Inspector: takes a file's change back")) { undoing = file }
                            .textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text)
                            .frame(minWidth: Metric.hit, minHeight: Metric.hit)
                            .accessibilityIdentifier("inspector.undo")
                    }
                }
                .rowInsets()
            }
        }
    }

    @ViewBuilder private var backgroundSection: some View {
        let commands: [TerminalView] = terminals.filter { $0.background && !$0.ended && !stopped.contains($0.id) }
        let others: [DotEngine.BackgroundWork] = (view?.background ?? []).filter { $0.kind != "command" }
        if !commands.isEmpty || !others.isEmpty {
            InspectorSection(header: String(localized: "Working in the background", comment: "Inspector section: the chat's background commands, helpers and loops"), footer: nil) {
                ForEach(commands) { command in
                    HStack(spacing: Space.s) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(command.command).textRole(.code).foregroundStyle(Tone.text).lineLimit(1).truncationMode(.tail)
                            Text(sinceWords(command.startedAt)).textRole(.footnote).foregroundStyle(Tone.secondary)
                        }
                        Spacer(minLength: Space.s)
                        Button(String(localized: "Stop", comment: "Chat toolbar: stops the run at work")) { stop(command) }
                            .textRole(.body).fontWeight(.medium).foregroundStyle(Tone.failure)
                            .frame(minWidth: Metric.hit, minHeight: Metric.hit)
                            .accessibilityIdentifier("inspector.stopCommand")
                    }
                    .rowInsets()
                }
                ForEach(others, id: \.id) { work in
                    HStack(spacing: Space.s) {
                        Text(work.title).textRole(.body).foregroundStyle(Tone.text).lineLimit(1)
                        Spacer(minLength: Space.s)
                        Text(
                            work.kind == "loop"
                                ? String(localized: "Loop", comment: "Background work kind: a repeating message of the chat")
                                : String(localized: "Subagent", comment: "Background work kind: a helper let go to the background")
                        )
                        .textRole(.body).foregroundStyle(Tone.secondary)
                    }
                    .rowInsets()
                }
            }
        }
    }

    // MARK: Reads and doings

    private func load() async {
        guard let id = run?.id else { return }
        async let acted: RunActions? = model.actions(of: id)
        async let changed: RunChanges? = model.changes(of: id)
        async let recapped: Loadable<RecapView> = model.recap(of: id)
        async let costed: Loadable<RunCosts> = model.costs(of: id)
        async let sessions: Loadable<[TerminalView]> = model.terminals(of: thread)
        actions = await acted
        changes = await changed
        recap = await recapped
        costs = await costed
        terminals = (await sessions).value ?? []
    }

    private func writeRecap(_ run: RunRecord) {
        writingRecap = true
        Task {
            let outcome: Result<RecapView, SettingsFailureBox> = await model.writeRecap(of: run.id)
            writingRecap = false
            switch outcome {
            case .success(let fresh): recap = .loaded(fresh)
            case .failure(let box): failure = failureWords(box.failure, macPlace: macPlace)
            }
        }
    }

    private func undo(_ file: FileChange) {
        guard let run else { return }
        Task {
            switch await model.undo(run: run.id, path: file.path) {
            case .success(let fresh):
                actions = fresh
                undone.insert(file.path)
                changes = await model.changes(of: run.id)
            case .failure(let box):
                failure = failureWords(box.failure, macPlace: macPlace)
            }
        }
    }

    private func stop(_ command: TerminalView) {
        stopped.insert(command.id)
        Task {
            if case .failure(let box) = await model.closeTerminal(thread: thread, session: command.id) {
                stopped.remove(command.id)
                failure = failureWords(box.failure, macPlace: macPlace)
            }
        }
    }

    // MARK: Words

    /// Whether one tap takes the file back: the undo plan holds it as the runs left it (`ready`).
    private func canUndo(_ file: FileChange) -> Bool {
        actions?.edits.contains { $0.path == file.path && $0.state == "ready" } ?? false
    }

    private var unrecoverableWords: String? {
        guard let actions, actions.unrecoverable > 0 else { return nil }
        return String(localized: "\(actions.unrecoverable) steps no undo can take back", comment: "Inspector: steps that acted outside the undo journal; the argument is how many")
    }

    private func fileLine(_ file: FileChange) -> String {
        let folder: String = (file.path as NSString).deletingLastPathComponent
        if file.created { return String(localized: "New · \(folder)", comment: "Inspector: a file the task made; the argument is its folder") }
        if file.gone { return String(localized: "Deleted · \(folder)", comment: "Inspector: a file the task deleted; the argument is its folder") }
        return folder
    }

    private func tokensIn(_ cost: RunCosts) -> String {
        let read: String = tokenWords(cost.stats.inputTokens, locale: locale)
        guard let share = cost.stats.time.cachedShare, share > 0 else { return read }
        return String(
            localized: "\(read) · \(share.formatted(.percent.precision(.fractionLength(0)).locale(locale))) cached",
            comment: "Inspector cost row: tokens in and the share the provider had cached; first argument the tokens, second the share")
    }

    private func budgetWords(_ budget: RunBudget) -> String? {
        let spent: String = tokenWords(budget.spent, locale: locale)
        if budget.cap > 0 {
            return String(localized: "\(spent) of \(tokenWords(budget.cap, locale: locale))", comment: "Inspector cost row: tokens spent against the cap")
        }
        if let estimate = budget.estimate {
            return String(
                localized: "\(spent) · usually \(tokenWords(estimate.median, locale: locale))",
                comment: "Inspector cost row: tokens spent and what like tasks usually spend")
        }
        return budget.spent > 0 ? spent : nil
    }

    private func sinceWords(_ startedAt: Double) -> String {
        String(
            localized: "Running for \(durationWords(Int(Date().timeIntervalSince1970 - startedAt), locale: locale))",
            comment: "Inspector: how long a background command has run; the argument is a duration")
    }

    private var planSteps: [PlanStep]? {
        guard let run, let events = model.chats[thread]?.runs[run.id]?.events else { return nil }
        guard let plan = events.last(where: { $0.kind == "plan" }) else { return nil }
        return (plan.data["steps"]?.array ?? []).compactMap { step in
            step["text"]?.string.map { PlanStep(text: $0, status: step["status"]?.string ?? "pending") }
        }
    }

    /// The share of the model's context the newest step read.
    private var contextMeter: Double? {
        guard let run, let events = model.chats[thread]?.runs[run.id]?.events else { return nil }
        guard let used = events.last(where: { $0.data["input_tokens"]?.int != nil })?.data["input_tokens"]?.int else { return nil }
        guard let window = model.models.flatMap(\.models).first(where: { $0.ref == run.model })?.contextLength, window > 0 else { return nil }
        return min(1, Double(used) / Double(window))
    }

    private func diffFile(_ file: FileChange) -> URL? {
        let name: String = (file.path as NSString).lastPathComponent + ".diff"
        let url: URL = FileManager.default.temporaryDirectory.appending(path: name)
        do {
            try Data(file.diff.utf8).write(to: url, options: .atomic)
            return url
        } catch {
            return nil
        }
    }

    private func statusWords(_ status: String) -> String {
        switch status {
        case "running", "ending": ChatStatus.working.word
        case "queued": ChatStatus.starting.word
        case "blocked": ChatStatus.waitingOnYou.word
        case "failed": ChatStatus.failed.word
        case "cancelled": String(localized: "Stopped", comment: "Helper state: the helper was stopped")
        default: ChatStatus.done.word
        }
    }
}

/// A fact of the inspector: the label in body ink at the leading edge, the value in body secondary at the trailing edge
/// (tabular), on one 44 pt row; at the accessibility sizes the value stands under the label.
struct FactRow: View {
    let title: String
    let value: String
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let layout: AnyLayout =
            dynamicTypeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 2)) : AnyLayout(HStackLayout(alignment: .firstTextBaseline, spacing: Space.s))
        layout {
            Text(title).textRole(.body).foregroundStyle(Tone.text)
            if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: Space.s) }
            Text(value).textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers().multilineTextAlignment(.trailing)
        }
        .rowInsets()
        .accessibilityElement(children: .combine)
    }
}

/// An action as a row of its own: the words in ink, a spinner while it works.
struct RowButton: View {
    let title: String
    let busy: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.s) {
                Text(title).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text)
                Spacer(minLength: 0)
                if busy { ProgressView().controlSize(.small) }
            }
            .rowInsets()
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
        .disabled(busy)
    }
}

extension View {
    /// A row of a plain section: the page's own side margin (the section adds none), 7 pt above and below, the 44 pt floor.
    func rowInsets() -> some View {
        padding(.vertical, Space.xs)
            .frame(maxWidth: .infinity, minHeight: Metric.hit, alignment: .leading)
    }
}

/// A section of the inspector: a small header in the quiet ink close above its rows, the rows on the sheet's ground with a
/// hairline between them, an optional footer in the faint ink. No card: the sheet's chrome is the only raised thing.
struct InspectorSection<Content: View>: View {
    let header: String?
    let footer: String?
    @ViewBuilder let content: () -> Content
    @Environment(\.displayScale) private var displayScale: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let header {
                Text(header).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
                    .frame(maxWidth: .infinity, minHeight: 32, alignment: .bottomLeading)
                    .padding(.bottom, Space.xxs)
                    .accessibilityAddTraits(.isHeader)
            }
            Group(subviews: content()) { subviews in
                ForEach(subviews) { subview in
                    subview
                    if subview.id != subviews.last?.id {
                        Rectangle().fill(Tone.separator).frame(height: 1 / displayScale)
                    }
                }
            }
            if let footer {
                Text(footer).textRole(.footnote).foregroundStyle(Tone.tertiary).padding(.top, Space.xs)
            }
        }
    }
}
