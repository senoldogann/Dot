import DotEngine
import SwiftUI

private var browserPlace: String { String(localized: "Settings › Browser", comment: "Where on the Mac: browser settings") }

private enum BrowserProfileAction {
    case clear(BrowserProfile)
    case delete(BrowserProfile)
}

struct BrowserSettingsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<BrowserSettingsView> = .loading
    @State private var profileAction: BrowserProfileAction?
    @State private var adding: Bool = false
    @State private var name: String = ""
    @State private var busy: Bool = false
    @State private var problem: SettingsFailure?
    @State private var notice: String?

    var body: some View {
        SettingsScroll(title: String(localized: "Browser", comment: "Settings: Dot's browser")) {
            if let problem { FailureBanner(failure: problem, macPlace: browserPlace, retry: nil) }
            if let notice { Notice(tone: .info, message: notice, dismiss: { self.notice = nil }) }
            LoadedPage(state: state, macPlace: browserPlace, retry: { Task { await load() } }) { settings in
                general(settings)
                profiles(settings)
                advanced(settings)
            }
        }
        .disabled(busy)
        .task { await load() }
        .refreshable { if !busy { await load() } }
        .alert(String(localized: "Add a profile", comment: "Browser: create a profile"), isPresented: $adding) {
            TextField(String(localized: "Name of the new profile", comment: "Browser: profile name"), text: $name)
            Button(String(localized: "Cancel", comment: "Browser: close the profile form"), role: .cancel) {}
            Button(String(localized: "Add", comment: "Browser: creates the profile")) {
                let newName = name.trimmingCharacters(in: .whitespacesAndNewlines)
                Task { _ = await change { api throws(DaemonRequestError) in try await api.addBrowserProfile(newName) } }
            }
            .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.unicodeScalars.count > 48)
        }
        .confirmationDialog(
            String(localized: "Change this profile?", comment: "Browser: confirmation of a destructive profile action"),
            isPresented: Binding(get: { profileAction != nil }, set: { if !$0 { profileAction = nil } }),
            titleVisibility: .visible, presenting: profileAction
        ) { action in
            switch action {
            case .clear(let profile):
                Button(String(localized: "Clear", comment: "Browser: clear a profile's sign-ins and site data"), role: .destructive) {
                    Task { _ = await change { api throws(DaemonRequestError) in try await api.clearBrowserProfile(profile.name) } }
                }
            case .delete(let profile):
                Button(String(localized: "Delete", comment: "Browser: delete the profile"), role: .destructive) {
                    Task { _ = await change { api throws(DaemonRequestError) in try await api.deleteBrowserProfile(profile.name) } }
                }
            }
            Button(String(localized: "Cancel", comment: "Browser: cancels clearing or deleting a profile"), role: .cancel) {}
        } message: { _ in
            Text(String(localized: "Its sign-ins and site data will be removed.", comment: "Browser: consequence of clearing or deleting a profile"))
        }
    }

    private func general(_ settings: BrowserSettingsView) -> some View {
        GroupedSection(
            header: String(localized: "Browse with", comment: "Browser: choose which Chrome Dot uses"),
            footer: String(localized: "Own Chrome is a profile of Dot's, apart from yours. My Chrome opens tabs in your Chrome, signed in as you.", comment: "Browser: explains which Chrome profile Dot can use")
        ) {
            ForEach(BrowserChoice.allCases, id: \.self) { mode in
                RadioRow(title: modeWords(mode), description: nil, isSelected: settings.mode == mode) {
                    Task { _ = await change { api throws(DaemonRequestError) in try await api.setBrowserChoice(mode) } }
                }
            }
            ToggleRow(systemImage: nil, title: String(localized: "Tasks may use the browser", comment: "Browser: makes the browser available to tasks"),
                      description: nil, isOn: Binding(get: { state.value?.enabled == true }, set: { value in
                Task { _ = await change { api throws(DaemonRequestError) in try await api.setBrowserEnabled(value) } }
            }))
            ToggleRow(systemImage: nil, title: String(localized: "Bring Chrome to the front while working", comment: "Browser: bring the window forward"),
                      description: nil, isOn: Binding(get: { state.value?.raiseWindow == true }, set: { value in
                Task { _ = await change { api throws(DaemonRequestError) in try await api.setBrowserRaise(value) } }
            }))
            ToggleRow(systemImage: nil, title: String(localized: "Use the page from the screen while I hold the task", comment: "Browser: enables input in a page while a task is held"),
                      description: String(localized: "On, a task you took over can be used from the screen that shows its page: your taps, scrolls and keys go to the page while the task waits. Nothing you type is kept.", comment: "Browser: explains input on a held page"),
                      isOn: Binding(get: { state.value?.remoteInput == true }, set: { value in
                Task { _ = await change { api throws(DaemonRequestError) in try await api.setBrowserRemoteInput(value) } }
            }))
        }
    }

    private func profiles(_ settings: BrowserSettingsView) -> some View {
        GroupedSection(header: String(localized: "Profiles", comment: "Browser: profiles of Dot's Chrome"), footer: nil) {
            ForEach(settings.profiles) { profile in
                DisclosureGroup {
                    profileButtons(profile, active: settings.profile)
                } label: {
                    ListRow(systemImage: "person.crop.circle", title: profileWords(profile), description: nil,
                            accessory: settings.profile == profile.name ? .detail(String(localized: "In use", comment: "Browser: the profile in use")) : .none)
                }
            }
            Button { name = ""; adding = true } label: {
                ListRow(systemImage: "plus", title: String(localized: "Add profile", comment: "Browser: adds a profile"),
                        description: nil, accessory: .none)
            }
            .buttonStyle(RowPressStyle())
            if !settings.profiles.contains(where: { $0.name == settings.profile && $0.kind == .incognito }) {
                Button {
                    Task {
                        if await change({ api throws(DaemonRequestError) in try await api.openBrowserProfileForSignIn() }) == nil {
                            notice = String(localized: "Finish on your Mac", comment: "Browser: Chrome opened on the Mac to sign in")
                        }
                    }
                } label: {
                    ListRow(systemImage: "desktopcomputer", title: String(localized: "Sign in…", comment: "Browser: opens Chrome on the Mac to sign in"),
                            description: settings.signingIn ? String(localized: "Finish on your Mac", comment: "Browser: Chrome opened on the Mac to sign in") : nil,
                            accessory: .none)
                }
                .buttonStyle(RowPressStyle())
                .disabled(settings.signingIn)
            }
        }
    }

    private func profileButtons(_ profile: BrowserProfile, active: String) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            if profile.name != active {
                Button(String(localized: "Use", comment: "Browser: use this profile")) {
                    Task { _ = await change { api throws(DaemonRequestError) in try await api.useBrowserProfile(profile.name) } }
                }
                .buttonStyle(SecondaryButtonStyle())
            }
            if profile.kind != .incognito {
                Button(String(localized: "Check", comment: "Browser: checks that Chrome can read sign-ins")) { Task { await check(profile) } }
                    .buttonStyle(SecondaryButtonStyle())
                Button(String(localized: "Clear", comment: "Browser: clear a profile's sign-ins and site data")) { profileAction = .clear(profile) }
                    .buttonStyle(DestructiveButtonStyle())
            }
            if profile.kind == .added && profile.name != active {
                Button(String(localized: "Delete", comment: "Browser: delete the profile")) { profileAction = .delete(profile) }
                    .buttonStyle(DestructiveButtonStyle())
            }
        }
        .padding(Space.groupedGutter)
    }

    private func advanced(_ settings: BrowserSettingsView) -> some View {
        GroupedSection(header: String(localized: "Advanced", comment: "Settings: advanced controls"), footer: nil) {
            NavigationRow(systemImage: "paintbrush", title: String(localized: "Appearance of pages", comment: "Browser: opens page appearance"),
                          description: nil, detail: nil) {
                BrowserLookPage(look: settings.look) { look in
                    await change { api throws(DaemonRequestError) in try await api.setBrowserPageLook(look) }
                }
            }
            if settings.mode == .own {
                NavigationRow(systemImage: "network", title: String(localized: "DevTools port", comment: "Browser: Chrome's developer port"),
                              description: nil, detail: settings.debugPort.formatted(.number.grouping(.never))) {
                    BrowserPortPage(port: settings.debugPort) { port in
                        await change { api throws(DaemonRequestError) in try await api.setBrowserDebugPort(port) }
                    }
                }
            }
        }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.browserSettings() }
    }

    private func change(_ call: (DaemonAPI) async throws(DaemonRequestError) -> BrowserSettingsView) async -> SettingsFailure? {
        guard !busy else {
            let failure: SettingsFailure = .other(String(localized: "Wait for the current change to finish.", comment: "Browser: a change is already saving"))
            problem = failure
            return failure
        }
        busy = true
        defer { busy = false }
        notice = nil
        switch await model.write(call) {
        case .success(let updated): state = .loaded(updated); problem = nil; return nil
        case .failure(let box): problem = box.failure; return box.failure
        }
    }

    private func check(_ profile: BrowserProfile) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        notice = nil
        switch await model.write({ api throws(DaemonRequestError) in try await api.checkBrowserProfile(profile.name) }) {
        case .success(let checked):
            problem = nil
            notice = String(localized: "Chrome read \(checked.readable) saved cookies.", comment: "Browser: number of readable cookies in a profile check")
        case .failure(let box): problem = box.failure
        }
    }

    private func modeWords(_ mode: BrowserChoice) -> String {
        switch mode {
        case .own: String(localized: "Own Chrome", comment: "Browser: Dot's own Chrome")
        case .attach: String(localized: "My Chrome", comment: "Browser: the owner's Chrome")
        }
    }

    private func profileWords(_ profile: BrowserProfile) -> String {
        switch profile.kind {
        case .default: String(localized: "Default", comment: "Browser: the default profile")
        case .incognito: String(localized: "Incognito", comment: "Browser: the profile that keeps no data")
        case .added: profile.name
        }
    }
}

