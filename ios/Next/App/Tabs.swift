import SwiftUI
import UIKit

/// The iPad decision of wave N0 (review S9): the tab bar stays the top level on every width; inside a tab, compact width
/// is a stack (list → pushed detail) and regular width a split view (list sidebar, independent detail). The selection is
/// one value, so collapsing to compact keeps the open chat pushed.
struct AdaptiveSplit<Item: Hashable, Sidebar: View, Detail: View, Placeholder: View>: View {
    @Binding var selection: Item?
    @ViewBuilder let sidebar: () -> Sidebar
    @ViewBuilder let detail: (Item) -> Detail
    @ViewBuilder let placeholder: () -> Placeholder
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?

    var body: some View {
        if sizeClass == .regular {
            NavigationSplitView {
                sidebar()
            } detail: {
                NavigationStack {
                    if let selection { detail(selection) } else { placeholder() }
                }
            }
            .navigationSplitViewStyle(.balanced)
        } else {
            NavigationStack {
                sidebar()
                    .navigationDestination(item: $selection) { item in detail(item) }
            }
        }
    }
}

// MARK: Chats

struct ChatsTab: View {
    @State private var selection: SampleChat?
    @State private var query: String = ""
    @State private var toast: ToastMessage?
    @Binding var chats: [SampleChat]
    @State private var removed: (SampleChat, Int)?

    let initialPanel: ChatPanel?

    init(chats: Binding<[SampleChat]>, opensSampleChat: Bool, initialPanel: ChatPanel?) {
        _chats = chats
        _selection = State(initialValue: opensSampleChat ? Samples.chats[0] : nil)
        self.initialPanel = initialPanel
    }

    var body: some View {
        AdaptiveSplit(selection: $selection) {
            list
        } detail: { chat in
            SampleChatScreen(chat: chat, initialPanel: chat == Samples.chats[0] ? initialPanel : nil)
        } placeholder: {
            EmptyState(
                title: String(localized: "No chat open", comment: "iPad detail column when no chat is selected"), systemImage: "bubble.left.and.bubble.right",
                detail: String(localized: "Pick a chat on the left, or start a new one.", comment: "iPad detail column when no chat is selected"), actionTitle: nil, action: {})
                .background(Tone.ground)
        }
        .toastHost($toast, seconds: 5) { _ in
            if let (chat, index) = removed { chats.insert(chat, at: min(index, chats.count)) }
            removed = nil
        }
    }

    /// The chats the search keeps (titles and last lines; message bodies come with the store in N1-client).
    private var visible: [SampleChat] {
        let words: String = query.trimmingCharacters(in: .whitespaces)
        guard !words.isEmpty else { return chats }
        return chats.filter { $0.title.localizedStandardContains(words) || $0.preview.localizedStandardContains(words) }
    }

