import DotEngine
import SwiftUI

// MARK: Scheduled tasks

/// A schedule's rhythm in words, as the web's `rhythm.js` says it ("Every 30 minutes", "Every day at 08:00", "Weekdays
/// at 09:30", "Every Monday at 09:00", "Mon, Wed at 18:30", "Once, 17 Nov at 12:00"); `nil` for a cron form that has no
/// words (a day of the month, a range of minutes), where the daemon's own `when` stands (pure).
func rhythmWords(cron: String?, runAt: Double?, locale: Locale) -> String? {
    if let runAt {
        let date: String = Date(timeIntervalSince1970: runAt).formatted(.dateTime.day().month(.abbreviated).hour().minute().locale(locale))
        return String(localized: "Once, \(date)", comment: "Schedule rhythm: runs once at a date and time")
    }
    guard let cron else { return nil }
    let fields: [String] = cron.split(separator: " ", omittingEmptySubsequences: true).map(String.init)
    guard fields.count == 5 else { return nil }
    func number(_ text: String, after prefix: String) -> Int? { text.hasPrefix(prefix) ? Int(text.dropFirst(prefix.count)) : nil }
    if fields[1] == "*", fields[2] == "*", fields[3] == "*", fields[4] == "*" {
        if let every = number(fields[0], after: "*/") { return String(localized: "Every \(every) minutes", comment: "Schedule rhythm") }
    }
    if fields[2] == "*", fields[3] == "*", fields[4] == "*", fields[0] == "0" {
        if fields[1] == "*" { return String(localized: "Every hour", comment: "Schedule rhythm") }
        if let every = number(fields[1], after: "*/") { return String(localized: "Every \(every) hours", comment: "Schedule rhythm") }
    }
    guard fields[2] == "*", fields[3] == "*", let minute = Int(fields[0]), (0..<60).contains(minute) else { return nil }
    let hours: [Int] = fields[1].split(separator: ",").compactMap { Int($0) }
    guard !hours.isEmpty, hours.count == fields[1].split(separator: ",").count, hours.allSatisfy({ (0..<24).contains($0) }) else { return nil }
    let times: String = Array(Set(hours)).sorted().map { String(format: "%02d:%02d", $0, minute) }.joined(separator: ", ")
    var calendar: Calendar = Calendar(identifier: .gregorian)
    calendar.locale = locale
    switch fields[4] {
    case "*": return String(localized: "Every day at \(times)", comment: "Schedule rhythm; the argument is one or more clock times")
    case "1-5": return String(localized: "Weekdays at \(times)", comment: "Schedule rhythm; the argument is one or more clock times")
    default:
        let days: [Int] = fields[4].split(separator: ",").compactMap { Int($0) }.map { $0 % 7 }
        guard !days.isEmpty, days.count == fields[4].split(separator: ",").count, days.allSatisfy({ (0..<7).contains($0) }) else { return nil }
        let ordered: [Int] = Array(Set(days)).sorted { ($0 + 6) % 7 < ($1 + 6) % 7 }
        if ordered == [6, 0] { return String(localized: "Weekends at \(times)", comment: "Schedule rhythm; the argument is one or more clock times") }
        if ordered.count == 1 {
            return String(localized: "Every \(calendar.weekdaySymbols[ordered[0]]) at \(times)", comment: "Schedule rhythm; a weekday's name, then clock times")
        }
        let names: String = ordered.map { calendar.shortWeekdaySymbols[$0] }.joined(separator: ", ")
        return String(localized: "\(names) at \(times)", comment: "Schedule rhythm; short weekday names, then clock times")
    }
}

