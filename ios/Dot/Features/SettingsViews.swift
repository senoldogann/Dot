import SwiftUI

/// A refusal or a failure said on the page where the tap was: a card like every other, with a muted red glyph and the words in
/// the ink (a failure is a glyph and words, not a coloured box).
struct NoticeCard: View {
    let words: String
    var tone: Color = Theme.bad
    var icon: IconName = .alert

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            DotIcon(icon, size: 16, relativeTo: .footnote).foregroundStyle(tone).padding(.top, 2)
            Text(words).font(Theme.small).foregroundStyle(Theme.text)
            Spacer(minLength: 0)
        }
        .padding(Space.m)
        .card(radius: Radius.m)
        .accessibilityElement(children: .combine)
    }
}

/// Settings of the native app: models, schedules, notifications, the daemon's address and the phone's key, and the web
/// UI as a way out.
struct NativeSettingsView: View {
    let model: AppModel
    let store: NativeStore
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var changingAddress: Bool = false
    @State private var forgetting: Bool = false
    @State private var path: [String] = []
    @Environment(\.dynamicTypeSize) private var typeSize: DynamicTypeSize

    var body: some View {
        NavigationStack(path: $path) {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    macCard
                    CardGroup(title: "Dot") {
                        NavigationLink(value: "models") {
                            CardRow(icon: .cpu, title: "Models", detail: "The main and the review model", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                        CardDivider()
                        NavigationLink(value: "schedules") {
                            CardRow(icon: .history, title: "Schedules", detail: "What Dot does on its own, and when", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                    }
                    NotificationSwitches(awareness: store.awareness, online: store.linkState.online)
                    CardGroup(title: "Phone") {
                        Button {
                            // The iPhone's own page of this app's settings, where its notifications are switched.
                            if let url: URL = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        } label: {
                            CardRow(
                                icon: .bell, title: "iPhone notification settings",
                                detail: "Banners, sounds and the badge", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                        CardDivider()
                        Button { changingAddress = true } label: {
                            CardRow(icon: .network, title: "Change address", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                        CardDivider()
                        Button { forgetting = true } label: {
                            CardRow(icon: .key, title: "Forget this phone's key", destructive: true)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("forget-key")
                    }
                    CardGroup(
                        footer: "This app covers chats, approvals, helpers and schedules. Everything else is in Dot on your Mac."
                    ) {
                        Button {
                            dismiss()
                            model.showsWeb = true
                        } label: {
                            CardRow(icon: .globe, title: "Open web UI", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                    }
                    CardGroup(title: "About") {
                        NavigationLink(value: "licences") {
                            CardRow(icon: .shieldCheck, title: "Licences", detail: "What the app's icons are drawn from", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("licences")
                    }
                }
                .padding(Space.l)
            }
            .dotScreenBackground(inSheet: true)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: String.self) { page in
                switch page {
                case "models": ModelsView(store: store)
                case "licences": LicencesView()
                default: SchedulesView(store: store)
                }
            }
            .onAppear {
                if model.debugSchedules { path = ["schedules"] } else if let page: String = model.debugSettingsPage { path = [page] }
            }
            .toolbar { DoneButton { dismiss() } }
            .sheet(isPresented: $changingAddress) {
                AddressView(model: model, onSaved: { changingAddress = false; dismiss() }).dotSheet()
            }
            .confirmationDialog("Forget this phone's key?", isPresented: $forgetting, titleVisibility: .visible) {
                Button("Forget the key", role: .destructive) {
                    model.forgetKey()
                    dismiss()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("You will need a new code from your Mac to pair this phone again.")
            }
        }
    }

    /// The Mac this phone is paired with, and whether it answers: the first thing the owner wants to know here.
    private var macCard: some View {
        let layout: AnyLayout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.m)) : AnyLayout(HStackLayout(spacing: Space.m))
        return layout {
            DotIcon(.laptop, size: 24, relativeTo: .title3)
                .foregroundStyle(Theme.muted)
                .frame(minWidth: 28, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                Text(store.host.name).font(.headline).foregroundStyle(Theme.text).lineLimit(typeSize.isAccessibilitySize ? 3 : 1).truncationMode(.middle)
                StateMark(words: connectionWords, kind: connectionKind)
            }
            if !typeSize.isAccessibilitySize { Spacer() }
        }
        .padding(Space.l)
        .card()
        .accessibilityElement(children: .combine)
    }

    private var connectionWords: String {
        switch store.connection {
        case .live: "Connected"
        case .connecting: "Connecting"
        case .reconnecting: "Reconnecting"
        case .unauthorized: "Key refused"
        }
    }

    /// Connected is quiet; connecting has the moving mark; a refused key waits on the owner, so it is amber.
    private var connectionKind: StateMark.Kind {
        switch store.connection {
        case .live: .done
        case .connecting, .reconnecting: .working
        case .unauthorized: .waiting
        }
    }
}

/// The models: the default one and the review model, chosen on the daemon's own routes as the web UI's Models page
/// does (`POST /api/models/main`, `POST /api/models/roles`).
struct ModelsView: View {
    let store: NativeStore
    @State private var providers: [ProviderModels] = []
    @State private var loading: Bool = true
    @State private var failure: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                if let failure { NoticeCard(words: failure) }
                CardGroup(footer: "The main model answers new messages. The review model checks runs and draws lessons.") {
                    picker("Main model", icon: .sparkles, current: store.models?.main) { ref in
                        try await store.api.chooseMain(model: ref)
                    }
                    CardDivider()
                    picker("Review model", icon: .badgeCheck, current: store.models?.review) { ref in
                        try await store.api.chooseRole("review", model: ref)
                    }
                }
                if loading && providers.isEmpty { ProgressView().frame(maxWidth: .infinity) }
            }
            .padding(Space.l)
        }
        .dotScreenBackground(inSheet: true)
        .navigationTitle("Models")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await store.loadModels()
            do { providers = try await store.api.models() } catch { failure = "Could not read the models from your Mac. Is it awake?" }
            loading = false
        }
    }

    private func picker(_ title: String, icon: IconName, current: String?, choose: @escaping (String) async throws -> Void) -> some View {
        NavigationLink {
            ModelChoiceList(title: title, providers: providers, current: current, choose: choose, reload: { await store.loadModels() })
        } label: {
            CardRow(icon: icon, title: title, showsChevron: true) {
                Text(label(of: current)).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(1)
            }
        }
        .buttonStyle(.plain)
    }

    private func label(of ref: String?) -> String {
        guard let ref else { return "…" }
        return providers.flatMap(\.models).first(where: { $0.ref == ref })?.label ?? ref
    }
}

/// The models of every provider that can use tools, one to choose; a refusal is said on this page, where the tap was.
struct ModelChoiceList: View {
    let title: String
    let providers: [ProviderModels]
    let current: String?
    let choose: (String) async throws -> Void
    let reload: () async -> Void
    @State private var failure: String?
    @State private var chosen: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                if let failure { NoticeCard(words: failure) }
                ForEach(providers.filter { !$0.models.filter(\.tools).isEmpty }) { provider in
                    CardGroup(title: provider.provider) {
                        let options: [ModelOption] = provider.models.filter(\.tools)
                        ForEach(Array(options.enumerated()), id: \.element.id) { index, option in
                            if index > 0 { CardDivider(inset: Space.l) }
                            Button {
                                Task {
                                    do {
                                        try await choose(option.ref)
                                        chosen = option.ref
                                        failure = nil
                                        await reload()
                                    } catch {
                                        failure = "Your Mac refused that model. Pick another."
                                    }
                                }
                            } label: {
                                CardRow(icon: nil, title: option.label) {
                                    if option.ref == (chosen ?? current) {
                                        DotIcon(.check, size: 16, relativeTo: .footnote).foregroundStyle(Theme.text)
                                    }
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(Space.l)
        }
        .dotScreenBackground(inSheet: true)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .sensoryFeedback(.selection, trigger: chosen)
    }
}

/// Every schedule of Dot, with an on/off switch each.
struct SchedulesView: View {
    let store: NativeStore
    @State private var schedules: [ScheduleView] = []
    @State private var loading: Bool = true
    @State private var failure: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                if let failure { NoticeCard(words: failure) }
                if schedules.isEmpty && !loading {
                    DotEmptyState(
                        icon: .calendarClock, title: "No schedules",
                        message: "What Dot does on its own, at a time or on a loop, shows here with a switch each.")
                        .padding(.top, Space.xxl)
                } else {
                    CardGroup(
                        title: "Every schedule", footer: schedules.isEmpty ? nil : "A switch stops a schedule or starts it again; nothing is deleted."
                    ) {
                        ForEach(Array(schedules.enumerated()), id: \.element.id) { index, schedule in
                            if index > 0 { CardDivider() }
                            ScheduleRow(schedule: schedule, onChange: { on in toggle(schedule.id, on: on) })
                        }
                    }
                }
            }
            .padding(Space.l)
        }
        .dotScreenBackground(inSheet: true)
        .navigationTitle("Schedules")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        // What the disk held is on the screen already; a read that fails leaves it, with its line.
        if schedules.isEmpty { schedules = store.schedules }
        do {
            schedules = try await store.api.schedules()
            failure = nil
        } catch {
            failure = schedules.isEmpty ? "Could not read the schedules from your Mac. Is it awake?" : "Showing the schedules saved on this phone."
        }
        loading = false
    }

    private func toggle(_ id: String, on: Bool) {
        if let at: Int = schedules.firstIndex(where: { $0.id == id }) { schedules[at].record.enabled = on }
        Task {
            do { _ = try await store.api.setSchedule(id, on: on) } catch { failure = "Could not switch the schedule." }
            await load()
        }
    }
}

/// The licences of what the app is made from (Settings › About › Licences): the icons are Lucide's, the shapes the web app draws,
/// under the ISC licence, and those Lucide took from Feather under Feather's MIT licence. The text is the web app's own file
/// (`static/js/LICENSE-Lucide.txt`), copied into the app by `scripts/export_ios_icons.py`.
struct LicencesView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                CardGroup(footer: "Dot's icons are drawn from Lucide, as the web app's are.") {
                    NavigationLink {
                        LicenceTextView(title: "Lucide", resource: "LICENSE-Lucide")
                    } label: {
                        CardRow(icon: .shieldCheck, title: "Lucide", detail: "Icons · ISC licence", showsChevron: true)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("licence-lucide")
                }
            }
            .padding(Space.l)
        }
        .dotScreenBackground(inSheet: true)
        .navigationTitle("Licences")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// A licence's text, as the file carries it, to read and to select.
struct LicenceTextView: View {
    let title: String
    let resource: String

    var body: some View {
        ScrollView {
            Text(text)
                .font(Theme.mono).foregroundStyle(Theme.text).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Space.l)
        }
        .dotScreenBackground(inSheet: true)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var text: String {
        guard let url: URL = Bundle.main.url(forResource: resource, withExtension: "txt"),
            let words: String = try? String(contentsOf: url, encoding: .utf8)
        else { return "The licence text is missing from this build." }
        return words
    }
}
