import DotEngine
import SwiftUI

// The N0 screens wired to the engine (wave N1-client, plan §14): the chat list, one chat's transcript, the helpers and
// background sheets, pairing. The chat itself is `ChatScreen.swift` (wave N2).

// MARK: Words

extension DotEngine.ToolCallMode {
    var title: String {
        switch self {
        case .showAll: String(localized: "Show all", comment: "Tool calls setting: every tool call shows")
        case .hideSuccessful: String(localized: "Hide successful", comment: "Tool calls setting: only failed tool calls show")
        case .hideAll: String(localized: "Hide all", comment: "Tool calls setting: no tool call shows")
        case .zen: String(localized: "Zen", comment: "Tool calls setting: only final answers show")
        }
    }
}

/// The one status word of a chat row, by the fixed priority (plan §2): asks first, then a run's state.
func chatStatus(of row: ThreadRow) -> ChatStatus? {
    if row.asks > 0 { return .waitingOnYou }
    switch row.summary.latestStatus {
    case "blocked": return .waitingOnYou
    case "running", "ending": return .working
    case "queued": return .starting
    case "failed": return row.summary.unread ? .failed : nil
    case "done": return row.summary.unread ? .done : nil
    default: return nil
    }
}

/// A row's time: the clock today, the day otherwise.
func rowTime(_ seconds: Double, now: Date) -> String {
    let date: Date = Date(timeIntervalSince1970: seconds)
    if Calendar.current.isDate(date, inSameDayAs: now) { return clockWords(date, format: TimestampFormat.current(.standard), locale: .current) }
    return date.formatted(.dateTime.day().month(.abbreviated))
}

/// The quiet line under a chat's title, only when the picture is not live (plan §4.1 Freshness); transport and data
/// are told apart (R-f §3): a healthy stream never says "Reconnecting".
func freshnessLine(freshness: Freshness, transport: Transport, savedAt: Double?, workingAt: Double?, now: Date) -> String? {
    switch freshness {
    case .live: return nil
    case .deleted: return String(localized: "This chat was deleted", comment: "Chat subtitle: the chat was deleted on the Mac or the web")
    case .empty, .cached, .syncing:
        switch transport {
        case .offline:
            guard let savedAt else { return String(localized: "Offline", comment: "Chat subtitle: the phone has no link") }
            let ago: String = Date(timeIntervalSince1970: savedAt).formatted(.relative(presentation: .numeric))
            return String(localized: "Offline · saved \(ago)", comment: "Chat subtitle: no link; the argument says how long ago the copy was saved, e.g. '4 min ago'")
        case .reconnecting: return String(localized: "Reconnecting…", comment: "Chat subtitle: the link to the Mac broke and is being opened again")
        case .keyRefused: return String(localized: "This phone's key was refused", comment: "Chat subtitle: the Mac no longer takes this phone's key")
        case .daemonTooOld: return String(localized: "Update Dot on your Mac", comment: "Chat subtitle: the Mac runs a Dot too old for this app")
        case .connecting, .up:
            if let workingAt {
                let clock: String = clockWords(Date(timeIntervalSince1970: workingAt), format: TimestampFormat.current(.standard), locale: .current)
                return String(localized: "Was working at \(clock) · syncing…", comment: "Chat subtitle: a cached copy saved while a run worked; the argument is the clock time")
            }
            return String(localized: "Syncing…", comment: "Chat subtitle: the chat is being brought up to date")
        }
    }
}

/// Where an owner's message stands, in words (plan §13 B3).
func deliveryWords(_ delivery: Delivery) -> String {
    switch delivery {
    case .offline: String(localized: "Waiting for the link", comment: "Under an owner's message: it is kept and goes when the phone is online")
    case .sending: String(localized: "Sending…", comment: "Under an owner's message: it is going to the Mac")
    case .queued(let position):
        String(localized: "Queued · \(position) · starts after this run", comment: "Under an owner's message: its place in the chat's queue")
    case .held(let position): String(localized: "Queued · \(position) · the queue is held", comment: "Under an owner's message: its place in a held queue")
    case .sentToRun: String(localized: "Sent to the running task", comment: "Under an owner's message: handed to the run at work")
    case .readsAfterStep:
        String(localized: "Delivered · Dot reads it after this step", comment: "Under an owner's message: handed over while a tool of the run is still open")
    case .delivered: String(localized: "Sent", comment: "Under an owner's message: the Mac took it")
    case .rejected(let failure): failureWords(failure)
    }
}

