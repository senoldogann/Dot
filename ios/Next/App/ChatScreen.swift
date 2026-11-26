import DotEngine
import SwiftUI
import UIKit

/// Where each chat was read last, so a chat opens where the owner left it (plan §13 S5). In memory for the session.
@MainActor
enum ChatScrollMemory {
    static var anchors: [String: String] = [:]
}

/// The chat screen's drawings, counted by reference (writing it draws nothing again). Debug builds with
/// `-DotNextRenderMeter 1` show the count to the UI tests, which check that a scene settles (item 1932).
@MainActor
final class RenderMeter {
    private(set) var count: Int = 0

    func counted() -> Int {
        count += 1
        return count
    }

    static var shown: Bool {
        #if DEBUG
            UserDefaults.standard.bool(forKey: "DotNextRenderMeter")
        #else
            false
        #endif
    }
}

/// What stands at a chat's end: its last row, and the line under it that says a run starts or works. The chat follows
/// a change of it, never a change of its height (item 1932).
struct ChatEnd: Equatable {
    let last: TranscriptItem?
    let starting: Bool
    let run: String?
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
    @AppStorage("toolCallMode") private var toolModeRaw: String = DotEngine.ToolCallMode.hideAll.rawValue
    @AppStorage(PrefKey.showSteps) private var showStepsEverywhere: Bool = true
    @AppStorage("responseStreaming") private var streaming: Bool = true
    @AppStorage("followUp") private var followUpRaw: String = FollowUp.queue.rawValue
    @AppStorage(PrefKey.readingWidth) private var readingWidthRaw: String = ReadingWidth.standard.rawValue
    @State private var draft: String = ""
    /// A message the owner quoted by a swipe or the menu (item 1972): its words ride on the next message as a "> quote".
    @State private var quoted: String?
    @State private var attachments: [ComposerAttachment] = []
    @State private var keepingMessage: Bool = false
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
    /// How many times the screen was drawn, for the debug render meter (item 1932).
    @State private var renders: RenderMeter = RenderMeter()
    @State private var toast: ToastMessage?
    @State private var stopping: Bool = false
    /// The steps shown for this chat from the live line (item 1769), over a mode that hides them.
    @State private var stepsShown: Bool = false
    /// The helper whose chat a row of the stream opened (item 1909).
    @State private var openedHelper: String?
    /// The answer of a side question (`/btw`), shown in an alert.
    @State private var aside: SideAnswer?
    /// This chat's changed files, for the `#` picker.
    @State private var changedFiles: RunChanges?
    /// "Plan first" for the next message, set in the chat's settings (PLAN §24); spent by that message.
    @State private var planFirst: Bool = false
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    @Environment(\.locale) private var locale: Locale
    /// The bot this chat belongs to (PLAN §27): its header and "Ask <name>"; `nil` for a plain chat.
    @Environment(\.botChat) private var botChat: BotChat?
    /// Find in chat (the bot menu's): the words looked for and which find is shown; `nil` while the bar is closed.
    @State private var finding: String?
    @State private var findIndex: Int = 0

    private var chat: ChatProjection? { model.chats[thread] }
    private var view: ThreadView? { chat?.view }
    /// The chat's own runs (`ownRuns`): a helper's own chat shows the run that is its work.
    private var ownRuns: [RunRecord] { view.map(DotEngine.ownRuns(of:)) ?? [] }
    private var activeRun: RunRecord? { ownRuns.last { $0.isActive } }
    private var helpers: [HelperView] { view?.helpers ?? [] }
    /// The helpers the chat's newest run started (item 1880): the pill counts these, never an older run's.
    private var newestRunHelpers: [HelperView] {
        guard let run = ownRuns.last else { return [] }
        return helpers.filter { $0.parentRunId == run.id }
    }
    /// The tool mode the owner chose (Settings › Appearance); Hide all, the web's "What it is doing", until they choose.
    private var chosenMode: DotEngine.ToolCallMode { DotEngine.ToolCallMode(rawValue: toolModeRaw) ?? .hideAll }
    /// "Show steps" for this chat: its own where its This chat sheet set it, else Settings › Chat's (item 1915).
    private var showsSteps: Bool { model.stepsOverrides[thread] ?? showStepsEverywhere }
    /// Whether the steps are hidden, so that the live line says the work (items 1769 and 1915).
    private var linesTheWork: Bool { !showsSteps || chosenMode == .hideAll || chosenMode == .zen }
    private var mode: DotEngine.ToolCallMode { stepsShown && linesTheWork ? .showAll : chosenMode }
    private var followUp: FollowUp { FollowUp(rawValue: followUpRaw) ?? .queue }
    private var phase: ChatPhase { chatPhase(of: view) }

