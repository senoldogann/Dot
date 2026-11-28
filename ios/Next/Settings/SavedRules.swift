import DotEngine
import SwiftUI

private let ruleHistoryDays: Int = 30

func ruleAnswerWords(_ answer: RuleAnswer) -> String {
    switch answer {
    case .allow: String(localized: "Goes ahead without asking", comment: "Rules: an action the rule lets Dot do without asking")
    case .preapproved: String(localized: "Goes ahead, whatever it has read", comment: "Rules: a preapproved action, even after outside content was read")
    case .ask: String(localized: "Ask me first", comment: "Rules: ask before doing the matching action")
    case .handoff: String(localized: "Leave it to me", comment: "Rules: hand the matching action back to the owner")
    }
}

struct SavedRulesSection: View {
    let macPlace: String
    @Environment(AppModel.self) private var model: AppModel
    @State private var rules: Loadable<[SavedRule]> = .loading
    @State private var adding: Bool = false

    var body: some View {
        GroupedSection(header: String(localized: "Rules", comment: "Settings row: what Dot may do without asking"), footer: nil) {
            LoadedPage(state: rules, macPlace: macPlace, retry: { Task { await load() } }) { saved in
                if saved.isEmpty {
                    ListRow(systemImage: nil, title: String(localized: "No rules yet", comment: "Rules: empty list"), description: nil, accessory: .none)
                }
                ForEach(saved) { rule in
                    NavigationRow(
                        systemImage: "shield", title: rule.description.isEmpty ? ruleAnswerWords(rule.behavior) : rule.description,
                        description: !rule.enabled ? String(localized: "Off", comment: "Rules: a rule is switched off") : rule.neverDecidedBy == nil
                            ? ruleAnswerWords(rule.behavior) : String(localized: "Never used", comment: "Rules: another rule always decides first"),
                        detail: nil
                    ) { SavedRulePage(rule: rule, macPlace: macPlace) }
                }
                Button { adding = true } label: {
                    ListRow(systemImage: "plus", title: String(localized: "Add rule", comment: "Rules: add a rule from the owner’s words"), description: nil, accessory: .none)
                }
                .buttonStyle(.plain)
            }
        }
        .task { await load() }
        .refreshable { await load() }
        .sheet(isPresented: $adding) { AddRuleSheet(macPlace: macPlace, added: load) }
    }

    private func load() async {
        rules = await model.read { api throws(DaemonRequestError) in try await api.rules() }
    }
}

private struct SavedRulePage: View {
    let rule: SavedRule
    let macPlace: String
    @Environment(AppModel.self) private var model: AppModel
    @State private var activity: Loadable<RuleActivity> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Details", comment: "Waiting line: opens the run inspector")) {
            GroupedSection(header: ruleAnswerWords(rule.behavior), footer: nil) {
                ListRow(systemImage: nil, title: String(localized: "Tool", comment: "Rules detail: the action’s tool name"), description: rule.toolGlob, accessory: .none)
                ListRow(systemImage: nil, title: String(localized: "Pattern", comment: "Rules detail: the pattern an action must match"), description: rule.matchRegex.isEmpty
                        ? String(localized: "None: it fits every time.", comment: "Rules detail: there is no pattern to match") : rule.matchRegex, accessory: .none)
            }
            if rule.enabled {
                GroupedSection(header: String(localized: "Used lately", comment: "Rules detail: recent use of the rule"), footer: nil) {
                    LoadedPage(state: activity, macPlace: macPlace, retry: { Task { await load() } }) { usage in
                        if let problem = usage.problem {
                            FailureBanner(failure: .other(problem), macPlace: macPlace, retry: nil)
                        } else if let entry = usage.rules.first(where: { $0.ruleId == rule.id }) {
                            ListRow(systemImage: nil, title: String(localized: "Steps decided", comment: "Rules detail: count of steps the rule decided"),
                                    description: usage.partial ? String(localized: "Only the newest steps were checked.", comment: "Rules: the estimate covers only part of the requested time") : nil,
                                    accessory: .detail(entry.decides.formatted()))
                        }
                    }
                }
            }
        }
        .task { if rule.enabled { await load() } }
    }

    private func load() async {
        activity = await model.read { api throws(DaemonRequestError) in try await api.ruleActivity(days: ruleHistoryDays) }
    }
}

private struct ReviewedRule {
    let draft: DraftRule
    let preview: RuleTryout
}

private struct AddRuleSheet: View {
    let macPlace: String
    let added: () async -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var words: String = ""
    @FocusState private var editingWords: Bool
    @State private var answer: PhoneRuleAnswer = .ask
    @State private var reviewed: ReviewedRule?
    @State private var problem: SettingsFailure?
    @State private var busy: Bool = false

