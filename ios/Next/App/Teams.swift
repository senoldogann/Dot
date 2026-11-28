import DotEngine
import SwiftUI

// The home's "+" (PLAN §27): a group chat with some of the owner's bots (a team, item 610, where Dot leads them and hands
// each its part), and a bot or a team made from the ready ones (items 315 and 504).

private var teamsOnTheMac: String { String(localized: "Agents › Your teams", comment: "Where on the Mac: where teams are changed") }

/// A group chat: its bots, its chats, and a box that starts a new chat with the team.
struct TeamSheet: View {
    let id: String
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<TeamDetail> = .loading
    @State private var message: String = ""
    @State private var sending: Bool = false
    @State private var problem: SettingsFailure?
    @State private var started: String?
    @State private var editing: TeamRecordView?
    @State private var confirmsRemoval: Bool = false
    @State private var removing: Bool = false

    var body: some View {
        NavigationStack {
            SettingsScroll(title: state.value?.record.name ?? String(localized: "Group chat", comment: "Group chat: the title before its team is read")) {
                if let problem { FailureBanner(failure: problem, macPlace: teamsOnTheMac, retry: nil) }
                LoadedPage(state: state, macPlace: teamsOnTheMac, retry: { Task { await load() } }) { team in
                    members(team)
                    GroupedSection(
                        header: String(localized: "New chat", comment: "Group chat: the box that starts a new chat with the team"),
                        footer: String(localized: "Dot leads the team: it hands each bot its part and puts their answers together.", comment: "Group chat: how a team's chat works")
                    ) {
                        TextField(String(localized: "Message the team", comment: "Group chat: the placeholder of the new chat's box"), text: $message, axis: .vertical)
                            .textRole(.body)
                            .lineLimit(2...8)
                            .padding(Space.groupedGutter)
                            .accessibilityIdentifier("team.message")
                        Button(String(localized: "Send", comment: "Group chat: starts the new chat")) { send() }
                            .buttonStyle(PrimaryButtonStyle())
                            .disabled(removing || sending || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            .padding([.horizontal, .bottom], Space.groupedGutter)
                            .accessibilityIdentifier("team.send")
                    }
                    chats(team.chats)
                    actions(team.record)
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                }
            }
            .sheet(item: $editing) { team in
                EditTeamSheet(team: team) {
                    await load()
                    await model.loadTeams()
                }
            }
            .confirmationDialog(
                String(localized: "Remove the team?", comment: "Group chat: asks before removing the team"),
                isPresented: $confirmsRemoval, titleVisibility: .visible
            ) {
                Button(String(localized: "Remove", comment: "Group chat: removes the team"), role: .destructive) {
                    Task { await remove() }
                }
                Button(String(localized: "Cancel", comment: "Group chat: cancels removal"), role: .cancel) {}
            } message: {
                Text(String(localized: "Its bots and chats stay.", comment: "Group chat: what remains after removing the team"))
            }
            .interactiveDismissDisabled(removing)
            .task { await load() }
            .refreshable { await load() }
            .navigationDestination(isPresented: Binding(get: { started != nil }, set: { if !$0 { started = nil } })) {
                if let key = started {
                    ChatScreen(thread: model.startedChats[key] ?? key, initialPanel: nil, editsFirstQueued: false).id(key)
                }
            }
        }
    }

    private func members(_ team: TeamDetail) -> some View {
        GroupedSection(header: String(localized: "Bots", comment: "Group chat: the bots of the team"), footer: team.record.purpose.isEmpty ? nil : team.record.purpose) {
            ForEach(team.members) { agent in
                HStack(spacing: Space.s) {
                    BotFace(id: agent.id, look: agent.look, size: 32).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        Text(agent.name).textRole(.body).foregroundStyle(Tone.text)
                        if !agent.role.isEmpty { Text(agent.role).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(2) }
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, Space.groupedGutter)
                .padding(.vertical, Space.s)
                .frame(minHeight: Metric.rowWithDescription)
                .accessibilityElement(children: .combine)
            }
        }
    }

    @ViewBuilder private func chats(_ chats: [ThreadSummary]) -> some View {
        if !chats.isEmpty {
            GroupedSection(header: String(localized: "Chats", comment: "Group chat: the team's chats"), footer: nil) {
                ForEach(chats, id: \.id) { chat in
                    NavigationRow(systemImage: nil, title: chat.title, description: chat.lastMessage.isEmpty ? nil : chat.lastMessage, detail: nil) {
                        ChatScreen(thread: chat.id, initialPanel: nil, editsFirstQueued: false)
                    }
                }
            }
        }
    }

    private func actions(_ team: TeamRecordView) -> some View {
        GroupedSection(header: nil, footer: nil) {
            Button { editing = team } label: {
                ListRow(systemImage: "pencil", title: String(localized: "Edit the team", comment: "Group chat: edits the team"),
                        description: nil, accessory: .chevron)
            }
            .buttonStyle(RowPressStyle())
            Button { confirmsRemoval = true } label: {
                ListRow(systemImage: "trash", title: String(localized: "Remove the team", comment: "Group chat: opens removal confirmation"),
                        description: nil, accessory: .none)
            }
            .buttonStyle(RowPressStyle())
        }
        .disabled(removing || sending)
    }

    private func remove() async {
        guard !removing else { return }
        removing = true
        defer { removing = false }
        switch await model.write({ api throws(DaemonRequestError) in try await api.deleteTeam(id) }) {
        case .success:
            await model.loadTeams()
            close()
        case .failure(let box): problem = box.failure
        }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.team(id) }
    }

