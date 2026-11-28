import DotEngine
import SwiftUI

// Settings › AI services as a page of the app (item 2054), on the routes the web's `views/keys.js` uses: each model
// provider and the typed judge's service, the state of its key, its last check, and Add, Replace, Remove and Check. A key
// goes to the Mac and never comes back: the page shows where the key in use comes from and its last four characters, as
// the web does. A typed key lives only in the sheet's field until it is sent; the field is emptied when the sheet goes.
// Every change made from a phone is told to the owner on the Mac (`roadmap/2054.md`).

// MARK: Words

/// What a provider's key is for and where it is made: the web's `PROVIDERS` table.
private struct ProviderAbout {
    let use: String
    let site: String
    let needed: Bool
}

private func aboutProvider(_ provider: String) -> ProviderAbout {
    switch provider {
    case "openai":
        ProviderAbout(use: String(localized: "The GPT models.", comment: "AI services: what OpenAI's key is for"), site: "platform.openai.com", needed: true)
    case "anthropic":
        ProviderAbout(use: String(localized: "The Claude models.", comment: "AI services: what Anthropic's key is for"), site: "platform.claude.com", needed: true)
    case "opencode_go":
        ProviderAbout(use: String(localized: "The open models of an OpenCode Go subscription.", comment: "AI services: what OpenCode Go's key is for"), site: "opencode.ai", needed: true)
    case "openrouter":
        ProviderAbout(use: String(localized: "The models of many labs, on one key and one balance.", comment: "AI services: what OpenRouter's key is for"), site: "openrouter.ai", needed: true)
    case "google":
        ProviderAbout(
            use: String(localized: "The Gemini models of a Google AI Studio key, free within their daily limits.", comment: "AI services: what Google's key is for"), site: "aistudio.google.com",
            needed: true)
    case "ollama":
        ProviderAbout(
            use: String(localized: "Optional: the online web search, and ollama.com without the Ollama app. The models the app runs need no key.", comment: "AI services: what Ollama's key is for"),
            site: "ollama.com", needed: false)
    case "typesafe":
        ProviderAbout(
            use: String(localized: "Optional: an extra checker that reads button labels no rule knows and checks what a campaign claims.", comment: "AI services: what TypeSafe's key is for"),
            site: "console.typesafe.ai", needed: false)
    default:
        ProviderAbout(use: "", site: "", needed: false)
    }
}

private func sourceWords(_ source: KeySource) -> String {
    switch source {
    case .environment: String(localized: "Set when Dot was started", comment: "AI services: the key in use comes from Dot's environment")
    case .envFile: String(localized: "Saved in Dot", comment: "AI services: the key in use is in Dot's own file")
    case .keychain: String(localized: "From the Keychain", comment: "AI services: the key in use comes from the Mac's Keychain")
    }
}

/// The line under a provider's name: "Connected · ••••7c38", "No key yet", "The key was refused at its last check".
private func stateWords(_ key: KeyView, needed: Bool) -> String {
    switch key.state {
    case .missing:
        needed
            ? String(localized: "No key yet", comment: "AI services: a provider without a key")
            : String(localized: "Optional", comment: "AI services: an optional service without a key")
    case .set:
        String(localized: "Connected · ••••\(key.tail)", comment: "AI services: a key is set; the argument is its last four characters")
    case .failing:
        String(localized: "Refused at the last check · ••••\(key.tail)", comment: "AI services: the provider refused the key; the argument is its last four characters")
    }
}

/// A moment as the owner reads it: "5 minutes ago".
private func checkedWhen(_ seconds: Double, locale: Locale) -> String {
    let relative: RelativeDateTimeFormatter = RelativeDateTimeFormatter()
    relative.locale = locale
    relative.dateTimeStyle = .named
    return relative.localizedString(for: Date(timeIntervalSince1970: seconds), relativeTo: .now)
}

private var keysMacPlace: String { String(localized: "Settings › AI services", comment: "Where on the Mac: the AI services page") }

// MARK: Rows

/// A provider's mark in the row's icon slot: its own logo, or the generic one.
private struct ProviderMarkSlot: View {
    let provider: String
    @ScaledMetric(relativeTo: .body) private var side: CGFloat = 22

