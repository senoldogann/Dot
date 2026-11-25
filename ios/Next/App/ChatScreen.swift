import DotEngine
import SwiftUI
import UIKit

/// Where each chat was read last, so a chat opens where the owner left it (plan §13 S5). In memory for the session.
@MainActor
enum ChatScrollMemory {
    static var anchors: [String: String] = [:]
}

/// The row of a chat that came into sight last, kept by reference: writing it draws nothing again (item 1848).
@MainActor
final class ReadingMark {
    var topVisible: String?
}

/// One chat (wave N2, plan §4.1): the transcript with its work log, the ask dock in the composer's place, the
/// composer with Send / Queue / Stop, the queue with its edit, the header's quick panels and the run inspector.
struct ChatScreen: View {
    let thread: String
    /// A panel open at once (the debug fixtures); `nil` in the app.
    let initialPanel: ChatPanel?
    /// Begins the edit of the first queued message at once (the debug fixtures); `false` in the app.
    let editsFirstQueued: Bool
    @Environment(AppModel.self) private var model: AppModel
    @AppStorage("toolCallMode") private var toolModeRaw: String = DotEngine.ToolCallMode.showAll.rawValue
    @AppStorage("responseStreaming") private var streaming: Bool = true
    @AppStorage("followUp") private var followUpRaw: String = FollowUp.queue.rawValue
    @AppStorage(PrefKey.confirmStop) private var confirmStop: Bool = true
    @AppStorage(PrefKey.readingWidth) private var readingWidthRaw: String = ReadingWidth.standard.rawValue
    @State private var draft: String = ""
    @State private var attachments: [ComposerAttachment] = []
    @State private var panel: ChatPanel?
    @State private var edit: QueueEdit?
    @State private var dockFolded: Bool = false
    @State private var shownAsks: [DockEntry] = []
    @State private var dismissedAsks: Set<String> = []
    @State private var position: ScrollPosition = ScrollPosition()
    /// The transcript follows new words (the owner is at its end, or went back to it).
    @State private var pinned: Bool = true
    /// The owner's finger moves the transcript, or it glides on after it.
    @State private var ownerScrolls: Bool = false
    @State private var showsJump: Bool = false
    @State private var reading: ReadingMark = ReadingMark()
    @State private var toast: ToastMessage?
    @State private var stopping: Bool = false
    @State private var confirmingStop: Bool = false
    /// The answer of a side question (`/btw`), shown in an alert.
    @State private var aside: SideAnswer?
    /// This chat's changed files, for the `#` picker.
    @State private var changedFiles: RunChanges?
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    @Environment(\.locale) private var locale: Locale

    private var chat: ChatProjection? { model.chats[thread] }
    private var view: ThreadView? { chat?.view }
    private var activeRun: RunRecord? { view?.runs.last { $0.parentRunId == nil && $0.isActive } }
    private var helpers: [HelperView] { view?.helpers ?? [] }
    private var mode: DotEngine.ToolCallMode { DotEngine.ToolCallMode(rawValue: toolModeRaw) ?? .showAll }
    private var followUp: FollowUp { FollowUp(rawValue: followUpRaw) ?? .queue }
    private var phase: ChatPhase { chatPhase(of: view) }

    /// The transcript, built once per drawing of the screen (`body` reads it once and hands it on).
    private var builtItems: [TranscriptItem] {
        // Before the chat's picture is opened (a new chat the daemon just named, item 1849) its own messages still show.
        transcript(
            of: chat ?? ChatProjection(threadId: thread), pending: model.pending(in: thread), mode: mode, streaming: streaming,
            context: TranscriptContext(answeredHere: model.answeredHere))
    }

    private var dock: [DockEntry] {
        let ownRuns: Set<String> = Set((view?.runs ?? []).filter { $0.parentRunId == nil }.map(\.id))
        return dockEntries(
            asks: view?.asks ?? [], chatRuns: ownRuns, outbox: model.outbox, online: model.online, local: model.askPhases,
            shown: shownAsks, decided: decidedAsks, answeredHere: model.answeredHere
        ).filter { !dismissedAsks.contains($0.id) }
    }