func failureWords(_ failure: OutboxItem.Failure) -> String {
    switch failure {
    case .refused(_, let detail) where !detail.isEmpty:
        String(localized: "Not sent: \(detail)", comment: "Under an owner's message the Mac refused; the argument is the Mac's own words")
    case .refused: String(localized: "Not sent: the Mac refused it", comment: "Under an owner's message the Mac refused without a reason")
    case .runEnded: String(localized: "Not sent: the run had ended", comment: "Under a message meant for a run that ended before it arrived")
    case .tooOld: String(localized: "Not sent: it waited more than a day", comment: "Under an owner's message too old to send again safely")
    case .unreadable: String(localized: "Not sent: the Mac's answer could not be read", comment: "Under an owner's message whose answer was unreadable")
    case .photoMissing: String(localized: "Not sent: a photo of it could not be read on this phone", comment: "Under an owner's message whose photo file is gone")
    }
}

/// A helper's state word from its run's status.
func helperState(_ status: String) -> HelperState {
    switch status {
    case "running", "queued", "ending": .working
    case "blocked": .waitingOnYou
    case "failed": .failed
    case "cancelled": .stopped
    default: .done
    }
}

/// "Provider · model · effort", the daemon's words.
func engineLine(_ helper: HelperView) -> String {
    [helper.provider, helper.model, helper.effort].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
}

// MARK: The chat list

/// Which chats a list shows: all of them, or one bot's (Dot's own, `agent` nil, or an agent's), as a bot's profile will
/// (PLAN §27).
enum ChatListScope: Equatable, Sendable {
    case all
    case bot(agent: String?)
    /// A bot's chats as the home counts them (`bots(...)`: Dot's are every chat no known agent wears, a campaign's and a
    /// team's among them), by id; `agent` is whose chat the pencil starts (`nil`: Dot's).
    case chats(of: Set<String>, agent: String?)

    func holds(_ row: ThreadRow) -> Bool {
        switch self {
        case .all: true
        case .bot(let agent): row.summary.actorId == agent
        case .chats(let ids, _): ids.contains(row.summary.id)
        }
    }

    /// The agent whose chat the list's pencil starts.
    var agent: String? {
        switch self {
        case .all: nil
        case .bot(let agent), .chats(_, let agent): agent
        }
    }
}

/// A list of chats, self-contained so it can stand in a tab or in a bot's profile (PLAN §23, §27): the Pinned section
/// on top, then Needs you, Working, Recent; search; swipes and a context menu per row; Select mode with Pin and Delete
/// for several chats at once; the pencil starts a new chat. `selection` is the chat open beside it.
struct ChatListView: View {
    let scope: ChatListScope
    @Binding var selection: String?
    @Environment(AppModel.self) private var model: AppModel
    @State private var query: String = ""
    @State private var renaming: ThreadRow?
    @State private var newTitle: String = ""
    @State private var deleting: ThreadRow?
    @AppStorage(PrefKey.confirmDelete) private var confirmDelete: Bool = true
    @State private var composing: Bool = false
    /// Select mode (Select in the bar, or Select on a row's menu): rows take a check, the bottom bar acts on them.
    @State private var selecting: Bool = false
    @State private var picked: Set<String> = []
    @State private var deletingPicked: Bool = false
    /// What the daemon refused to delete, said once.
    @State private var toast: ToastMessage?