    var body: some View {
        NavigationStack {
            SettingsScroll(title: String(localized: "Add rule", comment: "Rules: add a rule from the owner’s words")) {
                if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
                if let reviewed {
                    reviewSection(reviewed)
                } else {
                    GroupedSection(header: String(localized: "When Dot wants to", comment: "Rules: introduces the action that triggers the rule"), footer: nil) {
                        TextField(String(localized: "Describe what Dot wants to do", comment: "Rules: placeholder for the action that triggers the rule"), text: $words, axis: .vertical)
                            .lineLimit(2...6).padding(Space.groupedGutter)
                            .focused($editingWords).accessibilityIdentifier("rule.words")
                        ForEach(PhoneRuleAnswer.allCases, id: \.self) { choice in
                            RadioRow(title: ruleAnswerWords(choice.answer), description: nil, isSelected: answer == choice) {
                                answer = choice
                            }
                        }
                    }
                    Button(String(localized: "Add rule", comment: "Rules: add a rule from the owner’s words")) { Task { await prepare() } }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || words.unicodeScalars.count > 400 || busy)
                }
                if busy { ProgressView() }
            }
            .disabled(busy)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits")) { dismiss() }.disabled(busy)
                }
            }
        }
        .interactiveDismissDisabled(busy)
    }

    private func reviewSection(_ reviewed: ReviewedRule) -> some View {
        GroupedSection(header: ruleAnswerWords(answer.answer), footer: nil) {
            ListRow(systemImage: "shield", title: reviewed.draft.description, description: nil, accessory: .none)
            DisclosureGroup(String(localized: "Details", comment: "Waiting line: opens the run inspector")) {
                ListRow(systemImage: nil, title: String(localized: "Tool", comment: "Rules detail: the action’s tool name"), description: reviewed.draft.toolGlob, accessory: .none)
                ListRow(systemImage: nil, title: String(localized: "Pattern", comment: "Rules detail: the pattern an action must match"), description: reviewed.draft.matchRegex.isEmpty
                        ? String(localized: "None: it fits every time.", comment: "Rules detail: there is no pattern to match") : reviewed.draft.matchRegex, accessory: .none)
            }
            .padding(Space.groupedGutter)
            ListRow(systemImage: nil, title: String(localized: "Would have matched \(reviewed.preview.fits) of \(reviewed.preview.scanned) steps.", comment: "Rules preview: number of matching steps and total steps checked"),
                    description: String(localized: "It would decide \(reviewed.preview.wouldDecide) of them.", comment: "Rules preview: how many steps the draft would decide"), accessory: .none)
            if reviewed.preview.partial {
                ListRow(systemImage: nil, title: String(localized: "Only the newest steps were checked.", comment: "Rules: the estimate covers only part of the requested time"), description: nil, accessory: .none)
            }
            if let problem = reviewed.preview.problem {
                FailureBanner(failure: .other(problem), macPlace: macPlace, retry: nil)
            }
            Button(String(localized: "Add it", comment: "Rules: confirm adding the previewed draft")) { Task { await save(reviewed.draft) } }
                .buttonStyle(PrimaryButtonStyle()).disabled(reviewed.preview.problem != nil)
            Button(String(localized: "Change the words", comment: "Rules: return to editing a draft")) {
                self.reviewed = nil
                problem = nil
            }
            .buttonStyle(SecondaryButtonStyle())
        }
    }

    private func prepare() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        problem = nil
        editingWords = false
        let action: String = words.trimmingCharacters(in: .whitespacesAndNewlines)
        let sentence: String = switch answer {
        case .ask: String(localized: "When Dot wants to \(action), it should ask me first.", comment: "Rule sent to the drafter: the argument is the action the owner describes")
        case .handoff: String(localized: "When Dot wants to \(action), it should stop and let me do it.", comment: "Rule sent to the drafter: the argument is the action the owner wants Dot to hand back")
        }
        let draft: DraftRule
        switch await model.write({ api throws(DaemonRequestError) in try await api.draftRule(sentence) }) {
        case .success(let value): draft = value
        case .failure(let box): problem = box.failure; return
        }
        guard draft.behavior == answer.answer else {
            problem = .other(String(localized: "Dot read your words differently from your choice.", comment: "Rules: the draft proposed a different answer from the one selected"))
            return
        }
        let chosen = answer
        switch await model.write({ api throws(DaemonRequestError) in try await api.previewRule(draft, answer: chosen, days: ruleHistoryDays) }) {
        case .success(let preview): reviewed = ReviewedRule(draft: draft, preview: preview)
        case .failure(let box): problem = box.failure
        }
    }

    private func save(_ draft: DraftRule) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        let chosen = answer
        switch await model.write({ api throws(DaemonRequestError) in try await api.addRule(draft, answer: chosen) }) {
        case .success:
            await added()
            dismiss()
        case .failure(let box): problem = box.failure
        }
    }
}