    /// The transcript, built once per drawing of the screen (`body` reads it once and hands it on).
    private var builtItems: [TranscriptItem] {
        // Messages only (item 1915): every step is built, the run at work's are left out, and a finished run's fold keeps
        // its steps behind "Worked for 2m", so a tap shows them and nothing is lost.
        guard !showsSteps, !stepsShown else { return transcript(in: mode) }
        return messagesOnly(transcript(in: .showAll))
    }

    private func transcript(in mode: DotEngine.ToolCallMode) -> [TranscriptItem] {
        // Before the chat's picture is opened (a new chat the daemon just named, item 1849) its own messages still show.
        DotEngine.transcript(
            of: chat ?? ChatProjection(threadId: thread), pending: model.pending(in: thread), mode: mode, streaming: streaming,
            context: TranscriptContext(answeredHere: model.answeredHere))
    }

    private var dock: [DockEntry] {
        let ownRunIds: Set<String> = Set(ownRuns.map(\.id))
        return dockEntries(
            asks: view?.asks ?? [], chatRuns: ownRunIds, outbox: model.outbox, online: model.online, local: model.askPhases,
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
        let drawings: Int = renders.counted()
        let items: [TranscriptItem] = builtItems
        return content(items)
            .background(Tone.ground)
            .navigationTitle(view?.thread.title ?? model.list.rows.first { $0.summary.id == thread }?.summary.title ?? model.firstWords(of: thread) ?? "")
            .navigationSubtitle(botChat == nil ? subtitle : "")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(item: $openedHelper) { helperThread in
                ChatScreen(thread: helperThread, initialPanel: nil, editsFirstQueued: false)
            }
            .overlay(alignment: .topLeading) {
                if RenderMeter.shown {
                    Text(verbatim: "\(drawings)").font(.system(size: 1)).opacity(0.01).accessibilityIdentifier("debug.renders")
                }
            }
            .toolbar(sizeClass == .regular ? .automatic : .hidden, for: .tabBar)
            .toolbar { chrome }
            .liveFollow(thread: thread)
            .sheet(item: $panel) { panel in sheet(panel) }
            .toastHost($toast, seconds: 4) { _ in }
            .onAppear {
                PhaseMeter.enter("chat")
                model.open(thread)
                draft = model.draft(of: thread)
                if let initialPanel { panel = initialPanel }
                if editsFirstQueued, let first = view?.queue.messages.first {
                    draft = String(localized: "And book the car service too", comment: "Debug fixture: a half-written draft kept while a queued message is edited")
                    beginEdit(first)
                }
            }
            .onDisappear {
                PhaseMeter.enter("list")
                model.close(thread)
                if edit == nil { model.keep(draft: draft, of: thread) }
                if let topVisible = reading.topVisible { ChatScrollMemory.anchors[thread] = topVisible }
            }
            .onChange(of: draft) { _, words in if edit == nil { model.keep(draft: words, of: thread) } }
            .onChange(of: dock) { _, entries in shownAsks = entries }
            .onChange(of: activeRun?.id) { _, run in if run == nil { stopping = false } }
            .onChange(of: items.count) { _, _ in LatencyProbe.saw(items: items, model: model) }
            .task { await model.loadModels() }
            .task { OutputBox.warmUp() }
            .modifier(ComposerLists(thread: thread, lastRun: ownRuns.last?.id, changedFiles: $changedFiles, aside: $aside))
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

    /// Shows the chat's end while the owner is there: a few scrolls over half a second, then none. The lazy stack learns
    /// the true heights of the rows a scroll brings into sight, so one scroll lands short of the end; the later passes
    /// reach it, and the follow ends there instead of chasing every height change (item 1932). The first pass waits for
    /// the next turn of the main actor, after the new words are laid out, so they never stand under the composer.
    private func showEnd() async {
        for pause in [0, 60, 150, 300] {
            do { try await Task.sleep(for: .milliseconds(pause)) } catch { return }
            guard pinned, !ownerScrolls else { return }
            position.scrollTo(edge: .bottom)
        }
    }

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
                ForEach(TranscriptBlock.of(streamItems(items))) { block in
                    Group {
                        switch block {
                        case .item(let item): row(item)
                        case .steps(let rows): StepRun(rows: rows)
                        }
                    }
                    .id(block.id)
                    // Kept outside the view's state: a row coming into sight must not draw the whole screen again.
                    .onAppear { reading.topVisible = block.id }
                }
                if startingSince != nil {
                    // The message went out and no run has started yet: motion at once, replaced in place by the run's
                    // own line (item 1919). Nothing more is claimed than that it starts.
                    StartingLine()
                }
                if let run = activeRun, let chat, let now = workNow(of: chat, run: run.id, stopping: stopping) {
                    // An ask that waits on the owner is the dock's, never this line's.
                    if !stopping, now != .waitingForYou, case .tool(let step)? = activity(of: chat, run: run.id), let waiting = waitingWords(step) {
                        // While the run waits on something, one quiet line says on what in every mode (item 1909); Stop
                        // is the pill's, so the line offers only Details. An ask of the owner's comes first: the run
                        // waits on them, not on its command, and the line says so (item 1932).
                        WaitingLine(words: waiting, details: { panel = .inspector })
                    } else if linesTheWork || now == .queued || now == .typing {
                        // With the steps shown too, while the run has no row to show yet (it waits to start, or its model
                        // has not answered), the line moves so the chat never stands still (item 1919).
                        WorkLineView(now: now, stepsShown: stepsShown) { stepsShown.toggle() }
                    }
                }
                ChatDrafts(thread: thread)
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
        // A row opened or closed by the owner stays where it is: the follow stops (see `disclosureToggled`).
        .environment(\.disclosureToggled, { pinned = false })
        .scrollDismissesKeyboard(.interactively)
        .onScrollPhaseChange { _, now in ownerScrolls = now == .interacting || now == .decelerating }
        // Follow new words only while the owner is at the end (plan §13 S5, item 1848): their own scroll away stops it,
        // their scroll back to the end starts it again; words that grow the transcript never stop it.
        .onScrollGeometryChange(for: Bool.self) { geometry in
            isAtEnd(distanceFromEnd: Double(geometry.contentSize.height + geometry.contentInsets.bottom - geometry.contentOffset.y - geometry.containerSize.height))
        } action: { _, atEnd in
            if atEnd { pinned = true } else if ownerScrolls { pinned = false }
        }
        // New words are followed when the transcript's end changes, never when its height does (item 1932): a lazy
        // stack estimates the rows out of sight, so its height moves as rows come into sight, and following each move
        // scrolled, which brought other rows into sight, which moved the height again, without end (the asks scene
        // held the phone at 100 % CPU). A newer end cancels the older one's passes.
        .task(id: ChatEnd(last: items.last, starting: startingSince != nil, run: activeRun?.id)) {
            await showEnd()
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
                await showEnd()
            }
        }
        .safeAreaInset(edge: .bottom) { bottom(items) }
        .safeAreaInset(edge: .top) {
            if let finding {
                let found: [String] = findMatches(items, words: finding)
                FindBar(
                    words: Binding(get: { self.finding ?? "" }, set: { self.finding = $0; findIndex = 0 }), index: findIndex, count: found.count,
                    step: { delta in
                        guard !found.isEmpty else { return }
                        findIndex = (findIndex + delta + found.count) % found.count
                        pinned = false
                        position.scrollTo(id: found[findIndex], anchor: .center)
                    },
                    close: { self.finding = nil }
                )
                .onChange(of: found.first) { _, first in
                    if let first {
                        pinned = false
                        position.scrollTo(id: first, anchor: .center)
                    }
                }
            }
        }
    }