    private var list: some View {
        List(selection: $selection) {
            section(String(localized: "Needs you", comment: "Chat list section: chats that wait for the owner"), visible.filter { $0.status == .waitingOnYou || $0.status == .question })
            section(String(localized: "Working", comment: "Chat list section: chats with a run at work"), visible.filter { $0.status == .working })
            section(String(localized: "Recent", comment: "Chat list section: the other chats, newest first"), visible.filter { $0.status == nil || $0.status == .done || $0.status == .failed })
        }
        .listStyle(.plain)
        .focusEffectDisabled()
        .pageGround()
        .overlay {
            if visible.isEmpty && !query.isEmpty { ContentUnavailableView.search(text: query) }
        }
        .navigationTitle(String(localized: "Chats", comment: "Title of the chat list"))
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $query, prompt: String(localized: "Search chats", comment: "Placeholder of the chat list's search field"))
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Menu {
                    Button(String(localized: "All chats", comment: "Chat list filter menu"), systemImage: "tray", action: {})
                    Button(String(localized: "Needs you", comment: "Chat list filter menu: only chats that wait for the owner"), systemImage: "exclamationmark.circle", action: {})
                    Button(String(localized: "Settled", comment: "Chat list filter menu: chats the owner put away"), systemImage: "archivebox", action: {})
                } label: {
                    Label(String(localized: "Filter", comment: "Chat list toolbar: the filter menu"), systemImage: "line.3.horizontal.decrease")
                }
                Button(String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), systemImage: "square.and.pencil", action: {})
            }
        }
    }

    @ViewBuilder private func section(_ title: String, _ rows: [SampleChat]) -> some View {
        if !rows.isEmpty {
            Section {
                ForEach(rows) { chat in
                    ChatRow(chat: chat)
                        .tag(chat)
                        .listRowBackground(selection == chat ? Tone.nested : Tone.ground)
                        .focusEffectDisabled()
                        .listRowSeparatorTint(Tone.separator)
                        .listRowInsets(EdgeInsets(top: Space.s, leading: Space.gutter, bottom: Space.s, trailing: Space.gutter))
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            Button(String(localized: "Settle", comment: "Swipe and context action: puts a chat away (reversible)"), systemImage: "archivebox") { settle(chat) }
                                .tint(Tone.secondary)
                        }
                        .swipeActions(edge: .leading) {
                            Button(chat.isPinned ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat") : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"), systemImage: chat.isPinned ? "pin.slash" : "pin") {}
                                .tint(Tone.glyph)
                        }
                        .contextMenu {
                            Button(String(localized: "Rename", comment: "Context menu of a chat"), systemImage: "pencil", action: {})
                            Button(chat.isPinned ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat") : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"), systemImage: "pin", action: {})
                            Button(String(localized: "Settle", comment: "Swipe and context action: puts a chat away (reversible)"), systemImage: "archivebox") { settle(chat) }
                            Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive, action: {})
                        }
                }
            } header: {
                Text(title).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
            }
        }
    }

    private func settle(_ chat: SampleChat) {
        guard let index = chats.firstIndex(of: chat) else { return }
        chats.remove(at: index)
        removed = (chat, index)
        toast = ToastMessage(id: UUID(), text: String(localized: "Settled “\(chat.title)”", comment: "Toast after a chat was put away; the argument is its title"), systemImage: "checkmark.circle", actionTitle: String(localized: "Undo", comment: "Toast action that takes the last change back"))
    }
}

/// A chat in the list: the agent's face, the title (two lines at most, never cut mid-word), one status word by
/// priority, the last line and a tabular time. VoiceOver reads one label.
struct ChatRow: View {
    let chat: SampleChat
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            if !dynamicTypeSize.isAccessibilitySize {
                if chat.agent == "Dot" { DotFace(size: 32) } else { AgentAvatar(size: 32) }
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
                Text(chat.preview).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(2)
                if dynamicTypeSize.isAccessibilitySize {
                    Text(chat.time).textRole(.label).foregroundStyle(Tone.tertiary).tabularNumbers()
                }
            }
        }
        .frame(minHeight: Metric.rowWithDescription)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [chat.title, chat.status?.word, chat.agent == "Dot" ? nil : chat.agent, chat.preview, chat.time]
                .compactMap { $0 }.joined(separator: ", "))
    }
}

/// The chat screen's placeholder: a transcript built from every transcript component and the composer with the
/// floating capsule. Wave N2 replaces it with the real chat.
/// A quick panel of a chat (plan §18): the glass capsule of the header and the floating capsule open these sheets.
enum ChatPanel: String, Identifiable, Sendable {
    case agents
    case background
    case files
    case screen

    var id: String { rawValue }
}

struct SampleChatScreen: View {
    let chat: SampleChat
    @State private var draft: String = ""
    @State private var panel: ChatPanel?

