import DotEngine
import QuickLook
import SwiftUI

/// The chat's queue (owner's reference shots `ref-queued-sheet.png`, `ref-editing-queued.png`, §20): each queued message
/// with a "Send now" ink pill; a tap edits it in the composer; a swipe removes it with Undo; a drag (or the VoiceOver
/// actions Move up / Move down) reorders; a held queue offers "Resume queue".
struct QueueSheet: View {
    let thread: String
    let edit: (QueuedMessageView) -> Void
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var removing: Set<String> = []
    @State private var toast: ToastMessage?
    @State private var removal: [UUID: String] = [:]

    private var queue: QueueView? { model.chats[thread]?.view?.queue }
    private var messages: [QueuedMessageView] { (queue?.messages ?? []).filter { !removing.contains($0.id) } }

    var body: some View {
        Group {
            if messages.isEmpty {
                EmptyState(
                    title: String(localized: "Nothing queued", comment: "Queue sheet empty state"), systemImage: "list.number",
                    detail: String(localized: "A message written while Dot works waits here and starts after the run.", comment: "Queue sheet empty state detail"),
                    actionTitle: nil, action: {})
            } else {
                List {
                    if queue?.held == true {
                        HStack(spacing: Space.s) {
                            Text(String(localized: "The queue is held: nothing starts until you resume it.", comment: "Queue sheet: the queue is held"))
                                .textRole(.footnote).foregroundStyle(Tone.secondary)
                            Spacer(minLength: 0)
                            Button(String(localized: "Resume queue", comment: "Queue sheet: lets a held queue go on")) { model.perform(.resumeQueue(thread: thread)) }
                                .buttonStyle(SecondaryButtonStyle())
                        }
                        .listRowBackground(Color.clear)
                    }
                    ForEach(messages, id: \.id) { message in
                        row(message)
                            .listRowBackground(Color.clear)
                            .listRowSeparatorTint(Tone.separator)
                            .alignmentGuide(.listRowSeparatorLeading) { _ in 0 }
                            .listRowInsets(EdgeInsets(top: Space.xs, leading: Space.l, bottom: Space.xs, trailing: Space.l))
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button(String(localized: "Remove", comment: "Queue sheet: removes a queued message"), systemImage: "trash", role: .destructive) { remove(message) }
                                    .tint(Tone.failure)
                            }
                    }
                    .onMove { from, to in
                        var ids: [String] = messages.map(\.id)
                        ids.move(fromOffsets: from, toOffset: to)
                        model.perform(.reorder(thread: thread, ids: ids))
                    }
                }
                .listStyle(.plain)
            }
        }
        .toastHost($toast, seconds: 4) { message in
            // Undo: the message stays.
            if let id = removal[message.id] { removing.remove(id) }
            removal[message.id] = nil
        }
        .onChange(of: toast) { old, new in
            // The toast went without Undo: the removal is sent.
            guard let old, old != new, let id = removal[old.id] else { return }
            removal[old.id] = nil
            if removing.contains(id) { model.perform(.removeQueued(thread: thread, message: id)) }
        }
        .inspectorSheet(title: String(localized: "Queued", comment: "Title of the queue sheet"), close: close)
    }

    private func row(_ message: QueuedMessageView) -> some View {
        let ids: [String] = messages.map(\.id)
        let at: Int = ids.firstIndex(of: message.id) ?? 0
        return HStack(spacing: Space.s) {
            Button { edit(message) } label: {
                Text(message.text).textRole(.body).foregroundStyle(Tone.text).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                    .frame(minHeight: Metric.hit).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("queue.row")
            .accessibilityHint(String(localized: "Edits it in the composer", comment: "VoiceOver hint of a queued message"))
            Button { model.perform(.steerQueued(thread: thread, message: message.id)) } label: {
                Label(String(localized: "Send now", comment: "Queue sheet: sends a queued message into the running task now"), systemImage: "arrow.up")
                    .textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.onAction)
                    .padding(.horizontal, Space.m).frame(minHeight: 34).background(Tone.action, in: Capsule())
                    .frame(minHeight: Metric.hit).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(String(localized: "Queued \(at + 1) of \(ids.count): \(message.text)", comment: "VoiceOver: a queued message; the arguments are its place, the count and its words"))
        .accessibilityActions {
            Button(String(localized: "Send now", comment: "Queue sheet: sends a queued message into the running task now")) { model.perform(.steerQueued(thread: thread, message: message.id)) }
            Button(String(localized: "Edit", comment: "Under a refused message: puts its words back in the composer")) { edit(message) }
            if at > 0 {
                Button(String(localized: "Move up", comment: "VoiceOver action: moves a queued message one place earlier")) {
                    model.perform(.reorder(thread: thread, ids: reordered(ids, moving: message.id, to: at - 1)))
                }
            }
            if at < ids.count - 1 {
                Button(String(localized: "Move down", comment: "VoiceOver action: moves a queued message one place later")) {
                    model.perform(.reorder(thread: thread, ids: reordered(ids, moving: message.id, to: at + 1)))
                }
            }
            Button(String(localized: "Remove", comment: "Queue sheet: removes a queued message")) { remove(message) }
        }
    }

    private func remove(_ message: QueuedMessageView) {
        removing.insert(message.id)
        let shown: ToastMessage = ToastMessage(
            id: UUID(), text: String(localized: "Removed from the queue", comment: "Toast after removing a queued message"), systemImage: "trash",
            actionTitle: String(localized: "Undo", comment: "Toast action: takes the last change back"))
        removal[shown.id] = message.id
        toast = shown
    }
}

/// The run inspector (plan §4.1): the run, its plan, its helpers, what it changed (with the steps no undo can take back),
/// the files (QuickLook of each diff), the context it used, its tokens, the chat's loops and the model of this chat.
struct RunInspector: View {
    let thread: String
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var actions: RunActions?
    @State private var changes: RunChanges?
    @State private var preview: URL?

    private var view: ThreadView? { model.chats[thread]?.view }
    private var run: RunRecord? { view.flatMap { DotEngine.ownRuns(of: $0).last } }

    var body: some View {
        List {
            if let run {
                Section(String(localized: "Run", comment: "Inspector section: the newest run")) {
                    InspectorRow(title: String(localized: "State", comment: "Inspector row: the run's status"), systemImage: "circle.dotted", accessory: .detail(statusWords(run.status)))
                    InspectorRow(title: String(localized: "Started", comment: "Inspector row: when the run started"), systemImage: "clock",
                        accessory: .detail(Date(timeIntervalSince1970: run.createdAt).formatted(date: .abbreviated, time: .shortened)))
                    InspectorRow(title: String(localized: "Took", comment: "Inspector row: how long the run worked"), systemImage: "timer",
                        accessory: .detail(durationWords(Int((run.finishedAt ?? Date().timeIntervalSince1970) - run.createdAt), locale: locale)))
                    if let approvals = run.approvals {
                        InspectorRow(title: String(localized: "Approval mode", comment: "Inspector row: the run's approval mode"), systemImage: "checkmark.shield", accessory: .detail(approvals))
                    }
                }
                Section(String(localized: "Model for this chat", comment: "Inspector section: the chat's model")) {
                    InspectorRow(title: [view?.newest?.choices?.model ?? run.model, view?.newest?.choices?.effort ?? run.effort].compactMap { $0 }.joined(separator: " · "), systemImage: "cpu", accessory: .none)
                    if let tokensIn = run.inputTokens, let tokensOut = run.outputTokens {
                        InspectorRow(title: String(localized: "Tokens", comment: "Inspector row: tokens the run read and wrote"), systemImage: "number",
                            accessory: .detail(String(localized: "\(tokensIn) in · \(tokensOut) out", comment: "Inspector: tokens read and written")))
                    }
                    if let meter = contextMeter {
                        InspectorRow(title: String(localized: "Context used", comment: "Inspector row: how full the model's context is"), systemImage: "gauge.medium",
                            accessory: .detail(meter.formatted(.percent.precision(.fractionLength(0)))))
                    }
                }
            }
            if let plan = planSteps, !plan.isEmpty {
                Section(String(localized: "Plan", comment: "Inspector section: the run's plan")) { PlanCard(steps: plan).listRowBackground(Color.clear) }
            }
            if !(view?.helpers ?? []).isEmpty {
                Section(String(localized: "Helpers", comment: "Inspector section: the chat's helpers")) {
                    ForEach(view?.helpers ?? [], id: \.runId) { helper in
                        InspectorRow(title: helper.title, systemImage: "person.2", accessory: .detail(helperState(helper.status).word))
                    }
                }
            }
            Section(String(localized: "Changes", comment: "Inspector section: what the runs changed")) {
                if let actions, actions.unrecoverable > 0 {
                    Label(String(localized: "\(actions.unrecoverable) steps no undo can take back", comment: "Inspector: steps that acted outside the undo journal; the argument is how many"), systemImage: "exclamationmark.triangle")
                        .textRole(.footnote).foregroundStyle(Tone.warn)
                }
                if let changes, !changes.files.isEmpty {
                    ForEach(changes.files, id: \.path) { file in
                        Button { preview = diffFile(file) } label: {
                            InspectorRow(title: file.path, systemImage: file.created ? "doc.badge.plus" : file.gone ? "doc.badge.minus" : "doc.text",
                                accessory: .detailAndChevron("+\(file.added) −\(file.removed)"))
                        }
                        .buttonStyle(.plain)
                    }
                } else {
                    Text(String(localized: "No file changed yet.", comment: "Inspector: no changes")).textRole(.footnote).foregroundStyle(Tone.secondary)
                }
            }
            if !(view?.background ?? []).isEmpty {
                Section(String(localized: "Loops and background work", comment: "Inspector section: the chat's loops and background commands")) {
                    ForEach(view?.background ?? [], id: \.id) { work in InspectorRow(title: work.title, systemImage: work.kind == "loop" ? "repeat" : "terminal", accessory: .none) }
                }
            }
        }
        .listStyle(.insetGrouped)
        .task(id: run?.id) {
            guard let id = run?.id else { return }
            async let acted: RunActions? = model.actions(of: id)
            async let changed: RunChanges? = model.changes(of: id)
            actions = await acted
            changes = await changed
        }
        .quickLookPreview($preview)
        .inspectorSheet(title: String(localized: "This chat", comment: "Title of the run inspector"), close: close)
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

/// One line of the inspector: a glyph, the title, the value at the trailing edge (a chevron where it opens something).
struct InspectorRow: View {
    let title: String
    let systemImage: String
    let accessory: RowAccessory
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let layout: AnyLayout = dynamicTypeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.xxs)) : AnyLayout(HStackLayout(spacing: Space.s))
        layout {
            Label { Text(title).textRole(.body).foregroundStyle(Tone.text) } icon: { Symbol(name: systemImage, context: .row).foregroundStyle(Tone.secondary) }
            if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: Space.xs) }
            switch accessory {
            case .detail(let text), .detailAndChevron(let text):
                Text(text).textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
            case .none, .chevron, .web:
                EmptyView()
            }
            if accessory == .chevron || { if case .detailAndChevron = accessory { true } else { false } }() {
                Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
            }
        }
        .frame(minHeight: Metric.hit)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
