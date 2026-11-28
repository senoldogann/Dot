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
    /// The bot's header, handed on to the pages pushed from here (a destination does not inherit it).
    @Environment(\.botChat) private var botChat: BotChat?
    @State private var schedules: Loadable<[ScheduleView]> = .loading
    @State private var deletingChat: ThreadRow?
    @State private var library: String?
    @State private var composing: Bool = false
    @State private var editing: Bool = false
    @State private var showsInstructions: Bool = false
    @State private var orders: Loadable<[WorkOrderView]> = .loading
    @State private var objectives: Loadable<[ObjectiveRecord]> = .loading
    @State private var handoffs: Loadable<BotHandoffs> = .loading
    @State private var pausing: Bool = false
    /// The manager review's settings (item 2068), for its row's detail; `nil` until read or when the read failed.
    @State private var review: TeamReviewView?
    @State private var problem: SettingsFailure?
    @AppStorage(PrefKey.confirmDelete) private var confirmDelete: Bool = true

    private var bot: Bot? { model.bots.first { $0.id == botId } }

    var body: some View {
        SettingsScroll(title: "") {
            if let bot {
                if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Agents", comment: "Where on the Mac: the Agents page"), retry: nil) }
                header(bot)
                if bot.record.pausedAt != nil {
                    Notice(tone: .warn, message: String(localized: "Paused: its routines start nothing by themselves.", comment: "Bot profile: a paused bot"), dismiss: nil)
                        .accessibilityIdentifier("profile.paused")
                }
                if bot.overloaded {
                    Notice(
                        tone: .info,
                        message: String(localized: "Overloaded: \(overloadWords(bot.load))", comment: "Bot profile: a bot that carries too much; the argument is why"),
                        dismiss: nil
                    )
                    .accessibilityIdentifier("profile.overloaded")
                }
                GroupedSection(header: nil, footer: nil) {
                    if let latest = bot.latest {
                        Button { library = latest.summary.id } label: {
                            ListRow(systemImage: "folder", title: String(localized: "Library", comment: "Bot chat menu: the files and pages this bot made"), description: nil, accessory: .chevron)
                        }
                        .buttonStyle(RowPressStyle())
                        .accessibilityIdentifier("profile.library")
                    }
                    BotComputerRow(bot: bot)
                }
                BotWorkSection(botId: bot.id, orders: orders, objectives: objectives, handoffs: handoffs)
                GroupedSection(header: String(localized: "Configure the bot", comment: "Bot profile: the section of what the bot does and runs on"), footer: nil) {
                    NavigationRow(
                        systemImage: "clock", title: String(localized: "Routines", comment: "Bot profile: the bot's scheduled tasks"), description: nil,
                        detail: routinesWords(bot)
                    ) { SchedulesPage(editsFirst: false, scope: bot.isDot ? .dot : .agent(bot.id)) }
                    .accessibilityIdentifier("profile.routines")
                    NavigationRow(
                        systemImage: "person.3", title: String(localized: "Manager review", comment: "Bot profile: the manager's review of the team"), description: nil,
                        detail: review.map { reviewRowWords($0, botId: bot.id, names: Dictionary(model.bots.map { ($0.id, $0.record.name) }, uniquingKeysWith: { first, _ in first })) }
                    ) { TeamReviewPage(botId: bot.id) }
                    .accessibilityIdentifier("profile.review")
                    modelRow(bot)
                    NavigationRow(
                        systemImage: "clock.arrow.circlepath", title: String(localized: "Activity", comment: "Tab bar: what Dot did while the owner was away"),
                        description: bot.isDot ? nil : String(localized: "What \(bot.record.name) did", comment: "Bot profile: the Activity row of an agent; the argument is its name"),
                        detail: nil
                    ) { ActivityScreen(agent: bot.isDot ? nil : bot.id) }
                    .accessibilityIdentifier("profile.activity")
                }
                GroupedSection(
                    header: nil,
                    footer: String(localized: "Count this bot's unread chats and what waits for you on the home. Push alerts follow Settings › Notifications.", comment: "Bot profile: what the Notifications switch does")
                ) {
                    ToggleRow(
                        systemImage: nil, title: String(localized: "Notifications", comment: "Settings row"), description: nil,
                        isOn: Binding(get: { !model.mutedBots.contains(bot.id) }, set: { _ in model.toggleMute(bot.id) }))
                    .accessibilityIdentifier("profile.notifications")
                }
                chats(bot)
                if bot.isDot {
                    GroupedSection(header: String(localized: "Your Mac", comment: "Dot's profile: the section of the Mac"), footer: nil) {
                        NavigationRow(
                            systemImage: "desktopcomputer", title: String(localized: "This Mac", comment: "Settings row: the paired Mac"), description: nil,
                            detail: transportWord(model.transport)
                        ) { MacPage() }
                    }
                } else if !bot.record.instructions.isEmpty {
                    instructions(bot)
                }
            }
        }
        .navigationDestination(for: SettingsPage.self) { page in SettingsDestination(page: page, start: SettingsStart(path: [], query: "", editsFirstSchedule: false, triesRaise: false, connectApps: .closed)) }
        .toolbar {
            if let bot, !bot.isDot {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        if bot.record.pausedAt == nil {
                            Button(String(localized: "Pause bot", comment: "Bot profile menu: its routines stop starting by themselves"), systemImage: "pause.circle") { setPaused(bot, true) }
                                .accessibilityIdentifier("profile.pause")
                        } else {
                            Button(String(localized: "Resume bot", comment: "Bot profile menu: its routines start again"), systemImage: "play.circle") { setPaused(bot, false) }
                                .accessibilityIdentifier("profile.resume")
                        }
                    } label: {
                        Image(systemName: "ellipsis")
                    }
                    .disabled(pausing)
                    .accessibilityLabel(String(localized: "More", comment: "VoiceOver: the ⋯ menu of a bot's chat"))
                    .accessibilityIdentifier("profile.menu")
                }
            }
        }
        .task { await loadSchedules() }
        .task { await loadWork() }
        // Read again on each return from the review's page, whose choice its detail shows.
        .onAppear { Task { await loadReview() } }
        .refreshable {
            problem = nil
            await model.loadAgents()
            await loadSchedules()
            await loadWork()
            await loadReview()
        }
        .sheet(item: Binding(get: { library.map(LibraryChat.init) }, set: { library = $0?.id })) { chat in
            RunInspector(thread: chat.id, close: { library = nil })
        }
        .navigationDestination(isPresented: $composing) { PushedNewChat(botId: botId, header: botChat) }
        .navigationDestination(isPresented: $editing) { BotEditPage(botId: botId) }
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
            BotFace(id: bot.id, look: bot.record.look, size: 96)
            Text(bot.record.name).textRole(.title).foregroundStyle(Tone.text).multilineTextAlignment(.center)
            if !bot.record.role.isEmpty {
                Text(shownRole(bot.record)).textRole(.footnote).foregroundStyle(Tone.secondary).multilineTextAlignment(.center)
            }
            if !bot.isDot {
                Button(String(localized: "Edit", comment: "Bot profile: opens the bot's edit page")) { editing = true }
                    .buttonStyle(SecondaryButtonStyle())
                    .padding(.top, Space.xs)
                    .accessibilityIdentifier("profile.edit")
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.s)
    }

    /// "2 on": how many of the bot's routines are switched on.
    private func routinesWords(_ bot: Bot) -> String? {
        guard let all = schedules.value else { return nil }
        let own: [ScheduleView] = all.filter { ($0.agent?.id ?? dotAgentId) == bot.id }
        let on: Int = own.filter { $0.record.enabled }.count
        return String(localized: "\(on) on", comment: "Bot profile: how many of its routines are switched on")
    }

    @ViewBuilder private func chats(_ bot: Bot) -> some View {
        GroupedSection(header: String(localized: "Chats", comment: "Title of the chat list"), footer: nil) {
            Button { composing = true } label: {
                ListRow(systemImage: "plus.bubble", title: String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), description: nil, accessory: .none)
            }
            .buttonStyle(RowPressStyle())
            .accessibilityIdentifier("profile.newChat")
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
                .accessibilityIdentifier("profile.chat")
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
                ) { BotChatsPage(scope: .botChats(bot: bot.id, agent: bot.isDot ? nil : bot.id), open: open).environment(\.botChat, botChat) }
                .accessibilityIdentifier("profile.allChats")
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

    /// The three reads of the Work group, together and each its own (a route that fails leaves the others' counts).
    private func loadWork() async {
        async let read: Loadable<[WorkOrderView]> = readOrders(model)
        async let objectivesRead: Loadable<[ObjectiveRecord]> = readObjectives(model)
        async let team: Loadable<TeamView> = readTeam(model)
        let all: Loadable<[WorkOrderView]> = await read
        orders = all.map { botOrders(botId: botId, orders: $0) }
        objectives = await objectivesRead.and(all).map { botObjectives(botId: botId, orders: $0.1, objectives: $0.0) }
        handoffs = await team.map { botHandoffs(botId: botId, team: $0) }
    }

    /// Pauses the bot or lets it go on (`POST /api/agents/{id}/pause|resume`), then reads the agents again; a refusal is said at the top.
    /// The review's settings, for its row; a read that fails leaves the row without its detail, and its page says why.
    private func loadReview() async {
        if case .loaded(let view) = await model.read({ api throws(DaemonRequestError) in try await api.teamReview() }) { review = view }
    }

    private func setPaused(_ bot: Bot, _ paused: Bool) {
        guard !pausing else { return }
        Task {
            pausing = true
            defer { pausing = false }
            let result: Result<AgentRecord, SettingsFailureBox> = await model.write { api throws(DaemonRequestError) -> AgentRecord in
                if paused { return try await api.pauseAgent(bot.id) }
                return try await api.resumeAgent(bot.id)
            }
            switch result {
            case .success:
                problem = nil
                await model.loadAgents()
            case .failure(let box): problem = box.failure
            }
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

/// An agent's model: the configured main model or one of the daemon's, on the app's one model catalog
/// (`ModelCatalog`, as the chat's settings and Settings › Models draw it; the owner's report of 2026-11-28): "Main
/// model" on top, then the recent models and the providers as groups that fold, the chosen model's open, each model
/// with its mark and ✓, and the search with its filter at the foot. Read as Settings › Models reads them (the failure
/// and its Retry when the Mac cannot be asked). `choose` takes `""` for the main model; which model a run uses is not
/// this page's (`choose` writes the agent's own choice only).
struct AgentModelPage: View {
    let current: String
    let choose: (String) async -> SettingsFailure?
    @Environment(AppModel.self) private var model: AppModel
    @State private var options: Loadable<[ProviderModels]> = .loading
    @State private var chosen: String?
    @State private var problem: SettingsFailure?
    @State private var saving: Bool = false
    @State private var marks: ModelMarks = ModelMarks.read(.standard)
    @State private var query: String = ""
    @State private var filter: ModelFilter = .all

    private var picked: String { chosen ?? current }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.xl) {
                if let problem { FailureBanner(failure: problem, macPlace: agentsOnTheMac, retry: nil) }
                LoadedPage(state: options, macPlace: agentsOnTheMac, retry: { Task { await load() } }) { providers in
                    ModelCatalog(
                        providers: providers, defaultRef: model.chatDefaults?.model, chosenRef: picked.isEmpty ? nil : picked, query: query,
                        filter: filter, managesHidden: false, marks: $marks, pick: { option in pick(option.ref) }, problem: nil, retry: {}
                    ) {
                        if query.isEmpty { mainRow }
                    }
                }
            }
            .padding(.horizontal, Space.gutter)
            .padding(.top, Space.xs)
            .padding(.bottom, 96)
            .readableColumn()
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Tone.ground)
        .safeAreaInset(edge: .bottom) { ModelSearchBar(query: $query, filter: $filter) }
        .navigationTitle(String(localized: "Model", comment: "Bot profile: the bot's model"))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    /// The configured main model, ✓ while the agent follows it.
    private var mainRow: some View {
        InsetSection(header: nil) {
            Button { pick("") } label: {
                HStack(spacing: Space.s) {
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        Text(String(localized: "Main model", comment: "Bot profile: an agent on the configured main model")).textRole(.body).foregroundStyle(Tone.text)
                        // A chat's own model wins: this is only what a chat with none takes.
                        Text(String(localized: "For chats that have no model of their own", comment: "Bot profile's Model page: what the main model row is for"))
                            .textRole(.footnote).foregroundStyle(Tone.secondary)
                    }
                    Spacer(minLength: Space.s)
                    Image(systemName: "checkmark").font(.system(.body, weight: .semibold)).foregroundStyle(Tone.text)
                        .opacity(picked.isEmpty ? 1 : 0).accessibilityHidden(true)
                }
                .padding(.horizontal, Space.groupedGutter)
                .padding(.vertical, Space.xs)
                .frame(minHeight: Metric.rowWithDescription)
                .contentShape(Rectangle())
            }
            .buttonStyle(RowPressStyle())
            .accessibilityAddTraits(picked.isEmpty ? [.isSelected] : [])
            .accessibilityIdentifier("agentModel.main")
        }
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

// MARK: Computer

/// Whether a bot has a run at work (queued, running or ending): a chat that only waits on the owner is not at work.
func botIsWorking(_ bot: Bot) -> Bool {
    bot.chats.contains { ["queued", "running", "ending"].contains($0.summary.latestStatus) }
}

/// The profile's Computer row (item 1963): "Working" while a run of the bot works, else "Idle". A tap opens what it does
/// on the Mac: the live picture of the run's browser or desktop with Take over, when one of the bot's chats is following a run
/// that uses them, else the Mac's own screen.
struct BotComputerRow: View {
    let bot: Bot
    @Environment(AppModel.self) private var model: AppModel
    @State private var live: LiveCover?
    @State private var watching: Bool = false

    var body: some View {
        Button(action: open) {
            ListRow(
                systemImage: "desktopcomputer", title: String(localized: "Computer", comment: "Bot profile: what the bot does on the Mac"), description: nil,
                accessory: .detailAndChevron(
                    botIsWorking(bot)
                        ? String(localized: "Working", comment: "Bot profile: the bot has a run at work")
                        : String(localized: "Idle", comment: "Bot profile: the bot has no run at work")))
        }
        .buttonStyle(RowPressStyle())
        .accessibilityIdentifier("profile.computer")
        .fullScreenCover(isPresented: $watching) { WatchMacScreen(close: { watching = false }) }
        .fullScreenCover(item: $live) { cover in
            LiveCoverScreen(follow: cover.follow, facts: cover.facts, close: { live = nil })
        }
    }

    private func open() {
        for row in bot.chats {
            if let facts = liveRunFacts(model.chats[row.summary.id]) {
                live = LiveCover(follow: LiveFollow(run: facts.run, surface: facts.surface), facts: facts)
                return
            }
        }
        watching = true
    }
}

/// A run's live picture opened from the profile.
struct LiveCover: Identifiable {
    let follow: LiveFollow
    let facts: LiveRunFacts
    var id: String { facts.run }
}

/// The run's live screen with Take over, reading the picture while it shows.
struct LiveCoverScreen: View {
    let follow: LiveFollow
    let facts: LiveRunFacts
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel

    var body: some View {
        RunLiveScreen(follow: follow, facts: facts, close: close)
            .onAppear { if let api = model.settingsAPI { follow.start(api: api) } }
            .onDisappear { follow.stop() }
    }
}

// MARK: Edit

/// A bot's edit page (item 1963, as Grok's "Edit bot"): its avatar and its name; Done keeps both on the Mac.
struct BotEditPage: View {
    let botId: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var name: String = ""
    @State private var look: AgentLook?
    @State private var seeded: Bool = false
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?

    private var bot: Bot? { model.bots.first { $0.id == botId } }

    private var trimmed: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var changed: Bool {
        guard let bot else { return false }
        return trimmed != bot.record.name || look != bot.record.look
    }

    var body: some View {
        SettingsScroll(title: String(localized: "Edit bot", comment: "Bot edit page title")) {
            if let bot {
                if let problem { FailureBanner(failure: problem, macPlace: agentsOnTheMac, retry: nil) }
                BotFace(id: bot.id, look: look, size: 96).frame(maxWidth: .infinity).padding(.vertical, Space.s)
                GroupedSection(header: String(localized: "Name", comment: "Rename alert: the field of the chat's new name"), footer: nil) {
                    TextField(String(localized: "Name", comment: "Rename alert: the field of the chat's new name"), text: $name)
                        .textRole(.body).foregroundStyle(Tone.text)
                        .padding(.horizontal, Space.groupedGutter)
                        .frame(minHeight: Metric.row)
                        .accessibilityIdentifier("botEdit.name")
                }
                GroupedSection(header: String(localized: "Appearance", comment: "Bot edit page: the section of how the bot looks"), footer: nil) {
                    NavigationRow(systemImage: nil, title: String(localized: "Avatar", comment: "Bot edit page: opens the bot's shape and colour"), description: nil, detail: nil) {
                        BotAvatarPage(id: bot.id, look: $look)
                    }
                    .accessibilityIdentifier("botEdit.avatar")
                }
            }
        }
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button(String(localized: "Done", comment: "Find in chat: closes the bar")) { Task { await save() } }
                    .disabled(!changed || trimmed.isEmpty || saving)
                    .accessibilityIdentifier("botEdit.done")
            }
        }
        .onAppear {
            guard !seeded, let bot else { return }
            seeded = true
            name = bot.record.name
            look = bot.record.look
        }
    }

    private func save() async {
        guard let bot, !trimmed.isEmpty else { return }
        saving = true
        let record: AgentRecord = bot.record
        let request: AgentRequest = AgentRequest(
            name: String(trimmed.prefix(AgentRequest.nameLimit)), role: record.role, instructions: record.instructions, model: record.model, effort: record.effort,
            approvals: record.approvals, tools: record.tools, look: look, refuses: record.refuses)
        switch await model.write({ api throws(DaemonRequestError) in try await api.updateAgent(record.id, request) }) {
        case .success:
            await model.loadAgents()
            dismiss()
        case .failure(let box):
            problem = box.failure
            saving = false
        }
    }
}

