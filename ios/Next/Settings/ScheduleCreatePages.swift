import DotEngine
import SwiftUI

// MARK: New scheduled task

/// How often a new schedule runs, as the form's picker offers it (the web form's presets, a single time, or a cron form).
enum RhythmKind: String, CaseIterable, Identifiable, Sendable {
    case daily
    case weekdays
    case weekly
    case hourly
    case once
    case custom

    var id: String { rawValue }

    var title: String {
        switch self {
        case .daily: String(localized: "Every day", comment: "New schedule: runs every day")
        case .weekdays: String(localized: "Weekdays", comment: "New schedule: runs Monday to Friday")
        case .weekly: String(localized: "Every week", comment: "New schedule: runs on one weekday")
        case .hourly: String(localized: "Every hour", comment: "Schedule rhythm")
        case .once: String(localized: "Once", comment: "New schedule: runs a single time")
        case .custom: String(localized: "Custom (cron)", comment: "New schedule: a cron form typed by hand")
        }
    }
}

/// The rhythm the form stands for (pure): the clock time is read in `calendar`, the phone's own.
func scheduleRhythm(kind: RhythmKind, time: Date, weekday: Int, day: Date, cron: String, calendar: Calendar) -> ScheduleRhythm {
    let parts: DateComponents = calendar.dateComponents([.hour, .minute], from: time)
    let hour: Int = parts.hour ?? 9
    let minute: Int = parts.minute ?? 0
    switch kind {
    case .daily: return .daily(hour: hour, minute: minute)
    case .weekdays: return .weekdays(hour: hour, minute: minute)
    case .weekly: return .weekly(weekday: weekday, hour: hour, minute: minute)
    case .hourly: return .hourly
    case .once: return .once(day)
    case .custom: return .custom(cron)
    }
}

/// Settings › Scheduled tasks › +: a new schedule, as the Mac's form makes one, within what a phone is for: its name and
/// what Dot does, when (every day, weekdays, one weekday, every hour, once, or a cron form), the agent it runs as, task
/// or research, the model and its reasoning, the approvals and goal mode. Each choice left on Default takes the Mac's
/// configured one (or the agent's) when it fires; the time zone, history, chat and notification choices stay on the
/// Mac's form.
struct ScheduleCreateSheet: View {
    /// The agent a bot's Routines makes it for; `nil`: Dot.
    let agentId: String?
    let save: (ScheduleCreate) async -> SettingsFailure?
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @Environment(\.locale) private var locale: Locale
    @State private var name: String = ""
    @State private var goal: String = ""
    @State private var kind: RhythmKind = .daily
    @State private var time: Date = Calendar.current.date(bySettingHour: 9, minute: 0, second: 0, of: .now) ?? .now
    @State private var weekday: Int = 1
    @State private var day: Date = Date.now.addingTimeInterval(3600)
    @State private var cron: String = ""
    @State private var agent: String = ""
    @State private var mode: String = "task"
    @State private var modelRef: String = ""
    @State private var effort: String?
    @State private var approvals: String?
    @State private var goalMode: Bool = false
    @State private var agents: [AgentSummary] = []
    @State private var options: [ProviderModels] = []
    @State private var picking: ModelPick?
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?
    @State private var discardAsked: Bool = false