    var body: some View {
        list
        .alert(String(localized: "Rename", comment: "Context menu of a chat"), isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField(String(localized: "Name", comment: "Rename alert: the field of the chat's new name"), text: $newTitle)
            Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), role: .cancel) { renaming = nil }
            Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits")) {
                if let row = renaming, !newTitle.trimmingCharacters(in: .whitespaces).isEmpty {
                    model.perform(.rename(thread: row.summary.id, title: newTitle))
                }
                renaming = nil
            }
        }
        .confirmationDialog(
            String(localized: "Delete this chat with everything it holds?", comment: "Confirmation of deleting a chat"),
            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible
        ) {
            Button(String(localized: "Delete", comment: "Confirmation button that deletes a chat"), role: .destructive) {
                if let row = deleting { model.perform(.delete(thread: row.summary.id)) }
                deleting = nil
            }
        }
        .confirmationDialog(
            pickedDeleteTitle, isPresented: $deletingPicked, titleVisibility: .visible
        ) {
            Button(String(localized: "Delete", comment: "Confirmation button that deletes a chat"), role: .destructive) { deletePicked() }
                .accessibilityIdentifier("chats.confirmDelete")
        } message: {
            if pickedRows.contains(where: isWorking) {
                Text(String(localized: "Chats with a run at work or waiting on you are kept; stop or answer them first.", comment: "Bulk delete confirmation: chats at work or waiting on the owner are not deleted"))
            }
        }
        .sheet(isPresented: $composing) {
            NewChatSheet(
                agent: scopedAgent, close: { composing = false },
                opened: { key in
                    composing = false
                    selection = key
                })
        }
        .toastHost($toast, seconds: 5) { _ in }
        .onChange(of: model.refusedDeletes) { _, refused in
            guard let detail = refused.values.first else { return }
            toast = ToastMessage(
                id: UUID(), text: String(localized: "Some chats were not deleted: \(detail)", comment: "Toast: the Mac refused to delete chats; the argument is its words"),
                systemImage: "exclamationmark.triangle", actionTitle: nil)
            model.forgetRefusedDeletes()
        }
    }

    /// The agent a bot's list belongs to, whose chat the pencil starts (Dot's and the whole list's: none).
    private var scopedAgent: AgentRecord? {
        guard let agent = scope.agent else { return nil }
        return model.agents.first { $0.record.id == agent }?.record
    }

    /// The checked chats the list shows now: a check hidden by the search or gone from the list is not acted on.
    private var pickedRows: [ThreadRow] { rows.filter { picked.contains($0.summary.id) } }

    private var pickedDeleteTitle: String {
        let count: Int = pickedRows.filter { !isWorking($0) }.count
        return String(localized: "Delete \(count) chats with everything they hold?", comment: "Confirmation of deleting the selected chats; the argument is how many")
    }

    /// A chat the daemon refuses to delete (409): its newest run queued, running, waiting on the owner or ending
    /// (`WORKING_STATUSES`), or an ask open. A helper still at work is not in the row; its refusal comes back as a toast.
    private func isWorking(_ row: ThreadRow) -> Bool {
        chatHoldsWork(row)
    }

    private func deletePicked() {
        for row in pickedRows where !isWorking(row) { model.perform(.delete(thread: row.summary.id)) }
        if let open = selection, picked.contains(open) { selection = nil }
        endSelecting()
    }

    /// Pins every selected chat, or unpins them when all of them are pinned already.
    private func pinPicked() {
        let pin: Bool = !pickedRows.allSatisfy { $0.summary.pinnedAt != nil }
        for row in pickedRows where (row.summary.pinnedAt != nil) != pin { model.perform(.pin(thread: row.summary.id, pinned: pin)) }
        endSelecting()
    }

    /// Starts select mode, or adds the row to the checks already made.
    private func beginSelecting(with row: ThreadRow?) {
        if let row { picked.insert(row.summary.id) }
        selecting = true
    }

    private func endSelecting() {
        selecting = false
        picked = []
    }

    /// Deletes at once when the owner turned the confirmation off (Settings › General › Confirmations).
    private func askOrDelete(_ row: ThreadRow) {
        if confirmDelete { deleting = row } else { model.perform(.delete(thread: row.summary.id)) }
    }

    private var rows: [ThreadRow] {
        let words: String = query.trimmingCharacters(in: .whitespaces)
        let all: [ThreadRow] = model.list.ordered.filter(scope.holds)
        guard !words.isEmpty else { return all }
        return all.filter { $0.summary.title.localizedStandardContains(words) || $0.summary.lastMessage.localizedStandardContains(words) }
    }

    private var list: some View {
        // In select mode a tap checks a row and opens nothing.
        List(selection: selecting ? .constant(nil) : $selection) {
            if model.reset != nil {
                Notice(tone: .info, message: resetWords, dismiss: { model.resetSeen() })
                    .listRowBackground(Tone.ground)
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: Space.s, leading: Space.gutter, bottom: Space.s, trailing: Space.gutter))
                    .accessibilityIdentifier("reset-notice")
            }
            if let line = listLine {
                Text(line).textRole(.footnote).foregroundStyle(Tone.secondary).listRowBackground(Tone.ground)
            }
            ForEach(model.pendingChats) { pending in
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(pending.text).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(2)
                    Text(deliveryWords(pending.delivery)).textRole(.footnote).foregroundStyle(Tone.secondary)
                }
                .listRowBackground(Tone.ground)
            }
            let grouped: ChatListSections = chatListSections(rows)
            section(String(localized: "Pinned", comment: "Chat list section: the chats the owner pinned, on top"), grouped.pinned)
            section(String(localized: "Needs you", comment: "Chat list section: chats that wait for the owner"), grouped.needsYou)
            section(String(localized: "Working", comment: "Chat list section: chats with a run at work"), grouped.working)
            section(String(localized: "Recent", comment: "Chat list section: the other chats, newest first"), grouped.recent)
        }
        .listStyle(.plain)
        .focusEffectDisabled()
        .pageGround()
        .overlay {
            if rows.isEmpty && !query.isEmpty {
                ContentUnavailableView.search(text: query)
            } else if rows.isEmpty, model.list.freshness == .live {
                EmptyState(
                    title: String(localized: "No chats yet", comment: "Chat list empty state title"), systemImage: "bubble.left.and.bubble.right",
                    detail: String(localized: "Start one with the pencil above.", comment: "Chat list empty state detail"), actionTitle: nil, action: {})
            } else if rows.isEmpty, model.list.freshness == .empty, model.transport == .connecting {
                LoadingState(message: String(localized: "Reaching your Mac…", comment: "Chat list while the first list read is under way"))
            }
        }
        .navigationTitle(String(localized: "Chats", comment: "Title of the chat list"))
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $query, prompt: String(localized: "Search chats", comment: "Placeholder of the chat list's search field"))
        .toolbar { listToolbar }
        .toolbar(selecting ? .hidden : .automatic, for: .tabBar)
        .animation(.default, value: selecting)
    }

    @ToolbarContentBuilder private var listToolbar: some ToolbarContent {
        if selecting {
            ToolbarItem(placement: .topBarLeading) {
                let all: Bool = !rows.isEmpty && pickedRows.count == rows.count
                Button(all ? String(localized: "Deselect all", comment: "Chat list select mode: clears the checks") : String(localized: "Select all", comment: "Chat list select mode: checks every chat shown")) {
                    picked = all ? [] : Set(rows.map(\.summary.id))
                }
                .accessibilityIdentifier("chats.selectAll")
            }
            ToolbarItem(placement: .topBarTrailing) {
                // The system's ✓ of a confirm role: select mode ends, nothing else changes.
                Button(role: .confirm) { endSelecting() }
                    .accessibilityIdentifier("chats.doneSelecting")
            }
            ToolbarItemGroup(placement: .bottomBar) {
                let allPinned: Bool = !pickedRows.isEmpty && pickedRows.allSatisfy { $0.summary.pinnedAt != nil }
                Button(allPinned ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat") : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"), systemImage: allPinned ? "pin.slash" : "pin") { pinPicked() }
                    .disabled(pickedRows.isEmpty)
                    .accessibilityIdentifier("chats.pinPicked")
                Spacer()
                Text(String(localized: "\(pickedRows.count) selected", comment: "Chat list select mode: how many chats are checked")).textRole(.footnote).foregroundStyle(Tone.secondary)
                    .accessibilityIdentifier("chats.pickedCount")
                Spacer()
                Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive) { deletingPicked = true }
                    .tint(Tone.failure)
                    .disabled(pickedRows.allSatisfy(isWorking))
                    .accessibilityIdentifier("chats.deletePicked")
            }
        } else {
            ToolbarItem(placement: .topBarLeading) {
                Button(String(localized: "Select", comment: "Chat list toolbar: checks chats to act on several")) { beginSelecting(with: nil) }
                    .disabled(rows.isEmpty)
                    .accessibilityIdentifier("chats.select")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button(String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), systemImage: "square.and.pencil") { composing = true }
                    .accessibilityIdentifier("chats.new")
            }
        }
    }

    /// What the reset's notice says; where the unsent words went while the new chat's draft holds them.
    private var resetWords: String {
        var lines: [String] = [String(localized: "Your Mac's Dot was reset; the old chats are gone from this phone.", comment: "Chat list notice: the Mac's Dot was reset and its old chats left the phone")]
        if !model.draft(of: AppModel.newChatDraft).isEmpty {
            lines.append(String(localized: "Your unsent words wait in New chat.", comment: "Chat list notice after a reset: the words that were not sent are the new chat's draft"))
        }
        if model.reset?.photosLeft == true {
            lines.append(String(localized: "Photos of unsent messages were not kept.", comment: "Chat list notice after a reset: the photos of the messages that were not sent are gone"))
        }
        return lines.joined(separator: " ")
    }

    /// A row, with its check in select mode (a tap checks it there and opens nothing).
    @ViewBuilder private func selectableRow(_ row: ThreadRow) -> some View {
        if selecting {
            let on: Bool = picked.contains(row.summary.id)
            Button {
                if on { picked.remove(row.summary.id) } else { picked.insert(row.summary.id) }
            } label: {
                HStack(spacing: Space.s) {
                    Image(systemName: on ? "checkmark.circle.fill" : "circle").font(.system(.title3, weight: .regular))
                        .foregroundStyle(on ? Tone.text : Tone.tertiary)
                        .accessibilityHidden(true)
                    ChatRow(chat: ChatRowModel(row: row, now: .now))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .haptic(.selection, trigger: on)
            .accessibilityAddTraits(on ? [.isSelected] : [])
            .accessibilityIdentifier("chats.row")
        } else {
            ChatRow(chat: ChatRowModel(row: row, now: .now)).accessibilityIdentifier("chats.row")
        }
    }

    /// The list's own line when it is not live (transport apart from freshness).
    private var listLine: String? {
        freshnessLine(
            freshness: model.list.freshness, transport: model.transport, savedAt: model.savedAt["list"], workingAt: nil, now: .now)
    }

    @ViewBuilder private func section(_ title: String, _ rows: [ThreadRow]) -> some View {
        if !rows.isEmpty {
            Section {
                ForEach(rows, id: \.summary.id) { row in
                    selectableRow(row)
                        .tag(row.summary.id)
                        .listRowBackground(selection == row.summary.id ? Tone.nested : Tone.ground)
                        .focusEffectDisabled()
                        .listRowSeparatorTint(Tone.separator)
                        .listRowInsets(EdgeInsets(top: Space.s, leading: Space.gutter, bottom: Space.s, trailing: Space.gutter))
                        // In select mode a row only takes its check: no swipe, no menu.
                        .swipeActions(edge: .leading) {
                            if !selecting {
                                Button(row.summary.pinnedAt != nil ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat") : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"), systemImage: row.summary.pinnedAt != nil ? "pin.slash" : "pin") {
                                    model.perform(.pin(thread: row.summary.id, pinned: row.summary.pinnedAt == nil))
                                }
                                .tint(Tone.glyph)
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            if !selecting {
                                Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash") { askOrDelete(row) }
                                    .tint(Tone.failure)
                            }
                        }
                        .contextMenu {
                            if !selecting {
                                Button(String(localized: "Rename", comment: "Context menu of a chat"), systemImage: "pencil") {
                                    newTitle = row.summary.title
                                    renaming = row
                                }
                                Button(row.summary.pinnedAt != nil ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat") : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"), systemImage: row.summary.pinnedAt != nil ? "pin.slash" : "pin") {
                                    model.perform(.pin(thread: row.summary.id, pinned: row.summary.pinnedAt == nil))
                                }
                                Button(String(localized: "Select", comment: "Chat list toolbar: checks chats to act on several"), systemImage: "checkmark.circle") { beginSelecting(with: row) }
                                Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive) { askOrDelete(row) }
                            }
                        }
                }
            } header: {
                Text(title).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
            }
        }
    }
}

