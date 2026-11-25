import DotEngine
import SwiftUI

// Activity (item 1826, P1-gaps §8 under the phone scope of PLAN §26): what Dot did while the owner was away, the
// drafts that wait, the proposals of Self-improvement, what is in motion today, and the ways Dot comes to the owner (the
// morning brief, the evening check-in, its own initiative, the watchers), each on the daemon's own routes. The setup of
// those (times, the watchers' choices) is the Mac's. `ActivityScreen` stands alone (PLAN §27: Dot's profile opens it
// whole, an agent's profile scoped to its own work); `ActivityTab` is only its wrapper while the tab bar stays.

/// What the screens of Activity ask of the one that holds them: open a run's chat, open a chat, say something in the
/// toast.
struct ActivityActions {
    let openRun: (_ run: String, _ rootThread: String?) -> Void
    let openChat: (_ thread: String) -> Void
    let say: (_ words: String, _ symbol: String) -> Void
}

/// The tab's wrapper: a stack holding the whole screen (to be removed with the tab bar, PLAN §27).
struct ActivityTab: View {
    var body: some View {
        NavigationStack { ActivityScreen(agent: nil) }
    }
}

/// Activity as a screen of its own, pushed in any navigation stack. `agent` `nil` shows all of Dot's (Dot's profile);
/// an agent's id shows only that agent's runs and rows (its profile), without what belongs to Dot as a whole (the
/// drafts, the proposals, the brief, the check-in, its own initiative, the watchers).
struct ActivityScreen: View {
    let agent: String?
    @Environment(AppModel.self) private var model: AppModel
    @State private var openedChat: String?
    @State private var toast: ToastMessage?

    var body: some View {
        ActivityHome(agent: agent, actions: actions)
            .navigationDestination(item: $openedChat) { thread in ChatScreen(thread: thread, initialPanel: nil, editsFirstQueued: false) }
            // A failure's Diagnose opens This Mac here too (`FailureBanner`).
            .navigationDestination(for: SettingsPage.self) { page in
                if case .mac = page { MacPage() }
            }
            .toastHost($toast, seconds: 4) { _ in }
    }

    private var actions: ActivityActions {
        ActivityActions(
            openRun: { run, root in Task { await openRun(run, rootThread: root) } },
            openChat: { thread in openedChat = thread },
            say: { words, symbol in toast = ToastMessage(id: UUID(), text: words, systemImage: symbol, actionTitle: nil) })
    }

    private func openRun(_ run: String, rootThread: String?) async {
        switch await model.chat(ofRun: run, rootThread: rootThread) {
        case .success(let thread): openedChat = thread
        case .failure(let box):
            toast = ToastMessage(
                id: UUID(), text: failureWords(box.failure, macPlace: activityMacPlace), systemImage: "exclamationmark.triangle", actionTitle: nil)
        }
    }
}

/// Where the same things are on the Mac, for a refusal's continuation.
let activityMacPlace: String = String(localized: "the web app's Home and Settings › Scheduled tasks", comment: "Where on the Mac: the activity, the brief, the watchers")

/// Everything the tab reads, each part on its own route: one that fails leaves the others on screen.
struct ActivityPicture: Sendable {
    var activity: Loadable<ActivityView>
    /// `nil`: nothing happened while the owner was away (or they marked it read).
    var away: Loadable<AwayView?>
    var drafts: Loadable<[DraftView]>
    var harness: Loadable<HarnessView>
    var brief: Loadable<BriefView>
    var checkin: Loadable<CheckinView>
    var initiative: Loadable<InitiativeView>
    var watchers: Loadable<[WatcherView]>

    static let loading: ActivityPicture = ActivityPicture(
        activity: .loading, away: .loading, drafts: .loading, harness: .loading, brief: .loading, checkin: .loading, initiative: .loading,
        watchers: .loading)