    init(chat: SampleChat, initialPanel: ChatPanel?) {
        self.chat = chat
        _panel = State(initialValue: initialPanel)
    }

    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.m) {
                UserBubble(text: String(localized: "Book me the dentist for next week, afternoons if you can.", comment: "Sample owner message in the placeholder screen of wave N0"))
                SpeakerLine(agent: "Dot")
                ToolGroup(summary: String(localized: "Worked for 2m 14s · 4 steps", comment: "Work-log group summary (sample)"), calls: Samples.computerUse, startsOpen: true)
                HelperGroup(helpers: Samples.helpers, open: { _ in })
                AnswerText(text: Samples.booking())
                Notice(tone: .warn, message: String(localized: "Dot waits for you: pay the 20 € deposit?", comment: "Sample notice: Dot waits for a payment approval"), dismiss: nil)
            }
            .frame(maxWidth: proseWidth)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.m)
        }
        .defaultScrollAnchor(.bottom)
        .scrollDismissesKeyboard(.interactively)
        .background(Tone.ground)
        .navigationTitle(chat.title)
        .navigationSubtitle(subtitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(sizeClass == .regular ? .automatic : .hidden, for: .tabBar)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button(
                    String(localized: "Changes and files", comment: "Chat header quick panel: what the run changed"),
                    systemImage: "folder"
                ) { panel = .files }
                Button(String(localized: "Agents", comment: "Chat header quick panel: the chat's helpers"), systemImage: "person.2") {
                    panel = .agents
                }
                Button(
                    String(localized: "The Mac's screen", comment: "Chat header quick panel: the Mac's live screen"),
                    systemImage: "desktopcomputer"
                ) { panel = .screen }
            }
        }
        .sheet(item: $panel) { panel in
            switch panel {
            case .agents: AgentsSheet(runs: Samples.helperRuns(now: .now), close: { self.panel = nil })
            case .background: BackgroundWorkSheet(items: Samples.backgroundWork, close: { self.panel = nil })
            case .files:
                EmptyState(
                    title: String(localized: "No changes yet", comment: "Changes panel empty state"), systemImage: "folder",
                    detail: String(localized: "Files the run changes show here.", comment: "Changes panel empty state detail"),
                    actionTitle: nil, action: {}
                )
                .inspectorSheet(
                    title: String(localized: "Changes and files", comment: "Chat header quick panel: what the run changed"),
                    close: { self.panel = nil })
            case .screen:
                EmptyState(
                    title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"),
                    systemImage: "desktopcomputer",
                    detail: String(localized: "The live screen comes in a later version.", comment: "Mac screen panel placeholder"),
                    actionTitle: nil, action: {}
                )
                .inspectorSheet(
                    title: String(localized: "The Mac's screen", comment: "Chat header quick panel: the Mac's live screen"),
                    close: { self.panel = nil })
            }
        }
        .safeAreaInset(edge: .bottom) {
            VStack(alignment: .trailing, spacing: Space.xs) {
                FloatingWorkingControl(
                    summary: WorkingSummary(status: .waitingOnYou, elapsed: Self.elapsed, queued: 2, helpers: 2),
                    showsScrollToBottom: true, openStatus: { panel = .background }, openQueue: {}, openHelpers: { panel = .agents },
                    scrollToBottom: {})
                    .frame(maxWidth: .infinity, alignment: .center)
                ComposerShell(text: $draft, placeholder: String(localized: "Message Dot", comment: "Placeholder of the composer"), modelTitle: String(localized: "Default · medium", comment: "Composer model chip: the model and the reasoning effort"), send: {})
            }
            .frame(maxWidth: proseWidth)
            .padding(.horizontal, Space.s)
            .padding(.bottom, Space.xs)
        }
    }

    /// Who and where (plan §18): the agent and the Mac.
    private var subtitle: String { [chat.agent, Samples.macName].joined(separator: " · ") }

    /// A sample clock, formatted by the system so it reads "3 min" in English and "3 dk" in Turkish.
    private static let elapsed: String = Duration.seconds(180).formatted(.units(allowed: [.minutes], width: .abbreviated))

    /// The reading column: about 68 characters of the body font on a wide screen, the full width on a phone.
    private var proseWidth: CGFloat? {
        guard sizeClass == .regular else { return nil }
        return ProseColumn.width(charactersPerLine: ProseColumn.target, font: ProseColumn.bodyFont(dynamicTypeSize))
    }
}