/// Whether a chat holds work the daemon will not delete under (`WORKING_STATUSES`: queued, running, blocked, ending) or
/// an ask open to the owner (pure).
func chatHoldsWork(_ row: ThreadRow) -> Bool {
    row.asks > 0 || ["queued", "running", "blocked", "ending"].contains(row.summary.latestStatus)
}

extension AppModel {
    /// The list told the owner of the refused deletes.
    func forgetRefusedDeletes() {
        guard let engine else { return }
        Task { await engine.store.forgetRefusedDeletes() }
    }
}

/// The chat list's sections (PLAN §23): the pinned chats on top whatever their state, then the chats that wait for
/// the owner, those at work, and the rest newest first.
struct ChatListSections: Equatable {
    let pinned: [ThreadRow]
    let needsYou: [ThreadRow]
    let working: [ThreadRow]
    let recent: [ThreadRow]
}

func chatListSections(_ rows: [ThreadRow]) -> ChatListSections {
    let pinned: [ThreadRow] = rows.filter { $0.summary.pinnedAt != nil }
    let rest: [ThreadRow] = rows.filter { $0.summary.pinnedAt == nil }
    return ChatListSections(
        pinned: pinned, needsYou: rest.filter { chatStatus(of: $0) == .waitingOnYou },
        working: rest.filter { [.working, .starting].contains(chatStatus(of: $0)) },
        recent: rest.filter { ![.waitingOnYou, .working, .starting].contains(chatStatus(of: $0)) })
}