    /// The first part that failed, for the one notice over the page.
    var failure: SettingsFailure? {
        for part in [activity.failure, away.failure, drafts.failure, harness.failure, brief.failure, checkin.failure, initiative.failure, watchers.failure] {
            if let part { return part }
        }
        return nil
    }

    /// Whether the parts every view of the screen reads are still on their way.
    var cold: Bool {
        if case .loading = activity, case .loading = away { return true }
        return false
    }

    /// `fresh` where it read, the parts of `self` kept where a read again failed (the screen keeps what it showed).
    func merged(with fresh: ActivityPicture) -> ActivityPicture {
        ActivityPicture(
            activity: activity.keeping(fresh.activity), away: away.keeping(fresh.away), drafts: drafts.keeping(fresh.drafts),
            harness: harness.keeping(fresh.harness), brief: brief.keeping(fresh.brief), checkin: checkin.keeping(fresh.checkin),
            initiative: initiative.keeping(fresh.initiative), watchers: watchers.keeping(fresh.watchers))
    }
}

extension Loadable {
    var failure: SettingsFailure? {
        if case .failed(let failure) = self { return failure }
        return nil
    }

    /// `fresh`, unless it failed where `self` holds a value: that value stays.
    func keeping(_ fresh: Loadable<Value>) -> Loadable<Value> {
        if case .failed = fresh, case .loaded = self { return self }
        return fresh
    }
}

/// How many drafts and versions the tab reads at most.
private let draftsRead: Int = 50
private let versionsRead: Int = 100
/// How often the tab reads again while it is on screen, as the web's panels do.
private let refreshSeconds: Double = 30

