import DotEngine
import SwiftUI

// A bot's profile (PLAN §27, item 1884): its face, name and role; its chats, routines, library, model, activity,
// instructions and notifications. Dot's profile also holds the Mac: This Mac and Watch the Mac.

struct BotProfile: View {
    let botId: String
    /// Opens a chat of the bot in its chat screen (and closes the profile).
    let open: (String) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var schedules: Loadable<[ScheduleView]> = .loading
    @State private var problem: SettingsFailure?
    @State private var deletingChat: ThreadRow?
    @State private var library: String?
    @State private var composing: Bool = false
    @State private var showsInstructions: Bool = false
    @AppStorage(PrefKey.confirmDelete) private var confirmDelete: Bool = true

    private var bot: Bot? { model.bots.first { $0.id == botId } }

    var body: some View {
        SettingsScroll(title: bot?.record.name ?? "") {
            if let bot {
                header(bot)
                chats(bot)
                routines(bot)
                GroupedSection(header: nil, footer: nil) {
                    if let latest = bot.latest {
                        Button { library = latest.summary.id } label: {
                            ListRow(
                                systemImage: "folder", title: String(localized: "Library", comment: "Bot chat menu: the files and pages this bot made"),
                                description: String(localized: "Files and pages of its latest chat", comment: "Bot profile: what the Library row opens"), accessory: .chevron)
                        }
                        .buttonStyle(RowPressStyle())
                    }
                    modelRow(bot)
                    NavigationRow(
                        systemImage: "clock.arrow.circlepath", title: String(localized: "Activity", comment: "Tab bar: what Dot did while the owner was away"),
                        description: bot.isDot ? nil : String(localized: "What \(bot.record.name) did", comment: "Bot profile: the Activity row of an agent; the argument is its name"),
                        detail: nil
                    ) { ActivityPage(agent: bot.isDot ? nil : bot.id) }
                    ToggleRow(
                        systemImage: "bell.slash", title: String(localized: "Mute", comment: "Home: a bot's unread chats are not counted"),
                        description: String(localized: "Its unread chats are not counted on the home.", comment: "Bot profile: what Mute does"),
                        isOn: Binding(get: { model.mutedBots.contains(bot.id) }, set: { _ in model.toggleMute(bot.id) }))
                }
                if bot.isDot {
                    GroupedSection(header: String(localized: "Your Mac", comment: "Dot's profile: the section of the Mac"), footer: nil) {
                        NavigationRow(
                            systemImage: "desktopcomputer", title: String(localized: "This Mac", comment: "Settings row: the paired Mac"), description: nil,
                            detail: transportWord(model.transport)
                        ) { MacPage() }
                        NavigationRow(
                            systemImage: "play.display", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"), description: nil,
                            detail: nil
                        ) {
                            WebSettingsPage(target: WebSettingsPage.Target(tab: "/watch", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen")))
                        }
                    }
                } else if !bot.record.instructions.isEmpty {
                    instructions(bot)
                }
            }
        }
        .navigationDestination(for: SettingsPage.self) { page in SettingsDestination(page: page, start: SettingsStart(path: [], query: "", editsFirstSchedule: false, triesRaise: false, connectApps: .closed)) }
        .task { await loadSchedules() }
        .refreshable {
            await model.loadAgents()
            await loadSchedules()
        }
        .sheet(item: Binding(get: { library.map(LibraryChat.init) }, set: { library = $0?.id })) { chat in
            RunInspector(thread: chat.id, close: { library = nil })
        }
        .sheet(isPresented: $composing) {
            if let bot {
                NewChatSheet(
                    agent: bot.isDot ? nil : bot.record, close: { composing = false },
                    opened: { key in
                        composing = false
                        open(key)
                    })
            }
        }
        .confirmationDialog(
            String(localized: "Delete this chat with everything it holds?", comment: "Confirmation of deleting a chat"),
            isPresented: Binding(get: { deletingChat != nil }, set: { if !$0 { deletingChat = nil } }), titleVisibility: .visible
        ) {
            Button(String(localized: "Delete", comment: "Confirmation button that deletes a chat"), role: .destructive) {
                if let row = deletingChat { model.perform(.delete(thread: row.summary.id)) }
                deletingChat = nil
            }
        }
    }

    // MARK: Parts

    private func header(_ bot: Bot) -> some View {
        VStack(spacing: Space.s) {
            BotFace(id: bot.id, look: bot.record.look, size: 88)
            Text(bot.record.name).textRole(.title).foregroundStyle(Tone.text)
            if !bot.record.role.isEmpty {
                Text(bot.record.role).textRole(.footnote).foregroundStyle(Tone.secondary).multilineTextAlignment(.center)
            }
            Button(String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), systemImage: "plus.bubble") { composing = true }
                .buttonStyle(SecondaryButtonStyle())
                .padding(.top, Space.xs)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.s)
    }

    @ViewBuilder private func chats(_ bot: Bot) -> some View {
        GroupedSection(header: String(localized: "Chats", comment: "Title of the chat list"), footer: nil) {
            if bot.chats.isEmpty {
                ListRow(systemImage: nil, title: String(localized: "No chat yet", comment: "Home: a bot that has no chat"), description: nil, accessory: .none)
            }
            // The newest three here; every chat, with Pinned, search and Select (pin or delete several), on its own page.
            ForEach(bot.chats.prefix(3), id: \.summary.id) { row in
                Button { open(row.summary.id) } label: {
                    ListRow(
                        systemImage: row.summary.pinnedAt != nil ? "pin" : nil, title: row.summary.title,
                        description: row.summary.lastMessage.isEmpty ? nil : row.summary.lastMessage,
                        accessory: .detail(rowTime(row.summary.updatedAt, now: .now)))
                }
                .buttonStyle(RowPressStyle())
                .contextMenu {
                    Button(
                        row.summary.pinnedAt != nil
                            ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat")
                            : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"),
                        systemImage: row.summary.pinnedAt != nil ? "pin.slash" : "pin"
                    ) { model.perform(.pin(thread: row.summary.id, pinned: row.summary.pinnedAt == nil)) }
                    Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive) {
                        // Deletes at once when the owner turned the confirmation off (Settings › General › Confirmations).
                        if confirmDelete { deletingChat = row } else { model.perform(.delete(thread: row.summary.id)) }
                    }
                }
            }
            if !bot.chats.isEmpty {
                NavigationRow(
                    systemImage: "bubble.left.and.bubble.right", title: String(localized: "All chats", comment: "Bot profile: opens every chat of the bot"),
                    description: nil, detail: nil
                ) { BotChatsPage(scope: .chats(of: Set(bot.chats.map(\.summary.id)), agent: bot.isDot ? nil : bot.id), open: open) }
            }
        }
    }

