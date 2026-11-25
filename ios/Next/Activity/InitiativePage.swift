import DotEngine
import SwiftUI

/// Dot's own initiative (item 715, the web's `initiativecard.js`): the switch, the mode its tasks start in, and the
/// list of what Dot means to do on its own while the Mac is idle: a thing to do can be taken off, one let go put back,
/// one being done or done opens its chat, and the owner adds one at the foot. Which model looks for work is chosen on
/// the Mac (Settings › Models).
struct InitiativePage: View {
    let actions: ActivityActions
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<InitiativeView> = .loading
    @State private var problem: SettingsFailure?
    @State private var busy: Bool = false
    @State private var newThing: String = ""
    @FocusState private var typing: Bool

    /// The fewest characters a thing of the list has (the daemon's `TodoRequest`).
    private let fewestCharacters: Int = 3
    private let mostCharacters: Int = 200

    var body: some View {
        SettingsScroll(title: String(localized: "On its own", comment: "Activity: Dot's own initiative while the Mac is idle")) {
            if let problem { FailureBanner(failure: problem, macPlace: activityMacPlace, retry: nil) }
            LoadedPage(state: state, macPlace: activityMacPlace, retry: { Task { await load() } }) { view in
                GroupedSection(header: nil, footer: lead(view)) {
                    ToggleRow(
                        systemImage: "wand.and.stars", title: String(localized: "Work on its own while the Mac is idle", comment: "Own initiative: the switch"),
                        description: initiativeLine(view), isOn: Binding(get: { view.enabled }, set: { on in Task { await setEnabled(view, on) } })
                    )
                    .disabled(busy)
                }
                if view.enabled && !view.idleReadable {
                    Notice(
                        tone: .warn,
                        message: String(
                            localized: "macOS does not say how long the Mac has been idle, so Dot starts nothing on its own. The daemon's log on the Mac has why.",
                            comment: "Own initiative: the Mac's idle time cannot be read"),
                        dismiss: nil)
                }
                list(view)
                addSection
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func lead(_ view: InitiativeView) -> String {
        var parts: [String] = [
            String(
                localized: "While the Mac is idle, Dot does on its own what is on this list, each in a chat of its own; with nothing on it, it looks for work that helps you now and then.",
                comment: "Own initiative: what the switch does"),
            initiativeModeWords(view.approvals),
        ]
        if view.peakHoursUtc.count == 2 {
            parts.append(
                String(
                    localized: "On weekdays it starts nothing from \(localHour(view.peakHoursUtc[0])) to \(localHour(view.peakHoursUtc[1])), the provider's dearer hours, but a thing marked [URGENT].",
                    comment: "Own initiative: the weekday hours its tasks wait in, in the phone's time"))
        }
        return parts.joined(separator: " ")
    }

    /// An hour of the day in UTC as this phone's clock shows it today.
    private func localHour(_ utcHour: Int) -> String {
        var calendar: Calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC") ?? .current
        let moment: Date = calendar.date(bySettingHour: utcHour, minute: 0, second: 0, of: .now) ?? .now
        return moment.formatted(.dateTime.hour().minute())
    }

    @ViewBuilder private func list(_ view: InitiativeView) -> some View {
        GroupedSection(header: String(localized: "Its list", comment: "Own initiative: the list of what Dot means to do"), footer: nil) {
            if view.todos.isEmpty {
                ListRow(systemImage: nil, title: String(localized: "Nothing on the list yet.", comment: "Own initiative: the list is empty"), description: nil, accessory: .none)
            }
            ForEach(view.todos) { todo in
                HStack(spacing: 0) {
                    ActivityItem(
                        symbol: todoStateWords(todo.status).symbol, title: todo.title, line: todoLine(todo), tone: .neutral, opens: todo.runId != nil
                    ) {
                        if let run = todo.runId { actions.openRun(run, nil) }
                    }
                    if todo.status == "open" {
                        Button(String(localized: "Remove", comment: "Own initiative: takes a thing off the list")) { Task { await change(todo, drop: true) } }
                            .buttonStyle(.borderless).foregroundStyle(Tone.text).textRole(.footnote).fontWeight(.medium)
                            .frame(minWidth: Metric.hit, minHeight: Metric.hit).padding(.trailing, Space.groupedGutter)
                            .disabled(busy)
                            .accessibilityLabel(String(localized: "Take \(todo.title) off the list", comment: "VoiceOver: removes a thing from Dot's list"))
                    } else if todo.status == "dropped" {
                        Button(String(localized: "Again", comment: "Own initiative: puts a thing that was let go back on the list")) { Task { await change(todo, drop: false) } }
                            .buttonStyle(.borderless).foregroundStyle(Tone.text).textRole(.footnote).fontWeight(.medium)
                            .frame(minWidth: Metric.hit, minHeight: Metric.hit).padding(.trailing, Space.groupedGutter)
                            .disabled(busy)
                            .accessibilityLabel(String(localized: "Put \(todo.title) back on the list", comment: "VoiceOver: puts a thing back on Dot's list"))
                    }
                }
            }
        }
    }

    private var addSection: some View {
        GroupedSection(
            header: String(localized: "Add to the list", comment: "Own initiative: the field of a new thing"),
            footer: String(
                localized: "What Dot finds in a mail or a page while at it is handled as untrusted: what would reach others asks you first.",
                comment: "Own initiative: how content others wrote is handled")
        ) {
            HStack(spacing: Space.s) {
                TextField(String(localized: "Something Dot can do for you on its own", comment: "Own initiative: the placeholder of a new thing"), text: $newThing, axis: .vertical)
                    .lineLimit(1...3).textRole(.body).focused($typing).submitLabel(.done)
                    .onChange(of: newThing) { _, text in if text.count > mostCharacters { newThing = String(text.prefix(mostCharacters)) } }
                Button(String(localized: "Add", comment: "Own initiative: adds a thing to the list")) { Task { await add() } }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(busy || newThing.trimmingCharacters(in: .whitespacesAndNewlines).count < fewestCharacters)
            }
            .padding(.leading, Space.groupedGutter).padding(.trailing, Space.s).padding(.vertical, Space.s)
        }
    }

    private func load() async {
        let fresh: Loadable<InitiativeView> = await model.read { api throws(DaemonRequestError) in try await api.initiative() }
        state = state.keeping(fresh)
        problem = fresh.failure.flatMap { state.value == nil ? nil : $0 }
    }

    /// Sends a change whose answer is the list as it stands, and shows it.
    private func apply(_ said: String, _ call: (DaemonAPI) async throws(DaemonRequestError) -> InitiativeView) async -> Bool {
        busy = true
        defer { busy = false }
        switch await model.write(call) {
        case .success(let view):
            state = .loaded(view)
            problem = nil
            actions.say(said, "checkmark")
            return true
        case .failure(let box):
            problem = box.failure
            return false
        }
    }

    private func setEnabled(_ view: InitiativeView, _ on: Bool) async {
        _ = await apply(
            on
                ? String(localized: "Dot works on its own while the Mac is idle.", comment: "Own initiative: switched on")
                : String(localized: "Dot no longer works on its own.", comment: "Own initiative: switched off")
        ) { api throws(DaemonRequestError) in try await api.setInitiative(enabled: on) }
    }

    private func change(_ todo: InitiativeTodo, drop: Bool) async {
        if drop {
            _ = await apply(String(localized: "Taken off the list.", comment: "Own initiative: a thing was removed")) { api throws(DaemonRequestError) in
                try await api.dropTodo(todo.id)
            }
        } else {
            _ = await apply(String(localized: "Back on the list.", comment: "Own initiative: a thing was put back")) { api throws(DaemonRequestError) in
                try await api.todoAgain(todo.id)
            }
        }
    }

    private func add() async {
        let title: String = newThing.trimmingCharacters(in: .whitespacesAndNewlines)
        guard title.count >= fewestCharacters else { return }
        if await apply(String(localized: "On Dot's list.", comment: "Own initiative: a thing was added"), { api throws(DaemonRequestError) in try await api.addTodo(title: title) }) {
            newThing = ""
            typing = false
        }
    }
}

/// The watchers (item 571, the web's `watcherspanel.js`), read: what Dot keeps an eye on for the owner (new mail, an
/// event about to start, GitHub notifications), how each one's looks go, and its switch. Making and changing one is
/// done on the Mac.
struct WatchersPage: View {
    let actions: ActivityActions
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<[WatcherView]> = .loading
    @State private var problem: SettingsFailure?
    @State private var busy: Set<String> = []

    var body: some View {
        SettingsScroll(title: String(localized: "Watchers", comment: "Activity: what Dot keeps an eye on for the owner")) {
            if let problem { FailureBanner(failure: problem, macPlace: activityMacPlace, retry: nil) }
            LoadedPage(state: state, macPlace: activityMacPlace, retry: { Task { await load() } }) { watchers in
                if watchers.isEmpty {
                    EmptyState(
                        title: String(localized: "No watchers", comment: "Watchers: none"), systemImage: "eye",
                        detail: String(
                            localized: "On your Mac, Settings › Scheduled tasks › Watchers makes one: Dot then tells you when a mail, an event or a GitHub notification you care about comes.",
                            comment: "Watchers: where one is made"),
                        actionTitle: nil, action: {})
                } else {
                    GroupedSection(
                        header: nil,
                        footer: String(
                            localized: "Dot tells you on your phone and in the chat Watchers. Their choices are changed on your Mac.",
                            comment: "Watchers: how Dot tells, and where they are changed")
                    ) {
                        ForEach(watchers) { watcher in row(watcher) }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func row(_ watcher: WatcherView) -> some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            ToggleRow(
                systemImage: watcherKindWords(watcher.kind).symbol, title: watcher.name, description: watcherLine(watcher, now: .now, locale: locale),
                isOn: Binding(get: { watcher.enabled }, set: { on in Task { await setEnabled(watcher, on) } })
            )
            .disabled(busy.contains(watcher.id))
            if !watcher.words.isEmpty {
                Text(watcher.words).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(3)
                    .padding(.leading, Space.groupedGutter + Metric.iconSlot + Space.s).padding(.trailing, Space.groupedGutter)
            }
            if let error = watcher.error {
                Notice(
                    tone: .failure,
                    message: watcher.lookedAt == nil
                        ? String(localized: "It has not looked yet: \(error)", comment: "Watcher: why its first look failed")
                        : String(localized: "Its last look failed: \(error)", comment: "Watcher: why its last look failed"),
                    dismiss: nil
                )
                .padding(.horizontal, Space.groupedGutter)
            }
        }
        .padding(.bottom, Space.xs)
    }

    private func load() async {
        let fresh: Loadable<[WatcherView]> = await model.read { api throws(DaemonRequestError) in try await api.watchers() }
        state = state.keeping(fresh)
        problem = fresh.failure.flatMap { state.value == nil ? nil : $0 }
    }

    private func setEnabled(_ watcher: WatcherView, _ on: Bool) async {
        busy.insert(watcher.id)
        defer { busy.remove(watcher.id) }
        switch await model.write({ api throws(DaemonRequestError) in try await api.changeWatcher(watcher.id, watcher.request(enabled: on)) }) {
        case .success(let saved):
            if var all = state.value, let index = all.firstIndex(where: { $0.id == saved.id }) {
                all[index] = saved
                state = .loaded(all)
            }
            problem = nil
            actions.say(
                on
                    ? String(localized: "\(watcher.name) is on.", comment: "Watchers: one was switched on")
                    : String(localized: "\(watcher.name) is off.", comment: "Watchers: one was switched off"), "eye")
        case .failure(let box): problem = box.failure
        }
    }
}