// MARK: Agents

struct AgentsTab: View {
    @State private var selection: SampleAgent?

    var body: some View {
        AdaptiveSplit(selection: $selection) {
            List(selection: $selection) {
                ForEach(Samples.agents) { agent in
                    HStack(spacing: Space.s) {
                        if agent.id == "dot" { DotFace(size: 36) } else { AgentAvatar(size: 36) }
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text(agent.name).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text)
                            Text(agent.role).textRole(.footnote).foregroundStyle(Tone.secondary)
                        }
                        Spacer(minLength: Space.xs)
                        if let status = agent.status { StatusWord(status: status) }
                    }
                    .frame(minHeight: Metric.rowWithDescription)
                    .accessibilityElement(children: .combine)
                    .tag(agent)
                    .listRowBackground(selection == agent ? Tone.nested : Tone.ground)
                    .focusEffectDisabled()
                    .listRowSeparatorTint(Tone.separator)
                }
            }
            .listStyle(.plain)
            .pageGround()
            .navigationTitle(String(localized: "Agents", comment: "Title of the agents list"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button(String(localized: "New agent", comment: "Agents toolbar: creates an agent"), systemImage: "plus", action: {}) }
            }
        } detail: { agent in
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    GroupedSection(header: nil, footer: String(localized: "The agent's profile, tools and schedules come in a later version.", comment: "Footer of the agent placeholder screen")) {
                        ListRow(systemImage: "bubble.left", title: String(localized: "Home chat", comment: "Agent profile row: the agent's own chat"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "cpu", title: String(localized: "Model", comment: "Agent profile row: the model the agent runs on"), description: nil, accessory: .detailAndChevron(String(localized: "Default", comment: "The daemon's default model")))
                        ListRow(
                            systemImage: "calendar.badge.clock", title: String(localized: "Schedules", comment: "Agent profile row: the agent's scheduled tasks"), description: nil,
                            accessory: .detailAndChevron("2"))
                    }
                }
                .padding(Space.gutter)
            }
            .background(Tone.ground)
            .navigationTitle(agent.name)
            .navigationBarTitleDisplayMode(.inline)
        } placeholder: {
            EmptyState(
                title: String(localized: "No agent open", comment: "iPad detail column when no agent is selected"), systemImage: "person.2", detail: String(localized: "Pick an agent to see its profile.", comment: "iPad detail column when no agent is selected"),
                actionTitle: nil, action: {}
            ).background(Tone.ground)
        }
    }
}

// MARK: Activity

struct ActivityTab: View {
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    GroupedSection(header: nil, footer: String(localized: "Opens the Mac's live screen; nothing plays until you open it.", comment: "Activity tab: footer under Watch the Mac")) {
                        ListRow(systemImage: "desktopcomputer", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"), description: nil, accessory: .chevron)
                    }
                    EmptyState(
                        title: String(localized: "Nothing new since you were away", comment: "Activity tab empty state title"), systemImage: "clock.arrow.circlepath",
                        detail: String(localized: "What Dot does on its own (schedules, helpers, drafts that wait) shows here.", comment: "Activity tab empty state detail"),
                        actionTitle: nil, action: {})
                }
                .padding(Space.gutter)
                .readableColumn()
            }
            .background(Tone.ground)
            .navigationTitle(String(localized: "Activity", comment: "Title of the activity tab"))
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: Settings

/// How much of the work log a chat shows (D9): a pure view filter, default Show all.
enum ToolCallMode: CaseIterable, Hashable, Sendable {
    case showAll
    case hideSuccessful
    case hideAll
    case zen

    var title: String {
        switch self {
        case .showAll: String(localized: "Show all", comment: "Tool calls setting: every tool call shows")
        case .hideSuccessful: String(localized: "Hide successful", comment: "Tool calls setting: only failed tool calls show")
        case .hideAll: String(localized: "Hide all", comment: "Tool calls setting: no tool call shows")
        case .zen: String(localized: "Zen", comment: "Tool calls setting: only final answers show")
        }
    }
}

