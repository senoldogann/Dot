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

struct ChatsTab: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var selection: String?
    @State private var query: String = ""
    @State private var renaming: ThreadRow?
    @State private var newTitle: String = ""
    @State private var deleting: ThreadRow?
    @AppStorage(PrefKey.confirmDelete) private var confirmDelete: Bool = true
    @State private var composing: Bool = false
    let opensFirstChat: Bool

    var body: some View {
        AdaptiveSplit(selection: $selection) {
            list
        } detail: { id in
            if model.startingChats[id] != nil {
                StartingChat(key: id)
            } else {
                ChatScreen(thread: id, initialPanel: nil, editsFirstQueued: false)
            }
        } placeholder: {
            EmptyState(
                title: String(localized: "No chat open", comment: "iPad detail column when no chat is selected"), systemImage: "bubble.left.and.bubble.right",
                detail: String(localized: "Pick a chat on the left, or start a new one.", comment: "iPad detail column when no chat is selected"), actionTitle: nil, action: {})
                .background(Tone.ground)
        }
        // A new chat opened under its outbox key becomes the real chat, in place, once the daemon names it (item 1849).
        .onChange(of: selection.flatMap { model.startedChats[$0] }) { _, thread in
            if let thread { selection = thread }
        }
        .onChange(of: model.list.rows.count) { _, count in
            if opensFirstChat, selection == nil, count > 0 { selection = model.list.ordered.first?.summary.id }
        }
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
        .sheet(isPresented: $composing) {
            NewChatSheet(
                close: { composing = false },
                opened: { key in
                    composing = false
                    selection = key
                })
        }
    }

    /// Deletes at once when the owner turned the confirmation off (Settings › General › Confirmations).
    private func askOrDelete(_ row: ThreadRow) {
        if confirmDelete { deleting = row } else { model.perform(.delete(thread: row.summary.id)) }
    }

    private var rows: [ThreadRow] {
        let words: String = query.trimmingCharacters(in: .whitespaces)
        let all: [ThreadRow] = model.list.ordered
        guard !words.isEmpty else { return all }
        return all.filter { $0.summary.title.localizedStandardContains(words) || $0.summary.lastMessage.localizedStandardContains(words) }
    }

    private var list: some View {
        List(selection: $selection) {
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
            section(String(localized: "Needs you", comment: "Chat list section: chats that wait for the owner"), rows.filter { chatStatus(of: $0) == .waitingOnYou })
            section(String(localized: "Working", comment: "Chat list section: chats with a run at work"), rows.filter { [.working, .starting].contains(chatStatus(of: $0)) })
            section(String(localized: "Recent", comment: "Chat list section: the other chats, newest first"), rows.filter { ![.waitingOnYou, .working, .starting].contains(chatStatus(of: $0)) })
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
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), systemImage: "square.and.pencil") { composing = true }
            }
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
                    ChatRow(chat: ChatRowModel(row: row, now: .now))
                        .tag(row.summary.id)
                        .listRowBackground(selection == row.summary.id ? Tone.nested : Tone.ground)
                        .focusEffectDisabled()
                        .listRowSeparatorTint(Tone.separator)
                        .listRowInsets(EdgeInsets(top: Space.s, leading: Space.gutter, bottom: Space.s, trailing: Space.gutter))
                        .swipeActions(edge: .leading) {
                            Button(row.summary.pinnedAt != nil ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat") : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"), systemImage: row.summary.pinnedAt != nil ? "pin.slash" : "pin") {
                                model.perform(.pin(thread: row.summary.id, pinned: row.summary.pinnedAt == nil))
                            }
                            .tint(Tone.glyph)
                        }
                        .swipeActions(edge: .trailing) {
                            Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash") { askOrDelete(row) }
                                .tint(Tone.failure)
                        }
                        .contextMenu {
                            Button(String(localized: "Rename", comment: "Context menu of a chat"), systemImage: "pencil") {
                                newTitle = row.summary.title
                                renaming = row
                            }
                            Button(row.summary.pinnedAt != nil ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat") : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"), systemImage: "pin") {
                                model.perform(.pin(thread: row.summary.id, pinned: row.summary.pinnedAt == nil))
                            }
                            Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive) { askOrDelete(row) }
                        }
                }
            } header: {
                Text(title).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
            }
        }
    }
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

/// A new chat: the composer on an empty sheet (plan §4.2); once sent, the chat opens at once under the outbox's key
/// (item 1849) and shows in the list from the outbox.
struct NewChatSheet: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var text: String = ""
    @State private var attachments: [ComposerAttachment] = []
    let close: () -> Void
    /// The chat was kept in the outbox under this key: the sheet closes and the chat opens.
    let opened: (String) -> Void

    var body: some View {
        VStack {
            Spacer()
            Text(String(localized: "What should Dot work on?", comment: "New chat sheet: the prompt above the composer"))
                .textRole(.title).foregroundStyle(Tone.text).multilineTextAlignment(.center)
            Spacer()
            ComposerView(
                text: $text, attachments: $attachments, placeholder: String(localized: "Message Dot", comment: "Placeholder of the composer"),
                buttons: composerButtons(hasContent: !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, runActive: false, followUp: .queue, editing: false),
                perform: { _ in
                    let words: String = text.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !words.isEmpty else { return }
                    let photos: [Data] = attachments.map(\.jpeg)
                    Task {
                        if let key = await model.startChat(words, photos: photos) { opened(key) } else { close() }
                    }
                }, model: nil, models: [:], chooseModel: nil, suggestions: .none)
            .padding(Space.s)
        }
        .padding(Space.gutter)
        .inspectorSheet(title: String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), close: close)
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
                        HelperRow(run: run(of: helper), open: {}, stop: {
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
        .inspectorSheet(title: String(localized: "Agents", comment: "Title of the sheet of a chat's helpers"), close: close)
    }

    private func run(of helper: HelperView) -> HelperRun {
        var state: HelperState = helperState(helper.status)
        if stopping.contains(helper.runId), state.isLive { state = .stopping }
        return HelperRun(
            id: helper.runId, title: helper.shortTitle, state: state, startedAt: Date(timeIntervalSince1970: helper.startedAt),
            endedAt: helper.finishedAt.map { Date(timeIntervalSince1970: $0) }, engine: engineLine(helper))
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