    var body: some View {
        NavigationStack {
            SettingsScroll(title: String(localized: "New scheduled task", comment: "Title of the new schedule sheet")) {
                if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: nil) }
                GroupedSection(header: String(localized: "Name", comment: "Schedule edit: the name"), footer: nil) {
                    TextField(String(localized: "Weekly report", comment: "New schedule: the name's placeholder"), text: $name).textRole(.body).padding(Space.groupedGutter)
                        .accessibilityIdentifier("scheduleNew.name")
                }
                GroupedSection(header: String(localized: "What Dot does", comment: "Schedule edit: the goal"), footer: nil) {
                    TextField(String(localized: "Read my mail and tell me what needs me", comment: "New schedule: the goal's placeholder"), text: $goal, axis: .vertical)
                        .lineLimit(3...10).textRole(.body).padding(Space.groupedGutter)
                        .accessibilityIdentifier("scheduleNew.goal")
                }
                when
                runsAs
                GroupedSection(header: String(localized: "Approvals", comment: "Schedule edit: the approval mode"), footer: nil) {
                    ForEach(scheduleApprovals, id: \.self) { mode in
                        RadioRow(title: approvalWords(mode), description: nil, isSelected: approvals == mode) { approvals = mode }
                    }
                    ToggleRow(
                        systemImage: nil, title: String(localized: "Goal mode", comment: "Schedule edit: keep working until the goal is met"),
                        description: String(localized: "Keeps working until the goal is met", comment: "Schedule edit: what goal mode does"), isOn: $goalMode)
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), systemImage: "xmark") { if dirty { discardAsked = true } else { dismiss() } }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Add", comment: "New schedule toolbar: makes it"), systemImage: "checkmark") { Task { await commit() } }
                        .disabled(saving || !complete)
                        .accessibilityIdentifier("scheduleNew.add")
                }
            }
            .confirmationDialog(String(localized: "Discard your changes?", comment: "Edit sheet: cancel with unsaved changes"), isPresented: $discardAsked, titleVisibility: .visible) {
                Button(String(localized: "Discard", comment: "Edit sheet: throws the changes away"), role: .destructive) { dismiss() }
            }
            .sheet(item: $picking) { pick in ModelPickerSheet(pick: pick, defaultRef: "") }
        }
        .presentationDetents([.large])
        .interactiveDismissDisabled(dirty || saving)
        .task {
            agent = agentId ?? ""
            agents = (await model.read { api throws(DaemonRequestError) in try await api.agents() }).value ?? []
            options = (await model.read { api throws(DaemonRequestError) in try await api.models() }).value ?? []
        }
    }

    /// When it runs: the rhythm, then the one control it needs; the footer says it in words, as the list will.
    private var when: some View {
        GroupedSection(header: String(localized: "When", comment: "New schedule: when it runs"), footer: whenFooter) {
            Picker(String(localized: "Repeat", comment: "New schedule: how often it runs"), selection: $kind) {
                ForEach(RhythmKind.allCases) { kind in Text(kind.title).tag(kind) }
            }
            .pickerRow()
            .accessibilityIdentifier("scheduleNew.repeat")
            switch kind {
            case .daily, .weekdays:
                timeRow
            case .weekly:
                Picker(String(localized: "Day", comment: "New schedule: the weekday it runs on"), selection: $weekday) {
                    ForEach(weekdayOrder, id: \.self) { day in Text(weekdayCalendar.weekdaySymbols[day]).tag(day) }
                }
                .pickerRow()
                timeRow
            case .hourly:
                EmptyView()
            case .once:
                DatePicker(String(localized: "Date and time", comment: "New schedule: the moment of a single run"), selection: $day, in: Date.now..., displayedComponents: [.date, .hourAndMinute])
                    .textRole(.body).padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
            case .custom:
                TextField(String("0 9 * * 1-5"), text: $cron).textRole(.body).monospaced().textInputAutocapitalization(.never).autocorrectionDisabled()
                    .padding(Space.groupedGutter)
                    .accessibilityLabel(String(localized: "Cron form", comment: "VoiceOver: the custom timing field"))
            }
        }
    }

    private var timeRow: some View {
        DatePicker(String(localized: "Time of day", comment: "New schedule: the clock time it runs at"), selection: $time, displayedComponents: [.hourAndMinute])
            .textRole(.body).padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
    }

    private var whenFooter: String {
        switch kind {
        case .once:
            String(localized: "At this moment in your time.", comment: "New schedule footer: a single run is read in the phone's zone")
        case .custom:
            String(
                localized: "Five parts separated by spaces: minute, hour, day of the month, month, day of the week, in your Mac's time zone. Very frequent schedules are refused.",
                comment: "New schedule footer: the cron form")
        default:
            String(
                localized: "\(rhythmWords(cron: rhythm.cron, runAt: nil, locale: locale) ?? kind.title), in your Mac's time zone.",
                comment: "New schedule footer: the rhythm in words; repeats are read in the Mac's zone")
        }
    }

    /// Who runs it and how: the agent, task or research, the model and its reasoning.
    private var runsAs: some View {
        GroupedSection(
            header: String(localized: "Runs as", comment: "New schedule: the agent, mode and model its runs take"),
            footer: String(localized: "What is left on Default takes your Mac's setting, or the agent's, when it runs.", comment: "New schedule: what Default means")
        ) {
            Picker(String(localized: "Agent", comment: "New schedule: the agent its runs run as"), selection: $agent) {
                Text(verbatim: "Dot").tag("")
                ForEach(agents, id: \.record.id) { summary in Text(summary.record.name).tag(summary.record.id) }
            }
            .pickerRow()
            RadioRow(title: String(localized: "Task", comment: "New schedule mode: do the job"), description: String(localized: "Does the job", comment: "New schedule mode: what a task does"), isSelected: mode == "task") { mode = "task" }
            RadioRow(
                title: String(localized: "Research", comment: "New schedule mode: look things up"),
                description: String(localized: "Looks things up, changes nothing", comment: "New schedule mode: what research does"), isSelected: mode == "research"
            ) { mode = "research" }
            Button { pickModel() } label: {
                ListRow(
                    systemImage: nil, title: String(localized: "Model", comment: "New schedule: the model its runs use"), description: nil,
                    accessory: .detailAndChevron(modelDetail))
            }
            .buttonStyle(RowPressStyle())
            .disabled(options.isEmpty)
        }
    }

    private var modelDetail: String {
        guard !modelRef.isEmpty else { return String(localized: "Default", comment: "New schedule: the model or level left to the Mac") }
        let label: String = modelLabel(modelRef, in: options)
        return effort.map { "\(label) · \(effortTitle($0))" } ?? label
    }

    private func pickModel() {
        picking = ModelPick(
            id: "schedule", title: String(localized: "Model", comment: "New schedule: the model its runs use"), current: modelRef, effort: effort ?? "",
            candidates: modelCandidates(options, for: .main), none: String(localized: "Default model", comment: "New schedule: no model of its own")
        ) { ref, level in
            modelRef = ref
            // A level belongs to a model: with Default there is none to keep.
            effort = ref.isEmpty || level?.isEmpty != false ? nil : level
            return nil
        }
    }

    /// Weekdays from Monday, as the Mac's lists show them; cron's numbers (0 Sunday).
    private var weekdayOrder: [Int] { [1, 2, 3, 4, 5, 6, 0] }

    private var weekdayCalendar: Calendar {
        var calendar: Calendar = Calendar(identifier: .gregorian)
        calendar.locale = locale
        return calendar
    }

    private var rhythm: ScheduleRhythm { scheduleRhythm(kind: kind, time: time, weekday: weekday, day: day, cron: cron, calendar: .current) }

    private var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedGoal: String { goal.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var complete: Bool {
        !trimmedName.isEmpty && !trimmedGoal.isEmpty && (kind != .custom || rhythm.cron?.split(separator: " ").count == 5)
    }

    /// Any choice moved from where the sheet opened: a swipe or Cancel then asks before throwing it away.
    private var dirty: Bool {
        !name.isEmpty || !goal.isEmpty || kind != .daily || !cron.isEmpty || agent != (agentId ?? "") || mode != "task" || !modelRef.isEmpty || approvals != nil || goalMode
    }

    private func commit() async {
        let body: ScheduleCreate = ScheduleCreate(
            name: trimmedName, goal: trimmedGoal, rhythm: rhythm, zone: .current, mode: mode, model: modelRef.isEmpty ? nil : modelRef, effort: effort,
            approvals: approvals, goalMode: goalMode ? true : nil, agentId: agent.isEmpty ? nil : agent)
        saving = true
        problem = await save(body)
        saving = false
        if problem == nil { dismiss() }
    }
}