    private func send() {
        let words: String = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !sending, !words.isEmpty else { return }
        sending = true
        Task {
            if let key = await model.startChat(words, files: [], start: ChatStart(team: id), agent: nil) {
                problem = nil
                message = ""
                started = key
            } else {
                problem = .other(String(localized: "The message was not kept. Try sending it again.", comment: "Group chat: the new chat could not be put in the outbox"))
            }
            sending = false
        }
    }
}

/// A new group chat: a name, what it is for and the bots in it; or a ready team, its bots made where the owner lacks them.
struct NewTeamSheet: View {
    let made: (TeamRecordView) -> Void
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var name: String = ""
    @State private var purpose: String = ""
    @State private var chosen: [String] = []
    @State private var ready: Loadable<[TeamTemplate]> = .loading
    @State private var making: Bool = false
    @State private var problem: SettingsFailure?

    private var bots: [AgentRecord] { model.bots.dropFirst().map(\.record) }
    private var edit: TeamEdit? { TeamEdit.checked(name: name, purpose: purpose, agents: chosen) }

    var body: some View {
        NavigationStack {
            SettingsScroll(title: String(localized: "New group chat", comment: "Home: makes a group chat with some bots")) {
                if let problem { FailureBanner(failure: problem, macPlace: teamsOnTheMac, retry: nil) }
                GroupedSection(header: String(localized: "Name", comment: "New group chat: the field of its name"), footer: nil) {
                    TextField(String(localized: "Launch, Research desk…", comment: "New group chat: example names"), text: $name)
                        .textRole(.body)
                        .padding(.horizontal, Space.groupedGutter)
                        .frame(minHeight: Metric.row)
                        .accessibilityIdentifier("newTeam.name")
                    TextField(String(localized: "What is it for? (optional)", comment: "New group chat: the field of its purpose"), text: $purpose, axis: .vertical)
                        .textRole(.body)
                        .lineLimit(1...4)
                        .padding(Space.groupedGutter)
                }
                GroupedSection(
                    header: String(localized: "Bots", comment: "Group chat: the bots of the team"),
                    footer: bots.isEmpty ? String(localized: "Make a bot first, or start from a ready team below.", comment: "New group chat: the owner has no bot yet") : nil
                ) {
                    ForEach(bots) { bot in
                        ToggleRow(systemImage: nil, title: bot.name, description: bot.role.isEmpty ? nil : bot.role, isOn: member(bot.id))
                            .accessibilityIdentifier("newTeam.bot.\(bot.name)")
                    }
                }
                readyTeams
            }
            .disabled(making)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if making {
                        ProgressView()
                    } else {
                        Button(String(localized: "Create", comment: "New bot: makes it"), systemImage: "checkmark", role: .confirm) { create() }
                            .disabled(edit == nil)
                            .accessibilityIdentifier("newTeam.create")
                    }
                }
            }
            .task { ready = await model.read { api throws(DaemonRequestError) in try await api.teamTemplates() } }
        }
        .interactiveDismissDisabled(making)
    }

    @ViewBuilder private var readyTeams: some View {
        if case .loaded(let teams) = ready, !teams.isEmpty {
            GroupedSection(
                header: String(localized: "Ready teams", comment: "New group chat: the teams to start from"),
                footer: String(localized: "Its bots are made where you have none of the name; the ones you have join as they are.", comment: "New group chat: what a ready team's Use does")
            ) {
                ForEach(teams) { team in
                    Button { use(team) } label: {
                        ListRow(systemImage: "person.3", title: team.name, description: team.purpose, accessory: .none)
                    }
                    .buttonStyle(RowPressStyle())
                }
            }
        }
    }

    private func member(_ id: String) -> Binding<Bool> {
        Binding(
            get: { chosen.contains(id) },
            set: { on in chosen = on ? chosen.filter { $0 != id } + [id] : chosen.filter { $0 != id } })
    }

    private func create() {
        guard let edit, !making else { return }
        finish { api throws(DaemonRequestError) in try await api.createTeam(edit) }
    }

    private func use(_ team: TeamTemplate) {
        guard !making else { return }
        finish { api throws(DaemonRequestError) in try await api.useTeamTemplate(team.id).team }
    }

    private func finish(_ call: @escaping @Sendable (DaemonAPI) async throws(DaemonRequestError) -> TeamRecordView) {
        making = true
        Task {
            switch await model.write(call) {
            case .success(let team):
                await model.loadTeams()
                await model.loadAgents()
                made(team)
            case .failure(let box): problem = box.failure
            }
            making = false
        }
    }
}