struct SettingsTab: View {
    @State private var toolMode: ToolCallMode = .showAll
    @State private var stopAsked: Bool = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    Button(String(localized: "Emergency stop", comment: "Settings: stops every run of Dot at once"), systemImage: "stop.circle", role: .destructive) { stopAsked = true }
                        .buttonStyle(DestructiveButtonStyle())
                        .frame(maxWidth: .infinity)
                        .confirmationDialog(String(localized: "Stop everything Dot is doing?", comment: "Confirmation of the emergency stop"), isPresented: $stopAsked) {
                            Button(String(localized: "Stop everything", comment: "Confirmation button of the emergency stop"), role: .destructive, action: {})
                        }
                    GroupedSection(header: String(localized: "Connections", comment: "Settings section: the Mac, notifications, this phone"), footer: nil) {
                        ListRow(systemImage: "desktopcomputer", title: String(localized: "This Mac", comment: "Settings row: the paired Mac"), description: nil, accessory: .detailAndChevron(String(localized: "Connected", comment: "Settings: the Mac answers")))
                        ListRow(systemImage: "bell", title: String(localized: "Notifications", comment: "Settings row"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "key", title: String(localized: "This phone's key", comment: "Settings row: the key this phone was paired with"), description: nil, accessory: .chevron)
                    }
                    GroupedSection(header: nil, footer: nil) {
                        ListRow(systemImage: "slider.horizontal.3", title: String(localized: "General", comment: "Settings row"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "textformat.size", title: String(localized: "Appearance", comment: "Settings row"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "bubble.left", title: String(localized: "Chat", comment: "Settings row: chat preferences"), description: nil, accessory: .chevron)
                    }
                    GroupedSection(
                        header: String(localized: "Tool calls in chat", comment: "Settings section: how much of the work log a chat shows"),
                        footer: String(localized: "Hide successful keeps failures. Zen shows only final answers. Approvals, questions and errors always show.", comment: "Footer of the tool calls setting")
                    ) {
                        ForEach(ToolCallMode.allCases, id: \.self) { mode in
                            RadioRow(title: mode.title, description: nil, isSelected: toolMode == mode) { toolMode = mode }
                        }
                    }
                    GroupedSection(header: String(localized: "Agents", comment: "Settings section: models, schedules, rules, memory"), footer: nil) {
                        ListRow(systemImage: "cpu", title: String(localized: "Models", comment: "Settings row"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "calendar.badge.clock", title: String(localized: "Scheduled tasks", comment: "Settings row"), description: nil, accessory: .detailAndChevron("7"))
                        ListRow(systemImage: "lock.shield", title: String(localized: "Rules", comment: "Settings row: what Dot may do without asking"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "brain", title: String(localized: "Memory", comment: "Settings row"), description: nil, accessory: .chevron)
                    }
                    GroupedSection(header: String(localized: "System", comment: "Settings section: health, version"), footer: nil) {
                        ListRow(systemImage: "heart.text.square", title: String(localized: "Health", comment: "Settings row: the daemon's health"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "info.circle", title: String(localized: "About", comment: "Settings row"), description: nil, accessory: .detailAndChevron("0.1.0"))
                    }
                    #if DEBUG
                        GroupedSection(header: String(localized: "Design", comment: "Settings section of debug builds"), footer: String(localized: "Debug builds only.", comment: "Footer of the debug-only design section")) {
                            NavigationRow(systemImage: "square.grid.2x2", title: String(localized: "Gallery", comment: "Settings row of debug builds: the component gallery"), description: nil, detail: nil) {
                                GalleryIndex()
                            }
                        }
                    #endif
                }
                .padding(.horizontal, Space.gutter)
                .padding(.vertical, Space.groupedGutter)
                .readableColumn()
            }
            .background(Tone.ground)
            .navigationTitle(String(localized: "Settings", comment: "Title of the settings tab"))
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}
