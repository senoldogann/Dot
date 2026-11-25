import DotEngine
import SwiftUI

// The agents of Dot, native (item 1774; the web's `#/agents`), within what a phone is for (PLAN §26): the list, an
// agent's page to read, a chat with it, and its model. Making agents, their instructions and teams are the Mac's.
// Settings › Agents pushes `AgentsHome`; the Agents tab is to show the same screens.

private var agentsMacPlace: String { String(localized: "Agents", comment: "Where on the Mac: the Agents page") }

/// The agents, Dot first, each with its role; one opens its page.
struct AgentsHome: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<[AgentListItem]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Agents", comment: "Title of the agents list")) {
            LoadedPage(state: state, macPlace: agentsMacPlace, retry: { Task { await load() } }) { agents in
                GroupedSection(
                    header: nil,
                    footer: String(localized: "Ask Dot for a big job and it hands parts to them. New agents are made on your Mac.", comment: "Agents: what agents are for")
                ) {
                    ForEach(agents) { item in
                        if item.id == "dot" {
                            ListRow(systemImage: "circle.circle", title: item.record.name, description: String(localized: "Leads every task", comment: "Agents: Dot's line"), accessory: .none)
                        } else {
                            NavigationRow(systemImage: item.record.pausedAt == nil ? "person.crop.circle" : "pause.circle", title: item.record.name, description: agentLine(item.record), detail: nil) {
                                AgentProfilePage(id: item.id)
                            }
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.agentList() }
    }
}

/// An agent's role, and whether it is paused, in one line.
func agentLine(_ record: AgentRecordView) -> String {
    var parts: [String] = [record.role.isEmpty ? String(localized: "No role yet", comment: "Agents: an agent with no role") : record.role]
    if record.pausedAt != nil { parts.append(String(localized: "Paused", comment: "Schedule: switched off")) }
    return parts.joined(separator: " · ")
}

/// The language a new agent greets the owner in, by name in English as the web sends it ("Turkish").
func welcomeLanguage(_ locale: Locale) -> String {
    let code: String = locale.language.languageCode?.identifier ?? "en"
    return Locale(identifier: "en").localizedString(forLanguageCode: code) ?? "English"
}

/// An agent's page: a chat with it (its own chat, or the first one, in which it greets the owner), its model (chosen
/// here), and its role, approvals, actions and instructions to read.
struct AgentProfilePage: View {
    let id: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<AgentDetailView> = .loading
    @State private var options: [ProviderModels] = []
    @State private var picking: ModelPick?
    @State private var chat: String?
    @State private var opening: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        SettingsScroll(title: state.value?.record.name ?? String(localized: "Agent", comment: "Agents: an agent's page")) {
            if let problem { FailureBanner(failure: problem, macPlace: agentsMacPlace, retry: nil) }
            LoadedPage(state: state, macPlace: agentsMacPlace, retry: { Task { await load() } }) { detail in
                let record: AgentRecordView = detail.record
                if record.pausedAt != nil {
                    Notice(tone: .warn, message: String(localized: "Paused: its schedules start nothing by themselves.", comment: "Agents: a paused agent"), dismiss: nil)
                }
                Button { Task { await openChat(detail) } } label: {
                    Label(String(localized: "Chat with \(record.name)", comment: "Agents: opens a chat with the agent; the argument is its name"), systemImage: "bubble.left.and.bubble.right")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(opening)
                GroupedSection(header: nil, footer: nil) {
                    Button {
                        picking = ModelPick(
                            id: "agent.model", title: String(localized: "Model", comment: "Schedule create: the model"), current: record.model, effort: nil,
                            candidates: modelCandidates(options, for: .main)
                        ) { ref, _ in await setModel(record, ref) }
                    } label: {
                        ListRow(
                            systemImage: nil, title: String(localized: "Model", comment: "Schedule create: the model"), description: nil,
                            accessory: .detailAndChevron(record.model.isEmpty ? String(localized: "Default model", comment: "Agents: an agent on Dot's default model") : modelLabel(record.model, in: options)))
                    }
                    .buttonStyle(RowPressStyle())
                    ListRow(systemImage: nil, title: String(localized: "Role", comment: "Agents: an agent's role"), description: nil, accessory: .detail(record.role.isEmpty ? "–" : record.role))
                    ListRow(systemImage: nil, title: String(localized: "Approvals", comment: "Schedule edit: the approval mode"), description: nil, accessory: .detail(approvalWords(record.approvals)))
                    ListRow(
                        systemImage: nil, title: String(localized: "Actions", comment: "Agents: the tools an agent may use"), description: nil,
                        accessory: .detail(record.tools.isEmpty ? String(localized: "All actions", comment: "Agents: every tool") : String(localized: "\(record.tools.count) actions", comment: "Agents: a number of tools")))
                }
                if !record.instructions.isEmpty {
                    GroupedSection(header: String(localized: "Instructions", comment: "Agents: an agent's instructions"), footer: String(localized: "Changed on your Mac.", comment: "Agents: instructions are edited on the Mac")) {
                        Text(record.instructions).textRole(.body).foregroundStyle(Tone.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(Space.groupedGutter)
                    }
                }
            }
        }
        .sheet(item: $picking) { pick in ModelPickerSheet(pick: pick, defaultRef: "") }
        .navigationDestination(item: $chat) { thread in ChatScreen(thread: thread, initialPanel: nil, editsFirstQueued: false) }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.agent(id) }
        if options.isEmpty { options = (await model.read { api throws(DaemonRequestError) in try await api.models() }).value ?? [] }
    }

    /// Opens the agent's own chat, or has the agent greet the owner in its first one and opens that.
    private func openChat(_ detail: AgentDetailView) async {
        if let home = detail.home {
            chat = home.id
            return
        }
        opening = true
        let welcome: AgentWelcome = AgentWelcome(language: welcomeLanguage(locale))
        let id: String = id
        switch await model.write({ api throws(DaemonRequestError) in try await api.welcomeAgent(id, welcome: welcome) }) {
        case .success(let run):
            problem = nil
            chat = run.threadId
        case .failure(let box): problem = box.failure
        }
        opening = false
    }

    /// Writes the agent back whole with another model (the daemon takes no partial edit); its own actions are kept.
    private func setModel(_ record: AgentRecordView, _ ref: String) async -> SettingsFailure? {
        let agent: AgentWrite = AgentWrite(
            name: record.name, role: record.role, instructions: record.instructions, model: ref,
            effort: record.effort.flatMap { effortChoices(ref, in: options).contains($0) ? $0 : nil }, approvals: record.approvals,
            tools: record.tools.isEmpty ? nil : record.tools, refuses: record.refuses, look: record.look)
        let id: String = id
        switch await model.write({ api throws(DaemonRequestError) in try await api.editAgent(id, agent: agent) }) {
        case .success:
            problem = nil
            await load()
            return nil
        case .failure(let box): return box.failure
        }
    }
}
