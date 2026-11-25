import DotEngine
import SwiftUI

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

/// Millions of tokens as typed, in tokens; `nil` for what is not a number or does not fit (pure).
func capTokens(_ millions: String) -> Int? {
    guard let value = Int(millions), value >= 0 else { return nil }
    let (tokens, overflow): (Int, Bool) = value.multipliedReportingOverflow(by: 1_000_000)
    return overflow ? nil : tokens
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
                            changed[keyPath: key.keyPath] = noCap ? 0 : capTokens(millions) ?? 0
                            problem = await save(changed)
                            saving = false
                            if problem == nil { dismiss() }
                        }
                    }
                    .disabled(saving || (!noCap && capTokens(millions) == nil))
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