    /// How each ask of the transcript ended, by approval id.
    private var decidedAsks: [String: String] {
        var decided: [String: String] = [:]
        for timeline in chat?.runs.values.map({ $0 }) ?? [] {
            for event in timeline.events where event.kind == "approval_decided" {
                if let id = event.data["approval_id"]?.string { decided[id] = event.data["status"]?.string ?? "" }
            }
        }
        return decided
    }

    var body: some View {
        let items: [TranscriptItem] = builtItems
        return content(items)
            .background(Tone.ground)
            .navigationTitle(view?.thread.title ?? model.list.rows.first { $0.summary.id == thread }?.summary.title ?? model.firstWords(of: thread) ?? "")
            .navigationSubtitle(subtitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(sizeClass == .regular ? .automatic : .hidden, for: .tabBar)
            .toolbar { header }
            .sheet(item: $panel) { panel in sheet(panel) }
            .toastHost($toast, seconds: 4) { _ in }
            .confirmationDialog(
                String(localized: "Stop this run?", comment: "Confirmation of Stop from the chat header"), isPresented: $confirmingStop, titleVisibility: .visible
            ) {
                Button(String(localized: "Stop", comment: "Chat toolbar: stops the run at work"), role: .destructive) { stop() }
            }
            .onAppear {
                model.open(thread)
                draft = model.draft(of: thread)
                if let initialPanel { panel = initialPanel }
                if editsFirstQueued, let first = view?.queue.messages.first {
                    draft = String(localized: "And book the car service too", comment: "Debug fixture: a half-written draft kept while a queued message is edited")
                    beginEdit(first)
                }
            }
            .onDisappear {
                model.close(thread)
                if edit == nil { model.keep(draft: draft, of: thread) }
                if let topVisible = reading.topVisible { ChatScrollMemory.anchors[thread] = topVisible }
            }
            .onChange(of: draft) { _, words in if edit == nil { model.keep(draft: words, of: thread) } }
            .onChange(of: dock) { _, entries in shownAsks = entries }
            .onChange(of: activeRun?.id) { _, run in if run == nil { stopping = false } }
            .onChange(of: items.count) { _, _ in LatencyProbe.saw(items: items, model: model) }
            .task { await model.loadModels() }
            .modifier(ComposerLists(thread: thread, lastRun: view?.runs.last(where: { $0.parentRunId == nil })?.id, changedFiles: $changedFiles, aside: $aside))
            .task { await LatencyProbe.run(thread: thread, model: model) }
            .modifier(FrameMeterHook(running: activeRun != nil))
            .onKeyPress(.escape) {
                if panel != nil {
                    panel = nil
                    return .handled
                }
                return .ignored
            }
    }

    // MARK: The transcript

    @ViewBuilder private func content(_ items: [TranscriptItem]) -> some View {
        if chat?.freshness == .deleted, items.isEmpty {
            EmptyState(
                title: String(localized: "This chat was deleted", comment: "Chat subtitle: the chat was deleted on the Mac or the web"), systemImage: "trash",
                detail: String(localized: "It was deleted on your Mac or another device.", comment: "Empty state of a deleted chat"), actionTitle: nil, action: {})
        } else if items.isEmpty, model.transport == .offline || model.transport == .keyRefused {
            EmptyState(
                title: model.transport == .keyRefused
                    ? String(localized: "This phone's key was refused", comment: "Chat subtitle: the Mac no longer takes this phone's key")
                    : String(localized: "Can't reach your Mac", comment: "Empty chat when offline with nothing saved"),
                systemImage: "wifi.slash",
                detail: model.transport == .keyRefused
                    ? String(localized: "Pair this phone again in Settings.", comment: "Empty chat when the key was refused")
                    : String(localized: "Nothing of this chat is saved on this phone yet. It loads once the phone reaches your Mac.", comment: "Empty chat when offline with nothing saved"),
                actionTitle: nil, action: {})
        } else {
            transcriptView(items)
        }
    }

    private func transcriptView(_ items: [TranscriptItem]) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.m) {
                if chat?.freshness == .deleted {
                    Notice(tone: .info, message: String(localized: "This chat was deleted", comment: "Chat subtitle: the chat was deleted on the Mac or the web"), dismiss: nil)
                }
                if let unreadable = chat?.unreadable, unreadable > 0 {
                    Notice(tone: .info, message: String(localized: "\(unreadable) steps could not be shown", comment: "Notice in a chat: frames the app could not read; the argument is how many"), dismiss: nil)
                }
                ForEach(TranscriptBlock.of(items)) { block in
                    Group {
                        switch block {
                        case .item(let item): row(item)
                        case .tools(let entries): ToolRun(entries: entries)
                        }
                    }
                    .id(block.id)
                    // Kept outside the view's state: a row coming into sight must not draw the whole screen again.
                    .onAppear { reading.topVisible = block.id }
                }
            }
            .frame(maxWidth: proseWidth)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.m)
        }
        .overlay {
            if items.isEmpty, chat?.freshness != .deleted {
                LoadingState(message: String(localized: "Syncing…", comment: "Chat subtitle: the chat is being brought up to date"))
            }
        }
        // A short chat stands at the top under the header; a long one opens at its end (the lead's look at N1c), or
        // where the owner left it.
        .defaultScrollAnchor(.top, for: .alignment)
        .scrollPosition($position)
        .scrollDismissesKeyboard(.interactively)
        .onScrollPhaseChange { _, now in ownerScrolls = now == .interacting || now == .decelerating }
        // Follow new words only while the owner is at the end (plan §13 S5, item 1848): their own scroll away stops it,
        // their scroll back to the end starts it again; words that grow the transcript never stop it.
        .onScrollGeometryChange(for: Bool.self) { geometry in
            isAtEnd(distanceFromEnd: Double(geometry.contentSize.height + geometry.contentInsets.bottom - geometry.contentOffset.y - geometry.containerSize.height))
        } action: { _, atEnd in
            if atEnd { pinned = true } else if ownerScrolls { pinned = false }
        }
        // Once the new words are laid out (never before, which left them under the composer), the end is shown.
        .onScrollGeometryChange(for: Double.self) { geometry in Double(geometry.contentSize.height) } action: { _, _ in
            if pinned, !ownerScrolls { position.scrollTo(edge: .bottom) }
        }
        .task(id: pinned) {
            // The jump button comes after a short pause, so it does not flicker while words stream (debounced).
            if pinned {
                showsJump = false
                return
            }
            do { try await Task.sleep(for: .milliseconds(350)) } catch { return }
            showsJump = !pinned
        }
        .task(id: thread) {
            try? await Task.sleep(for: .milliseconds(80))
            if let anchor = ChatScrollMemory.anchors[thread] {
                pinned = false
                position.scrollTo(id: anchor, anchor: .top)
            } else {
                position.scrollTo(edge: .bottom)
            }
        }
        .safeAreaInset(edge: .bottom) { bottom() }
    }

    @ViewBuilder private func row(_ item: TranscriptItem) -> some View {
        switch item {
        case .owner(let id, let text, let delivery):
            VStack(alignment: .trailing, spacing: Space.xxs) {
                OwnerMessage(text: text)
                if let delivery { deliveryLine(id: id, delivery: delivery) }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        case .note(_, let text):
            DotNoteRow(text: text)
        case .answer(let id, let text, let writing, let endedAt):
            VStack(alignment: .leading, spacing: Space.xxs) {
                AnswerView(markdown: text, writing: writing)
                // The footer is the final answer's alone, once the run ended (plan §24): none under passing words.
                if let endedAt, let target = chat.flatMap({ reactionTarget(of: id, in: $0) }) {
                    ReactionsBar(
                        at: Date(timeIntervalSince1970: endedAt),
                        on: Set((view?.reactions ?? []).filter { $0.runId == target.run && $0.message == target.message }.map(\.reaction)),
                        toggle: { reaction in react(reaction, target: target) },
                        copy: {
                            UIPasteboard.general.string = text
                            toast = ToastMessage(id: UUID(), text: String(localized: "Copied", comment: "Code block header after a copy"), systemImage: "checkmark", actionTitle: nil)
                        })
                }
            }
            .contextMenu {
                Button(String(localized: "Copy", comment: "Code block header button that copies the code"), systemImage: "doc.on.doc") { UIPasteboard.general.string = text }
            }
        case .thought(_, let text, let live):
            ThoughtRow(text: text, live: live)
        case .tool(let entry):
            WorkToolRow(entry: entry)
        case .helpers(_, _, let list):
            HelperGroupCard(helpers: list, open: { panel = .agents })
        case .plan(_, let steps):
            PlanCard(steps: steps)
        case .decision(let anchor):
            AskAnchorRow(anchor: anchor)
        case .work(let fold):
            WorkFoldView(fold: fold, row: { AnyView(self.row($0)) })
        case .ended(_, let status, let detail):
            RunEndedCard(status: status, detail: detail, retry: status == "failed" && activeRun == nil ? { retryLast() } : nil)
        case .limit(_, let resetsAt):
            UsageLimitCard(resetsAt: resetsAt, resume: { retryLast() })
        }
    }

    @ViewBuilder private func deliveryLine(id: String, delivery: Delivery) -> some View {
        HStack(spacing: Space.xs) {
            Text(deliveryWords(delivery)).textRole(.label).foregroundStyle(isRejected(delivery) ? Tone.failure : Tone.tertiary)
            if case .rejected(let failure) = delivery {
                let key: String = String(id.dropFirst(2))
                if case .runEnded = failure {
                    Button(String(localized: "Send as a new message", comment: "Under a steer refused because its run ended")) { model.sendAsMessage(key) }.textRole(.label)
                } else {
                    Button(String(localized: "Edit", comment: "Under a refused message: puts its words back in the composer")) {
                        // The newer draft is kept: the refused words go after it, never over it (plan §13 B3).
                        Task { if let words = await model.takeBack(key) { draft = draft.isEmpty ? words : draft + "\n" + words } }
                    }
                    .textRole(.label)
                    Button(String(localized: "Try again", comment: "Under a refused message: sends it again")) { model.retry(key) }.textRole(.label)
                }
            }
        }
    }

    // MARK: The bottom: capsule, dock or composer

    @ViewBuilder private func bottom() -> some View {
        let entries: [DockEntry] = dock
        VStack(alignment: .leading, spacing: Space.xs) {
            FloatingWorkingControl(
                summary: WorkingSummary(
                    status: capsuleStatus, activity: activityWords,
                    since: activeRun.map { Date(timeIntervalSince1970: $0.createdAt) }, queued: view?.queue.messages.count ?? 0,
                    helpers: helpers.filter { helperState($0.status).isLive }.count),
                showsScrollToBottom: showsJump, openStatus: { panel = .inspector }, openQueue: { panel = .queue }, openHelpers: { panel = .agents },
                scrollToBottom: {
                    pinned = true
                    withAnimation { position.scrollTo(edge: .bottom) }
                }
            )
            .frame(maxWidth: .infinity, alignment: .center)
            if !entries.isEmpty {
                AskDock(
                    entries: entries,
                    actions: AskDockActions(
                        decide: { ask, approve, note, remember in model.decide(ask, approve: approve, note: note, remember: remember, thread: thread) },
                        giveSecret: { ask, value in model.giveSecret(ask, value: value) },
                        dismiss: { id in dismissedAsks.insert(id) },
                        stop: activeRun == nil ? nil : { stop() }),
                    folded: $dockFolded)
            }
            if entries.isEmpty || dockFolded {
                if edit != nil { EditingQueuedBar(cancel: cancelEdit) }
                ComposerView(
                    text: edit == nil ? $draft : Binding(get: { edit?.text ?? "" }, set: { edit?.text = $0 }),
                    attachments: $attachments,
                    placeholder: activeRun == nil
                        ? String(localized: "Message Dot", comment: "Placeholder of the composer")
                        : followUp == .queue
                            ? String(localized: "Queue a message", comment: "Placeholder of the composer while a run works and follow-ups queue")
                            : String(localized: "Tell the running task", comment: "Placeholder of the composer while a run works and follow-ups steer"),
                    buttons: composerButtons(
                        hasContent: !(edit?.text ?? draft).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty,
                        runActive: activeRun != nil && !stopping, followUp: followUp, editing: edit != nil),
                    perform: perform,
                    model: currentModel, models: modelChoices, chooseModel: { choose($0) },
                    suggestions: ComposerSuggestions(sections: { trigger in
                        pickerSections(trigger, commands: composerCommands(), catalog: catalog, limit: 8)
                    }))
                .disabled(chat?.freshness == .deleted)
            }
        }
        .frame(maxWidth: proseWidth)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, Space.s)
        .padding(.bottom, Space.xs)
    }

    private var capsuleStatus: ChatStatus? {
        if stopping, activeRun != nil { return nil }
        switch phase {
        case .working: return .working
        case .starting: return .starting
        case .waitingOnYou: return .waitingOnYou
        default: return nil
        }
    }

    // MARK: Doings

    private func perform(_ action: ComposerAction) {
        switch action {
        case .stop:
            stop()
        case .confirmEdit:
            confirmEdit()
        case .send, .queue, .steer:
            let typed: String = draft.trimmingCharacters(in: .whitespacesAndNewlines)
            let photos: [Data] = attachments.map(\.jpeg)
            guard !typed.isEmpty || !photos.isEmpty else { return }
            // A command the composer does itself (PLAN §22) leaves the draft only once it is done or understood.
            switch composerCommand(typed) {
            case .stop:
                draft = ""
                stop()
                return
            case .model:
                draft = ""
                panel = .model
                return
            case .loopHow:
                toast = ToastMessage(id: UUID(), text: String(localized: "Ask again every so often: /loop 30m <message>", comment: "Composer command: what /loop does"), systemImage: "info.circle", actionTitle: nil)
                return
            case .aside(let question):
                guard let run = view?.runs.last(where: { $0.parentRunId == nil }) else { break }
                draft = ""
                Task { aside = SideAnswer(question: question, answer: await model.aside(run: run.id, question: question)) }
                return
            case .loop(let every, let text):
                guard let run = view?.runs.last(where: { $0.parentRunId == nil }) else { break }
                draft = ""
                Task {
                    if let problem = await model.loop(run: run.id, every: every, text: text) {
                        toast = ToastMessage(id: UUID(), text: String(localized: "The loop was not made: \(problem)", comment: "Composer: /loop failed; the argument is why"), systemImage: "exclamationmark.triangle", actionTitle: nil)
                        draft = typed
                        return
                    }
                    await model.send(text, thread: thread, steerInto: nil, photos: photos, picks: .none)
                }
                attachments = []
                return
            case .message:
                break
            }
            let (words, picks): (String, MessagePicks) = messagePicks(typed, catalog: catalog)
            draft = ""
            attachments = []
            model.keep(draft: "", of: thread)
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            Task { await model.send(words, thread: thread, steerInto: action == .steer ? activeRun?.id : nil, photos: photos, picks: picks) }
        }
    }

    /// What the pickers offer (PLAN §22): the daemon's skills, agents, teams and accounts, the other chats, this chat's
    /// changed files.
    private var catalog: ComposerCatalog {
        ComposerCatalog(
            skills: (model.palette?.skills ?? []).map { PickerItem(kind: .skill, token: "/" + $0.name, name: $0.name, detail: $0.description) },
            agents: model.agents.map { PickerItem(kind: .agent, token: "@" + $0.record.name.replacingOccurrences(of: " ", with: "-"), name: $0.record.name, detail: $0.record.role ?? "") },
            teams: model.teams.map { PickerItem(kind: .team, token: "@" + $0.record.name.replacingOccurrences(of: " ", with: "-"), name: $0.record.name, detail: "") },
            chats: model.list.ordered.filter { $0.summary.id != thread }.prefix(30).map { row in
                PickerItem(kind: .chat, token: "@\"" + row.summary.title + "\"", name: row.summary.title, detail: rowTime(row.summary.latestCreatedAt, now: .now))
            },
            accounts: (model.palette?.accounts ?? []).map { PickerItem(kind: .account, token: "@" + $0.address, name: $0.address, detail: [$0.providerName, $0.reads].filter { !$0.isEmpty }.joined(separator: " · ")) },
            files: (changedFiles?.files ?? []).map { PickerItem(kind: .file, token: "#" + $0.path, name: $0.path, detail: "+\($0.added) −\($0.removed)") })
    }

    private func stop() {
        guard let run = activeRun else { return }
        stopping = true
        model.perform(.stop(run: run.id, thread: thread))
    }

    private func react(_ reaction: ReactionKind, target: (run: String, message: String)) {
        let on: Bool = !(view?.reactions ?? []).contains { $0.runId == target.run && $0.message == target.message && $0.reaction == reaction.rawValue }
        model.perform(.react(run: target.run, thread: thread, message: target.message, reaction: reaction.rawValue, on: on))
        if on {
            toast = ToastMessage(id: UUID(), text: reaction.meaning, systemImage: "checkmark", actionTitle: nil)
        }
    }

    /// Try again / Resume: the newest run's goal sent again as a message of the chat.
    private func retryLast() {
        guard let last = view?.runs.last(where: { $0.parentRunId == nil }), !last.goal.isEmpty else { return }
        Task { await model.send(last.goal, thread: thread, steerInto: nil, photos: [], picks: .none) }
    }

    // MARK: The queue's edit (§20)

    func beginEdit(_ message: QueuedMessageView) {
        edit = QueueEdit(message: message, liveDraft: draft)
        panel = nil
    }

    private func cancelEdit() {
        guard let edit else { return }
        draft = edit.liveDraft
        self.edit = nil
    }

    private func confirmEdit() {
        guard let edit, let view else { return }
        switch confirm(edit, queue: view.queue) {
        case .save(let message, let text):
            model.perform(.editQueued(thread: thread, message: message, text: text, attachments: nil))
            draft = edit.liveDraft
        case .unchanged, .empty:
            draft = edit.liveDraft
        case .gone(let text):
            // The message started meanwhile: the words stay in the composer as a new message.
            draft = text
            toast = ToastMessage(
                id: UUID(), text: String(localized: "That message already started; your words stay here", comment: "Toast: a queued message being edited left the queue"),
                systemImage: "info.circle", actionTitle: nil)
        }
        self.edit = nil
    }

    // MARK: The model chip

    private var currentModel: ModelChoice? {
        let choices: ChatChoices? = view?.newest?.choices
        let ref: String = choices?.model ?? view?.runs.last?.model ?? ""
        guard !ref.isEmpty else { return nil }
        // Before the daemon's models are read, the model's own name without its provider ("claude_code:opus" → "opus").
        let label: String = model.models.flatMap(\.models).first { $0.ref == ref }?.label ?? String(ref.split(separator: ":").last ?? Substring(ref))
        return ModelChoice(ref: ref, label: label, effort: choices?.effort ?? view?.runs.last?.effort)
    }

    private var modelChoices: [String: [ModelChoice]] {
        var out: [String: [ModelChoice]] = [:]
        for provider in model.models where provider.error == nil {
            out[provider.provider] = provider.models.flatMap { option in
                option.efforts.count < 2
                    ? [ModelChoice(ref: option.ref, label: option.label, effort: nil)]
                    : option.efforts.map { ModelChoice(ref: option.ref, label: option.label, effort: $0) }
            }
        }
        return out
    }

    private func choose(_ choice: ModelChoice) {
        let base: ChatChoices = view?.newest?.choices ?? ChatChoices(mode: "auto", model: nil, effort: nil, speed: "standard", approvals: nil, goalMode: nil)
        model.perform(.choices(thread: thread, choices: base.with(model: choice.ref, effort: choice.effort)))
    }

    // MARK: Header

    @ToolbarContentBuilder private var header: some ToolbarContent {
        ToolbarItemGroup(placement: .topBarTrailing) {
            Button(String(localized: "Changes and files", comment: "Chat header quick panel: what the run changed"), systemImage: "folder") { panel = .inspector }
            Button(String(localized: "Agents", comment: "Chat header quick panel: the chat's helpers"), systemImage: "person.2") { panel = .agents }
            if activeRun != nil {
                Button(String(localized: "Stop", comment: "Chat toolbar: stops the run at work"), systemImage: "stop.fill") { if confirmStop { confirmingStop = true } else { stop() } }
            }
        }
    }

    private var subtitle: String {
        if let chat, let line = freshnessLine(
            freshness: chat.freshness, transport: model.transport, savedAt: model.savedAt[thread], workingAt: model.workingAt[thread], now: .now)
        {
            return line
        }
        if stopping, activeRun != nil { return String(localized: "Stopping…", comment: "Helper state: the owner pressed Stop and it has not ended yet") }
        switch phase {
        case .waitingOnYou: return ChatStatus.waitingOnYou.word
        // The clock is the capsule's, which ticks on its own; a title drawn with the screen would freeze between events.
        case .working: return ChatStatus.working.word
        case .starting: return ChatStatus.starting.word
        case .failed: return ChatStatus.failed.word
        default:
            let who: String = view?.thread.agentId ?? String(localized: "Dot", comment: "The assistant's name in a chat's subtitle")
            return [who, model.macName].compactMap { $0 }.joined(separator: " · ")
        }
    }

    /// What the run at work does now, for the capsule (thinking, writing, the tool that runs); `nil` between steps.
    private var activityWords: String? {
        guard capsuleStatus == .working, let run = activeRun, let chat, let now = activity(of: chat, run: run.id) else { return nil }
        switch now {
        case .thinking: return String(localized: "Thinking", comment: "Capsule: the run's model is thinking")
        case .writing: return String(localized: "Writing", comment: "Capsule: the run's model is writing its words")
        case .tool(let step):
            return toolLine(
                ToolEntry(
                    id: "", tool: step.tool, kind: ToolKind.of(step.tool), target: step.target, args: "", output: "", state: .running, images: [],
                    startedAt: step.at, endedAt: nil))
        }
    }

    @ViewBuilder private func sheet(_ panel: ChatPanel) -> some View {
        switch panel {
        case .agents:
            LiveAgentsSheet(thread: thread, close: { self.panel = nil })
        case .background:
            BackgroundWorkSheet(
                items: (view?.background ?? []).map { work in
                    BackgroundWork(id: work.id, title: work.title, kind: work.kind == "command" ? .command : work.kind == "loop" ? .loop : .subagent)
                }, close: { self.panel = nil })
        case .queue:
            QueueSheet(thread: thread, edit: { beginEdit($0) }, close: { self.panel = nil })
        case .inspector, .files, .screen:
            RunInspector(thread: thread, close: { self.panel = nil })
        case .model:
            List(modelChoices.keys.sorted(), id: \.self) { provider in
                Section(provider) {
                    ForEach(modelChoices[provider] ?? []) { choice in
                        Button {
                            choose(choice)
                            self.panel = nil
                        } label: {
                            InspectorRow(title: [choice.label, choice.effort?.capitalized].compactMap { $0 }.joined(separator: " · "), systemImage: "cpu",
                                accessory: choice == currentModel ? .detail("✓") : .none)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .inspectorSheet(title: String(localized: "Model for this chat", comment: "Inspector section: the chat's model"), close: { self.panel = nil })
        }
    }

    private func isRejected(_ delivery: Delivery) -> Bool {
        if case .rejected = delivery { return true }
        return false
    }

    private var proseWidth: CGFloat? {
        guard sizeClass == .regular else { return nil }
        return ProseColumn.width(charactersPerLine: ReadingWidth.current(readingWidthRaw).charactersPerLine, font: ProseColumn.bodyFont(dynamicTypeSize))
    }
}

/// A side question and the run's answer (`/btw`).
struct SideAnswer: Identifiable, Equatable {
    let question: String
    let answer: String
    var id: String { question + answer }
}

/// The lists the composer's pickers read (the daemon's palette, this chat's changed files) and the side answer's alert.
private struct ComposerLists: ViewModifier {
    let thread: String
    let lastRun: String?
    @Binding var changedFiles: RunChanges?
    @Binding var aside: SideAnswer?
    @Environment(AppModel.self) private var model: AppModel

    func body(content: Content) -> some View {
        content
            .task { await model.loadPalette() }
            .task(id: lastRun) {
                if let lastRun { changedFiles = await model.changes(of: lastRun) }
            }
            .alert(
                String(localized: "Side answer", comment: "Title of the alert with a /btw answer"),
                isPresented: Binding(get: { aside != nil }, set: { if !$0 { aside = nil } }), presenting: aside
            ) { _ in
                Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), role: .cancel) { aside = nil }
            } message: { answer in
                Text(verbatim: answer.question + "\n\n" + answer.answer)
            }
    }
}