    /// The bot's schedules with their switches (Dot's: those of no agent).
    @ViewBuilder private func routines(_ bot: Bot) -> some View {
        GroupedSection(header: String(localized: "Routines", comment: "Bot profile: the bot's scheduled tasks"), footer: nil) {
            // A switch the Mac refused snaps back; why stands here, beside it.
            if let problem {
                FailureBanner(failure: problem, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: nil)
                    .padding(Space.groupedGutter)
            }
            switch schedules {
            case .loading:
                ProgressView().frame(maxWidth: .infinity, minHeight: Metric.row)
            case .failed(let failure):
                FailureBanner(failure: failure, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: {
                    Task { await loadSchedules() }
                })
                .padding(Space.groupedGutter)
            case .loaded(let all):
                let own: [ScheduleView] = all.filter { ($0.agent?.id ?? dotAgentId) == bot.id }
                if own.isEmpty {
                    ListRow(
                        systemImage: nil, title: String(localized: "No routine", comment: "Bot profile: the bot has no scheduled task"),
                        description: String(localized: "Ask it in a chat to do something on a rhythm.", comment: "Bot profile: how a routine is made"), accessory: .none)
                }
                ForEach(own) { schedule in
                    ToggleRow(
                        systemImage: nil, title: schedule.record.name, description: scheduleLine(schedule, now: .now, locale: locale),
                        isOn: Binding(get: { schedule.record.enabled }, set: { on in Task { await setEnabled(schedule, on) } }))
                }
            }
        }
    }

    @ViewBuilder private func modelRow(_ bot: Bot) -> some View {
        if bot.isDot {
            NavigationRow(systemImage: "cpu", title: String(localized: "Model", comment: "Bot profile: the bot's model"), description: nil, detail: nil) { ModelsPage() }
        } else {
            NavigationRow(
                systemImage: "cpu", title: String(localized: "Model", comment: "Bot profile: the bot's model"), description: nil,
                detail: [bot.record.model.isEmpty ? String(localized: "Main model", comment: "Bot profile: an agent on the configured main model") : modelLabel(bot.record.model, in: model.models), bot.record.effort?.capitalized]
                    .compactMap { $0 }.joined(separator: " · ")
            ) {
                AgentModelPage(current: bot.record.model) { ref in await changeModel(bot.record, to: ref) }
            }
        }
    }

