import DotEngine
import SwiftUI

/// The Settings tab (PLAN §4.4, the reference's structure with Dot's pages; UI-STANDARD 14:25): the emergency stop at
/// the top, then Connections, the phone's own preferences, Agents, System, and one quiet footnote: what is set up at the
/// computer is the Mac's (PLAN §26), so no row opens the web app. A search with synonyms finds any page.
struct SettingsTab: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var path: [SettingsPage]
    @State private var query: String
    @State private var failsafe: Loadable<FailsafeState> = .loading
    @State private var stopAsked: Bool = false
    @State private var stopProblem: SettingsFailure?
    @State private var releasing: Bool = false

    let start: SettingsStart

    init(start: SettingsStart) {
        self.start = start
        _path = State(initialValue: start.path)
        _query = State(initialValue: start.query)
    }

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if query.trimmingCharacters(in: .whitespaces).isEmpty { root } else { results }
            }
            .navigationDestination(for: SettingsPage.self) { page in destination(page) }
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: String(localized: "Search settings", comment: "Settings: the search field's placeholder"))
        }
        .task { failsafe = await model.read { api throws(DaemonRequestError) in try await api.failsafe() } }
    }

    // MARK: The root list

    private var root: some View {
        SettingsScroll(title: String(localized: "Settings", comment: "Title of the settings tab")) {
            stopBlock
            GroupedSection(header: String(localized: "Connections", comment: "Settings section: the Mac, notifications, this phone"), footer: nil) {
                PageRow(page: .mac, systemImage: "desktopcomputer", title: String(localized: "This Mac", comment: "Settings row: the paired Mac"), description: nil, detail: transportWord(model.transport))
                PageRow(page: .notifications, systemImage: "bell", title: String(localized: "Notifications", comment: "Settings row"), description: nil, detail: nil)
            }
            GroupedSection(header: nil, footer: nil) {
                PageRow(page: .general, systemImage: "gearshape", title: String(localized: "General", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .appearance, systemImage: "paintbrush", title: String(localized: "Appearance", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .chat, systemImage: "bubble.left.and.bubble.right", title: String(localized: "Chat", comment: "Settings row: chat preferences"), description: nil, detail: nil)
            }
            GroupedSection(header: String(localized: "Agents", comment: "Settings section: models, schedules, rules, memory"), footer: nil) {
                PageRow(page: .models, systemImage: "cpu", title: String(localized: "Models", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .caps, systemImage: "gauge.with.dots.needle.33percent", title: String(localized: "Usage limits and caps", comment: "Settings row: the plan's windows and the caps of what tasks spend"), description: nil, detail: nil)
                PageRow(page: .schedules, systemImage: "clock", title: String(localized: "Scheduled tasks", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .rules, systemImage: "lock.shield", title: String(localized: "Rules", comment: "Settings row: what Dot may do without asking"), description: nil, detail: nil)
                PageRow(page: .memory, systemImage: "brain", title: String(localized: "Memory", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .agents, systemImage: "person.2", title: String(localized: "Agents", comment: "Title of the agents list"), description: nil, detail: nil)
            }
            GroupedSection(header: String(localized: "System", comment: "Settings section: health, version"), footer: nil) {
                PageRow(page: .system, systemImage: "heart.text.square", title: String(localized: "Health and version", comment: "Settings row: the daemon's health, version, emergency stop"), description: nil, detail: nil)
                PageRow(page: .usage, systemImage: "chart.bar", title: String(localized: "Usage", comment: "Settings row: the tokens of the last days"), description: nil, detail: nil)
                PageRow(page: .maintenance, systemImage: "arrow.triangle.2.circlepath", title: String(localized: "Maintenance", comment: "Settings row: updates and what happens after a restart"), description: nil, detail: nil)
            }
            Text(String(localized: "More settings are on your Mac.", comment: "Settings: the quiet footnote at the end; what is set up at the computer is done on the Mac"))
                .textRole(.footnote).foregroundStyle(Tone.tertiary)
                .frame(maxWidth: .infinity, alignment: .center)
            #if DEBUG
                GroupedSection(header: String(localized: "Design", comment: "Settings section of debug builds"), footer: String(localized: "Debug builds only.", comment: "Footer of the debug-only design section")) {
                    NavigationRow(systemImage: "square.grid.2x2", title: String(localized: "Gallery", comment: "Settings row of debug builds: the component gallery"), description: nil, detail: nil) {
                        GalleryIndex()
                    }
                }
            #endif
        }
    }

    /// The emergency stop, one tap from the top (PLAN §4.4), and while it holds, what holds and how to release it.
    @ViewBuilder private var stopBlock: some View {
        if failsafe.value?.engaged == true {
            VStack(alignment: .leading, spacing: Space.s) {
                Notice(tone: .warn, message: String(localized: "Everything is stopped: no run starts and schedules wait until you release the stop.", comment: "Settings: the emergency stop holds"), dismiss: nil)
                Button(String(localized: "Release the stop", comment: "Settings: lets Dot work again after the emergency stop"), systemImage: "play.circle") {
                    Task {
                        releasing = true
                        switch await model.write({ api throws(DaemonRequestError) in try await api.releaseFailsafe() }) {
                        case .success(let state): failsafe = .loaded(state)
                        case .failure(let box): stopProblem = box.failure
                        }
                        releasing = false
                    }
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(releasing)
            }
        } else {
            Button(String(localized: "Emergency stop", comment: "Settings: stops every run of Dot at once"), systemImage: "stop.circle", role: .destructive) { stopAsked = true }
                .buttonStyle(DestructiveButtonStyle())
                .frame(maxWidth: .infinity)
                .confirmationDialog(String(localized: "Stop everything Dot is doing?", comment: "Confirmation of the emergency stop"), isPresented: $stopAsked, titleVisibility: .visible) {
                    Button(String(localized: "Stop everything", comment: "Confirmation button of the emergency stop"), role: .destructive) {
                        Task {
                            switch await model.write({ api throws(DaemonRequestError) in try await api.emergencyStop() }) {
                            case .success(let state):
                                failsafe = .loaded(state)
                                stopProblem = nil
                            case .failure(let box): stopProblem = box.failure
                            }
                        }
                    }
                } message: {
                    Text(String(localized: "Every run stops, desktop control is blocked and schedules wait until you release the stop.", comment: "Emergency stop confirmation: what it does"))
                }
        }
        if let stopProblem {
            FailureBanner(failure: stopProblem, macPlace: String(localized: "Settings › System", comment: "Where on the Mac: the System page"), retry: nil)
        }
    }

    // MARK: Search

    private var results: some View {
        let index: [(SettingsEntry, SettingsPage)] = settingsIndex()
        let found: [SettingsEntry] = searchSettings(query, in: index.map(\.0))
        return Group {
            if found.isEmpty {
                ContentUnavailableView.search(text: query).background(Tone.ground)
            } else {
                SettingsScroll(title: String(localized: "Settings", comment: "Title of the settings tab")) {
                    GroupedSection(header: nil, footer: nil) {
                        ForEach(found) { entry in
                            if let page = index.first(where: { $0.0.id == entry.id })?.1 {
                                NavigationLink(value: page) {
                                    ListRow(systemImage: nil, title: entry.title, description: entry.path.isEmpty ? nil : entry.path, accessory: .chevron)
                                }
                                .buttonStyle(RowPressStyle())
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: Pages

    @ViewBuilder private func destination(_ page: SettingsPage) -> some View {
        switch page {
        case .mac: MacPage()
        case .notifications: NotificationsPage()
        case .general: GeneralPage()
        case .appearance: AppearancePage()
        case .chat: ChatSettingsPage()
        case .models: ModelsPage()
        case .caps: CapsPage(triesRaise: start.triesRaise)
        case .schedules: SchedulesPage(editsFirst: start.editsFirstSchedule, agentId: nil)
        case .rules: RulesPage()
        case .memory: MemoryPage()
        case .agents: AgentsHome()
        case .system: SystemPage()
        case .usage: UsagePage()
        case .maintenance: MaintenancePage()
        }
    }
}

/// Where Settings opens: its stack, its search, and (fixtures of debug builds) a sheet or a write made at once.
struct SettingsStart: Equatable, Sendable {
    let path: [SettingsPage]
    let query: String
    let editsFirstSchedule: Bool
    let triesRaise: Bool
}

/// How the link to the Mac stands, in one word.
func transportWord(_ transport: Transport) -> String {
    switch transport {
    case .up: String(localized: "Connected", comment: "Settings: the Mac answers")
    case .connecting: String(localized: "Connecting…", comment: "Settings: the link to the Mac is being opened")
    case .reconnecting: String(localized: "Reconnecting…", comment: "Chat subtitle: the link to the Mac broke and is being opened again")
    case .offline: String(localized: "Offline", comment: "Chat subtitle: the phone has no link")
    case .keyRefused: String(localized: "Key refused", comment: "Settings: the Mac no longer takes this phone's key")
    case .daemonTooOld: String(localized: "Update Dot on your Mac", comment: "Chat subtitle: the Mac runs a Dot too old for this app")
    }
}

/// Every place the search finds, with the page it opens: the native pages, each with synonyms in English (whatever the
/// app's language, as the web's words) and in the app's language.
func settingsIndex() -> [(SettingsEntry, SettingsPage)] {
    let agents: String = String(localized: "Agents", comment: "Settings section: models, schedules, rules, memory")
    let connections: String = String(localized: "Connections", comment: "Settings section: the Mac, notifications, this phone")
    let system: String = String(localized: "System", comment: "Settings section: health, version")
    func entry(_ id: String, _ title: String, _ path: String, _ english: String, _ local: String, _ page: SettingsPage) -> (SettingsEntry, SettingsPage) {
        (SettingsEntry(id: id, title: title, path: path, synonyms: [english, local]), page)
    }
    let all: [(SettingsEntry, SettingsPage)] = [
        entry(
            "mac", String(localized: "This Mac", comment: "Settings row: the paired Mac"), connections, "connection tailscale address key pair diagnose network status proof",
            String(localized: "connection tailscale address key pair diagnose network", comment: "Settings search synonyms of This Mac (space separated)"), .mac),
        entry(
            "notifications", String(localized: "Notifications", comment: "Settings row"), connections, "push alerts banner needs you done ready",
            String(localized: "push alerts banner", comment: "Settings search synonyms of Notifications (space separated)"), .notifications),
        entry(
            "general", String(localized: "General", comment: "Settings row"), "", "name language timestamps clock follow-up queue steer confirmations voice dictation",
            String(localized: "name language timestamps clock queue steer confirmations voice dictation", comment: "Settings search synonyms of General (space separated)"), .general),
        entry(
            "appearance", String(localized: "Appearance", comment: "Settings row"), "", "theme dark light mode text size font tool calls zen streaming reading width",
            String(localized: "theme dark light mode text size font tool calls zen streaming", comment: "Settings search synonyms of Appearance (space separated)"), .appearance),
        entry(
            "chat", String(localized: "Chat", comment: "Settings row: chat preferences"), "", "haptics vibration reactions follow ups",
            String(localized: "haptics vibration reactions", comment: "Settings search synonyms of Chat (space separated)"), .chat),
        entry(
            "models", String(localized: "Models", comment: "Settings row"), agents, "model llm main review vision fast code review fallback provider",
            String(localized: "model main review vision code review", comment: "Settings search synonyms of Models (space separated)"), .models),
        entry(
            "caps", String(localized: "Usage limits and caps", comment: "Settings row: the plan's windows and the caps of what tasks spend"), agents,
            "budget cost spend tokens limit cap plan window", String(localized: "budget cost spend tokens limit", comment: "Settings search synonyms of the caps (space separated)"), .caps),
        entry(
            "schedules", String(localized: "Scheduled tasks", comment: "Settings row"), agents, "schedules cron repeat morning brief loops automations",
            String(localized: "schedules repeat morning brief", comment: "Settings search synonyms of Scheduled tasks (space separated)"), .schedules),
        entry(
            "rules", String(localized: "Rules", comment: "Settings row: what Dot may do without asking"), agents, "permissions approvals policy ask safety allow deny",
            String(localized: "permissions approvals policy ask safety", comment: "Settings search synonyms of Rules (space separated)"), .rules),
        entry(
            "memory", String(localized: "Memory", comment: "Settings row"), agents, "notes remember lessons search proposals",
            String(localized: "notes remember lessons proposals", comment: "Settings search synonyms of Memory (space separated)"), .memory),
        entry(
            "agents", String(localized: "Agents", comment: "Title of the agents list"), agents, "agent agents helpers chat",
            String(localized: "agent agents helpers chat", comment: "Settings search synonyms of Agents (space separated)"), .agents),
        entry(
            "system", String(localized: "Health and version", comment: "Settings row: the daemon's health, version, emergency stop"), system,
            "daemon health emergency stop version uptime failsafe", String(localized: "health emergency stop version", comment: "Settings search synonyms of System (space separated)"), .system),
        entry(
            "usage", String(localized: "Usage", comment: "Settings row: the tokens of the last days"), system, "usage tokens days stats",
            String(localized: "usage tokens days", comment: "Settings search synonyms of Usage (space separated)"), .usage),
        entry(
            "maintenance", String(localized: "Maintenance", comment: "Settings row: updates and what happens after a restart"), system,
            "updates versions resume restart continue claude opencode cli",
            String(localized: "updates versions resume restart continue", comment: "Settings search synonyms of Maintenance (space separated)"), .maintenance),
    ]
    return all
}