/// The second line of a schedule (PLAN §13 N2 nit: next/last run, paused, failed).
func scheduleLine(_ schedule: ScheduleView, now: Date, locale: Locale) -> String {
    let relative: RelativeDateTimeFormatter = RelativeDateTimeFormatter()
    relative.locale = locale
    relative.dateTimeStyle = .named
    func ago(_ seconds: Double) -> String { relative.localizedString(for: Date(timeIntervalSince1970: seconds), relativeTo: now) }
    let record: ScheduleRecord = schedule.record
    var parts: [String] = [rhythmWords(cron: record.cron, runAt: record.cron == nil ? record.runAt : nil, locale: locale) ?? schedule.when]
    if !record.enabled {
        parts.append(String(localized: "Paused", comment: "Schedule: switched off"))
    } else if schedule.agent?.pausedAt != nil {
        parts.append(String(localized: "Its agent is paused", comment: "Schedule: the agent it runs as is paused"))
    } else if let next = record.nextRunAt {
        parts.append(
            String(localized: "Next run \(ago(next))", comment: "Schedule: when it runs next (relative)"))
    }
    if record.lastStatus == "failed" {
        parts.append(
            record.runsFailedInRow > 1
                ? String(localized: "Failed \(record.runsFailedInRow) times in a row", comment: "Schedule: its last runs failed")
                : String(localized: "Last run failed", comment: "Schedule: its last run failed"))
    } else if let last = record.lastRunAt {
        parts.append(String(localized: "Last run \(ago(last))", comment: "Schedule: when it ran last (relative)"))
    }
    return parts.joined(separator: " · ")
}