    private func instructions(_ bot: Bot) -> some View {
        GroupedSection(
            header: String(localized: "Instructions", comment: "Bot profile: what the agent was told to do"),
            footer: String(localized: "Change them on your Mac.", comment: "Bot profile: instructions are edited on the Mac")
        ) {
            Button { showsInstructions.toggle() } label: {
                Text(bot.record.instructions)
                    .textRole(.footnote).foregroundStyle(Tone.text).multilineTextAlignment(.leading)
                    .lineLimit(showsInstructions ? nil : 4)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(Space.groupedGutter)
            }
            .buttonStyle(.plain)
            .accessibilityHint(String(localized: "Shows all of it", comment: "VoiceOver hint: the instructions unfold"))
        }
    }

    // MARK: Writes

    private func loadSchedules() async {
        schedules = await model.read { api throws(DaemonRequestError) in try await api.schedules() }
    }

    private func setEnabled(_ schedule: ScheduleView, _ on: Bool) async {
        switch await model.write({ api throws(DaemonRequestError) in try await api.setSchedule(schedule.id, enabled: on) }) {
        case .success(let saved):
            problem = nil
            if var all = schedules.value, let index = all.firstIndex(where: { $0.id == saved.id }) {
                all[index] = saved
                schedules = .loaded(all)
            }
        case .failure(let box): problem = box.failure
        }
    }

    private func changeModel(_ record: AgentRecord, to ref: String) async -> SettingsFailure? {
        let request: AgentRequest = AgentRequest(
            name: record.name, role: record.role, instructions: record.instructions, model: ref, effort: record.effort, approvals: record.approvals,
            tools: record.tools, look: record.look, refuses: record.refuses)
        switch await model.write({ api throws(DaemonRequestError) in try await api.updateAgent(record.id, request) }) {
        case .success:
            await model.loadAgents()
            return nil
        case .failure(let box): return box.failure
        }
    }
}

/// An agent's model: the configured main model or one of the daemon's, read as Settings › Models reads them (the
/// failure and its Retry when the Mac cannot be asked). `choose` takes `""` for the main model.
struct AgentModelPage: View {
    let current: String
    let choose: (String) async -> SettingsFailure?
    @Environment(AppModel.self) private var model: AppModel
    @State private var options: Loadable<[ProviderModels]> = .loading
    @State private var chosen: String?
    @State private var problem: SettingsFailure?
    @State private var saving: Bool = false

    var body: some View {
        SettingsScroll(title: String(localized: "Model", comment: "Bot profile: the bot's model")) {
            if let problem { FailureBanner(failure: problem, macPlace: agentsOnTheMac, retry: nil) }
            LoadedPage(state: options, macPlace: agentsOnTheMac, retry: { Task { await load() } }) { providers in
                GroupedSection(header: nil, footer: nil) {
                    RadioRow(
                        title: String(localized: "Main model", comment: "Bot profile: an agent on the configured main model"),
                        description: nil, isSelected: (chosen ?? current).isEmpty
                    ) { pick("") }
                }
                ForEach(providers, id: \.provider) { provider in
                    GroupedSection(header: provider.provider, footer: provider.error) {
                        ForEach(provider.models, id: \.ref) { option in
                            RadioRow(title: option.label, description: option.ref, isSelected: (chosen ?? current) == option.ref) { pick(option.ref) }
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        options = await model.read { api throws(DaemonRequestError) in try await api.models() }
    }

    private func pick(_ ref: String) {
        guard !saving else { return }
        Task {
            saving = true
            let before: String? = chosen
            chosen = ref
            if let failure = await choose(ref) {
                chosen = before
                problem = failure
            } else {
                problem = nil
            }
            saving = false
        }
    }
}

/// Every chat of a bot as the chat list draws it (`ChatListView`, items 1800-1802: Pinned, search, Select with Pin and
/// Delete for several); a chat tapped opens in the bot's chat screen.
struct BotChatsPage: View {
    let scope: ChatListScope
    let open: (String) -> Void
    @State private var selection: String?

    var body: some View {
        ChatListView(scope: scope, selection: $selection)
            .onChange(of: selection) { _, thread in if let thread { open(thread) } }
    }
}

/// A chat whose library is open.
private struct LibraryChat: Identifiable {
    let id: String
}

/// What Dot did while the owner was away; helper W2 builds the view (`agent` scopes it to one agent, `nil` is all).
struct ActivityPage: View {
    let agent: String?

    var body: some View {
        SettingsScroll(title: String(localized: "Activity", comment: "Tab bar: what Dot did while the owner was away")) {
            EmptyState(
                title: String(localized: "Activity comes in the next version", comment: "Activity page: placeholder until the view is built"), systemImage: "clock.arrow.circlepath",
                detail: String(localized: "What Dot does on its own (schedules, helpers, drafts that wait) will show here.", comment: "Activity page: what it will show"),
                actionTitle: nil, action: {})
        }
    }
}