/// A bot's shape and colour (its face; the daemon keeps one more thing, its resting expression, which stays as it is).
struct BotAvatarPage: View {
    let id: String
    @Binding var look: AgentLook?

    private var current: (shape: BotShape, colour: BotColour) { botLook(id: id, look: look) }
    private var expression: String { look?.expression ?? "neutral" }
    private let columns: [GridItem] = [GridItem(.adaptive(minimum: 64), spacing: Space.s)]

    var body: some View {
        SettingsScroll(title: String(localized: "Avatar", comment: "Bot edit page: opens the bot's shape and colour")) {
            BotFace(id: id, look: look, size: 96).frame(maxWidth: .infinity).padding(.vertical, Space.s)
            GroupedSection(header: String(localized: "Shape", comment: "Bot avatar page: the section of body shapes"), footer: nil) {
                LazyVGrid(columns: columns, spacing: Space.s) {
                    ForEach(BotShape.allCases, id: \.self) { shape in
                        let chosen: Bool = current.shape == shape
                        Button { set(shape: shape, colour: current.colour) } label: {
                            BotFace(id: id, look: AgentLook(shape: shape.rawValue, expression: expression, colour: current.colour.rawValue), size: 40)
                                .frame(maxWidth: .infinity, minHeight: Metric.hit + Space.s)
                                .background(chosen ? Tone.nested : Color.clear, in: RoundedRectangle(cornerRadius: Radius.inner))
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(shape.title)
                        .accessibilityAddTraits(chosen ? [.isButton, .isSelected] : [.isButton])
                        .accessibilityIdentifier("avatar.shape." + shape.rawValue)
                    }
                }
                .padding(Space.groupedGutter)
            }
            GroupedSection(header: String(localized: "Colour", comment: "Bot avatar page: the section of body colours"), footer: nil) {
                LazyVGrid(columns: columns, spacing: Space.s) {
                    ForEach(BotColour.allCases, id: \.self) { colour in
                        let chosen: Bool = current.colour == colour
                        Button { set(shape: current.shape, colour: colour) } label: {
                            Circle().fill(colour.color).frame(width: 32, height: 32)
                                .overlay(Circle().stroke(Tone.text, lineWidth: chosen ? 2 : 0).padding(-4))
                                .frame(maxWidth: .infinity, minHeight: Metric.hit + Space.s)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(colour.title)
                        .accessibilityAddTraits(chosen ? [.isButton, .isSelected] : [.isButton])
                        .accessibilityIdentifier("avatar.colour." + colour.rawValue)
                    }
                }
                .padding(Space.groupedGutter)
            }
        }
    }

    private func set(shape: BotShape, colour: BotColour) {
        look = AgentLook(shape: shape.rawValue, expression: expression, colour: colour.rawValue)
    }
}

extension BotShape {
    var title: String {
        switch self {
        case .circle: String(localized: "Circle", comment: "Bot shape")
        case .pebble: String(localized: "Pebble", comment: "Bot shape")
        case .squircle: String(localized: "Squircle", comment: "Bot shape")
        case .capsule: String(localized: "Capsule", comment: "Bot shape")
        case .triangle: String(localized: "Triangle", comment: "Bot shape")
        case .hexagon: String(localized: "Hexagon", comment: "Bot shape")
        case .cloud: String(localized: "Cloud", comment: "Bot shape")
        case .droplet: String(localized: "Droplet", comment: "Bot shape")
        }
    }
}

extension BotColour {
    var title: String {
        switch self {
        case .ink: String(localized: "Ink", comment: "Bot colour")
        case .brown: String(localized: "Brown", comment: "Bot colour")
        case .red: String(localized: "Red", comment: "Bot colour")
        case .orange: String(localized: "Orange", comment: "Bot colour")
        case .amber: String(localized: "Amber", comment: "Bot colour")
        case .green: String(localized: "Green", comment: "Bot colour")
        case .teal: String(localized: "Teal", comment: "Bot colour")
        case .blue: String(localized: "Blue", comment: "Bot colour")
        case .violet: String(localized: "Violet", comment: "Bot colour")
        case .pink: String(localized: "Pink", comment: "Bot colour")
        case .grey: String(localized: "Grey", comment: "Bot colour")
        case .cream: String(localized: "Cream", comment: "Bot colour")
        }
    }
}
