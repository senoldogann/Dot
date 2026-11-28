import DotEngine
import Foundation

// The bots of the home (PLAN §27, items 1881-1884) on the app's one adapter: the list read from the Mac, and the
// owner's doings on a bot (make one and let it greet, rename, delete, share as a template, mark read).

extension AppModel {
    /// Dot's record before `GET /api/agents` answered (its chats are drawn under it meanwhile).
    static let fallbackDot: AgentRecord = AgentRecord(
        id: dotAgentId, name: "Dot", role: "", instructions: "", model: "", effort: nil, approvals: nil, tools: [], createdAt: 0, updatedAt: 0, look: nil,
        pausedAt: nil, refuses: nil)

    /// The home's bots, Dot first, from the agents and the chat list as they stand.
    var bots: [Bot] {
        DotEngine.bots(agents: agents, rows: list.rows, pinned: pinnedBots, fallbackDot: Self.fallbackDot, gone: goneChats)
    }

    /// The bot a chat belongs to.
    func bot(of thread: String) -> Bot? {
        bots.first { bot in bot.chats.contains { $0.summary.id == thread } }
    }

    /// Reads Dot and the agents again; a failure keeps what was read and is said on the home (`agentsProblem`).
    func loadAgents() async {
        switch await read({ api throws(DaemonRequestError) in try await api.agents() }) {
        case .loaded(let read):
            agents = read
            agentsProblem = nil
        case .failed(let failure): agentsProblem = failure
        case .loading: break
        }
    }

    /// Reads the agents again after deletions: one read at a time, and one more after it when more were asked for
    /// meanwhile, so twenty chats deleted at once are not twenty reads (the review of item 1965).
    func rereadAgents() {
        if agentsReread.running {
            agentsReread.owed = true
            return
        }
        agentsReread = (true, false)
        Task {
            await loadAgents()
            let again: Bool = agentsReread.owed
            agentsReread = (false, false)
            if again { rereadAgents() }
        }
    }

    /// Makes a bot from its name and what it should do, then starts its first meeting (item 993): it greets the owner
    /// in its role and asks one question. The new agent's record, or why it could not be made; a meeting that could not
    /// start leaves the agent made, and its chat says why (`welcomeProblems`).
    func createBot(name: String, does: String) async -> Result<AgentRecord, SettingsFailureBox> {
        guard let request: AgentRequest = AgentRequest.newBot(name: name, does: does) else {
            return .failure(SettingsFailureBox(failure: .other(String(localized: "A bot needs a name.", comment: "New bot: the name was left empty"))))
        }
        let made: Result<AgentRecord, SettingsFailureBox> = await write { api throws(DaemonRequestError) in try await api.createAgent(request) }
        guard case .success(let record) = made else { return made }
        await welcome(record)
        await loadAgents()
        return .success(record)
    }

    /// Makes a bot from a template with the owner's answers to its questions, then starts its first meeting as
    /// `createBot` does; with `schedule`, adds the schedule the template suggests, switched off (item 464). A meeting or a
    /// schedule that could not be added leaves the bot made, and its chat says why (`welcomeProblems`).
    func createBot(from template: BotTemplate, answers: [String], schedule: Bool) async -> Result<AgentRecord, SettingsFailureBox> {
        let made: Result<AgentRecord, SettingsFailureBox> = await write { api throws(DaemonRequestError) in try await api.useBotTemplate(template.id, answers: answers) }
        guard case .success(let record) = made else { return made }
        await welcome(record)
        if schedule, !template.cron.isEmpty,
            case .failure(let box) = await write({ api throws(DaemonRequestError) in try await api.addSuggestedSchedule(template, bot: record) })
        {
            welcomeProblems[record.id] = .other(
                String(
                    localized: "Its schedule was not added: \(failureWords(box.failure, macPlace: agentsOnTheMac))",
                    comment: "New bot from a template: the suggested schedule could not be added; the argument is why"))
        }
        await loadAgents()
        return .success(record)
    }

