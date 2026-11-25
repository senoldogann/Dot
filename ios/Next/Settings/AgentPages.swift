import DotEngine
import SwiftUI

// MARK: Models

/// What the Models page reads at once.
struct ModelsPicture: Sendable {
    let config: ModelsConfig
    let options: [ProviderModels]
    let review: CodeReviewConfig?
}

/// The label of a model reference, from the daemon's list; the reference itself when the list does not hold it.
func modelLabel(_ ref: String, in options: [ProviderModels]) -> String {
    guard !ref.isEmpty else { return String(localized: "Not set", comment: "Models: a role with no model of its own") }
    for provider in options { if let model = provider.models.first(where: { $0.ref == ref }) { return model.label } }
    return ref
}

/// Settings › Agents › Models: the main model and the roles (fast, review, vision) chosen from the daemon's list; the
/// models of the code review (item 1663) read only, as the daemon takes them from the Mac alone.
struct ModelsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<ModelsPicture> = .loading
    @State private var problem: SettingsFailure?

    var body: some View {
        SettingsScroll(title: String(localized: "Models", comment: "Settings row")) {
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Models", comment: "Where on the Mac: the Models page"), retry: nil) }
            LoadedPage(state: state, macPlace: String(localized: "Settings › Models", comment: "Where on the Mac: the Models page"), retry: { Task { await load() } }) { picture in
                GroupedSection(
                    header: String(localized: "Dot thinks with", comment: "Models section: the main model and the roles"),
                    footer: String(localized: "A new model applies from each task's next start; running tasks keep theirs.", comment: "Models: footer under the roles")
                ) {
                    pickRow(String(localized: "Main model", comment: "Models: the main model"), "cpu", current: picture.config.main, picture: picture) { api, ref throws(DaemonRequestError) in
                        try await api.setMainModel(ref)
                    }
                    pickRow(String(localized: "Fast model", comment: "Models: the role for quick small steps"), "hare", current: picture.config.fast, picture: picture) { api, ref throws(DaemonRequestError) in
                        try await api.setRole(.fast, model: ref)
                    }
                    pickRow(String(localized: "Review model", comment: "Models: the role that reviews in auto mode"), "checkmark.shield", current: picture.config.review, picture: picture) { api, ref throws(DaemonRequestError) in
                        try await api.setRole(.review, model: ref)
                    }
                    pickRow(String(localized: "Vision model", comment: "Models: the role that reads pictures"), "eye", current: picture.config.vision, picture: picture) { api, ref throws(DaemonRequestError) in
                        try await api.setRole(.vision, model: ref)
                    }
                }
                if !picture.config.standby.isEmpty {
                    GroupedSection(
                        header: String(localized: "Standby", comment: "Models section: the models used when the main one fails"),
                        footer: String(localized: "Used in this order when a model does not answer.", comment: "Models: footer under the standby list")
                    ) {
                        ForEach(picture.config.standby, id: \.self) { ref in
                            ListRow(systemImage: "arrow.triangle.branch", title: modelLabel(ref, in: picture.options), description: ref, accessory: .none)
                        }
                    }
                }
                if let review = picture.review {
                    GroupedSection(
                        header: String(localized: "Code review", comment: "Models section: the pre-push review's models (item 1663)"),
                        footer: String(localized: "A phone reads these; change them on the Mac: Settings › Models › Code review.", comment: "Models: the code review is read-only from a phone")
                    ) {
                        ListRow(systemImage: "text.magnifyingglass", title: String(localized: "Review model", comment: "Code review: the first model"), description: nil, accessory: .detail("\(modelLabel(review.model, in: picture.options)) · \(review.effort)"))
                        ForEach(Array(review.fallback.enumerated()), id: \.offset) { index, ref in
                            ListRow(systemImage: "arrow.turn.down.right", title: String(localized: "Fallback \(index + 1)", comment: "Code review: a fallback model's place"), description: nil, accessory: .detail("\(modelLabel(ref, in: picture.options)) · \(review.fallbackEffort)"))
                        }
                        ListRow(systemImage: "doc.plaintext", title: String(localized: "Small change model", comment: "Code review: the model of a small change"), description: nil, accessory: .detail(review.smallModel.isEmpty ? String(localized: "None", comment: "No value set") : modelLabel(review.smallModel, in: picture.options)))
                        ListRow(systemImage: "number", title: String(localized: "Small change", comment: "Code review: the size of a small change"), description: nil, accessory: .detail(String(localized: "up to \(review.smallMaxLines) lines", comment: "Code review: the small change's lines")))
                        ListRow(systemImage: "timer", title: String(localized: "Time limit", comment: "Code review: the time limit"), description: nil, accessory: .detail(String(localized: "\(review.maxMinutes) min", comment: "A number of minutes")))
                    }
                }
                GroupedSection(header: nil, footer: nil) {
                    WebRow(target: WebSettingsPage.Target(tab: "models", title: String(localized: "Context, effort and providers", comment: "Models: the web page's other parts")), systemImage: "slider.horizontal.3", description: nil)
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        let status: Loadable<StatusView> = await model.read { api throws(DaemonRequestError) in try await api.fullStatus() }
        guard case .loaded(let current) = status else {
            if case .failed(let failure) = status { state = .failed(failure) }
            return
        }
        let options: Loadable<[ProviderModels]> = await model.read { api throws(DaemonRequestError) in try await api.models() }
        let review: Loadable<CodeReviewConfig> = await model.read { api throws(DaemonRequestError) in try await api.codeReview() }
        state = .loaded(ModelsPicture(config: current.models, options: options.value ?? [], review: review.value))
    }

    private func pickRow(
        _ title: String, _ symbol: String, current: String, picture: ModelsPicture, choose: @escaping @Sendable (DaemonAPI, String) async throws(DaemonRequestError) -> ModelsConfig
    ) -> some View {
        NavigationLink {
            ModelPickList(title: title, current: current, options: picture.options) { ref in
                switch await model.write({ api throws(DaemonRequestError) in try await choose(api, ref) }) {
                case .success(let config):
                    state = .loaded(ModelsPicture(config: config, options: picture.options, review: picture.review))
                    problem = nil
                    return nil
                case .failure(let box):
                    return box.failure
                }
            }
        } label: {
            ListRow(systemImage: symbol, title: title, description: nil, accessory: .detailAndChevron(modelLabel(current, in: picture.options)))
        }
        .buttonStyle(RowPressStyle())
    }
}

/// A list of the daemon's models, by provider; tapping one chooses it and says whether the Mac took it.
struct ModelPickList: View {
    let title: String
    let current: String
    let options: [ProviderModels]
    let choose: (String) async -> SettingsFailure?
    @State private var chosen: String?
    @State private var problem: SettingsFailure?
    @State private var saving: Bool = false

    var body: some View {
        SettingsScroll(title: title) {
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Models", comment: "Where on the Mac: the Models page"), retry: nil) }
            ForEach(options, id: \.provider) { provider in
                GroupedSection(header: provider.provider, footer: provider.error) {
                    ForEach(provider.models, id: \.ref) { option in
                        RadioRow(title: option.label, description: option.ref, isSelected: (chosen ?? current) == option.ref) {
                            guard !saving else { return }
                            Task {
                                saving = true
                                let before: String? = chosen
                                chosen = option.ref
                                if let failure = await choose(option.ref) {
                                    chosen = before
                                    problem = failure
                                } else {
                                    problem = nil
                                }
                                saving = false
                            }
                        }
                    }
                }
            }
        }
    }
}

// MARK: Usage limits and caps

/// What the caps page reads.
struct CapsPicture: Sendable {
    let budget: Loadable<BudgetView>
    let usage: UsageView?
}

/// A cap of `[budget]` (item 1731) with its words.
enum CapKey: String, CaseIterable, Sendable, Identifiable {
    case run
    case day
    case ui
    case schedule
    case initiative
    case review
    case delegate

    var id: String { rawValue }

    var keyPath: WritableKeyPath<BudgetCaps, Int> {
        switch self {
        case .run: \.runTokens
        case .day: \.dayTokens
        case .ui: \.uiDayTokens
        case .schedule: \.scheduleDayTokens
        case .initiative: \.initiativeDayTokens
        case .review: \.reviewDayTokens
        case .delegate: \.delegateDayTokens
        }
    }

    var title: String {
        switch self {
        case .run: String(localized: "One task", comment: "Caps: a run and the runs it delegated to")
        case .day: String(localized: "All tasks a day", comment: "Caps: every run that began today")
        case .ui: String(localized: "Your tasks a day", comment: "Caps: the owner's own chats and tasks a day")
        case .schedule: String(localized: "Scheduled tasks a day", comment: "Caps: schedules a day")
        case .initiative: String(localized: "Dot's own initiative a day", comment: "Caps: Dot's own runs a day")
        case .review: String(localized: "Code reviews a day", comment: "Caps: pre-push reviews a day")
        case .delegate: String(localized: "Helpers a day", comment: "Caps: delegated helpers a day")
        }
    }
}

/// A source's word (`ui`, `schedule`, `initiative`, `review`, `delegate`, `team`).
func sourceWords(_ source: String) -> String {
    switch source {
    case "ui": String(localized: "Your tasks", comment: "Usage source: the owner's own")
    case "schedule": String(localized: "Scheduled tasks", comment: "Settings row")
    case "initiative": String(localized: "Dot's own initiative", comment: "Usage source: Dot's own runs")
    case "review": String(localized: "Code reviews", comment: "Usage source: pre-push reviews")
    case "delegate": String(localized: "Helpers", comment: "Usage source: delegated helpers")
    default: source
    }
}

/// A plan window's word.
func windowWords(_ kind: String) -> String {
    switch kind {
    case "five_hour": String(localized: "5-hour window", comment: "Usage: the plan's 5-hour window")
    case "seven_day": String(localized: "Weekly window", comment: "Usage: the plan's 7-day window")
    default: kind
    }
}

/// Settings › Agents › Usage limits and caps: the plan's windows (read), today's tokens by source, and the caps of what
/// tasks spend (item 1731), which a phone may lower or set but not raise or take off.
struct CapsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<CapsPicture> = .loading
    @State private var editing: CapKey?
    @State private var problem: SettingsFailure?
    @Environment(\.locale) private var locale: Locale
    /// A fixture's scene (debug builds) tries a raise at once, to show the refusal; always false in the shipped app.
    let triesRaise: Bool

    var body: some View {
        SettingsScroll(title: String(localized: "Usage limits and caps", comment: "Settings row: the plan's windows and the caps of what tasks spend")) {
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Models › Caps", comment: "Where on the Mac: the caps"), retry: nil) }
            LoadedPage(state: state, macPlace: String(localized: "Settings › Models › Caps", comment: "Where on the Mac: the caps"), retry: { Task { await load() } }) { picture in
                if let usage = picture.usage, !usage.windows.isEmpty {
                    GroupedSection(
                        header: String(localized: "Plan limits", comment: "Caps section: the plan's usage windows"),
                        footer: String(localized: "As your plan last reported them.", comment: "Caps: footer under the windows")
                    ) {
                        ForEach(usage.windows, id: \.kind) { window in
                            WindowRow(window: window, locale: locale)
                        }
                    }
                }
                switch picture.budget {
                case .loaded(let budget):
                    GroupedSection(header: String(localized: "Today", comment: "Caps section: today's tokens"), footer: nil) {
                        ListRow(systemImage: "sum", title: String(localized: "All tasks", comment: "Caps: today's total"), description: nil, accessory: .detail(tokenWords(budget.today, locale: locale)))
                        ForEach(budget.bySource, id: \.source) { spend in
                            ListRow(systemImage: nil, title: sourceWords(spend.source), description: nil, accessory: .detail(tokenWords(spend.tokens, locale: locale)))
                        }
                    }
                    GroupedSection(
                        header: String(localized: "Caps", comment: "Caps section: the caps of what tasks spend"),
                        footer: String(localized: "A task past a cap pauses and asks you to continue or stop, in every mode. From a phone you can lower or set a cap; raising one or taking one off is done on the Mac.", comment: "Caps: footer, what a cap does and what a phone may do")
                    ) {
                        ForEach(CapKey.allCases) { key in
                            Button { editing = key } label: {
                                let value: Int = budget.caps[keyPath: key.keyPath]
                                ListRow(systemImage: nil, title: key.title, description: nil, accessory: .detailAndChevron(value == 0 ? String(localized: "No cap", comment: "Caps: a cap of 0") : tokenWords(value, locale: locale)))
                            }
                            .buttonStyle(RowPressStyle())
                        }
                    }
                case .failed(let failure):
                    FailureBanner(failure: failure, macPlace: String(localized: "Settings › Models › Caps", comment: "Where on the Mac: the caps"), retry: { Task { await load() } })
                case .loading:
                    EmptyView()
                }
            }
        }
        .sheet(item: $editing) { key in
            if let budget = state.value?.budget.value {
                CapEditSheet(key: key, caps: budget.caps) { caps in await save(caps) }
            }
        }
        .task {
            await load()
            if triesRaise, var caps = state.value?.budget.value?.caps {
                caps.runTokens *= 2
                _ = await save(caps)
            }
        }
    }

    private func load() async {
        let budget: Loadable<BudgetView> = await model.read { api throws(DaemonRequestError) in try await api.budget() }
        let usage: Loadable<UsageView> = await model.read { api throws(DaemonRequestError) in try await api.usage(days: 7) }
        if case .failed(let failure) = budget, case .failed = usage {
            state = .failed(failure)
            return
        }
        state = .loaded(CapsPicture(budget: budget, usage: usage.value))
    }

    /// Writes the caps; `nil` when the Mac took them, else why not (the sheet stays open and says it).
    private func save(_ caps: BudgetCaps) async -> SettingsFailure? {
        switch await model.write({ api throws(DaemonRequestError) in try await api.changeBudget(caps) }) {
        case .success(let saved):
            state = .loaded(CapsPicture(budget: .loaded(saved), usage: state.value?.usage))
            problem = nil
            return nil
        case .failure(let box):
            problem = box.failure
            return box.failure
        }
    }
}

/// A plan window: how much is used, as a bar, and when it resets.
struct WindowRow: View {
    let window: UsageWindow
    let locale: Locale

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack {
                Text(windowWords(window.kind)).textRole(.body).foregroundStyle(Tone.text)
                Spacer()
                if let used = window.utilization {
                    Text(used.formatted(.percent.precision(.fractionLength(0)).locale(locale))).textRole(.footnote).foregroundStyle(Tone.secondary).tabularNumbers()
                }
            }
            if let used = window.utilization {
                ProgressView(value: min(max(used, 0), 1)).tint(used >= 0.9 ? Tone.warn : Tone.text)
            }
            if let resets = window.resetsAt {
                Text(String(localized: "Resets \(Date(timeIntervalSince1970: resets).formatted(.relative(presentation: .named).locale(locale)))", comment: "Usage: when a plan window resets"))
                    .textRole(.footnote).foregroundStyle(Tone.secondary)
            }
        }
        .padding(.horizontal, Space.groupedGutter)
        .padding(.vertical, Space.s)
        .accessibilityElement(children: .combine)
    }
}

