import SwiftUI

/// A refusal or a failure said on the page where the tap was: a tinted card with its symbol.
struct NoticeCard: View {
    let words: String
    var tone: Color = Theme.bad
    var icon: IconName = .alert

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            DotIcon(icon, size: 15, relativeTo: .footnote).foregroundStyle(tone).padding(.top, 2)
            Text(words).font(Theme.small).foregroundStyle(Theme.text)
            Spacer(minLength: 0)
        }
        .padding(Space.m)
        .background(tone.opacity(0.12), in: RoundedRectangle(cornerRadius: Radius.m))
        .overlay(RoundedRectangle(cornerRadius: Radius.m).strokeBorder(tone.opacity(0.35), lineWidth: 1))
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
                            CardRow(icon: .history, tint: Theme.accentFill, title: "Schedules", detail: "What Dot does on its own, and when", showsChevron: true)
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
                                icon: .bell, tint: Color(red: 0.9, green: 0.32, blue: 0.3), title: "iPhone notification settings",
                                detail: "Banners, sounds and the badge", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                        CardDivider()
                        Button { changingAddress = true } label: {
                            CardRow(icon: .network, tint: Color(red: 0.2, green: 0.6, blue: 0.62), title: "Change address", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                        CardDivider()
                        Button {
                            model.forgetKey()
                            dismiss()
                        } label: {
                            CardRow(icon: .key, tint: Theme.bad, title: "Forget this phone's key")
                        }
                        .buttonStyle(.plain)
                    }
                    CardGroup(
                        footer: "The web UI is the full Dot on this Mac's address. This app is the phone's own front for chats, approvals, helpers and schedules."
                    ) {
                        Button {
                            dismiss()
                            model.showsWeb = true
                        } label: {
                            CardRow(icon: .globe, tint: Color(red: 0.35, green: 0.4, blue: 0.9), title: "Open web UI", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                    }
                    CardGroup(title: "About") {
                        NavigationLink(value: "licences") {
                            CardRow(icon: .shieldCheck, tint: Theme.muted, title: "Licences", detail: "What the app's icons are drawn from", showsChevron: true)
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
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .sheet(isPresented: $changingAddress) {
                AddressView(model: model, onSaved: { changingAddress = false; dismiss() }).dotSheet()
            }
        }
    }

    /// The Mac this phone is paired with, and whether it answers: the first thing the owner wants to know here.
    private var macCard: some View {
        let layout: AnyLayout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.m)) : AnyLayout(HStackLayout(spacing: Space.m))
        return layout {
            DotIcon(.laptop, size: 24, relativeTo: .title3)
                .foregroundStyle(connectionTone)
                .frame(width: 52, height: 52)
                .background(connectionTone.opacity(0.14), in: RoundedRectangle(cornerRadius: Radius.m))
            VStack(alignment: .leading, spacing: 3) {
                Text(store.host.name).font(.headline).foregroundStyle(Theme.text).lineLimit(1).truncationMode(.middle)
                StatusPill(text: connectionWords, icon: connectionIcon, tone: connectionTone, pulse: store.connection == .connecting || store.connection == .reconnecting)
            }
            if !typeSize.isAccessibilitySize { Spacer() }
        }
        .padding(Space.l)
        .card(radius: Radius.l)
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

    private var connectionIcon: IconName {
        switch store.connection {
        case .live: .circleCheck
        case .connecting, .reconnecting: .refresh
        case .unauthorized: .key
        }
    }

    private var connectionTone: Color {
        switch store.connection {
        case .live: Theme.good
        case .connecting, .reconnecting: Theme.accent
        case .unauthorized: Theme.warn
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
            do { providers = try await store.api.models() } catch { failure = "Could not read the models." }
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
                                        failure = "The daemon refused that model."
                                    }
                                }
                            } label: {
                                CardRow(icon: nil, title: option.label) {
                                    if option.ref == (chosen ?? current) {
                                        DotIcon(.check, size: 16, relativeTo: .footnote).foregroundStyle(Theme.accent)
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
            failure = schedules.isEmpty ? "Could not read the schedules." : "Showing the schedules saved on this phone."
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
                        CardRow(icon: .shieldCheck, tint: Theme.muted, title: "Lucide", detail: "Icons · ISC licence", showsChevron: true)
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