    @ViewBuilder private func row(_ item: TranscriptItem) -> some View {
        switch item {
        case .owner(let id, let text, let delivery):
            VStack(alignment: .trailing, spacing: Space.xxs) {
                OwnerMessage(text: text).modifier(OwnerMessageActions(item: id, text: text, thread: thread, toComposer: toComposer, quote: quoteMessage, tell: { toast = $0 }))
                // "Pending" (or why it was refused) until the Mac took the message; nothing once it did (item 1927).
                if let delivery, delivery != .delivered { deliveryLine(id: id, delivery: delivery) }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
            .swipeToQuote(text, quote: quoteMessage)
        case .note(_, let text):
            DotNoteRow(text: text)
        case .answer(let id, let text, let writing, let endedAt):
            VStack(alignment: .leading, spacing: Space.xxs) {
                AnswerBody(markdown: text, writing: writing)
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
            .swipeToQuote(text, quote: quoteMessage)
            .contextMenu {
                Button(String(localized: "Quote", comment: "Message menu and VoiceOver action: puts the message in the composer as a quote"), systemImage: "arrowshape.turn.up.left") { quoteMessage(text) }
                Button(String(localized: "Copy", comment: "Code block header button that copies the code"), systemImage: "doc.on.doc") { UIPasteboard.general.string = text }
            }
        case .thought(_, let text, let live):
            ThoughtRow(text: text, live: live)
        case .tool(let entry):
            WorkToolRow(entry: entry)
        case .helpers(_, _, let list):
            // The run's helpers as one row where it started them, a tap away from a card of each (item 1957); the
            // capsule's people segment keeps them a tap away too.
            HelperGroupRow(
                lines: list.map { HelperEntry(helper: $0, engine: engineLine($0, models: model.models)) },
                open: { openedHelper = $0 })
        case .plan:
            // The plan is the chip beside the capsule (item 1880), never a card in the stream.
            EmptyView()
        case .decision(let anchor):
            AskAnchorRow(anchor: anchor)
        case .work(let fold):
            WorkFoldView(fold: fold, row: { AnyView(self.row($0)) })
        case .ended(let id, let status, let detail):
            RunEndCard(item: id, ending: .ended(status: status, detail: detail), thread: thread, toComposer: toComposer, tell: { toast = $0 })
        case .limit(let id, let resetsAt):
            RunEndCard(item: id, ending: .limit(resetsAt: resetsAt), thread: thread, toComposer: toComposer, tell: { toast = $0 })
        }
    }

    @ViewBuilder private func deliveryLine(id: String, delivery: Delivery) -> some View {
        let key: String = String(id.dropFirst(2))
        if let progress = model.outbox.first(where: { $0.id == key })?.attachmentUpload {
            AttachmentTransferLine(key: key, progress: progress)
        } else {
            deliveryStatus(key: key, delivery: delivery)
        }
    }

    private func deliveryStatus(key: String, delivery: Delivery) -> some View {
        HStack(spacing: Space.xs) {
            Text(deliveryWords(delivery)).textRole(.label).foregroundStyle(isRejected(delivery) ? Tone.failure : Tone.tertiary)
            if case .rejected(let failure) = delivery {
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

    @ViewBuilder private func bottom(_ items: [TranscriptItem]) -> some View {
        let entries: [DockEntry] = dock
        VStack(alignment: .leading, spacing: Space.xs) {
            FloatingWorkingControl(
                summary: WorkingSummary(
                    status: capsuleStatus, activity: activityWords,
                    since: activityWords == nil ? activeRun.map { Date(timeIntervalSince1970: $0.createdAt) } ?? startingSince : nil, queued: view?.queue.messages.count ?? 0,
                    helpersDone: newestRunHelpers.filter { !helperState($0.status).isLive }.count, helpersTotal: newestRunHelpers.count,
                    plan: newestPlan(items).map(PlanProgress.init(steps:)), canStop: activeRun != nil && !stopping),
                showsScrollToBottom: showsJump, openStatus: { panel = .inspector },
                stop: { stop() },
                openPlan: { panel = .plan }, openQueue: { panel = .queue }, openHelpers: { panel = .agents },
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
                    quote: $quoted,
                    placeholder: activeRun == nil
                        ? botChat.map { askPlaceholder($0.record.name) } ?? String(localized: "Message Dot", comment: "Placeholder of the composer")
                        : followUp == .queue
                            ? String(localized: "Queue a message", comment: "Placeholder of the composer while a run works and follow-ups queue")
                            : String(localized: "Tell the running task", comment: "Placeholder of the composer while a run works and follow-ups steer"),
                    buttons: composerButtons(
                        hasContent: !(edit?.text ?? draft).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty,
                        runActive: activeRun != nil && !stopping, followUp: followUp, editing: edit != nil),
                    perform: perform,
                    model: currentModel, openSettings: { panel = .model },
                    suggestions: ComposerSuggestions(sections: { trigger in
                        pickerSections(trigger, commands: composerCommands(), catalog: catalog, limit: 8)
                    }))
                .disabled(chat?.freshness == .deleted || keepingMessage)
            }
        }
        .frame(maxWidth: proseWidth)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, Space.s)
        .padding(.bottom, Space.xs)
    }

    /// When the owner's newest message went out, while it has not started a run yet (item 1919): the stream and the pill
    /// move at once instead of waiting 10-15 s for the run's first event. `nil` once a run works, while the message only
    /// waits for the link or behind a held queue, and when it was refused (its bubble says why).
    private var startingSince: Date? {
        guard activeRun == nil, let newest = model.pending(in: thread).last else { return nil }
        switch newest.delivery {
        case .sending, .delivered, .queued: return Date(timeIntervalSince1970: newest.at)
        default: return nil
        }
    }

    private var capsuleStatus: ChatStatus? {
        if startingSince != nil { return .working }
        if stopping, activeRun != nil { return nil }
        switch phase {
        case .working: return .working
        case .starting: return .starting
        case .waitingOnYou: return .waitingOnYou
        default: return nil
        }
    }

    // MARK: Doings

    /// Puts a message's words in the composer as the quote of the next message.
    private func quoteMessage(_ words: String) {
        quoted = quoteOf(words)
    }

    private func perform(_ action: ComposerAction) {
        switch action {
        case .stop:
            stop()
        case .confirmEdit:
            confirmEdit()
        case .send, .queue, .steer:
            let typed: String = draft.trimmingCharacters(in: .whitespacesAndNewlines)
            let files: [OutgoingAttachment] = attachments.compactMap(\.outgoing)
            guard !keepingMessage, files.count == attachments.count, !typed.isEmpty || !files.isEmpty else { return }
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
                guard let run = ownRuns.last else { break }
                draft = ""
                Task { aside = SideAnswer(question: question, answer: await model.aside(run: run.id, question: question)) }
                return
            case .loop(let every, let text):
                guard let run = ownRuns.last else { break }
                draft = ""
                Task {
                    if let problem = await model.loop(run: run.id, every: every, text: text) {
                        toast = ToastMessage(id: UUID(), text: String(localized: "The loop was not made: \(problem)", comment: "Composer: /loop failed; the argument is why"), systemImage: "exclamationmark.triangle", actionTitle: nil)
                        draft = typed
                        return
                    }
                    await model.send(text, thread: thread, steerInto: nil, files: files, picks: .none)
                }
                attachments = []
                return
            case .message:
                break
            }
            let (words, typedPicks): (String, MessagePicks) = messagePicks(typed, catalog: catalog)
            let picks: MessagePicks = MessagePicks(skills: typedPicks.skills, accounts: typedPicks.accounts, planFirst: typedPicks.planFirst || planFirst)
            let run: String? = action == .steer ? activeRun?.id : nil
            keepingMessage = true
            Task {
                defer { keepingMessage = false }
                let text: String = quotedWords(words.isEmpty && !files.isEmpty ? attachmentMessage() : words, quote: quoted)
                guard await model.send(text, thread: thread, steerInto: run, files: files, picks: picks) != nil else {
                    toast = ToastMessage(id: UUID(), text: messageStorageFailure(), systemImage: "exclamationmark.triangle", actionTitle: nil)
                    return
                }
                // A steer carries no picks, so plan first waits for a subsequent message.
                if run == nil { planFirst = false }
                draft = ""
                quoted = nil
                attachments = []
                model.keep(draft: "", of: thread)
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            }
        }
    }

    /// What the pickers offer (PLAN §22): the daemon's skills, agents, teams and accounts, the other chats, this chat's
    /// changed files.
    private var catalog: ComposerCatalog {
        ComposerCatalog(
            skills: (model.palette?.skills ?? []).map { PickerItem(kind: .skill, token: "/" + $0.name, name: $0.name, detail: $0.description) },
            agents: model.agents.map { PickerItem(kind: .agent, token: "@" + $0.record.name.replacingOccurrences(of: " ", with: "-"), name: $0.record.name, detail: shownRole($0.record)) },
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

    /// Words a message action hands back (Edit from here, a Retry that could not send): after the newer draft, never
    /// over it (plan §13 B3).
    private func toComposer(_ words: String) {
        draft = draft.isEmpty ? words : draft + "\n" + words
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

    /// What the chip shows: the chat's choices as the sheet reads them (a model left out is the daemon's main, a level
    /// left out is the model's default), so the chip and the sheet never differ.
    private var currentModel: ModelChoice? {
        let choices: ChatChoices = chatChoices
        guard let ref = effectiveModel(choices, defaultRef: model.chatDefaults?.model) else { return nil }
        return ModelChoice(ref: ref, label: model.modelLabel(ref), effort: choices.effort)
    }

    /// The chat's choices as its next follow-up starts with them: those kept for it, else what its last run ran on.
    private var chatChoices: ChatChoices {
        if let kept = view?.newest?.choices { return kept }
        let last: RunRecord? = view?.runs.last { $0.parentRunId == nil }
        return ChatChoices.daemons.with(model: last?.model, effort: last?.effort)
    }

    private func keep(_ settings: ChatSettingsDraft) {
        if settings.choices != chatChoices { model.perform(.choices(thread: thread, choices: settings.choices)) }
        planFirst = settings.planFirst
        panel = nil
    }

    // MARK: Header

    /// A bot's header in a bot's chat, the plain chat's otherwise.
    @ToolbarContentBuilder private var chrome: some ToolbarContent {
        if let botChat {
            BotHeader(
                chat: botChat,
                subtitle: chat.flatMap {
                    freshnessLine(
                        freshness: $0.freshness, transport: model.transport, savedAt: model.savedAt[thread], workingAt: model.workingAt[thread],
                        now: .now)
                },
                find: {
                    findIndex = 0
                    finding = ""
                }, library: { panel = .files })
        } else {
            header
        }
    }

    @ToolbarContentBuilder private var header: some ToolbarContent {
        ToolbarItemGroup(placement: .topBarTrailing) {
            Button(String(localized: "Changes and files", comment: "Chat header quick panel: what the run changed"), systemImage: "folder") { panel = .inspector }
            Button(String(localized: "Agents", comment: "Chat header quick panel: the chat's helpers"), systemImage: "person.2") { panel = .agents }
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

    /// The capsule's words in place of "Working" (item 1880, the owner's reference app): "Waiting on N helpers" while the
    /// main run's own step only waits for its helpers (N still working); `nil` while it takes a step of its own, which
    /// the capsule says as "Working" with the turn's clock.
    private var activityWords: String? {
        guard capsuleStatus == .working, let run = activeRun, let chat, case .tool(let step)? = activity(of: chat, run: run.id) else { return nil }
        let working: Int = newestRunHelpers.filter { helperState($0.status).isLive }.count
        guard working > 0, isHelperStep(step.tool) else { return nil }
        return String(localized: "Waiting on \(working) helpers", comment: "Capsule: the run waits for its helpers; the argument is how many still work")
    }

    /// The plan of the chat's newest run, live as its steps are done; `nil` when that run keeps none. Zen filters the
    /// plan out of its transcript, and the pill is its only place, so it is read from a transcript of every row.
    private func newestPlan(_ items: [TranscriptItem]) -> [PlanStep]? {
        guard let run = ownRuns.last else { return nil }
        return plan(of: run.id, in: mode == .zen ? transcript(in: .showAll) : items)
    }

    @ViewBuilder private func sheet(_ panel: ChatPanel) -> some View {
        switch panel {
        case .plan:
            ScrollView {
                PlanSteps(steps: newestPlan(builtItems) ?? [])
                    .padding(.horizontal, Space.gutter)
                    .padding(.vertical, Space.m)
            }
            .inspectorSheet(title: String(localized: "Plan", comment: "Inspector section: the run's plan"), close: { self.panel = nil })
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
            ChatSettingsSheet(
                initial: ChatSettingsDraft(choices: chatChoices, planFirst: planFirst), providers: model.models, defaults: model.chatDefaults,
                problem: model.modelsProblem, retry: { Task { await model.loadModels() } },
                confirm: { keep($0) }, cancel: { self.panel = nil })
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

/// A transcript as the stream draws it, at its top and inside each folded run: no plan (the chip beside the capsule,
/// item 1880). The helpers are rows of the stream again (item 1909). Pure.
func streamItems(_ items: [TranscriptItem]) -> [TranscriptItem] {
    items.compactMap { item in
        switch item {
        case .plan: return nil
        case .work(let fold):
            return .work(WorkFold(id: fold.id, seconds: fold.seconds, tally: fold.tally, items: streamItems(fold.items)))
        default: return item
        }
    }
}

/// A transcript with only the messages (item 1915): the owner's words, Dot's answers, the asks, the helpers, how runs
/// ended and their folds; the steps of the run at work (its tool calls that did not fail, its thoughts) are left out, its
/// live line saying what it does. A finished run's fold is kept whole: its steps open from "Worked for 2m". Pure.
func messagesOnly(_ items: [TranscriptItem]) -> [TranscriptItem] {
    items.filter { item in
        switch item {
        case .thought: false
        case .tool(let entry): entry.state == .failed
        default: true
        }
    }
}

/// The plan a run keeps, from the transcript, a folded run's included; `nil` when it keeps none. Pure.
func plan(of run: String, in items: [TranscriptItem]) -> [PlanStep]? {
    for item in items.reversed() {
        switch item {
        case .plan(let id, let steps) where id == "plan:\(run)": return steps
        case .work(let fold):
            if let steps = plan(of: run, in: fold.items) { return steps }
        default: continue
        }
    }
    return nil
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