/// Changes one cap, in millions of tokens.
struct CapEditSheet: View {
    let key: CapKey
    let caps: BudgetCaps
    let save: (BudgetCaps) async -> SettingsFailure?
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var millions: String = ""
    @State private var noCap: Bool = false
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        NavigationStack {
            SettingsScroll(title: key.title) {
                if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Models › Caps", comment: "Where on the Mac: the caps"), retry: nil) }
                GroupedSection(header: nil, footer: String(localized: "In millions of tokens, as the Usage page counts them.", comment: "Cap edit: the unit")) {
                    ToggleRow(systemImage: nil, title: String(localized: "No cap", comment: "Caps: a cap of 0"), description: nil, isOn: $noCap)
                    if !noCap {
                        HStack {
                            TextField(String(localized: "Millions of tokens", comment: "Cap edit: the field's placeholder"), text: $millions)
                                .keyboardType(.numberPad).textRole(.body)
                            Text(verbatim: "M").textRole(.body).foregroundStyle(Tone.secondary)
                        }
                        .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s).frame(minHeight: Metric.row)
                    }
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), systemImage: "xmark") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits"), systemImage: "checkmark") {
                        Task {
                            saving = true
                            var changed: BudgetCaps = caps
                            changed[keyPath: key.keyPath] = noCap ? 0 : (Int(millions) ?? 0) * 1_000_000
                            problem = await save(changed)
                            saving = false
                            if problem == nil { dismiss() }
                        }
                    }
                    .disabled(saving || (!noCap && Int(millions) == nil))
                }
            }
        }
        .presentationDetents([.medium, .large])
        .interactiveDismissDisabled(saving)
        .onAppear {
            let value: Int = caps[keyPath: key.keyPath]
            noCap = value == 0
            millions = value == 0 ? "" : String(value / 1_000_000)
        }
    }
}

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