struct ActivityHome: View {
    /// `nil`: all of Dot's; an agent's id: only its own work.
    let agent: String?
    let actions: ActivityActions
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var picture: ActivityPicture = .loading
    /// The failure of the last read or write, over the page.
    @State private var problem: SettingsFailure?
    @State private var showsAllAway: Bool = false
    @State private var draft: DraftView?
    @State private var proposal: HarnessVersion?
    @State private var briefActions: Bool = false
    @State private var checkinActions: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                if picture.cold {
                    LoadingState(message: String(localized: "Reading your Mac…", comment: "Settings: a page waits for its first read")).frame(minHeight: 240)
                } else {
                    if let problem { FailureBanner(failure: problem, macPlace: activityMacPlace, retry: { Task { await load() } }) }
                    content
                }
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.groupedGutter)
            .readableColumn()
        }
        .background(Tone.ground)
        .navigationTitle(String(localized: "Activity", comment: "Title of the activity tab"))
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await keepReading() }
        .sheet(item: $draft) { shown in
            DraftSheet(draft: shown, actions: actions) { settled in
                picture.drafts = picture.drafts.value.map { all in .loaded(all.filter { $0.id != settled }) } ?? picture.drafts
            }
        }
        .sheet(item: $proposal) { shown in
            ProposalSheet(version: shown, outside: picture.harness.value?.fromOutside[shown.id] ?? [], refusal: picture.harness.value?.refused[shown.id]) {
                words in
                actions.say(words, "checkmark")
                Task { await load() }
            }
        }
    }

    @ViewBuilder private var content: some View {
        if case .loaded(let whole?) = picture.away {
            let away: AwayView = agent.map { scoped(whole, to: $0) } ?? whole
            if !awayLine(away).isEmpty {
                AwayCard(view: away, showsAll: $showsAllAway, open: { actions.openRun($0, nil) }, markRead: agent == nil ? { await markRead(whole) } : nil)
            }
        }
        if agent == nil {
            if let drafts = picture.drafts.value, !drafts.isEmpty { readySection(drafts) }
            if let harness = picture.harness.value, !harness.proposals.isEmpty { proposalsSection(harness) }
        }
        if let whole = picture.activity.value { activitySections(agent.map { scoped(whole, to: $0) } ?? whole) }
        if agent == nil {
            comesToYouSection
            watchSection
        }
    }

    // MARK: Ready

    private func readySection(_ drafts: [DraftView]) -> some View {
        GroupedSection(
            header: String(localized: "Ready", comment: "Activity: the drafts that wait for the owner"),
            footer: String(localized: "Nothing goes out until you send it.", comment: "Activity: what a draft does until the owner sends it")
        ) {
            ForEach(drafts) { shown in
                ActivityItem(
                    symbol: shown.channel == "message" ? "message" : "envelope", title: draftTitle(shown), line: draftLine(shown),
                    tone: shown.status == "failed" ? .failure : .neutral, opens: true
                ) { draft = shown }
            }
        }
    }

    // MARK: Proposals

    private func proposalsSection(_ harness: HarnessView) -> some View {
        GroupedSection(
            header: String(localized: "Dot's proposals", comment: "Activity: the versions of habits Self-improvement proposed"),
            footer: String(
                localized: "Dot learns from its own tasks how to work better on this Mac; you choose the habits and tips it follows.",
                comment: "Activity: what the proposals of Self-improvement are")
        ) {
            ForEach(harness.proposals) { version in
                ActivityItem(
                    symbol: "sparkles", title: version.summary,
                    line: proposalLine(version, outside: harness.fromOutside[version.id] != nil, refused: harness.refused[version.id] != nil, locale: locale),
                    tone: harness.refused[version.id] != nil ? .failure : harness.fromOutside[version.id] != nil ? .warn : .neutral, opens: true
                ) { proposal = version }
            }
        }
    }

    // MARK: In motion

    @ViewBuilder private func activitySections(_ view: ActivityView) -> some View {
        if view.isEmpty {
            GroupedSection(header: String(localized: "Today", comment: "Activity: the section of today's runs"), footer: nil) {
                ListRow(
                    systemImage: "clock.arrow.circlepath", title: String(localized: "Nothing in motion today", comment: "Activity: no run works, waits, ended or is due today"),
                    description: String(localized: "What Dot does on its own shows here.", comment: "Activity: what fills the list"), accessory: .none)
            }
        }
        rows(String(localized: "Needs you", comment: "Chat list section: chats that wait for the owner"), view.waiting, tone: .warn)
        rows(String(localized: "Working", comment: "Chat list section: chats with a run at work"), view.working + view.endedDelegated, tone: .neutral)
        rows(String(localized: "Later today", comment: "Activity: the schedules and campaign sessions due before the day ends"), view.due, tone: .neutral)
        rows(String(localized: "Earlier today", comment: "Activity: the runs that ended today"), view.recent, tone: .neutral)
        rows(String(localized: "Learned today", comment: "Activity: the lessons and the journal written today"), view.learnedToday, tone: .neutral)
    }

    @ViewBuilder private func rows(_ header: String, _ shown: [ActivityRow], tone: StatusTone) -> some View {
        if !shown.isEmpty {
            GroupedSection(header: header, footer: nil) {
                ForEach(shown) { row in
                    let run: String? = runOpened(by: row)
                    ActivityItem(
                        symbol: activitySymbol(row), title: row.title, line: activityLine(row, now: .now, locale: locale),
                        tone: row.status == "failed" ? .failure : tone, opens: run != nil
                    ) {
                        if let run { actions.openRun(run, row.rootThreadId) }
                    }
                }
            }
        }
    }

    // MARK: Dot comes to you

    private var comesToYouSection: some View {
        GroupedSection(
            header: String(localized: "Dot comes to you", comment: "Activity: the brief, the check-in, its own initiative and the watchers"),
            footer: String(localized: "Their times and choices are set on your Mac.", comment: "Activity: where the brief's time and the watchers' choices are set")
        ) {
            if let brief = picture.brief.value {
                SwitchItem(
                    symbol: "sun.max", title: String(localized: "Morning brief", comment: "Activity: the brief Dot writes each morning"),
                    line: [briefLine(brief), brief.enabled ? brief.nextLocal.map { String(localized: "Next \($0)", comment: "Brief: when it is written next") } : nil]
                        .compactMap { $0 }.joined(separator: " · "),
                    isOn: Binding(get: { brief.enabled }, set: { on in Task { await setBrief(brief, on) } }),
                    tap: brief.scheduleId != nil || brief.lastRunId != nil ? { briefActions = true } : nil
                )
                .confirmationDialog(String(localized: "Morning brief", comment: "Activity: the brief Dot writes each morning"), isPresented: $briefActions) {
                    if let schedule = brief.scheduleId {
                        Button(String(localized: "Write one now", comment: "Brief: writes a brief at once")) { Task { await writeBriefNow(schedule) } }
                    }
                    if let last = brief.lastRunId {
                        Button(String(localized: "Open the last brief", comment: "Brief: opens the chat of the last one")) { actions.openRun(last, nil) }
                    }
                }
            }
            if let checkin = picture.checkin.value {
                SwitchItem(
                    symbol: "moon", title: String(localized: "Evening check-in", comment: "Activity: what Dot writes at the end of the day"), line: checkinLine(checkin),
                    isOn: Binding(get: { checkin.enabled }, set: { on in Task { await setCheckin(checkin, on) } }),
                    tap: checkin.lastRunId != nil ? { checkinActions = true } : nil
                )
                .confirmationDialog(String(localized: "Evening check-in", comment: "Activity: what Dot writes at the end of the day"), isPresented: $checkinActions) {
                    if let last = checkin.lastRunId {
                        Button(String(localized: "Open the last check-in", comment: "Check-in: opens the chat of the last one")) { actions.openRun(last, nil) }
                    }
                }
            }
            if let initiative = picture.initiative.value {
                NavigationLink {
                    InitiativePage(actions: actions)
                } label: {
                    ListRow(
                        systemImage: "wand.and.stars", title: String(localized: "On its own", comment: "Activity: Dot's own initiative while the Mac is idle"),
                        description: initiativeLine(initiative), accessory: .chevron)
                }
                .buttonStyle(RowPressStyle())
            }
            if let watchers = picture.watchers.value {
                NavigationLink {
                    WatchersPage(actions: actions)
                } label: {
                    ListRow(
                        systemImage: "eye", title: String(localized: "Watchers", comment: "Activity: what Dot keeps an eye on for the owner"),
                        description: watchersLine(watchers), accessory: .chevron)
                }
                .buttonStyle(RowPressStyle())
            }
        }
    }

    private func watchersLine(_ watchers: [WatcherView]) -> String {
        if watchers.isEmpty { return String(localized: "None yet: make one on your Mac", comment: "Activity: no watcher; they are made on the Mac") }
        let on: Int = watchers.filter(\.enabled).count
        return String(localized: "\(on) of \(watchers.count) on", comment: "Activity: how many watchers are switched on, of all")
    }

    private var watchSection: some View {
        GroupedSection(header: nil, footer: String(localized: "Opens the Mac's live screen; nothing plays until you open it.", comment: "Activity tab: footer under Watch the Mac")) {
            NavigationLink {
                WebSettingsPage(target: WebSettingsPage.Target(tab: "/watch", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen")))
            } label: {
                ListRow(systemImage: "desktopcomputer", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"), description: nil, accessory: .web)
            }
            .buttonStyle(RowPressStyle())
        }
    }

    // MARK: Reads and writes

    /// Reads everything again while the tab is on screen (the web's panels read every 30 s or on a lifecycle event).
    private func keepReading() async {
        while !Task.isCancelled {
            await load()
            do {
                try await Task.sleep(for: .seconds(refreshSeconds))
            } catch is CancellationError {
                return
            } catch {
                preconditionFailure("Task.sleep throws only CancellationError: \(error)")
            }
        }
    }

    private func load() async {
        if agent != nil {
            async let activity = model.read { api throws(DaemonRequestError) in try await api.activity() }
            async let away = readAwayUnmarked()
            var fresh: ActivityPicture = picture
            fresh.activity = await activity
            fresh.away = await away
            picture = picture.merged(with: fresh)
            problem = fresh.failure
            return
        }
        async let activity = model.read { api throws(DaemonRequestError) in try await api.activity() }
        async let away = readAway()
        async let drafts = model.read { api throws(DaemonRequestError) in try await api.waitingDrafts(limit: draftsRead) }
        async let harness = model.read { api throws(DaemonRequestError) in try await api.harness(limit: versionsRead) }
        async let brief = model.read { api throws(DaemonRequestError) in try await api.brief() }
        async let checkin = model.read { api throws(DaemonRequestError) in try await api.checkin() }
        async let initiative = model.read { api throws(DaemonRequestError) in try await api.initiative() }
        async let watchers = model.read { api throws(DaemonRequestError) in try await api.watchers() }
        let fresh: ActivityPicture = ActivityPicture(
            activity: await activity, away: await away, drafts: await drafts, harness: await harness, brief: await brief, checkin: await checkin,
            initiative: await initiative, watchers: await watchers)
        picture = picture.merged(with: fresh)
        problem = fresh.failure
    }

    private func readAway() async -> Loadable<AwayView?> {
        switch await model.awayMoment() {
        case .loaded(let since):
            switch await model.read({ api throws(DaemonRequestError) in try await api.away(since: since) }) {
            case .loaded(let view): return .loaded(view)
            case .loading: return .loading
            case .failed(let failure): return .failed(failure)
            }
        case .loading: return .loading
        case .failed(let failure): return .failed(failure)
        }
    }

    /// "While you were away" from the moment a device marked, or nothing before any did: it never marks the moment.
    private func readAwayUnmarked() async -> Loadable<AwayView?> {
        switch await model.awayMarked() {
        case .loaded(nil): return .loaded(nil)
        case .loaded(let since?):
            switch await model.read({ api throws(DaemonRequestError) in try await api.away(since: since) }) {
            case .loaded(let view): return .loaded(view)
            case .loading: return .loading
            case .failed(let failure): return .failed(failure)
            }
        case .loading: return .loading
        case .failed(let failure): return .failed(failure)
        }
    }

    private func markRead(_ away: AwayView) async {
        switch await model.write({ api throws(DaemonRequestError) in try await api.markAwayRead(until: away.until) }) {
        case .success:
            picture.away = .loaded(nil)
            showsAllAway = false
        case .failure(let box): problem = box.failure
        }
    }

    private func setBrief(_ brief: BriefView, _ on: Bool) async {
        picture.brief = .loaded(brief.switched(on))
        let change: BriefChange = BriefChange(enabled: on, time: brief.time, days: brief.days, lines: brief.lines)
        switch await model.write({ api throws(DaemonRequestError) in try await api.changeBrief(change) }) {
        case .success(let saved):
            picture.brief = .loaded(saved)
            actions.say(
                saved.enabled
                    ? String(localized: "Morning brief on, \(daysAtWords(days: saved.days, time: saved.time)).", comment: "Brief: switched on; the argument says when")
                    : String(localized: "Morning brief switched off.", comment: "Brief: switched off"), "sun.max")
        case .failure(let box):
            picture.brief = .loaded(brief)
            problem = box.failure
        }
    }

    private func writeBriefNow(_ schedule: String) async {
        switch await model.write({ api throws(DaemonRequestError) in try await api.runScheduleNow(schedule) }) {
        case .success(let run):
            actions.say(String(localized: "Writing a brief now.", comment: "Brief: a brief is being written at once"), "sun.max")
            actions.openChat(run.threadId)
        case .failure(let box): problem = box.failure
        }
    }

    private func setCheckin(_ checkin: CheckinView, _ on: Bool) async {
        picture.checkin = .loaded(checkin.switched(on))
        let change: CheckinChange = CheckinChange(enabled: on, time: checkin.time, days: checkin.days)
        switch await model.write({ api throws(DaemonRequestError) in try await api.changeCheckin(change) }) {
        case .success(let saved):
            picture.checkin = .loaded(saved)
            actions.say(
                saved.enabled
                    ? String(localized: "Evening check-in on, \(daysAtWords(days: saved.days, time: saved.time)).", comment: "Check-in: switched on; the argument says when")
                    : String(localized: "Evening check-in switched off.", comment: "Check-in: switched off"), "moon")
        case .failure(let box):
            picture.checkin = .loaded(checkin)
            problem = box.failure
        }
    }
}

extension BriefView {
    /// The brief as the switch shows it while its write is on the way.
    func switched(_ on: Bool) -> BriefView {
        BriefView(
            enabled: on, time: time, days: days, custom: custom, cron: cron, lines: lines, zone: zone, scheduleId: scheduleId, nextLocal: nextLocal,
            lastLocal: lastLocal, lastStatus: lastStatus, lastRunId: lastRunId, accounts: accounts)
    }
}

extension CheckinView {
    func switched(_ on: Bool) -> CheckinView {
        CheckinView(enabled: on, time: time, days: days, zone: zone, scheduleId: scheduleId, nextLocal: nextLocal, lastLocal: lastLocal, lastRunId: lastRunId)
    }
}

// MARK: Rows

/// A row of the tab: a mark, a title of up to two lines, a line under it, a chevron when it opens something. The mark
/// and the line take amber for what waits on the owner and red for a failure (D5).
struct ActivityItem: View {
    let symbol: String
    let title: String
    let line: String
    let tone: StatusTone
    let opens: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: Space.s) {
                Symbol(name: symbol, context: .row).foregroundStyle(tone == .neutral ? Tone.text : tone.color).frame(minWidth: Metric.iconSlot)
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(title).textRole(.body).foregroundStyle(Tone.text).lineLimit(2).multilineTextAlignment(.leading)
                    Text(line).textRole(.footnote).foregroundStyle(tone == .neutral ? Tone.secondary : tone.color).multilineTextAlignment(.leading)
                }
                Spacer(minLength: Space.s)
                if opens { Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph).frame(maxHeight: .infinity) }
            }
            .padding(.horizontal, Space.groupedGutter)
            .padding(.vertical, Space.s)
            .frame(minHeight: Metric.rowWithDescription)
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
        .disabled(!opens)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(verbatim: "\(title), \(line)"))
        .accessibilityAddTraits(opens ? .isButton : [])
    }
}