private struct BrowserLookPage: View {
    let save: (BrowserPageLook) async -> SettingsFailure?
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var fit: Bool
    @State private var width: Int
    @State private var height: Int
    @State private var appearance: BrowserAppearance
    @State private var zoom: Int
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?

    init(look: BrowserPageLook, save: @escaping (BrowserPageLook) async -> SettingsFailure?) {
        self.save = save
        self._fit = State(initialValue: look.fitWindow)
        self._width = State(initialValue: look.viewportWidth)
        self._height = State(initialValue: look.viewportHeight)
        self._appearance = State(initialValue: look.appearance)
        self._zoom = State(initialValue: look.zoom)
    }

    var body: some View {
        SettingsScroll(title: String(localized: "Appearance of pages", comment: "Browser: opens page appearance")) {
            if let problem { FailureBanner(failure: problem, macPlace: browserPlace, retry: nil) }
            GroupedSection(header: String(localized: "Size of a page", comment: "Browser: page dimensions"), footer: nil) {
                ToggleRow(systemImage: nil, title: String(localized: "Fit the window", comment: "Browser: make the page fill the window"),
                          description: nil, isOn: $fit)
                if !fit {
                    number(String(localized: "Width", comment: "Browser: viewport width in pixels"), value: $width)
                    number(String(localized: "Height", comment: "Browser: viewport height in pixels"), value: $height)
                }
                number(String(localized: "Zoom of pages", comment: "Browser: page zoom percentage"), value: $zoom)
            }
            GroupedSection(header: String(localized: "Appearance of pages", comment: "Browser: opens page appearance"), footer: nil) {
                ForEach(BrowserAppearance.allCases, id: \.self) { choice in
                    RadioRow(title: appearanceWords(choice), description: nil, isSelected: appearance == choice) { appearance = choice }
                }
            }
            Button(String(localized: "Save", comment: "Browser: saves the page appearance")) { Task { await apply() } }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(!(320...3840).contains(width) || !(240...2160).contains(height) || !(25...500).contains(zoom))
        }
        .disabled(saving)
    }

