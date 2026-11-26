import DotEngine
import SwiftUI

/// The levels a model offers, or every level when it names none (the daemon checks the level it is given).
func effortChoices(_ ref: String, in options: [ProviderModels]) -> [String] {
    for provider in options {
        if let model = provider.models.first(where: { $0.ref == ref }), model.efforts.count >= 2 { return model.efforts }
    }
    return ["low", "medium", "high", "xhigh", "max"]
}

/// What a model may be chosen for: each takes models that use actions, and some providers answer only as their own
/// agent program, which a role of Dot's tools cannot use (the web's `answersRole`).
enum ModelUse: Sendable {
    case main
    case fast
    /// A run of Dot's tools: the code review.
    case dotsTools
}

/// The models of `options` that may be chosen for `use`, by provider, providers left empty dropped (pure).
func modelCandidates(_ options: [ProviderModels], for use: ModelUse) -> [ProviderModels] {
    let shut: Set<String> =
        switch use {
        case .main: []
        case .fast: ["opencode", "claude_code"]
        case .dotsTools: ["opencode"]
        }
    return options.compactMap { provider in
        guard !shut.contains(provider.provider) else { return nil }
        let fit: [ModelOption] = provider.models.filter { $0.tools != false }
        return fit.isEmpty ? nil : ProviderModels(provider: provider.provider, models: fit, error: nil)
    }
}

/// A choice the picker sheet makes: which model, under which title, and how the Mac takes it.
struct ModelPick: Identifiable {
    let id: String
    let title: String
    let current: String
    /// The level of reasoning chosen with the model; `nil` when the choice has none.
    let effort: String?
    let candidates: [ProviderModels]
    let save: @MainActor (String, String?) async -> SettingsFailure?
}

/// Settings' model picker (PLAN §24, owner-refs/ref-model-picker-1/2.png) on the app's one model catalog
/// (`ModelCatalog`, as the chat's settings): the recent models, the providers as folding groups with their marks,
/// Default, ✓ and ☆, the search with its filter, hidden models; under them the level of reasoning when the choice has
/// one, the chosen model's own levels as one segmented control. ✕ drops the pick, ✓ writes it to the Mac.
struct ModelPickerSheet: View {
    let pick: ModelPick
    let defaultRef: String
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var chosen: String = ""
    @State private var effort: String = ""
    @State private var marks: ModelMarks = ModelMarks.read(.standard)
    @State private var query: String = ""
    @State private var filter: ModelFilter = .all
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Space.groupedGutter) {
                    if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Models", comment: "Where on the Mac: the Models page"), retry: nil) }
                    ModelCatalog(
                        providers: pick.candidates, defaultRef: defaultRef.isEmpty ? nil : defaultRef, chosenRef: chosen.isEmpty ? nil : chosen,
                        query: query, filter: filter, managesHidden: false, marks: $marks, pick: { option in choose(option) }, problem: nil, retry: {}
                    ) {
                        if query.isEmpty, pick.effort != nil, !chosen.isEmpty { reasoning }
                    }
                }
                .padding(.horizontal, Space.gutter)
                .padding(.top, Space.xs)
                .padding(.bottom, 96)
                .readableColumn()
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Tone.sheet)
            .safeAreaInset(edge: .bottom) { ModelSearchBar(query: $query, filter: $filter) }
            .navigationTitle(pick.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), systemImage: "xmark") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits"), systemImage: "checkmark") { Task { await commit() } }
                        .disabled(saving || !changed)
                }
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .interactiveDismissDisabled(saving)
        .tint(Tone.text)
        .onAppear {
            chosen = pick.current
            effort = pick.effort ?? ""
        }
    }

    private var changed: Bool { chosen != pick.current || (pick.effort != nil && effort != pick.effort) }

    /// A pick keeps the level when the model has it, else takes the model's own default.
    private func choose(_ option: ModelOption) {
        chosen = option.ref
        if pick.effort != nil, !effortChoices(option.ref, in: pick.candidates).contains(effort) { effort = option.defaultEffort ?? "medium" }
    }

    /// The level of reasoning: the chosen model's levels, one tap.
    private var reasoning: some View {
        GroupedSection(header: String(localized: "Options", comment: "Model picker: the options under the models"), footer: nil) {
            VStack(alignment: .leading, spacing: Space.xs) {
                Text(String(localized: "Reasoning", comment: "Model picker: the level of reasoning")).textRole(.body).foregroundStyle(Tone.text)
                Picker(String(localized: "Reasoning", comment: "Model picker: the level of reasoning"), selection: $effort) {
                    ForEach(effortChoices(chosen, in: pick.candidates), id: \.self) { level in Text(effortTitle(level)).tag(level) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("modelPicker.reasoning")
            }
            .padding(.horizontal, Space.groupedGutter)
            .padding(.vertical, Space.s)
        }
    }

    private func commit() async {
        saving = true
        problem = await pick.save(chosen, pick.effort == nil ? nil : effort)
        saving = false
        if problem == nil { dismiss() }
    }
}