/// A row with a switch at its trailing edge; tapping the rest of the row offers its actions when it has some.
struct SwitchItem: View {
    let symbol: String
    let title: String
    let line: String
    @Binding var isOn: Bool
    let tap: (() -> Void)?

    var body: some View {
        HStack(spacing: Space.s) {
            Button { tap?() } label: {
                HStack(spacing: Space.s) {
                    Symbol(name: symbol, context: .row).foregroundStyle(Tone.text).frame(minWidth: Metric.iconSlot)
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        Text(title).textRole(.body).foregroundStyle(Tone.text)
                        Text(line).textRole(.footnote).foregroundStyle(Tone.secondary).multilineTextAlignment(.leading)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // Not `disabled`: a row with nothing to offer keeps its full ink, it only takes no tap.
            .allowsHitTesting(tap != nil)
            .accessibilityLabel(Text(verbatim: "\(title), \(line)"))
            // The style draws its label itself: an empty one, the row's title being VoiceOver's.
            Toggle(isOn: $isOn) { EmptyView() }.toggleStyle(InkSwitchStyle()).fixedSize()
                .accessibilityLabel(title)
        }
        .padding(.leading, Space.groupedGutter)
        .padding(.trailing, Space.s)
        .padding(.vertical, Space.xs)
        .frame(minHeight: Metric.rowWithDescription)
    }
}
