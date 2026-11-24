import DotEngine
import SwiftUI

// The N0 screens wired to the engine (wave N1-client, plan §14): the chat list, one chat's transcript, the helpers and
// background sheets, pairing. The full chat UX (work log groups, ask dock, composer modes, inspector) is wave N2.

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
    if Calendar.current.isDate(date, inSameDayAs: now) { return date.formatted(date: .omitted, time: .shortened) }
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
                let clock: String = Date(timeIntervalSince1970: workingAt).formatted(date: .omitted, time: .shortened)
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
    }
}

/// The glyph of a tool call by its tool.
func toolSymbol(_ tool: String) -> String {
    if tool.hasPrefix("browser") { return "globe" }
    if tool.hasPrefix("screen") || tool.hasPrefix("computer") { return "cursorarrow.click" }
    switch tool {
    case "shell", "terminal", "run_command": return "terminal"
    case "read_file", "read": return "doc.text"
    case "write_file", "edit_file", "edit": return "pencil"
    case "search", "grep", "web_search": return "magnifyingglass"
    case "wait_until": return "hourglass"
    case "delegate": return "person.2"
    default: return "wrench.and.screwdriver"
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

// MARK: Pairing

struct PairingView: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var address: String = ""
    @State private var code: String = ""
    @State private var working: Bool = false
    @State private var problem: PairingProblem?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    DotFace(size: 56).frame(maxWidth: .infinity)
                    Text(String(localized: "On your Mac, open Dot › Settings › Phone and make a sign-in code.", comment: "Pairing screen: what to do on the Mac first"))
                        .textRole(.body).foregroundStyle(Tone.secondary)
                    GroupedSection(header: String(localized: "Your Mac", comment: "Pairing screen section: the Mac's address"), footer: nil) {
                        TextField(String(localized: "mac.your-tailnet.ts.net", comment: "Pairing screen: placeholder of the Mac's address field"), text: $address)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                            .textRole(.body).padding(.vertical, Space.s)
                            .accessibilityLabel(String(localized: "The Mac's address", comment: "VoiceOver: the pairing screen's address field"))
                    }
                    GroupedSection(header: String(localized: "Sign-in code", comment: "Pairing screen section: the 8-character code"), footer: nil) {
                        TextField(String(localized: "8 letters and digits", comment: "Pairing screen: placeholder of the code field"), text: $code)
                            .textInputAutocapitalization(.characters).autocorrectionDisabled()
                            .textRole(.body).padding(.vertical, Space.s)
                            .accessibilityLabel(String(localized: "Sign-in code", comment: "Pairing screen section: the 8-character code"))
                    }
                    if let problem { Notice(tone: .failure, message: words(problem), dismiss: nil) }
                    Button(String(localized: "Pair", comment: "Pairing screen: the button that pairs the phone"), systemImage: "link") {
                        Task {
                            working = true
                            problem = await model.pair(address: address, code: code)
                            working = false
                        }
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(working || address.isEmpty || code.isEmpty)
                    .frame(maxWidth: .infinity)
                }
                .padding(Space.gutter)
                .readableColumn()
            }
            .background(Tone.ground)
            .navigationTitle(String(localized: "Pair with your Mac", comment: "Title of the pairing screen"))
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func words(_ problem: PairingProblem) -> String {
        switch problem {
        case .address(.notTailnet), .address(.notHTTPS), .address(.hasPort), .address(.hasPath), .address(.hasCredentials), .address(.malformed),
            .address(.empty), .address(.notBuildHost):
            String(localized: "Type your Mac's tailnet address, like mac.your-tailnet.ts.net.", comment: "Pairing error: the address is not a tailnet address")
        case .pairing(.malformedCode): String(localized: "The code is 8 letters and digits.", comment: "Pairing error: the code has the wrong length")
        case .pairing(.codeRefused):
            String(localized: "That code is wrong, used or out of date. Make a new one on your Mac.", comment: "Pairing error: the Mac refused the code")
        case .pairing(.daemonTooOld):
            String(localized: "Update Dot on your Mac: it is too old for this app.", comment: "Pairing error: the Mac's Dot lacks the sync calls")
        case .pairing(.unreachable):
            String(localized: "Your Mac could not be reached. Check that it is on and on your tailnet.", comment: "Pairing error: no answer from the Mac")
        case .pairing(.keychain):
            String(localized: "The key could not be kept on this phone.", comment: "Pairing error: the Keychain refused to keep the key")
        }
    }
}