/// Settings › Agents › Scheduled tasks (the owner's reference shot): each with its rhythm, its next or last run and its
/// switch on the row, so which run and which wait is seen at once; its menu edits it, runs it now, opens its history or
/// deletes it; + makes a new one.
struct SchedulesPage: View {
    let editsFirst: Bool
    /// The agent whose schedules alone are shown (a bot's Routines); `nil` shows every schedule.
    let agentId: String?
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<[ScheduleView]> = .loading
    @State private var editing: ScheduleView?
    @State private var problem: SettingsFailure?
    @State private var switching: Set<String> = []
    @State private var deleting: ScheduleView?
    @State private var said: String?
    @State private var creating: Bool = false
    @State private var history: ScheduleView?
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        SettingsScroll(title: String(localized: "Scheduled tasks", comment: "Settings row")) {
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: nil) }
            if let said { Notice(tone: .info, message: said, dismiss: { self.said = nil }) }
            LoadedPage(state: state, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: { Task { await load() } }) { schedules in
                if schedules.isEmpty {
                    EmptyState(
                        title: String(localized: "No scheduled tasks", comment: "Schedules: empty"), systemImage: "clock",
                        detail: String(localized: "Dot does a task by itself, every morning or once at a time you pick.", comment: "Schedules: what one is"),
                        actionTitle: String(localized: "New scheduled task", comment: "Title of the new schedule sheet"), action: { creating = true }
                    ).fillsScrollPage()
                } else {
                    VStack(alignment: .leading, spacing: Space.xs) {
                        if let mac = model.macName {
                            Label(mac, systemImage: "laptopcomputer").textRole(.footnote).foregroundStyle(Tone.secondary).padding(.horizontal, Space.xs)
                        }
                        GroupedSection(header: nil, footer: nil) {
                            ForEach(schedules) { schedule in row(schedule) }
                        }
                    }
                }
            }
        }
        .confirmationDialog(
            String(localized: "Delete this schedule?", comment: "Schedules: confirm a delete"), isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
            titleVisibility: .visible, presenting: deleting
        ) { schedule in
            Button(String(localized: "Delete", comment: "Schedule menu: delete"), role: .destructive) { Task { await delete(schedule) } }
        } message: { schedule in
            Text(String(localized: "“\(schedule.record.name)” will not run again. Tasks it started earlier stay in the history.", comment: "Schedules: what a delete does; the argument is its name"))
        }
        .sheet(item: $editing) { schedule in
            ScheduleEditSheet(schedule: schedule) { edit in
                switch await model.write({ api throws(DaemonRequestError) in try await api.editSchedule(schedule.id, edit: edit) }) {
                case .success(let saved):
                    replace(saved)
                    return nil
                case .failure(let box): return box.failure
                }
            }
        }
        .sheet(isPresented: $creating) {
            ScheduleCreateSheet(agentId: agentId) { body in
                switch await model.write({ api throws(DaemonRequestError) in try await api.createSchedule(body) }) {
                case .success(let made):
                    // A bot's Routines show only its own: one made for another agent goes to that agent's list.
                    if agentId == nil || made.agent?.id == agentId { state = .loaded((state.value ?? []) + [made]) }
                    problem = nil
                    return nil
                case .failure(let box): return box.failure
                }
            }
        }
        .navigationDestination(item: $history) { schedule in ScheduleHistoryPage(schedule: schedule) }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button(String(localized: "New scheduled task", comment: "Title of the new schedule sheet"), systemImage: "plus") { creating = true }
                    .accessibilityIdentifier("schedules.new")
            }
        }
        .task {
            await load()
            if editsFirst, let first = state.value?.first { editing = first }
        }
        .refreshable { await load() }
    }

    /// A schedule: its name and its line open the edit sheet; its switch, on the row itself, pauses or resumes it, so
    /// which run and which wait is seen at once.
    private func row(_ schedule: ScheduleView) -> some View {
        HStack(spacing: Space.s) {
            Button { editing = schedule } label: {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(schedule.record.name).textRole(.body).foregroundStyle(schedule.record.enabled ? Tone.text : Tone.secondary).multilineTextAlignment(.leading)
                    Text(scheduleLine(schedule, now: .now, locale: locale)).textRole(.footnote)
                        .foregroundStyle(schedule.record.lastStatus == "failed" ? Tone.failure : Tone.secondary).multilineTextAlignment(.leading)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(String(localized: "Opens the edit", comment: "VoiceOver: a schedule's name opens its edit sheet"))
            // The ink switch draws its label, so the label is empty and VoiceOver hears the name.
            Toggle(isOn: Binding(get: { schedule.record.enabled }, set: { on in Task { await setEnabled(schedule, on) } })) { EmptyView() }
                .toggleStyle(InkSwitchStyle())
                .fixedSize()
                .accessibilityLabel(String(localized: "\(schedule.record.name) on", comment: "VoiceOver: a schedule's switch; the argument is its name"))
                .disabled(switching.contains(schedule.id))
            Menu {
                Button(String(localized: "Edit", comment: "Schedule menu: edit"), systemImage: "pencil") { editing = schedule }
                Button(String(localized: "Run now", comment: "Schedule menu: runs it at once"), systemImage: "play") { Task { await runNow(schedule) } }
                Button(String(localized: "History", comment: "Schedule: its last runs"), systemImage: "clock.arrow.circlepath") { history = schedule }
                Button(String(localized: "Delete", comment: "Schedule menu: delete"), systemImage: "trash", role: .destructive) { deleting = schedule }
            } label: {
                Image(systemName: "ellipsis").font(.body.weight(.medium)).foregroundStyle(Tone.text).frame(width: Metric.hit, height: Metric.hit)
            }
            .accessibilityLabel(String(localized: "Actions", comment: "VoiceOver: a row's menu of actions"))
        }
        .padding(.leading, Space.groupedGutter).padding(.trailing, Space.xs).padding(.vertical, Space.s)
        .frame(minHeight: Metric.rowWithDescription)
    }

    private func load() async {
        let agentId: String? = agentId
        let read: Loadable<[ScheduleView]> = await model.read { api throws(DaemonRequestError) in try await api.schedules() }
        guard let agentId, case .loaded(let all) = read else {
            state = read
            return
        }
        state = .loaded(all.filter { $0.agent?.id == agentId })
    }

    private func replace(_ saved: ScheduleView) {
        guard var all = state.value, let index = all.firstIndex(where: { $0.id == saved.id }) else { return }
        all[index] = saved
        state = .loaded(all)
    }

    private func runNow(_ schedule: ScheduleView) async {
        switch await model.write({ api throws(DaemonRequestError) in try await api.runSchedule(schedule.id) }) {
        case .success:
            problem = nil
            said = String(localized: "“\(schedule.record.name)” is running now; its chat shows it.", comment: "Schedules: a run started at once; the argument is its name")
        case .failure(let box): problem = box.failure
        }
    }

    private func delete(_ schedule: ScheduleView) async {
        switch await model.write({ api throws(DaemonRequestError) in try await api.deleteSchedule(schedule.id) }) {
        case .success:
            state = .loaded((state.value ?? []).filter { $0.id != schedule.id })
            problem = nil
        case .failure(let box): problem = box.failure
        }
    }

    private func setEnabled(_ schedule: ScheduleView, _ enabled: Bool) async {
        switching.insert(schedule.id)
        switch await model.write({ api throws(DaemonRequestError) in try await api.setSchedule(schedule.id, enabled: enabled) }) {
        case .success(let saved):
            replace(saved)
            problem = nil
        case .failure(let box): problem = box.failure
        }
        switching.remove(schedule.id)
    }
}

