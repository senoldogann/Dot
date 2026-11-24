import SwiftUI

/// Settings of the native app: models, schedules, notifications, the daemon's address and the phone's key, and the web
/// UI as a way out.
struct NativeSettingsView: View {
    let model: AppModel
    let store: NativeStore
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var changingAddress: Bool = false
    @State private var path: [String] = []

    var body: some View {
        NavigationStack(path: $path) {
            List {
                Section {
                    Group {
                        NavigationLink("Models", value: "models")
                        NavigationLink("Schedules", value: "schedules")
                    }
                    .listRowBackground(Theme.surface)
                }
                Section("Phone") {
                    Group {
                        LabeledContent("Mac") { Text(store.host.name).font(Theme.small).foregroundStyle(Theme.muted) }
                        LabeledContent("Connection") { Text(connectionWords).font(Theme.small).foregroundStyle(Theme.muted) }
                        Button("Notifications") {
                            // The iPhone's own page of this app's settings, where its notifications are switched.
                            if let url: URL = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                        Button("Change address") { changingAddress = true }
                        Button("Forget this phone's key", role: .destructive) {
                            model.forgetKey()
                            dismiss()
                        }
                    }
                    .listRowBackground(Theme.surface)
                }
                Section {
                    Group {
                        Button("Open web UI") {
                            dismiss()
                            model.showsWeb = true
                        }
                    }
                    .listRowBackground(Theme.surface)
                } footer: {
                    Text("The web UI is the full Dot on this Mac's address. This app is the phone's own front for chats, approvals, helpers and schedules.")
                        .font(Theme.meta)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: String.self) { page in
                if page == "models" { ModelsView(store: store) } else { SchedulesView(store: store) }
            }
            .onAppear { if model.debugSchedules { path = ["schedules"] } }
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .sheet(isPresented: $changingAddress) {
                AddressView(model: model, onSaved: { changingAddress = false; dismiss() }).tint(Theme.accent).preferredColorScheme(.dark).presentationBackground(Theme.background)
            }
        }
    }

    private var connectionWords: String {
        switch store.connection {
        case .live: "Connected"
        case .connecting: "Connecting"
        case .reconnecting: "Reconnecting"
        case .unauthorized: "Key refused"
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
        List {
            if let failure { Text(failure).font(Theme.small).foregroundStyle(Theme.bad) }
            Section {
                Group {
                    picker("Main model", current: store.models?.main) { ref in
                        try await store.api.chooseMain(model: ref)
                    }
                    picker("Review model", current: store.models?.review) { ref in
                        try await store.api.chooseRole("review", model: ref)
                    }
                }
                .listRowBackground(Theme.surface)
            } footer: {
                Text("The main model answers new messages. The review model checks runs and draws lessons.").font(Theme.meta)
            }
            if loading && providers.isEmpty { ProgressView().frame(maxWidth: .infinity).listRowBackground(Color.clear) }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.background)
        .navigationTitle("Models")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await store.loadModels()
            do { providers = try await store.api.models() } catch { failure = "Could not read the models." }
            loading = false
        }
    }

    private func picker(_ title: String, current: String?, choose: @escaping (String) async throws -> Void) -> some View {
        NavigationLink {
            ModelChoiceList(title: title, providers: providers, current: current, choose: choose, reload: { await store.loadModels() })
        } label: {
            LabeledContent(title) { Text(label(of: current)).font(Theme.small).foregroundStyle(Theme.muted) }
        }
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
        List {
            if let failure { Text(failure).font(Theme.small).foregroundStyle(Theme.bad) }
            ForEach(providers.filter { !$0.models.filter(\.tools).isEmpty }) { provider in
                Section(provider.provider) {
                    Group {
                        ForEach(provider.models.filter(\.tools)) { option in
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
                                HStack {
                                    Text(option.label).font(Theme.small).foregroundStyle(Theme.text)
                                    Spacer()
                                    if option.ref == (chosen ?? current) { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
                                }
                            }
                        }
                    }
                    .listRowBackground(Theme.surface)
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.background)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// Every schedule of Dot, with an on/off switch each.
struct SchedulesView: View {
    let store: NativeStore
    @State private var schedules: [ScheduleView] = []
    @State private var loading: Bool = true
    @State private var failure: String?

    var body: some View {
        List {
            if let failure { Text(failure).font(Theme.small).foregroundStyle(Theme.bad) }
            ForEach(schedules) { schedule in
                ScheduleRow(schedule: schedule, onChange: { on in toggle(schedule.id, on: on) })
            }
            if schedules.isEmpty && !loading {
                Text("No schedules.").font(Theme.small).foregroundStyle(Theme.faint)
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.background)
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