/// What a chat row shows.
struct ChatRowModel: Equatable {
    let title: String
    let preview: String
    let time: String
    let status: ChatStatus?
    let agent: String?

    init(row: ThreadRow, now: Date) {
        title = row.summary.title
        preview = row.summary.lastMessage
        time = rowTime(row.summary.latestCreatedAt, now: now)
        status = chatStatus(of: row)
        agent = row.summary.actorId
    }

    init(title: String, preview: String, time: String, status: ChatStatus?, agent: String?) {
        self.title = title
        self.preview = preview
        self.time = time
        self.status = status
        self.agent = agent
    }
}

/// A chat in the list: the agent's face, the title (two lines at most), one status word by priority, the last line and
/// a tabular time. VoiceOver reads one label.
struct ChatRow: View {
    let chat: ChatRowModel
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            if !dynamicTypeSize.isAccessibilitySize {
                if chat.agent == nil { DotFace(size: 32) } else { AgentAvatar(size: 32) }
            }
            VStack(alignment: .leading, spacing: Space.xxs) {
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Text(chat.title).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(2)
                    Spacer(minLength: Space.xs)
                    if !dynamicTypeSize.isAccessibilitySize {
                        Text(chat.time).textRole(.label).foregroundStyle(Tone.tertiary).tabularNumbers()
                    }
                }
                if let status = chat.status { StatusWord(status: status) }
                if !chat.preview.isEmpty {
                    Text(chat.preview).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(2)
                }
                if dynamicTypeSize.isAccessibilitySize {
                    Text(chat.time).textRole(.label).foregroundStyle(Tone.tertiary).tabularNumbers()
                }
            }
        }
        .frame(minHeight: Metric.rowWithDescription)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([chat.title, chat.status?.word, chat.preview, chat.time].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: ", "))
    }
}

