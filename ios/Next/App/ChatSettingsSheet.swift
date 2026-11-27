import DotEngine
import SwiftUI

/// The chat's settings (PLAN §24, owner-refs/ref-model-picker-1/2.png), opened from the composer's model chip and
/// before a new chat's first message, as inset groups the way iOS draws its settings (the owner's report of
/// 2026-11-27): the recent models, Options (reasoning level as a menu, fast mode only for a model that has it, approval
/// mode, plan first), the providers as groups that fold (their mark, a count when folded), each model a row with its
/// provider's mark, "Default" under the daemon's main, a ✓ on the chosen one and a quiet ★ on a favourite (a long press
/// stars it; favourites float up); a floating "Find a model" search with a filter button; the switch to manage hidden
/// models. ✕ leaves the chat's picks as they were; ✓ keeps them (the stars and hidden models are the phone's, kept
/// at once).
struct ChatSettingsSheet: View {
    let initial: ChatSettingsDraft
    let providers: [ProviderModels]
    /// The daemon's main model and approval mode (`GET /api/status`), what "Default" means; `nil` until read.
    let defaults: ChatDefaults?
    /// Why the models could not be read, and how to read them again.
    let problem: String?
    let retry: () -> Void
    let confirm: (ChatSettingsDraft) -> Void
    let cancel: () -> Void
    @State private var draft: ChatSettingsDraft
    @State private var marks: ModelMarks = ModelMarks.read(.standard)
    @State private var query: String = ""
    @State private var filter: ModelFilter = .all
    @State private var managesHidden: Bool = false

    init(
        initial: ChatSettingsDraft, providers: [ProviderModels], defaults: ChatDefaults?, problem: String?, retry: @escaping () -> Void,
        confirm: @escaping (ChatSettingsDraft) -> Void, cancel: @escaping () -> Void
    ) {
        self.initial = initial
        self.providers = providers
        self.defaults = defaults
        self.problem = problem
        self.retry = retry
        self.confirm = confirm
        self.cancel = cancel
        _draft = State(initialValue: initial)
    }