/// Settings › Agents › Scheduled tasks (the owner's reference shot): each with its rhythm and its next or last run, a
/// menu to pause, resume or edit it; a new one is made on the web page for now.
struct SchedulesPage: View {
    let editsFirst: Bool
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<[ScheduleView]> = .loading
    @State private var editing: ScheduleView?
    @State private var problem: SettingsFailure?
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        SettingsScroll(title: String(localized: "Scheduled tasks", comment: "Settings row")) {
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: nil) }
            LoadedPage(state: state, macPlace: String(localized: "Settings › Scheduled tasks", comment: "Where on the Mac: the schedules"), retry: { Task { await load() } }) { schedules in
                if schedules.isEmpty {
                    EmptyState(
                        title: String(localized: "No scheduled tasks", comment: "Schedules: empty"), systemImage: "clock",
                        detail: String(localized: "Ask Dot in a chat to do something every morning, or make one with +.", comment: "Schedules: how one is made"), actionTitle: nil, action: {})
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
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink(value: SettingsPage.web(WebSettingsPage.Target(tab: "schedules", title: String(localized: "Scheduled tasks", comment: "Settings row")))) {
                    Image(systemName: "plus")
                }
                .accessibilityLabel(String(localized: "New scheduled task", comment: "Schedules: makes a new one (on the web page)"))
            }
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
        .task {
            await load()
            if editsFirst, let first = state.value?.first { editing = first }
        }
        .refreshable { await load() }
    }

    private func row(_ schedule: ScheduleView) -> some View {
        Button { editing = schedule } label: {
            HStack(spacing: Space.s) {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(schedule.record.name).textRole(.body).foregroundStyle(schedule.record.enabled ? Tone.text : Tone.secondary).multilineTextAlignment(.leading)
                    Text(scheduleLine(schedule, now: .now, locale: locale)).textRole(.footnote)
                        .foregroundStyle(schedule.record.lastStatus == "failed" ? Tone.failure : Tone.secondary).multilineTextAlignment(.leading)
                }
                Spacer(minLength: Space.s)
                Menu {
                    Button(
                        schedule.record.enabled ? String(localized: "Pause", comment: "Schedule menu: switch off") : String(localized: "Resume", comment: "Schedule menu: switch on"),
                        systemImage: schedule.record.enabled ? "pause.circle" : "play.circle"
                    ) { Task { await setEnabled(schedule, !schedule.record.enabled) } }
                    Button(String(localized: "Edit", comment: "Schedule menu: edit"), systemImage: "pencil") { editing = schedule }
                } label: {
                    Image(systemName: "ellipsis").font(.body.weight(.medium)).foregroundStyle(Tone.text).frame(width: Metric.hit, height: Metric.hit)
                }
                .accessibilityLabel(String(localized: "Actions", comment: "VoiceOver: a row's menu of actions"))
            }
            .padding(.leading, Space.groupedGutter).padding(.trailing, Space.xs).padding(.vertical, Space.s)
            .frame(minHeight: Metric.rowWithDescription)
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: schedule.record.enabled ? String(localized: "Pause", comment: "Schedule menu: switch off") : String(localized: "Resume", comment: "Schedule menu: switch on")) {
            Task { await setEnabled(schedule, !schedule.record.enabled) }
        }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.schedules() }
    }

    private func replace(_ saved: ScheduleView) {
        guard var all = state.value, let index = all.firstIndex(where: { $0.id == saved.id }) else { return }
        all[index] = saved
        state = .loaded(all)
    }

    private func setEnabled(_ schedule: ScheduleView, _ enabled: Bool) async {
        switch await model.write({ api throws(DaemonRequestError) in try await api.setSchedule(schedule.id, enabled: enabled) }) {
        case .success(let saved):
            replace(saved)
            problem = nil
        case .failure(let box): problem = box.failure
        }
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
/// the web page), and the daemon gets the whole schedule back as it takes it.
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
                    Notice(tone: .info, message: String(localized: "A chat schedule is edited on the web page.", comment: "Schedule edit: a chat-mode schedule cannot be edited here"), dismiss: nil)
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

// MARK: Rules

func behaviourWords(_ behaviour: String) -> String {
    switch behaviour {
    case "allow": String(localized: "Allowed", comment: "Rule: Dot does it without asking")
    case "preapproved": String(localized: "Pre-approved", comment: "Rule: allowed ahead even in a tainted run")
    case "ask": String(localized: "Asks first", comment: "Rule: Dot asks the owner first")
    case "handoff": String(localized: "Hands to you", comment: "Rule: the owner does it")
    default: behaviour
    }
}

/// Settings › Agents › Rules, read (PARITY: the list with plain words); adding, moving and switching are the web
/// page's and the Mac's.
struct RulesPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<[RuleView]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Rules", comment: "Settings row: what Dot may do without asking")) {
            LoadedPage(state: state, macPlace: String(localized: "Settings › Rules", comment: "Where on the Mac: the Rules page"), retry: { Task { await load() } }) { rules in
                if rules.isEmpty {
                    EmptyState(
                        title: String(localized: "No rules of your own", comment: "Rules: empty"), systemImage: "lock.shield",
                        detail: String(localized: "Dot follows its approval mode alone.", comment: "Rules: what holds without rules"), actionTitle: nil, action: {})
                } else {
                    GroupedSection(
                        header: String(localized: "In order, the first that matches decides", comment: "Rules: header above the list"),
                        footer: String(localized: "To add a rule that allows, move or switch rules: open on the Mac Settings › Rules. A phone may add a rule that asks or hands to you on the web page.", comment: "Rules: footer: what a phone may not do")
                    ) {
                        ForEach(rules.sorted { $0.position < $1.position }) { rule in
                            ListRow(
                                systemImage: rule.enabled ? "checkmark.circle" : "circle", title: rule.description.isEmpty ? rule.toolGlob : rule.description,
                                description: rule.enabled ? "\(behaviourWords(rule.behavior)) · \(rule.toolGlob)" : "\(String(localized: "Off", comment: "This Mac: Tailscale is off")) · \(rule.toolGlob)",
                                accessory: .none)
                        }
                    }
                }
                GroupedSection(header: nil, footer: nil) {
                    WebRow(target: WebSettingsPage.Target(tab: "rules", title: String(localized: "Rules on the web page", comment: "Rules: the web page row")), systemImage: "lock.shield", description: nil)
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.rules() }
    }
}