// MARK: The chat list

struct ChatsTab: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var selection: String?
    @State private var query: String = ""
    @State private var renaming: ThreadRow?
    @State private var newTitle: String = ""
    @State private var deleting: ThreadRow?
    @State private var composing: Bool = false
    let opensFirstChat: Bool

    var body: some View {
        AdaptiveSplit(selection: $selection) {
            list
        } detail: { thread in
            ChatScreen(thread: thread)
        } placeholder: {
            EmptyState(
                title: String(localized: "No chat open", comment: "iPad detail column when no chat is selected"), systemImage: "bubble.left.and.bubble.right",
                detail: String(localized: "Pick a chat on the left, or start a new one.", comment: "iPad detail column when no chat is selected"), actionTitle: nil, action: {})
                .background(Tone.ground)
        }
        .onChange(of: model.snapshot.list.rows.count) { _, count in
            if opensFirstChat, selection == nil, count > 0 { selection = model.snapshot.list.ordered.first?.summary.id }
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
        .sheet(isPresented: $composing) { NewChatSheet(close: { composing = false }) }
    }

    private var rows: [ThreadRow] {
        let words: String = query.trimmingCharacters(in: .whitespaces)
        let all: [ThreadRow] = model.snapshot.list.ordered
        guard !words.isEmpty else { return all }
        return all.filter { $0.summary.title.localizedStandardContains(words) || $0.summary.lastMessage.localizedStandardContains(words) }
    }

    private var list: some View {
        List(selection: $selection) {
            if let line = listLine {
                Text(line).textRole(.footnote).foregroundStyle(Tone.secondary).listRowBackground(Tone.ground)
            }
            ForEach(model.snapshot.pendingChats) { pending in
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
            } else if rows.isEmpty, model.snapshot.list.freshness == .live {
                EmptyState(
                    title: String(localized: "No chats yet", comment: "Chat list empty state title"), systemImage: "bubble.left.and.bubble.right",
                    detail: String(localized: "Start one with the pencil above.", comment: "Chat list empty state detail"), actionTitle: nil, action: {})
            } else if rows.isEmpty, model.snapshot.list.freshness == .empty, model.snapshot.transport == .connecting {
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
            freshness: model.snapshot.list.freshness, transport: model.snapshot.transport, savedAt: model.snapshot.savedAt["list"], workingAt: nil, now: .now)
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
                            Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash") { deleting = row }
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
                            Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive) { deleting = row }
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

/// A new chat: the composer on an empty sheet (plan §4.2); it shows in the list at once from the outbox.
struct NewChatSheet: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var text: String = ""
    let close: () -> Void

    var body: some View {
        VStack {
            Spacer()
            Text(String(localized: "What should Dot work on?", comment: "New chat sheet: the prompt above the composer"))
                .textRole(.title).foregroundStyle(Tone.text).multilineTextAlignment(.center)
            Spacer()
            ComposerShell(text: $text, placeholder: String(localized: "Message Dot", comment: "Placeholder of the composer"), modelTitle: String(localized: "Default model", comment: "Composer model menu: use the daemon's default model")) {
                let words: String = text.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !words.isEmpty else { return }
                Task {
                    await model.startChat(words)
                    close()
                }
            }
            .padding(Space.s)
        }
        .padding(Space.gutter)
        .inspectorSheet(title: String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), close: close)
    }
}

// MARK: One chat

struct ChatScreen: View {
    let thread: String
    @Environment(AppModel.self) private var model: AppModel
    @AppStorage("toolCallMode") private var toolModeRaw: String = DotEngine.ToolCallMode.showAll.rawValue
    @AppStorage("responseStreaming") private var streaming: Bool = true
    @State private var draft: String = ""
    @State private var panel: ChatPanel?
    @State private var expanded: Set<String> = []
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    private var chat: ChatProjection? { model.snapshot.chats[thread] }
    private var view: ThreadView? { chat?.view }
    private var activeRun: RunRecord? { view?.runs.last { $0.parentRunId == nil && $0.isActive } }
    private var helpers: [HelperView] { view?.helpers ?? [] }
    private var mode: DotEngine.ToolCallMode { DotEngine.ToolCallMode(rawValue: toolModeRaw) ?? .showAll }

