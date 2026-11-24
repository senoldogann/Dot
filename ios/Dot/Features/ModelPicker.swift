import SwiftUI

/// The model and the reasoning level of one chat (or of a chat about to start): the default model first, then each
/// provider's models that can use tools, and, for a model that offers a choice, its levels. The choice is made on a tap
/// and the daemon told by the caller; this view only reports.
struct ModelPickerView: View {
    let store: NativeStore
    let model: String?
    let effort: String?
    let onModel: (String?) -> Void
    let onEffort: (String?) -> Void

    var body: some View {
        let providers: [ProviderModels] = store.providers
        let main: String? = store.models?.main
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                if let chosen: ModelOption = providers.option(model ?? main), !chosen.pickableEfforts.isEmpty {
                    CardGroup(title: "Reasoning", footer: "How long the model thinks before it answers.") {
                        effortRow(title: "Default", detail: chosen.defaultEffort.map(effortWords), level: nil)
                        ForEach(chosen.pickableEfforts, id: \.self) { level in
                            CardDivider(inset: Space.l)
                            effortRow(title: effortWords(level), detail: nil, level: level)
                        }
                    }
                }
                CardGroup(title: "Model") {
                    modelRow(title: "Default", detail: label(of: main, in: providers), ref: nil)
                    if providers.isEmpty {
                        CardDivider(inset: Space.l)
                        HStack(spacing: Space.s) {
                            ProgressView().controlSize(.small)
                            Text("Reading the models…").font(Theme.small).foregroundStyle(Theme.muted)
                        }
                        .padding(Space.l)
                    }
                }
                ForEach(providers.pickable) { provider in
                    // Each provider's name once, then its models.
                    CardGroup(title: provider.provider) {
                        ForEach(Array(provider.models.enumerated()), id: \.element.id) { index, option in
                            if index > 0 { CardDivider(inset: Space.l) }
                            modelRow(title: option.label, detail: option.free ? "free" : nil, ref: option.ref)
                        }
                    }
                }
            }
            .padding(Space.l)
        }
        .dotScreenBackground(inSheet: true)
        .navigationTitle("Model")
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadProviders() }
        .sensoryFeedback(.selection, trigger: model)
        .sensoryFeedback(.selection, trigger: effort)
    }

    private func modelRow(title: String, detail: String?, ref: String?) -> some View {
        Button { onModel(ref) } label: {
            choiceRow(title: title, detail: detail, selected: model == ref)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("model-\(ref ?? "default")")
    }

    private func effortRow(title: String, detail: String?, level: String?) -> some View {
        Button { onEffort(level) } label: {
            choiceRow(title: title, detail: detail, selected: effort == level)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("effort-\(level ?? "default")")
    }

    private func choiceRow(title: String, detail: String?, selected: Bool) -> some View {
        HStack(spacing: Space.s) {
            Text(title).font(.subheadline).foregroundStyle(Theme.text)
            if let detail { Text(detail).font(Theme.meta).foregroundStyle(Theme.faint) }
            Spacer()
            if selected {
                DotIcon(.check, size: 16, relativeTo: .footnote).foregroundStyle(Theme.text)
                    .transition(.scale.combined(with: .opacity))
            }
        }
        .padding(.horizontal, Space.l)
        .frame(minHeight: 48)
        .contentShape(Rectangle())
        .dotAnimation(Motion.snappy, value: selected)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func label(of ref: String?, in providers: [ProviderModels]) -> String? {
        guard let ref else { return nil }
        return providers.option(ref)?.label ?? ref.split(separator: ":").last.map(String.init)
    }
}

/// The words of a model for a pill or a row: its label when the lists hold it, else the name after the provider.
func modelWords(_ ref: String?, in providers: [ProviderModels], main: String?) -> String {
    guard let shown: String = ref ?? main else { return "Default model" }
    return providers.option(shown)?.label ?? shown.split(separator: ":").last.map(String.init) ?? shown
}