    /// Starts a new bot's first meeting (item 993) in the phone's language; a meeting that could not start is said in its
    /// chat (`welcomeProblems`).
    private func welcome(_ record: AgentRecord) async {
        let language: String = Locale(identifier: "en").localizedString(forLanguageCode: Locale.current.language.languageCode?.identifier ?? "en") ?? "English"
        if case .failure(let box) = await write({ api throws(DaemonRequestError) in try await api.welcome(agent: record.id, language: language) }) {
            welcomeProblems[record.id] = box.failure
        }
    }

    /// Reads the owner's teams again, for the home's group chats; a failure keeps what was read and is said on the home
    /// (`agentsProblem`, whose Try again reads both).
    func loadTeams() async {
        switch await read({ api throws(DaemonRequestError) in try await api.teams() }) {
        case .loaded(let read): teams = read
        case .failed(let failure): agentsProblem = failure
        case .loading: break
        }
    }

    func renameBot(_ record: AgentRecord, to name: String) async -> SettingsFailure? {
        switch await write({ api throws(DaemonRequestError) in try await api.updateAgent(record.id, AgentRequest.renamed(record, to: name)) }) {
        case .success:
            await loadAgents()
            return nil
        case .failure(let box): return box.failure
        }
    }

    /// Deletes an agent; its chats stay and are Dot's from then on.
    func deleteBot(_ id: String) async -> SettingsFailure? {
        switch await write({ api throws(DaemonRequestError) in try await api.deleteAgent(id) }) {
        case .success:
            pinnedBots.removeAll { $0 == id }
            mutedBots.remove(id)
            await loadAgents()
            return nil
        case .failure(let box): return box.failure
        }
    }

    /// The agent as a file to share (`<name>.dot-agent.json` in the temporary folder), or why not.
    func exportBot(_ record: AgentRecord) async -> Result<URL, SettingsFailureBox> {
        switch await write({ api throws(DaemonRequestError) in try await api.exportAgent(record.id) }) {
        case .success(let data):
            let safe: String = record.name.components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }.joined(separator: "-")
            let file: URL = FileManager.default.temporaryDirectory.appending(path: "\(safe.isEmpty ? "agent" : safe).dot-agent.json")
            do {
                try data.write(to: file, options: .atomic)
            } catch {
                return .failure(SettingsFailureBox(failure: .other(String(describing: error))))
            }
            return .success(file)
        case .failure(let box): return .failure(box)
        }
    }

    /// Marks every unread chat of a bot seen; the first refusal, or `nil` when every chat was marked.
    func markRead(_ bot: Bot) async -> SettingsFailure? {
        for row in bot.chats where row.summary.unread {
            if case .failure(let box) = await write({ api throws(DaemonRequestError) in try await api.markSeen(thread: row.summary.id) }) {
                return box.failure
            }
        }
        // A home chat outside the list's window is the agents list's copy: read it again so its dot goes.
        await loadAgents()
        return nil
    }

    func togglePin(_ id: String) {
        if pinnedBots.contains(id) { pinnedBots.removeAll { $0 == id } } else { pinnedBots.append(id) }
    }

    func toggleMute(_ id: String) {
        if mutedBots.contains(id) { mutedBots.remove(id) } else { mutedBots.insert(id) }
    }

    /// What the chats say that holds the words (the Mac's index), or why it could not be asked.
    func searchChats(_ query: String) async -> Loadable<[ChatHit]> {
        await read { api throws(DaemonRequestError) in try await api.searchChats(query, limit: 20) }
    }
}

/// The composer's placeholder in a bot's chat: "Ask <name>".
func askPlaceholder(_ name: String) -> String {
    String(localized: "Ask \(name)", comment: "Placeholder of the composer in a bot's chat; the argument is the bot's name")
}

/// Where the same thing is done on the Mac, for a failure's continuation: the web app's Agents page.
var agentsOnTheMac: String {
    String(localized: "the Agents page", comment: "Where on the Mac: the web app's page of agents (after 'Open on the Mac:')")
}

/// What a bot does, as its profile and the home say it: Dot's built-in role (the daemon's own English default,
/// `store/models.py`) in the app's language, any other role as its owner wrote it (item 1966).
func shownRole(_ record: AgentRecord) -> String {
    record.id == dotAgentId && record.role == "Your assistant"
        ? String(localized: "Your assistant", comment: "Dot's built-in role, under its name on its profile")
        : record.role
}