    private var items: [TranscriptItem] {
        guard let chat else { return [] }
        return transcript(of: chat, pending: model.snapshot.pending(in: thread), mode: mode, streaming: streaming)
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.m) {
                if chat?.freshness == .deleted {
                    Notice(tone: .info, message: String(localized: "This chat was deleted", comment: "Chat subtitle: the chat was deleted on the Mac or the web"), dismiss: nil)
                }
                if let unreadable = chat?.unreadable, unreadable > 0 {
                    Notice(tone: .info, message: String(localized: "\(unreadable) steps could not be shown", comment: "Notice in a chat: frames the app could not read; the argument is how many"), dismiss: nil)
                }
                ForEach(items) { item in row(item) }
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
        .defaultScrollAnchor(.bottom)
        .scrollDismissesKeyboard(.interactively)
        .background(Tone.ground)
        .navigationTitle(view?.thread.title ?? model.snapshot.list.rows.first { $0.summary.id == thread }?.summary.title ?? "")
        .navigationSubtitle(subtitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(sizeClass == .regular ? .automatic : .hidden, for: .tabBar)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                if activeRun != nil {
                    Button(String(localized: "Stop", comment: "Chat toolbar: stops the run at work"), systemImage: "stop.fill") {
                        if let run = activeRun { model.perform(.stop(run: run.id, thread: thread)) }
                    }
                }
                Button(String(localized: "Agents", comment: "Chat header quick panel: the chat's helpers"), systemImage: "person.2") { panel = .agents }
            }
        }
        .sheet(item: $panel) { panel in
            switch panel {
            case .agents:
                LiveAgentsSheet(thread: thread, close: { self.panel = nil })
            case .background:
                BackgroundWorkSheet(
                    items: (view?.background ?? []).map { work in
                        BackgroundWork(id: work.id, title: work.title, kind: work.kind == "command" ? .command : work.kind == "loop" ? .loop : .subagent)
                    }, close: { self.panel = nil })
            case .files, .screen:
                EmptyState(
                    title: String(localized: "No changes yet", comment: "Changes panel empty state"), systemImage: "folder",
                    detail: String(localized: "Files the run changes show here.", comment: "Changes panel empty state detail"), actionTitle: nil, action: {}
                )
                .inspectorSheet(title: String(localized: "Changes and files", comment: "Chat header quick panel: what the run changed"), close: { self.panel = nil })
            }
        }
        .safeAreaInset(edge: .bottom) {
            VStack(alignment: .trailing, spacing: Space.xs) {
                FloatingWorkingControl(
                    summary: WorkingSummary(
                        status: activeRun == nil ? nil : .working, elapsed: elapsed, queued: view?.queue.messages.count ?? 0,
                        helpers: helpers.filter { helperState($0.status).isLive }.count),
                    showsScrollToBottom: false, openStatus: { panel = .background }, openQueue: {}, openHelpers: { panel = .agents }, scrollToBottom: {})
                    .frame(maxWidth: .infinity, alignment: .center)
                ComposerShell(
                    text: $draft, placeholder: String(localized: "Message Dot", comment: "Placeholder of the composer"),
                    modelTitle: String(localized: "Default model", comment: "Composer model menu: use the daemon's default model"),
                    send: { send(steer: false) }, steer: activeRun == nil ? nil : { send(steer: true) })
                // A deleted chat takes nothing more.
                .disabled(chat?.freshness == .deleted)
            }
            .frame(maxWidth: proseWidth)
            .padding(.horizontal, Space.s)
            .padding(.bottom, Space.xs)
        }
        .onAppear { model.open(thread) }
        .onDisappear { model.close(thread) }
        .onChange(of: items.count) { _, _ in LatencyProbe.saw(items: items, model: model) }
        .task { await LatencyProbe.run(thread: thread, model: model) }
    }

    private func send(steer: Bool) {
        let words: String = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { return }
        draft = ""
        Task { await model.send(words, thread: thread, steerInto: steer ? activeRun?.id : nil) }
    }

    private var subtitle: String {
        if let chat, let line = freshnessLine(
            freshness: chat.freshness, transport: model.snapshot.transport, savedAt: model.snapshot.savedAt[thread], workingAt: model.snapshot.workingAt[thread], now: .now)
        {
            return line
        }
        return [String(localized: "Dot", comment: "The assistant's name in a chat's subtitle"), model.host?.name].compactMap { $0 }.joined(separator: " · ")
    }

    private var elapsed: String? {
        guard let run = activeRun else { return nil }
        return elapsedWords(from: Date(timeIntervalSince1970: run.createdAt), to: .now)
    }

    @ViewBuilder private func row(_ item: TranscriptItem) -> some View {
        switch item {
        case .owner(let id, let text, let delivery):
            VStack(alignment: .trailing, spacing: Space.xxs) {
                UserBubble(text: text)
                if let delivery {
                    HStack(spacing: Space.xs) {
                        Text(deliveryWords(delivery)).textRole(.label).foregroundStyle(isRejected(delivery) ? Tone.failure : Tone.tertiary)
                        if case .rejected(let failure) = delivery {
                            let key: String = String(id.dropFirst(2))
                            if case .runEnded = failure {
                                Button(String(localized: "Send as a new message", comment: "Under a steer refused because its run ended")) { model.sendAsMessage(key) }
                                    .textRole(.label)
                            } else {
                                Button(String(localized: "Edit", comment: "Under a refused message: puts its words back in the composer")) {
                                    Task { if let words = await model.takeBack(key) { draft = words } }
                                }
                                .textRole(.label)
                                Button(String(localized: "Try again", comment: "Under a refused message: sends it again")) { model.retry(key) }.textRole(.label)
                            }
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        case .note(_, let text):
            Text(text).textRole(.footnote).foregroundStyle(Tone.secondary).frame(maxWidth: .infinity, alignment: .leading)
        case .answer(_, let text, let writing):
            AnswerText(text: (try? answerText(markdown: text)) ?? AttributedString(text)).opacity(writing ? 0.85 : 1)
        case .tool(let id, let tool, let target, let state):
            ToolCallRow(
                call: ToolCall(id: id, symbol: toolSymbol(tool), label: target.isEmpty ? tool : target, state: toolState(state), detail: nil),
                isExpanded: Binding(get: { expanded.contains(id) }, set: { if $0 { expanded.insert(id) } else { expanded.remove(id) } }))
        case .ended(_, let status, let detail):
            Notice(tone: status == "failed" ? .failure : .info, message: endedWords(status, detail), dismiss: nil)
        }
    }

    private func isRejected(_ delivery: Delivery) -> Bool {
        if case .rejected = delivery { return true }
        return false
    }

    private func toolState(_ state: DotEngine.ToolState) -> ToolState {
        switch state {
        case .running: .running
        case .done: .done
        case .failed: .failed
        }
    }

    private func endedWords(_ status: String, _ detail: String) -> String {
        switch status {
        case "failed": String(localized: "The run failed", comment: "Notice at the end of a failed run")
        case "cancelled": String(localized: "The run was stopped", comment: "Notice at the end of a stopped run")
        default: String(localized: "The run ended", comment: "Notice at the end of a run that ended another way")
        }
    }

    private var proseWidth: CGFloat? {
        guard sizeClass == .regular else { return nil }
        return ProseColumn.width(charactersPerLine: ProseColumn.target, font: ProseColumn.bodyFont(dynamicTypeSize))
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
        let helpers: [HelperView] = model.snapshot.chats[thread]?.view?.helpers ?? []
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
            id: helper.runId, title: helper.title, state: state, startedAt: Date(timeIntervalSince1970: helper.startedAt),
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
                while !(model.snapshot.chats[thread]?.freshness == .live
                    && model.snapshot.chats[thread]?.view?.runs.contains(where: \.isActive) == false)
                {
                    try? await Task.sleep(for: .milliseconds(100))
                    if Task.isCancelled { return }
                }
                let text: String = "probe \(number) \(UUID().uuidString.prefix(6))"
                model.probeSent[text] = Date()
                await model.send(text, thread: thread, steerInto: nil)
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