/// A new chat (PLAN §24): a clean canvas, the composer, the model chip in the bar and in the composer. The chat's
/// settings are chosen before the first message (model, level, fast mode, approvals, plan first) and go with it
/// (`POST /runs`); once sent, the chat opens at once under the outbox's key (item 1849) and shows in the list from the
/// outbox. With an `agent` (a bot's New chat, item 1883) the chat is with that agent, its face above the composer.
struct NewChatSheet: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var text: String = ""
    @State private var attachments: [ComposerAttachment] = []
    let agent: AgentRecord?
    @State private var settings: ChatSettingsDraft = ChatSettingsDraft(choices: .daemons, planFirst: false)
    @State private var choosing: Bool = false
    let close: () -> Void
    /// The chat was kept in the outbox under this key: the sheet closes and the chat opens.
    let opened: (String) -> Void

    private var chip: ModelChoice? {
        guard let ref = effectiveModel(settings.choices, defaultRef: model.chatDefaults?.model) else { return nil }
        return ModelChoice(ref: ref, label: model.modelLabel(ref), effort: settings.choices.effort)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Spacer(minLength: 0)
                if let agent {
                    BotFace(id: agent.id, look: agent.look, size: 64)
                    Text(agent.name).textRole(.title).foregroundStyle(Tone.text).multilineTextAlignment(.center)
                    Spacer(minLength: 0)
                }
                ComposerView(
                    text: $text, attachments: $attachments,
                    placeholder: agent.map { askPlaceholder($0.name) } ?? String(localized: "Message Dot", comment: "Placeholder of the composer"),
                    buttons: composerButtons(
                        // A new chat needs words (the daemon's goal is never empty): photos alone do not start one.
                        hasContent: !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, runActive: false, followUp: .queue, editing: false),
                    perform: { _ in send() }, model: chip, openSettings: { choosing = true }, suggestions: .none)
                .padding(.horizontal, Space.s)
                .padding(.bottom, Space.xs)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Tone.ground)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                }
                ToolbarItem(placement: .principal) {
                    Button { choosing = true } label: {
                        HStack(spacing: Space.xxs) {
                            Text(chip?.label ?? String(localized: "New chat", comment: "Chat list toolbar: starts a new chat")).textRole(.body).fontWeight(.semibold)
                                .foregroundStyle(Tone.text).lineLimit(1)
                            Symbol(name: "chevron.down", context: .chevron).foregroundStyle(Tone.glyph)
                        }
                        .frame(minHeight: Metric.hit)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(
                        chip.map { String(localized: "Model, \($0.label)", comment: "VoiceOver: the new chat's model button; the argument is the model") }
                            ?? String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"))
                    .accessibilityHint(String(localized: "Opens the chat's settings", comment: "VoiceOver hint of the composer's model chip"))
                    .accessibilityIdentifier("newChat.model")
                }
            }
        }
        .presentationDetents([.large])
        .presentationBackground(Tone.ground)
        .tint(Tone.text)
        .task { await model.loadModels() }
        // The new chat keeps its half-written words like a chat does (and holds those a reset left unsent).
        .onAppear { text = model.draft(of: AppModel.newChatDraft) }
        .onChange(of: text) { _, words in model.keep(draft: words, of: AppModel.newChatDraft) }
        .sheet(isPresented: $choosing) {
            ChatSettingsSheet(
                initial: settings, providers: model.models, defaults: model.chatDefaults, problem: model.modelsProblem,
                retry: { Task { await model.loadModels() } },
                confirm: { picked in
                    settings = picked
                    choosing = false
                }, cancel: { choosing = false })
        }
    }

    private func send() {
        let words: String = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { return }
        let photos: [Data] = attachments.map(\.jpeg)
        let start: ChatStart = ChatStart(choices: settings.choices, planFirst: settings.planFirst)
        Task {
            if let key = await model.startChat(words, photos: photos, start: start, agent: agent?.id) {
                model.keep(draft: "", of: AppModel.newChatDraft)
                opened(key)
            } else {
                close()
            }
        }
    }
}