// MARK: Memory

/// Settings › Agents › Memory: search the notes, and the runs' proposed rewrites of the owner's notes (item 1711) to
/// Use or Drop.
struct MemoryPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var proposals: Loadable<[MemoryNote]> = .loading
    @State private var query: String = ""
    @State private var hits: Loadable<[MemoryHit]>?
    @State private var problem: SettingsFailure?
    @State private var deciding: String?

    var body: some View {
        SettingsScroll(title: String(localized: "Memory", comment: "Settings row")) {
            GroupedSection(header: String(localized: "Search", comment: "Memory: the search section"), footer: nil) {
                HStack(spacing: Space.s) {
                    Symbol(name: "magnifyingglass", context: .row).foregroundStyle(Tone.secondary)
                    TextField(String(localized: "Search what Dot remembers", comment: "Memory: the search field"), text: $query)
                        .submitLabel(.search).textRole(.body)
                        .onSubmit { Task { await search() } }
                }
                .padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
            }
            if let hits {
                LoadedPage(state: hits, macPlace: String(localized: "Settings › Memory", comment: "Where on the Mac: the Memory page"), retry: { Task { await search() } }) { found in
                    if found.isEmpty {
                        ContentUnavailableView.search(text: query)
                    } else {
                        GroupedSection(header: String(localized: "\(found.count) notes", comment: "Memory: the number of search hits"), footer: nil) {
                            ForEach(found, id: \.record.id) { hit in
                                ListRow(systemImage: hit.record.pinnedAt != nil ? "pin" : nil, title: hit.record.content, description: hit.record.tags.isEmpty ? nil : hit.record.tags.joined(separator: " · "), accessory: .none)
                            }
                        }
                    }
                }
            }
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Memory", comment: "Where on the Mac: the Memory page"), retry: nil) }
            LoadedPage(state: proposals, macPlace: String(localized: "Settings › Memory", comment: "Where on the Mac: the Memory page"), retry: { Task { await load() } }) { notes in
                GroupedSection(
                    header: String(localized: "Proposed changes", comment: "Memory: runs' rewrites of the owner's notes"),
                    footer: notes.isEmpty
                        ? String(localized: "None waits. When a task wants to change a note you wrote, it waits here.", comment: "Memory: no proposals")
                        : String(localized: "A task wants to change a note you wrote or approved. Your words stay until you choose.", comment: "Memory: what a proposal is")
                ) {
                    ForEach(notes) { note in proposalCard(note) }
                }
            }
            GroupedSection(header: nil, footer: nil) {
                WebRow(target: WebSettingsPage.Target(tab: "memory", title: String(localized: "All notes, trash and lessons", comment: "Memory: the web page row")), systemImage: "brain", description: nil)
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    @ViewBuilder private func proposalCard(_ note: MemoryNote) -> some View {
        if let proposed = note.proposed {
            VStack(alignment: .leading, spacing: Space.s) {
                Text(String(localized: "Now", comment: "Memory proposal: the note's words as they stand")).textRole(.label).foregroundStyle(Tone.secondary)
                Text(note.content).textRole(.body).foregroundStyle(Tone.secondary)
                HStack(spacing: Space.xs) {
                    Text(String(localized: "Proposed", comment: "Memory proposal: the run's words")).textRole(.label).foregroundStyle(Tone.secondary)
                    if proposed.tainted {
                        Text(String(localized: "From outside content", comment: "Memory proposal: the run had read outside content")).textRole(.label).foregroundStyle(Tone.warn)
                            .padding(.horizontal, Space.xs).padding(.vertical, 2).background(Tone.warnFill, in: Capsule())
                    }
                }
                Text(proposed.content).textRole(.body).foregroundStyle(Tone.text)
                HStack(spacing: Space.s) {
                    Button(String(localized: "Use", comment: "Memory proposal: take the run's words")) { Task { await decide(note, take: true) } }
                        .buttonStyle(PrimaryButtonStyle())
                    Button(String(localized: "Drop", comment: "Memory proposal: keep the owner's words")) { Task { await decide(note, take: false) } }
                        .buttonStyle(SecondaryButtonStyle())
                }
                .disabled(deciding == note.id)
            }
            .padding(Space.groupedGutter)
            .accessibilityElement(children: .contain)
        }
    }

    private func load() async {
        proposals = await model.read { api throws(DaemonRequestError) in try await api.proposedNotes() }
    }

    private func search() async {
        let words: String = query.trimmingCharacters(in: .whitespaces)
        guard !words.isEmpty else {
            hits = nil
            return
        }
        hits = .loading
        hits = await model.read { api throws(DaemonRequestError) in try await api.searchMemory(words, limit: 20) }
    }

    private func decide(_ note: MemoryNote, take: Bool) async {
        guard let at = note.proposed?.at else { return }
        deciding = note.id
        let id: String = note.id
        let result: Result<MemoryNote, SettingsFailureBox> = await model.write { api throws(DaemonRequestError) in
            take ? try await api.takeProposal(note: id, at: at) : try await api.dropProposal(note: id, at: at)
        }
        switch result {
        case .success:
            if var all = proposals.value { all.removeAll { $0.id == id }; proposals = .loaded(all) }
            problem = nil
        case .failure(let box):
            problem = box.failure
            await load()
        }
        deciding = nil
    }
}
