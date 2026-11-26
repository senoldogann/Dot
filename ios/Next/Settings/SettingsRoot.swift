import DotEngine
import SwiftUI

/// The one settings sheet (PLAN §27), opened from the owner's initials on the home: grouped cards in the order of the
/// owner's reference, the native pages inside; what belongs to the Mac stays there (§26). A search with synonyms finds
/// any page.
struct SettingsSheet: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var path: [SettingsPage]
    @State private var query: String
    @State private var failsafe: Loadable<FailsafeState> = .loading
    @State private var stopProblem: SettingsFailure?
    @State private var releasing: Bool = false
    @State private var owner: String = ""
    /// Why the owner's name could not be read (the card then says "You").
    @State private var ownerProblem: SettingsFailure?
    @State private var connectApps: ConnectAppsOpening

    let start: SettingsStart
    let close: () -> Void

    init(start: SettingsStart, close: @escaping () -> Void) {
        self.start = start
        self.close = close
        _path = State(initialValue: start.path)
        _query = State(initialValue: start.query)
        _connectApps = State(initialValue: start.connectApps)
    }

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if query.trimmingCharacters(in: .whitespaces).isEmpty { root } else { results }
            }
            .navigationDestination(for: SettingsPage.self) { page in SettingsDestination(page: page, start: start) }
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: String(localized: "Search settings", comment: "Settings: the search field's placeholder"))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                        .accessibilityIdentifier("settings.close")
                }
            }
        }
        .task { failsafe = await model.read { api throws(DaemonRequestError) in try await api.failsafe() } }
        .task { await readOwner() }
        .sheet(isPresented: Binding(get: { connectApps != .closed }, set: { if !$0 { connectApps = .closed } })) {
            ConnectAppsSheet(opensDetail: connectApps.detail)
        }
    }

    private func readOwner() async {
        switch await model.read({ api throws(DaemonRequestError) in try await api.assistant() }) {
        case .loaded(let assistant):
            owner = assistant.owner
            ownerProblem = nil
        case .failed(let failure): ownerProblem = failure
        case .loading: break
        }
    }

    // MARK: The root list

    /// The one settings sheet of the owner's initials (PLAN §27): the owner and what the work uses, Connect apps, the
    /// bot's own settings, the phone's preferences, the system, and a footnote that the rest is on the Mac.
    private var root: some View {
        SettingsScroll(title: String(localized: "Settings", comment: "Title of the settings tab")) {
            if let ownerProblem {
                FailureBanner(failure: ownerProblem, macPlace: String(localized: "Settings › General", comment: "Where on the Mac: the General page"), retry: { Task { await readOwner() } })
            }
            GroupedSection(header: nil, footer: nil) {
                PageRow(page: .general, systemImage: "person.crop.circle", title: owner.isEmpty ? String(localized: "You", comment: "Settings: the owner's card before their name is read") : owner, description: model.macName, detail: nil)
                PageRow(page: .usage, systemImage: "chart.bar", title: String(localized: "Usage", comment: "Settings row: the tokens of the last days"), description: nil, detail: nil)
                PageRow(page: .caps, systemImage: "gauge.with.dots.needle.33percent", title: String(localized: "Usage limits and caps", comment: "Settings row: the plan's windows and the caps of what tasks spend"), description: nil, detail: nil)
            }
            GroupedSection(header: nil, footer: nil) {
                Button { connectApps = .list } label: {
                    ListRow(
                        systemImage: "square.grid.2x2", title: String(localized: "Connect apps", comment: "Home: opens the sheet that connects apps"),
                        description: String(localized: "Search plugins", comment: "Settings: the line under Connect apps"), accessory: .chevron)
                }
                .buttonStyle(RowPressStyle())
            }
            GroupedSection(header: String(localized: "Bot", comment: "Settings section: how Dot and the bots work"), footer: nil) {
                PageRow(page: .rules, systemImage: "lock.shield", title: String(localized: "Permissions", comment: "Settings row: the approval mode and the rules"), description: String(localized: "Approval mode and rules", comment: "Settings: the line under Permissions"), detail: nil)
                PageRow(page: .mac, systemImage: "desktopcomputer", title: String(localized: "Dot's computer", comment: "Settings row: the paired Mac Dot works on"), description: nil, detail: transportWord(model.transport))
                PageRow(page: .schedules, systemImage: "clock", title: String(localized: "Scheduled tasks", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .memory, systemImage: "brain", title: String(localized: "Memory", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .agents, systemImage: "person.2", title: String(localized: "Agents", comment: "Title of the agents list"), description: nil, detail: nil)
            }
            GroupedSection(header: nil, footer: nil) {
                PageRow(page: .models, systemImage: "cpu", title: String(localized: "Models", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .notifications, systemImage: "bell", title: String(localized: "Notifications", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .appearance, systemImage: "paintbrush", title: String(localized: "Appearance", comment: "Settings row"), description: nil, detail: nil)
                PageRow(page: .chat, systemImage: "bubble.left.and.bubble.right", title: String(localized: "Chat", comment: "Settings row: chat preferences"), description: nil, detail: nil)
                PageRow(page: .voice, systemImage: "waveform", title: String(localized: "Voice", comment: "Settings row: voice typing"), description: nil, detail: nil)
                PageRow(page: .general, systemImage: "gearshape", title: String(localized: "General", comment: "Settings row"), description: nil, detail: nil)
            }
            GroupedSection(header: String(localized: "System", comment: "Settings section: health, version"), footer: nil) {
                PageRow(page: .maintenance, systemImage: "arrow.triangle.2.circlepath", title: String(localized: "Maintenance", comment: "Settings row: updates and what happens after a restart"), description: nil, detail: nil)
                PageRow(page: .system, systemImage: "heart.text.square", title: String(localized: "Health and version", comment: "Settings row: the daemon's health, version, emergency stop"), description: nil, detail: nil)
            }
            stopBlock
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

    /// While the Mac's emergency stop holds, what holds and how to release it. The phone does not engage it (owner's
    /// decision 2026-11-25): the Mac's own failsafe does.
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
        }
        if let stopProblem {
            FailureBanner(failure: stopProblem, macPlace: String(localized: "Settings › System", comment: "Where on the Mac: the System page"), retry: nil)
        }
    }

    /// The row that opens Connect apps (item 1862, PLAN §27, which overrides §26's cut of Connectors), until the settings
    /// sheet of PLAN §27 carries it.
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
                            if index.first(where: { $0.0.id == entry.id })?.1 == .connectApps {
                                Button { connectApps = .list } label: {
                                    ListRow(systemImage: nil, title: entry.title, description: entry.path.isEmpty ? nil : entry.path, accessory: .chevron)
                                }
                                .buttonStyle(RowPressStyle())
                            } else if let page = index.first(where: { $0.0.id == entry.id })?.1 {
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

}

/// The page of a Settings row, wherever a stack opens it (the settings sheet, a bot's profile).
struct SettingsDestination: View {
    let page: SettingsPage
    let start: SettingsStart

    var body: some View {
        switch page {
        case .mac: MacPage()
        case .notifications: NotificationsPage()
        case .general: GeneralPage()
        case .appearance: AppearancePage()
        case .chat: ChatSettingsPage()
        case .voice: VoicePage()
        case .models: ModelsPage()
        case .caps: CapsPage(triesRaise: start.triesRaise)
        case .schedules: SchedulesPage(editsFirst: start.editsFirstSchedule, agentId: nil)
        case .rules: RulesPage()
        case .memory: MemoryPage()
        case .agents: AgentsHome()
        case .system: SystemPage()
        case .usage: UsagePage()
        case .maintenance: MaintenancePage()
        case .connectApps: EmptyView()
        }
    }
}

/// Where Settings opens: its stack, its search, and (fixtures of debug builds) a sheet or a write made at once.
struct SettingsStart: Equatable, Sendable {
    let path: [SettingsPage]
    let query: String
    let editsFirstSchedule: Bool
    let triesRaise: Bool
    let connectApps: ConnectAppsOpening
}

/// Whether Settings opens with Connect apps shown, and on which service's detail.
enum ConnectAppsOpening: Equatable, Sendable {
    case closed
    case list
    case detail(String)

    var detail: String? {
        if case .detail(let id) = self { return id }
        return nil
    }
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
    // Each entry's path is the group it stands in on the sheet; the unheaded groups have none.
    let agents: String = String(localized: "Bot", comment: "Settings section: how Dot and the bots work")
    let system: String = String(localized: "System", comment: "Settings section: health, version")
    func entry(_ id: String, _ title: String, _ path: String, _ english: String, _ local: String, _ page: SettingsPage) -> (SettingsEntry, SettingsPage) {
        (SettingsEntry(id: id, title: title, path: path, synonyms: [english, local]), page)
    }
    let all: [(SettingsEntry, SettingsPage)] = [
        entry(
            "mac", String(localized: "Dot's computer", comment: "Settings row: the paired Mac Dot works on"), agents, "this mac connection tailscale address key pair diagnose network status proof",
            String(localized: "connection tailscale address key pair diagnose network", comment: "Settings search synonyms of This Mac (space separated)"), .mac),
        entry(
            "notifications", String(localized: "Notifications", comment: "Settings row"), "", "push alerts banner needs you done ready",
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
            "voice", String(localized: "Voice", comment: "Settings row: voice typing"), "", "voice dictation microphone transcribe speech bluetooth airpods carplay",
            String(localized: "voice dictation microphone transcribe speech", comment: "Settings search synonyms of Voice (space separated)"), .voice),
        entry(
            "models", String(localized: "Models", comment: "Settings row"), "", "model llm main review vision fast code review fallback provider",
            String(localized: "model main review vision code review", comment: "Settings search synonyms of Models (space separated)"), .models),
        entry(
            "caps", String(localized: "Usage limits and caps", comment: "Settings row: the plan's windows and the caps of what tasks spend"), "",
            "budget cost spend tokens limit cap plan window", String(localized: "budget cost spend tokens limit", comment: "Settings search synonyms of the caps (space separated)"), .caps),
        entry(
            "schedules", String(localized: "Scheduled tasks", comment: "Settings row"), agents, "schedules cron repeat morning brief loops automations",
            String(localized: "schedules repeat morning brief", comment: "Settings search synonyms of Scheduled tasks (space separated)"), .schedules),
        entry(
            "rules", String(localized: "Permissions", comment: "Settings row: the approval mode and the rules"), agents, "rules permissions approvals policy ask safety allow deny",
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
            "usage", String(localized: "Usage", comment: "Settings row: the tokens of the last days"), "", "usage tokens days stats",
            String(localized: "usage tokens days", comment: "Settings search synonyms of Usage (space separated)"), .usage),
        entry(
            "maintenance", String(localized: "Maintenance", comment: "Settings row: updates and what happens after a restart"), system,
            "updates versions resume restart continue claude opencode cli performance battery hangs crashes data",
            String(localized: "updates versions resume restart continue performance battery hangs crashes", comment: "Settings search synonyms of Maintenance (space separated)"), .maintenance),
        entry(
            "connect-apps", String(localized: "Connect apps", comment: "Title of the Connect apps sheet"), "", "connectors connect apps services mcp accounts google github slack notion plugins",
            String(localized: "connectors apps services accounts", comment: "Settings search synonyms of Connect apps (space separated)"), .connectApps),
    ]
    return all
}