    private func number(_ label: String, value: Binding<Int>) -> some View {
        HStack {
            Text(label).textRole(.body)
            TextField(label, value: value, format: .number.grouping(.never))
                .multilineTextAlignment(.trailing)
                .keyboardType(.numberPad)
        }
        .padding(Space.groupedGutter)
    }

    private func appearanceWords(_ choice: BrowserAppearance) -> String {
        switch choice {
        case .system: String(localized: "System", comment: "Browser: follows Mac appearance")
        case .light: String(localized: "Light", comment: "Browser: light appearance")
        case .dark: String(localized: "Dark", comment: "Browser: dark appearance")
        }
    }

    private func apply() async {
        guard !saving else { return }
        saving = true
        defer { saving = false }
        let look = BrowserPageLook(fitWindow: fit, viewportWidth: width, viewportHeight: height, appearance: appearance, zoom: zoom)
        problem = await save(look)
        if problem == nil { dismiss() }
    }
}

private struct BrowserPortPage: View {
    let save: (Int) async -> SettingsFailure?
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var port: Int
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?

    init(port: Int, save: @escaping (Int) async -> SettingsFailure?) {
        self.save = save
        self._port = State(initialValue: port)
    }

    var body: some View {
        let title = String(localized: "DevTools port", comment: "Browser: Chrome's developer port")
        SettingsScroll(title: title) {
            if let problem { FailureBanner(failure: problem, macPlace: browserPlace, retry: nil) }
            GroupedSection(header: nil, footer: String(localized: "Any program on this Mac can use the port while Dot's Chrome runs. 0 turns it off.", comment: "Browser: the optional port is local and does not ask for a key")) {
                TextField(title, value: $port, format: .number.grouping(.never))
                    .keyboardType(.numberPad).textRole(.body).padding(Space.groupedGutter)
            }
            Button(String(localized: "Save", comment: "Browser: saves Chrome's developer port")) { Task { await apply() } }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(port != 0 && !(1024...65535).contains(port))
        }
        .disabled(saving)
    }

    private func apply() async {
        guard !saving else { return }
        saving = true
        defer { saving = false }
        problem = await save(port)
        if problem == nil { dismiss() }
    }
}