/// The bots to start from: each with its face, name and job; one opens its page.
struct BotTemplatesPage: View {
    let made: (AgentRecord) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var templates: Loadable<[BotTemplate]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Templates", comment: "New bot: the bots to start from")) {
            LoadedPage(state: templates, macPlace: agentsOnTheMac, retry: { Task { await load() } }) { templates in
                GroupedSection(header: nil, footer: nil) {
                    ForEach(templates) { template in
                        NavigationLink {
                            BotTemplatePage(template: template, made: made)
                        } label: {
                            HStack(spacing: Space.s) {
                                BotFace(id: template.id, look: template.look, size: 32).accessibilityHidden(true)
                                ListRow(systemImage: nil, title: template.name, description: template.role, accessory: .chevron)
                            }
                            .padding(.leading, Space.groupedGutter)
                        }
                        .buttonStyle(RowPressStyle())
                        .accessibilityIdentifier("template.\(template.id)")
                    }
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        templates = await model.read { api throws(DaemonRequestError) in try await api.botTemplates() }
    }
}

/// One template: what its bot does and needs, its questions to answer first, and its schedule, added switched off.
private struct BotTemplatePage: View {
    let template: BotTemplate
    let made: (AgentRecord) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var answers: [String]
    @State private var schedule: Bool = false
    @State private var making: Bool = false
    @State private var problem: SettingsFailure?

    init(template: BotTemplate, made: @escaping (AgentRecord) -> Void) {
        self.template = template
        self.made = made
        self._answers = State(initialValue: template.questions.map { _ in "" })
    }

    var body: some View {
        SettingsScroll(title: template.name) {
            BotFace(id: template.id, look: template.look, size: 72).frame(maxWidth: .infinity).padding(.vertical, Space.s)
            if let problem { FailureBanner(failure: problem, macPlace: agentsOnTheMac, retry: nil) }
            GroupedSection(header: nil, footer: template.needs.isEmpty ? nil : String(localized: "Needs: \(template.needs)", comment: "Template: what its bot needs of the Mac; the argument is that")) {
                Text(template.role).textRole(.body).foregroundStyle(Tone.text).padding(Space.groupedGutter).frame(maxWidth: .infinity, alignment: .leading)
            }
            if !template.questions.isEmpty {
                GroupedSection(
                    header: String(localized: "First, a few answers", comment: "Template: its questions to answer before the bot is made"),
                    footer: String(localized: "Your answers go into its instructions. Leave one empty to skip it.", comment: "Template: what the answers do")
                ) {
                    ForEach(template.questions.indices, id: \.self) { index in
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text(template.questions[index]).textRole(.footnote).foregroundStyle(Tone.secondary)
                            TextField(String(localized: "Your answer", comment: "Template: the field of an answer"), text: $answers[index], axis: .vertical)
                                .textRole(.body)
                                .lineLimit(1...4)
                        }
                        .padding(Space.groupedGutter)
                    }
                }
            }
            if !template.schedule.isEmpty {
                GroupedSection(header: nil, footer: String(localized: "Added switched off: read its goal, then switch it on.", comment: "Template: the suggested schedule starts off")) {
                    ToggleRow(
                        systemImage: "calendar",
                        title: String(localized: "Add its schedule", comment: "Template: also adds the schedule it suggests"),
                        description: template.schedule, isOn: $schedule)
                }
            }
            Button(String(localized: "Make this bot", comment: "Template: makes the bot")) { make() }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(making)
                .frame(maxWidth: .infinity)
                .accessibilityIdentifier("template.make")
        }
    }

    private func make() {
        guard !making else { return }
        making = true
        Task {
            switch await model.createBot(from: template, answers: answers, schedule: schedule) {
            case .success(let record): made(record)
            case .failure(let box): problem = box.failure
            }
            making = false
        }
    }
}