/// A new chat between its first send and the daemon's answer (item 1849): the owner's message where the chat shows it,
/// with where it stands, under the chat's title; the list swaps it for the real chat, in place, once the daemon names it.
struct StartingChat: View {
    let key: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?

    var body: some View {
        let text: String = model.startingChats[key] ?? ""
        let delivery: Delivery = model.pendingChats.first { $0.key == key }?.delivery ?? .delivered
        ScrollView {
            VStack(alignment: .trailing, spacing: Space.xxs) {
                OwnerMessage(text: text)
                Text(deliveryWords(delivery)).textRole(.label).foregroundStyle(refused(delivery) ? Tone.failure : Tone.tertiary)
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.m)
        }
        .background(Tone.ground)
        .navigationTitle(text)
        .navigationSubtitle(ChatStatus.starting.word)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(sizeClass == .regular ? .automatic : .hidden, for: .tabBar)
    }

    private func refused(_ delivery: Delivery) -> Bool {
        if case .rejected = delivery { return true }
        return false
    }
}

/// The helpers of a chat (plan §18), live from its view: the clocks run from their starts, Stop goes through the outbox
/// and shows "Stopping…" until the helper's status says it ended.
struct LiveAgentsSheet: View {
    let thread: String
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var stopping: Set<String> = []
    /// The helper whose chat the sheet shows (item 1880: a row opens its helper's chat).
    @State private var opened: String?