extension View {
    /// A menu picker as a settings row: its title at the left, the choice at the right, the row's height.
    fileprivate func pickerRow() -> some View {
        pickerStyle(.menu).textRole(.body).tint(Tone.secondary).padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
    }
}

// MARK: History

/// The first line of a run's answer, for a history row; `nil` when it said nothing (pure).
func firingSummary(_ result: String) -> String? {
    let line: String = result.split(separator: "\n", omittingEmptySubsequences: true).first.map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
    return line.isEmpty ? nil : line
}

/// Scheduled tasks › a schedule › History: its last runs, at its time and by hand, newest first, each with how it went,
/// when, and the first line of its answer; a run opens its chat.
struct ScheduleHistoryPage: View {
    let schedule: ScheduleView
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<[ScheduleFiring]> = .loading
    @State private var chat: String?

    private var macPlace: String { String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules") }

    var body: some View {
        SettingsScroll(title: String(localized: "History", comment: "Schedule: its last runs")) {
            LoadedPage(state: state, macPlace: macPlace, retry: { Task { await load() } }) { firings in
                if firings.isEmpty {
                    EmptyState(
                        title: String(localized: "Not run yet", comment: "Schedule history: empty"), systemImage: "clock",
                        detail: String(localized: "Its runs show here once it has run.", comment: "Schedule history: what fills it"), actionTitle: nil, action: {}
                    ).fillsScrollPage()
                } else {
                    GroupedSection(header: schedule.record.name, footer: nil) {
                        ForEach(firings) { firing in row(firing) }
                    }
                }
            }
        }
        .navigationDestination(item: $chat) { thread in ChatScreen(thread: thread, initialPanel: nil, editsFirstQueued: false) }
        .task { await load() }
        .refreshable { await load() }
    }

    private func row(_ firing: ScheduleFiring) -> some View {
        let outcome: RunOutcome = RunOutcome(status: firing.run.status)
        let when: String = Date(timeIntervalSince1970: firing.run.createdAt).formatted(.dateTime.day().month(.abbreviated).hour().minute().locale(locale))
        let said: String = [outcome.word, firingSummary(firing.run.result)].compactMap { $0 }.joined(separator: " · ")
        return Button { chat = firing.run.threadId } label: {
            ListRow(systemImage: outcome.symbol, title: when, description: said, accessory: .chevron)
        }
        .buttonStyle(RowPressStyle())
        .accessibilityIdentifier("scheduleHistory.run")
        .accessibilityHint(String(localized: "Opens its chat", comment: "VoiceOver: a schedule's run opens its chat"))
    }

    private func load() async {
        let id: String = schedule.id
        state = await model.read { api throws(DaemonRequestError) in try await api.scheduleFirings(id, limit: 20) }
    }
}