    private var chosenRef: String? { effectiveModel(draft.choices, defaultRef: defaults?.model) }
    private var chosen: ModelOption? { providers.flatMap(\.models).first { $0.ref == chosenRef } }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Space.xl) {
                    ModelCatalog(
                        providers: providers, defaultRef: defaults?.model, chosenRef: chosenRef, query: query, filter: filter,
                        managesHidden: managesHidden, marks: $marks, pick: { draft.choices = picking($0, in: draft.choices) }, problem: problem, retry: retry
                    ) {
                        // The approvals and plan first do not wait on the models.
                        if query.isEmpty { options }
                    }
                    if query.isEmpty { catalog }
                }
                .padding(.horizontal, Space.gutter)
                .padding(.top, Space.xs)
                .padding(.bottom, 96)
                .readableColumn()
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Tone.sheet)
            .safeAreaInset(edge: .bottom) { ModelSearchBar(query: $query, filter: $filter) }
            .navigationTitle(String(localized: "Chat settings", comment: "Title of the chat's settings sheet: model, reasoning, approvals"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), systemImage: "xmark", role: .cancel, action: cancel)
                        .accessibilityIdentifier("chatSettings.cancel")
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Done", comment: "Chat settings toolbar: keeps the picks"), systemImage: "checkmark", role: .confirm) {
                        if let picked = draft.choices.model {
                            UserDefaults.standard.set(RecentModels.adding(picked, to: RecentModels.read(.standard)), forKey: RecentModels.key)
                        }
                        confirm(draft)
                    }
                        .accessibilityIdentifier("chatSettings.done")
                }
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .interactiveDismissDisabled(draft != initial)
        .tint(Tone.text)
    }

    // MARK: Options and catalog

    /// The options as one inset group (the owner's report of 2026-11-27): only what the chosen model has. The level is a
    /// menu of its own levels in full words; fast mode shows only for a model that has it; the approvals open their page;
    /// plan first is a switch with what it does under it.
    @ViewBuilder private var options: some View {
        InsetSection(header: String(localized: "Options", comment: "Chat settings section: reasoning, fast mode, approvals, plan first")) {
            if let chosen, chosen.efforts.count >= 2 {
                MenuPickerRow(
                    title: String(localized: "Reasoning", comment: "Chat settings row: the model's reasoning level"),
                    choices: [MenuChoice(value: String?.none, title: defaultLevelWords(chosen))]
                        + chosen.efforts.map { MenuChoice(value: String?.some($0), title: effortTitle($0)) },
                    selection: effortBinding(chosen))
                    .accessibilityIdentifier("chatSettings.reasoning")
            } else if chosen == nil, let effort = draft.choices.effort {
                // The models could not be read: the level held is shown, to change once they are.
                ListRow(systemImage: nil, title: String(localized: "Reasoning", comment: "Chat settings row: the model's reasoning level"), description: nil, accessory: .detail(effortTitle(effort)))
            }
            if chosen?.fastMode == true {
                ToggleRow(
                    systemImage: nil, title: String(localized: "Fast mode", comment: "Chat settings row: the model's fast mode"), description: nil,
                    isOn: Binding(get: { draft.choices.speed == "fast" }, set: { draft.choices = draft.choices.with(speed: $0 ? "fast" : "standard") }))
                    .accessibilityIdentifier("chatSettings.fast")
            }
            NavigationLink {
                approvalsPage
            } label: {
                ListRow(
                    systemImage: nil, title: String(localized: "Approvals", comment: "Chat settings row: the approval mode the run works under"), description: nil,
                    accessory: .detailAndChevron(approvalWords(draft.choices.approvals)))
            }
            .buttonStyle(RowPressStyle())
            .accessibilityIdentifier("chatSettings.approvals")
            ToggleRow(
                systemImage: nil, title: String(localized: "Plan first", comment: "Chat settings row: the next message writes its plan and asks before it changes anything"),
                description: String(localized: "The next message writes its plan and asks before it changes anything.", comment: "Chat settings: what plan first does"),
                isOn: $draft.planFirst)
                .accessibilityIdentifier("chatSettings.planFirst")
        }
    }

    @ViewBuilder private var catalog: some View {
        InsetSection(header: nil) {
            ToggleRow(
                systemImage: nil, title: String(localized: "Manage hidden models", comment: "Chat settings row: shows every model with a switch to hide it"),
                description: managesHidden
                    ? String(localized: "Tap the eye of a model to hide it from this list or show it again.", comment: "Chat settings: how hiding a model works")
                    : nil,
                isOn: $managesHidden)
                .accessibilityIdentifier("chatSettings.manageHidden")
        }
    }

    /// The level left to the model: its own default named when the daemon says it ("Default (High)").
    private func defaultLevelWords(_ option: ModelOption) -> String {
        guard let level = option.defaultEffort else {
            return String(localized: "Default level", comment: "Chat settings: the reasoning segment that leaves the level to the model's default")
        }
        return String(localized: "Default (\(effortTitle(level)))", comment: "Chat settings: the reasoning choice that leaves the level to the model; the argument is the model's own level")
    }

    /// The chosen model's level: the one picked, or `nil` for Default level (the configured level on it).
    private func effortBinding(_ option: ModelOption) -> Binding<String?> {
        Binding(
            get: { draft.choices.effort },
            set: { draft.choices = draft.choices.with(model: draft.choices.model ?? option.ref, effort: $0) })
    }

    private var approvalsPage: some View {
        SettingsScroll(title: String(localized: "Approvals", comment: "Chat settings row: the approval mode the run works under")) {
            GroupedSection(header: nil, footer: String(localized: "Free mode, which asks nothing, is switched for all of Dot on the Mac.", comment: "Chat settings: free mode is not a chat's choice")) {
                RadioRow(title: approvalWords(nil), description: macApprovalWords, isSelected: draft.choices.approvals == nil) {
                    draft.choices = draft.choices.with(approvals: nil)
                }
                ForEach(ApprovalChoice.allCases, id: \.self) { choice in
                    RadioRow(title: choice.title, description: choice.hint, isSelected: draft.choices.approvals == choice.rawValue) {
                        draft.choices = draft.choices.with(approvals: choice.rawValue)
                    }
                    .accessibilityIdentifier("chatSettings.approval.\(choice.rawValue)")
                }
            }
        }
        .background(Tone.sheet)
    }

    /// The Mac's own approval mode by name, under "Dot's setting" on the Approvals page; `nil` until it is read.
    private var macApprovalWords: String? {
        defaults.flatMap { ApprovalChoice(rawValue: $0.approvals) }?.title
    }

    private func approvalWords(_ approvals: String?) -> String {
        if let approvals, let choice = ApprovalChoice(rawValue: approvals) { return choice.title }
        // The Mac's own mode is named on the Approvals page; the row keeps to two words, so it is never cut.
        return String(localized: "Dot's setting", comment: "Chat settings: the approval mode configured on the Mac")
    }
}

/// What "Default" means in the chat's settings: the daemon's main model and approval mode.
struct ChatDefaults: Equatable, Sendable {
    let model: String
    let approvals: String
}