    var body: some View {
        let helpers: [HelperView] = model.chats[thread]?.view?.helpers ?? []
        Group {
            if helpers.isEmpty {
                EmptyState(
                    title: String(localized: "No helpers", comment: "Agents sheet empty state: the chat started no helper"), systemImage: "person.2",
                    detail: String(localized: "Helpers Dot starts for this chat show here.", comment: "Agents sheet empty state detail"), actionTitle: nil, action: {})
            } else {
                List {
                    ForEach(helpers, id: \.runId) { helper in
                        HelperRow(run: run(of: helper), open: { opened = helper.threadId }, stop: {
                            stopping.insert(helper.runId)
                            model.perform(.stopHelper(path: helper.stop, run: helper.runId, thread: thread))
                        })
                        .listRowBackground(Color.clear)
                        .listRowSeparatorTint(Tone.separator)
                        .listRowInsets(EdgeInsets(top: 0, leading: Space.l, bottom: 0, trailing: Space.l))
                    }
                }
                .listStyle(.plain)
            }
        }
        .navigationDestination(item: $opened) { helperThread in
            ChatScreen(thread: helperThread, initialPanel: nil, editsFirstQueued: false)
        }
        .inspectorSheet(title: String(localized: "Agents", comment: "Title of the sheet of a chat's helpers"), close: close)
    }

    private func run(of helper: HelperView) -> HelperRun {
        var state: HelperState = helperState(helper.status)
        if stopping.contains(helper.runId), state.isLive { state = .stopping }
        return HelperRun(
            id: helper.runId, title: helper.shortTitle, state: state, startedAt: Date(timeIntervalSince1970: helper.startedAt),
            endedAt: helper.finishedAt.map { Date(timeIntervalSince1970: $0) }, engine: engineLine(helper),
            doing: helper.step.map { stepWords(tool: $0.tool, target: $0.target) } ?? (state.isLive ? ChatStatus.starting.word : nil))
    }
}

/// Debug builds: measures send → first event on screen (the e2e record). `-DotNextProbe <thread>` makes the chat send
/// a numbered message on each tap of the probe; the time from the send to the first transcript item of the run it
/// started is appended to `Library/Caches/dotnext-latency.txt`.
enum LatencyProbe {
    /// Sends `-DotNextProbe <count>` numbered messages, one after the other's run ended, and leaves the times to `saw`.
    @MainActor
    static func run(thread: String, model: AppModel) async {
        #if DEBUG
            let count: Int = UserDefaults.standard.integer(forKey: "DotNextProbe")
            guard count > 0 else { return }
            for number in 1...count {
                while !(model.chats[thread]?.freshness == .live
                    && model.chats[thread]?.view?.runs.contains(where: \.isActive) == false)
                {
                    try? await Task.sleep(for: .milliseconds(100))
                    if Task.isCancelled { return }
                }
                let text: String = "probe \(number) \(UUID().uuidString.prefix(6))"
                model.probeSent[text] = Date()
                await model.send(text, thread: thread, steerInto: nil, photos: [], picks: .none)
                for _ in 0..<200 where model.probeSent[text] != nil { try? await Task.sleep(for: .milliseconds(100)) }
                try? await Task.sleep(for: .milliseconds(300))
            }
        #endif
    }

    @MainActor
    static func saw(items: [TranscriptItem], model: AppModel) {
        #if DEBUG
            guard !model.probeSent.isEmpty else { return }
            for (text, sent) in model.probeSent {
                // The run's own first event: its goal drawn from the stream (not the outbox's overlay, keyed "p:").
                let shown: Bool = items.contains { item in
                    if case .owner(let id, let words, nil) = item { return id.hasPrefix("u:") && words == text }
                    return false
                }
                guard shown else { continue }
                model.probeSent[text] = nil
                let ms: Int = Int(Date().timeIntervalSince(sent) * 1000)
                let line: String = "\(text)\t\(ms)\n"
                let file: URL = URL.cachesDirectory.appending(path: "dotnext-latency.txt")
                if let handle = try? FileHandle(forWritingTo: file) {
                    handle.seekToEndOfFile()
                    handle.write(Data(line.utf8))
                    try? handle.close()
                } else {
                    try? Data(line.utf8).write(to: file)
                }
            }
        #endif
    }
}
