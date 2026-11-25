import DotEngine
import SwiftUI

/// A level of reasoning in the owner's words (the web's `efforts` labels).
func effortWords(_ effort: String) -> String {
    switch effort {
    case "off": String(localized: "Off", comment: "Reasoning level")
    case "on": String(localized: "On", comment: "Reasoning level")
    case "minimal": String(localized: "Minimal", comment: "Reasoning level")
    case "low": String(localized: "Low", comment: "Reasoning level")
    case "medium": String(localized: "Medium", comment: "Reasoning level")
    case "high": String(localized: "High", comment: "Reasoning level")
    case "xhigh": String(localized: "Extra high", comment: "Reasoning level")
    case "max": String(localized: "Max", comment: "Reasoning level")
    default: effort
    }
}

/// The levels a model offers, or every level when it names none (the daemon checks the level it is given).
func effortChoices(_ ref: String, in options: [ProviderModels]) -> [String] {
    for provider in options {
        if let model = provider.models.first(where: { $0.ref == ref }), model.efforts.count >= 2 { return model.efforts }
    }
    return ["low", "medium", "high", "xhigh", "max"]
}

/// A provider's name as the web shows it.
func providerWords(_ provider: String) -> String {
    switch provider {
    case "openai": "OpenAI"
    case "anthropic": "Anthropic"
    case "opencode_go": "OpenCode Go"
    case "openrouter": "OpenRouter"
    case "google": "Google"
    case "antigravity": "Google Antigravity"
    case "opencode": "OpenCode"
    case "claude_code": "Claude Code"
    case "ollama": "Ollama"
    default: provider
    }
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

/// The models whose label or reference hold every word of `query`, folded for case and accents (pure).
func modelsMatching(_ query: String, in options: [ProviderModels]) -> [ProviderModels] {
    let words: [String] = query.lowercased().folding(options: .diacriticInsensitive, locale: nil).split(separator: " ").map(String.init)
    guard !words.isEmpty else { return options }
    return options.compactMap { provider in
        let hits: [ModelOption] = provider.models.filter { option in
            let text: String = "\(option.label) \(option.ref)".lowercased().folding(options: .diacriticInsensitive, locale: nil)
            return words.allSatisfy { text.contains($0) }
        }
        return hits.isEmpty ? nil : ProviderModels(provider: provider.provider, models: hits, error: nil)
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

/// The model picker of PLAN §24 (owner-refs/ref-model-picker-1/2.png): a sheet with ✕ and ✓, the providers as groups
/// that fold (a count when folded), a ✓ on the chosen model and a "Default" mark on Dot's default model, a floating
/// "Find a model" field at the foot, and an Options group with the level of reasoning when the choice has one.
struct ModelPickerSheet: View {
    let pick: ModelPick
    let defaultRef: String
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var chosen: String = ""
    @State private var effort: String = ""
    @State private var folded: Set<String> = []
    @State private var query: String = ""
    @State private var saving: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Models", comment: "Where on the Mac: the Models page"), retry: nil) }
                    let shown: [ProviderModels] = modelsMatching(query, in: pick.candidates)
                    if shown.isEmpty {
                        ContentUnavailableView.search(text: query)
                    }
                    ForEach(shown, id: \.provider) { provider in group(provider) }
                    if pick.effort != nil, !chosen.isEmpty {
                        GroupedSection(header: String(localized: "Options", comment: "Model picker: the options under the models"), footer: nil) {
                            Menu {
                                ForEach(effortChoices(chosen, in: pick.candidates), id: \.self) { level in
                                    Button(effortWords(level)) { effort = level }
                                }
                            } label: {
                                ListRow(systemImage: "brain", title: String(localized: "Reasoning", comment: "Model picker: the level of reasoning"), description: nil, accessory: .detailAndChevron(effortWords(effort)))
                            }
                            .buttonStyle(RowPressStyle())
                        }
                    }
                }
                .padding(.horizontal, Space.gutter)
                .padding(.top, Space.groupedGutter)
                .padding(.bottom, 96)
                .readableColumn()
            }
            .background(Tone.sheet)
            .safeAreaInset(edge: .bottom) { searchField }
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
            // Every group folds but the chosen model's and the default's, as the reference opens.
            folded = Set(pick.candidates.filter { provider in !provider.models.contains { $0.ref == pick.current || $0.ref == defaultRef } }.map(\.provider))
            if folded.count == pick.candidates.count { folded = [] }
        }
    }

    private var changed: Bool { chosen != pick.current || (pick.effort != nil && effort != pick.effort) }

    @ViewBuilder private func group(_ provider: ProviderModels) -> some View {
        let isFolded: Bool = folded.contains(provider.provider) && query.isEmpty
        VStack(alignment: .leading, spacing: Space.xs) {
            Button {
                if folded.contains(provider.provider) { folded.remove(provider.provider) } else { folded.insert(provider.provider) }
            } label: {
                HStack {
                    Text(providerWords(provider.provider)).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
                    Spacer()
                    if isFolded { Text(verbatim: "\(provider.models.count)").textRole(.footnote).foregroundStyle(Tone.secondary).tabularNumbers() }
                    Image(systemName: isFolded ? "chevron.down" : "chevron.up").font(.footnote.weight(.medium)).foregroundStyle(Tone.secondary)
                }
                .padding(.horizontal, Space.xs)
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(providerWords(provider.provider))
            .accessibilityValue(isFolded ? String(localized: "Folded", comment: "VoiceOver: a folded group") : String(localized: "Open", comment: "VoiceOver: an open group"))
            if !isFolded {
                VStack(spacing: 0) {
                    ForEach(provider.models, id: \.ref) { option in row(option) }
                }
                .background(Tone.card, in: rounded(Radius.section))
                .clipShape(rounded(Radius.section))
            }
        }
    }

    private func row(_ option: ModelOption) -> some View {
        Button {
            chosen = option.ref
            if pick.effort != nil, !effortChoices(option.ref, in: pick.candidates).contains(effort) { effort = option.defaultEffort ?? "medium" }
        } label: {
            HStack(spacing: Space.s) {
                Text(option.label).textRole(.body).foregroundStyle(Tone.text).multilineTextAlignment(.leading)
                if option.ref == defaultRef {
                    Text(String(localized: "Default", comment: "Model picker: the mark of Dot's default model")).textRole(.label).fontWeight(.medium).foregroundStyle(Tone.secondary)
                        .padding(.horizontal, Space.xs).padding(.vertical, 2).background(Tone.nested, in: rounded(6))
                }
                Spacer(minLength: Space.s)
                Image(systemName: "checkmark").font(.system(.callout, weight: .semibold)).foregroundStyle(Tone.text).opacity(chosen == option.ref ? 1 : 0).accessibilityHidden(true)
            }
            .padding(.horizontal, Space.groupedGutter)
            .frame(minHeight: Metric.row)
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(chosen == option.ref ? [.isSelected] : [])
    }

    private var searchField: some View {
        HStack(spacing: Space.s) {
            Image(systemName: "magnifyingglass").foregroundStyle(Tone.secondary)
            TextField(String(localized: "Find a model", comment: "Model picker: the search field"), text: $query)
                .textRole(.body).autocorrectionDisabled().textInputAutocapitalization(.never)
            if !query.isEmpty {
                Button(String(localized: "Clear", comment: "Clears a search field"), systemImage: "xmark.circle.fill") { query = "" }
                    .labelStyle(.iconOnly).foregroundStyle(Tone.secondary)
            }
        }
        .padding(.horizontal, Space.m)
        .frame(minHeight: Metric.hit + 4)
        .interactiveGlass(in: Capsule())
        .padding(.horizontal, Space.gutter)
        .padding(.bottom, Space.xs)
    }

    private func commit() async {
        saving = true
        problem = await pick.save(chosen, pick.effort == nil ? nil : effort)
        saving = false
        if problem == nil { dismiss() }
    }
}