/// The approval modes a schedule can start its runs in; nil = the default of Settings › Rules.
let scheduleApprovals: [String?] = [nil, "supervised", "ask", "auto", "yolo"]

func approvalWords(_ mode: String?) -> String {
    switch mode {
    case nil: String(localized: "As the default", comment: "Schedule edit: the default approval mode")
    case "supervised": String(localized: "Supervised", comment: "Approval mode: everything asks")
    case "ask": String(localized: "Ask", comment: "Approval mode: risky steps ask")
    case "auto": String(localized: "Auto", comment: "Approval mode: a review model decides")
    case "yolo": String(localized: "YOLO", comment: "Approval mode: nothing asks but payments, mail and posts")
    default: mode ?? ""
    }
}

/// Edits a schedule's name, goal, approval mode and goal mode; its rhythm and its agent stay as they are (changed on
/// the Mac), and the daemon gets the whole schedule back as it takes it.
struct ScheduleEditSheet: View {
    let schedule: ScheduleView
    let save: (ScheduleEdit) async -> SettingsFailure?
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var name: String = ""
    @State private var goal: String = ""
    @State private var approvals: String?
    @State private var goalMode: Bool = false
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?
    @State private var discardAsked: Bool = false

    var body: some View {
        NavigationStack {
            SettingsScroll(title: String(localized: "Edit scheduled task", comment: "Title of the schedule edit sheet")) {
                if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: nil) }
                if !editable {
                    Notice(tone: .info, message: String(localized: "A chat schedule is edited on your Mac.", comment: "Schedule edit: a chat-mode schedule cannot be edited here"), dismiss: nil)
                }
                GroupedSection(header: String(localized: "Name", comment: "Schedule edit: the name"), footer: nil) {
                    TextField(String(localized: "Name", comment: "Schedule edit: the name"), text: $name).textRole(.body).padding(Space.groupedGutter)
                }
                GroupedSection(header: String(localized: "What Dot does", comment: "Schedule edit: the goal"), footer: rhythmWords(cron: schedule.record.cron, runAt: schedule.record.cron == nil ? schedule.record.runAt : nil, locale: .current) ?? schedule.when) {
                    TextField(String(localized: "What Dot does", comment: "Schedule edit: the goal"), text: $goal, axis: .vertical).lineLimit(3...10).textRole(.body).padding(Space.groupedGutter)
                }
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
                    Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits"), systemImage: "checkmark") { Task { await commit() } }
                        .disabled(!dirty || saving || !editable || name.trimmingCharacters(in: .whitespaces).isEmpty || goal.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .confirmationDialog(String(localized: "Discard your changes?", comment: "Edit sheet: cancel with unsaved changes"), isPresented: $discardAsked, titleVisibility: .visible) {
                Button(String(localized: "Discard", comment: "Edit sheet: throws the changes away"), role: .destructive) { dismiss() }
            }
        }
        .presentationDetents([.large])
        .interactiveDismissDisabled(dirty || saving)
        .onAppear {
            name = schedule.record.name
            goal = schedule.record.goal
            approvals = schedule.record.options.approvals
            goalMode = schedule.record.options.goalMode ?? false
        }
    }

    /// The daemon's edit takes `task` or `research`; a chat schedule keeps its mode by not being edited here.
    private var editable: Bool { schedule.record.mode == "task" || schedule.record.mode == "research" }

    private var dirty: Bool {
        name != schedule.record.name || goal != schedule.record.goal || approvals != schedule.record.options.approvals || goalMode != (schedule.record.options.goalMode ?? false)
    }

    private func commit() async {
        let record: ScheduleRecord = schedule.record
        let edit: ScheduleEdit = ScheduleEdit(
            name: name.trimmingCharacters(in: .whitespaces), goal: goal.trimmingCharacters(in: .whitespacesAndNewlines), cron: record.cron,
            at: record.cron == nil ? schedule.runAtInput : nil, mode: record.mode, model: record.options.model, effort: record.options.effort, approvals: approvals,
            goalMode: record.options.goalMode == nil && !goalMode ? nil : goalMode, timezone: record.options.timezone)
        saving = true
        problem = await save(edit)
        saving = false
        if problem == nil { dismiss() }
    }
}