    var body: some View {
        Image(providerMarkAsset(provider)).resizable().renderingMode(.template).scaledToFit()
            .frame(width: side, height: side).foregroundStyle(Tone.text)
            .frame(minWidth: Metric.iconSlot)
            .accessibilityHidden(true)
    }
}

/// A provider in the list: its mark, its name, the state of its key, and the chevron.
private struct ProviderKeyRow: View {
    let key: KeyView

    var body: some View {
        let about: ProviderAbout = aboutProvider(key.provider)
        HStack(spacing: Space.s) {
            ProviderMarkSlot(provider: key.provider)
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(providerTitle(key.provider)).textRole(.body).foregroundStyle(Tone.text)
                Text(stateWords(key, needed: about.needed)).textRole(.footnote)
                    .foregroundStyle(key.state == .failing ? Tone.failure : Tone.secondary)
                    .multilineTextAlignment(.leading)
            }
            Spacer(minLength: Space.s)
            Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
        }
        .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s)
        .frame(minHeight: Metric.rowWithDescription)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([providerTitle(key.provider), stateWords(key, needed: about.needed)].joined(separator: ", "))
    }
}

/// A button row of a section (Check key, Replace key), in the ink or, for a removal, in the failure colour.
private struct KeyActionRow: View {
    let title: String
    let systemImage: String
    let destructive: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.s) {
                Symbol(name: systemImage, context: .row).frame(minWidth: Metric.iconSlot)
                Text(title).textRole(.body)
                Spacer(minLength: 0)
            }
            .foregroundStyle(destructive ? Tone.failure : Tone.text)
            .padding(.horizontal, Space.groupedGutter)
            .frame(minHeight: Metric.row)
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
    }
}

// MARK: Pages

/// Settings › AI services: the providers and the state of each one's key (`GET /api/keys`).
struct KeysPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<[KeyView]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "AI services", comment: "Settings row: the model providers and their API keys")) {
            LoadedPage(state: state, macPlace: keysMacPlace, retry: { Task { await load() } }) { keys in
                GroupedSection(
                    header: nil,
                    footer: String(localized: "A key goes to your Mac and is never shown again, here or on the Mac: only its last four characters.", comment: "AI services: what the page shows of a key")
                ) {
                    ForEach(keys) { key in
                        NavigationLink(value: SettingsPage.key(key.name)) { ProviderKeyRow(key: key) }
                            .buttonStyle(RowPressStyle())
                            .accessibilityIdentifier("keys.provider.\(key.provider)")
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.keys().keys }
    }
}

/// One provider's key: where it comes from, its last check, and Check, Add or Replace, and Remove.
struct KeyPage: View {
    let name: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<KeyView> = .loading
    @State private var problem: SettingsFailure?
    @State private var entering: Bool = false
    @State private var askingRemove: Bool = false
    @State private var checking: Bool = false
    @State private var acting: Bool = false

    private var shownLabel: String { state.value.map { providerTitle($0.provider) } ?? "" }
    private var shownSite: String { aboutProvider(state.value?.provider ?? "").site }

    var body: some View {
        SettingsScroll(title: state.value.map { providerTitle($0.provider) } ?? String(localized: "AI services", comment: "Settings row: the model providers and their API keys")) {
            if let problem { FailureBanner(failure: problem, macPlace: keysMacPlace, retry: nil) }
            LoadedPage(state: state, macPlace: keysMacPlace, retry: { Task { await load() } }) { key in content(key) }
        }
        .task { await load() }
        .refreshable { await load() }
        .sheet(isPresented: $entering) {
            if let key = state.value { KeyEntrySheet(key: key) { value in await save(value) } }
        }
        .confirmationDialog(
            String(localized: "Remove the \(shownLabel) key?", comment: "AI services: confirmation of Remove key; the argument is the provider"),
            isPresented: $askingRemove, titleVisibility: .visible
        ) {
            Button(String(localized: "Remove", comment: "AI services: removes the saved key"), role: .destructive) { Task { await remove() } }
        } message: {
            Text(
                String(
                    localized: "Dot deletes the saved key from your Mac. The key itself stays valid at \(shownSite) until you cancel it there.",
                    comment: "AI services: what Remove key does; the argument is the provider's site"))
        }
    }

    @ViewBuilder private func content(_ key: KeyView) -> some View {
        let about: ProviderAbout = aboutProvider(key.provider)
        GroupedSection(header: nil, footer: about.use.isEmpty ? nil : about.use) {
            HStack(spacing: Space.s) {
                ProviderMarkSlot(provider: key.provider)
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(providerTitle(key.provider)).textRole(.body).foregroundStyle(Tone.text)
                    Text(stateWords(key, needed: about.needed)).textRole(.footnote).foregroundStyle(key.state == .failing ? Tone.failure : Tone.secondary)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s)
            .frame(minHeight: Metric.rowWithDescription)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("key.state")
            ListRow(systemImage: "tray", title: String(localized: "Kept", comment: "AI services: where the key in use comes from"), description: nil, accessory: .detail(key.source.map(sourceWords) ?? String(localized: "Not set", comment: "AI services: no key is set")))
            ListRow(systemImage: "chevron.left.forwardslash.chevron.right", title: String(localized: "Variable", comment: "AI services: the name of the key's variable"), description: nil, accessory: .detail(key.name))
        }
        if key.shadowed {
            Text(
                String(
                    localized: "Dot was started with \(key.name) already set, which comes before a key saved here. Change it on your Mac.",
                    comment: "AI services: the key Dot was started with wins over one saved from the phone; the argument is the variable"))
                .textRole(.footnote).foregroundStyle(Tone.secondary).padding(.horizontal, Space.groupedGutter)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        if key.source != nil {
            GroupedSection(header: String(localized: "Last check", comment: "AI services: the last time the provider was asked about the key"), footer: nil) {
                if let check = key.check {
                    ListRow(
                        systemImage: check.ok ? "checkmark.circle" : "exclamationmark.triangle",
                        title: check.ok
                            ? String(localized: "\(providerTitle(key.provider)) took the key", comment: "AI services: the check passed; the argument is the provider")
                            : String(localized: "\(providerTitle(key.provider)) refused the key", comment: "AI services: the check failed; the argument is the provider"),
                        description: check.detail.isEmpty ? nil : check.detail, accessory: .detail(checkedWhen(check.at, locale: locale)))
                        .accessibilityIdentifier("key.check")
                } else {
                    ListRow(systemImage: "questionmark.circle", title: String(localized: "Not checked since the key changed or Dot started", comment: "AI services: no check is known"), description: nil, accessory: .none)
                }
                if checking {
                    HStack(spacing: Space.s) {
                        ProgressView().tint(Tone.secondary).frame(minWidth: Metric.iconSlot)
                        Text(String(localized: "Asking \(providerTitle(key.provider))…", comment: "AI services: a check is under way; the argument is the provider"))
                            .textRole(.body).foregroundStyle(Tone.secondary)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
                    .accessibilityElement(children: .combine)
                } else {
                    KeyActionRow(title: String(localized: "Check key", comment: "AI services: asks the provider whether it takes the key"), systemImage: "checkmark.seal", destructive: false) { Task { await check() } }
                        .accessibilityIdentifier("key.checkButton")
                }
            }
        }
        GroupedSection(
            header: nil,
            footer: String(localized: "A key you save or remove here is told on your Mac, so that a change you did not make is seen.", comment: "AI services: a phone's change is told to the owner")
        ) {
            KeyActionRow(
                title: key.source == nil ? String(localized: "Add key", comment: "AI services: saves a first key") : String(localized: "Replace key", comment: "AI services: saves a new key in place of the one in use"),
                systemImage: key.source == nil ? "plus" : "pencil", destructive: false
            ) { entering = true }
            .accessibilityIdentifier("key.edit")
            if key.removable {
                KeyActionRow(title: String(localized: "Remove key", comment: "AI services: deletes the key saved in Dot"), systemImage: "trash", destructive: true) { askingRemove = true }
                    .accessibilityIdentifier("key.remove")
            }
        }
        .disabled(acting)
    }

    private func load() async {
        let name: String = name
        let read: Loadable<[KeyView]> = await model.read { api throws(DaemonRequestError) in try await api.keys().keys }
        switch read {
        case .loaded(let keys):
            if let key = keys.first(where: { $0.name == name }) {
                state = .loaded(key)
                problem = nil
            } else {
                state = .failed(.daemonTooOld)
            }
        case .failed(let failure): state = .failed(failure)
        case .loading: state = .loading
        }
    }

    /// Save `value`; on success the sheet closes and the provider is asked about the new key, as the web does.
    private func save(_ value: String) async -> SettingsFailure? {
        let name: String = name
        acting = true
        defer { acting = false }
        switch await model.write({ api throws(DaemonRequestError) in try await api.saveKey(name, value: value) }) {
        case .success(let key):
            state = .loaded(key)
            problem = nil
            Task { await check() }
            return nil
        case .failure(let box):
            return box.failure
        }
    }

    private func remove() async {
        let name: String = name
        acting = true
        switch await model.write({ api throws(DaemonRequestError) in try await api.removeKey(name) }) {
        case .success(let key):
            state = .loaded(key)
            problem = nil
        case .failure(let box):
            await load()
            problem = box.failure
        }
        acting = false
    }

    private func check() async {
        let name: String = name
        checking = true
        switch await model.write({ api throws(DaemonRequestError) in try await api.checkKey(name) }) {
        case .success(let result):
            if let key = state.value { state = .loaded(key.checked(result, at: Date.now.timeIntervalSince1970)) }
            problem = nil
        case .failure(let box):
            problem = box.failure
        }
        checking = false
    }
}

/// The sheet a key is pasted into: a secure field, Save and Close. The key is sent once, at Save, and the field is
/// emptied when the sheet goes, saved or not.
private struct KeyEntrySheet: View {
    let key: KeyView
    let save: (String) async -> SettingsFailure?
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var value: String = ""
    @State private var working: Bool = false
    @State private var problem: SettingsFailure?
    @FocusState private var focused: Bool

    private var title: String {
        key.source == nil
            ? String(localized: "Add the \(providerTitle(key.provider)) key", comment: "AI services: the sheet that saves a first key; the argument is the provider")
            : String(localized: "Replace the \(providerTitle(key.provider)) key", comment: "AI services: the sheet that saves a new key; the argument is the provider")
    }

    /// Words under the field when what is in it cannot be sent.
    private var fieldProblem: String? {
        guard !trimmedKey(value).isEmpty, !isSendableKey(value) else { return nil }
        return String(localized: "A key has no spaces or line breaks inside.", comment: "AI services: the pasted key holds a space or a character a key never has")
    }

    var body: some View {
        NavigationStack {
            SettingsScroll(title: title) {
                if let problem { FailureBanner(failure: problem, macPlace: keysMacPlace, retry: nil) }
                GroupedSection(
                    header: String(localized: "API key", comment: "AI services: the field's header"),
                    footer: fieldProblem
                        ?? String(localized: "Dot keeps it on your Mac, in a file only you can read. This phone keeps nothing of it.", comment: "AI services: where a saved key is kept")
                ) {
                    SecureField(String(localized: "Paste the key", comment: "AI services: the key field's placeholder"), text: $value)
                        .textRole(.body).autocorrectionDisabled().textInputAutocapitalization(.never).privacySensitive()
                        .focused($focused).submitLabel(.done).onSubmit { Task { await run() } }
                        .padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
                        .accessibilityIdentifier("key.field")
                }
                if !aboutProvider(key.provider).site.isEmpty {
                    Text(String(localized: "Make one at \(aboutProvider(key.provider).site).", comment: "AI services: where a key is made; the argument is the site"))
                        .textRole(.footnote).foregroundStyle(Tone.secondary).padding(.horizontal, Space.groupedGutter)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits"), systemImage: "checkmark") { Task { await run() } }
                        .disabled(working || !isSendableKey(value))
                        .accessibilityIdentifier("key.save")
                }
            }
        }
        .presentationDetents([.medium, .large])
        .interactiveDismissDisabled(working)
        .onAppear { focused = true }
        .onDisappear { value = "" }
    }

    private func run() async {
        guard isSendableKey(value), !working else { return }
        working = true
        problem = await save(trimmedKey(value))
        working = false
        if problem == nil {
            value = ""
            dismiss()
        }
    }
}

// MARK: Search

/// AI services as Settings' search finds it: the web's own words of the page (settingstabs.js) and the same in the
/// app's language.
func keysIndexEntry() -> (SettingsEntry, SettingsPage) {
    (
        SettingsEntry(
            id: "keys", title: String(localized: "AI services", comment: "Settings row: the model providers and their API keys"), path: "",
            synonyms: [
                "keys api key providers openai anthropic openrouter google gemini ollama token",
                String(localized: "keys api key providers token", comment: "Settings search synonyms of AI services (space separated)"),
            ]),
        .keys
    )
}
