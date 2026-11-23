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
        List {
            let providers: [ProviderModels] = store.providers
            let main: String? = store.models?.main
            if let chosen: ModelOption = providers.option(model ?? main), !chosen.pickableEfforts.isEmpty {
                Section("Reasoning") {
                    Group {
                        effortRow(title: "Default", detail: chosen.defaultEffort.map(effortWords), level: nil)
                        ForEach(chosen.pickableEfforts, id: \.self) { level in
                            effortRow(title: effortWords(level), detail: nil, level: level)
                        }
                    }
                    .listRowBackground(Theme.surface)
                }
            }
            Section("Model") {
                Group {
                    modelRow(title: "Default", detail: label(of: main, in: providers), ref: nil)
                    ForEach(providers.pickable) { provider in
                        // Each provider's name once, then its models.
                        Text(provider.provider).font(Theme.meta).foregroundStyle(Theme.faint)
                        ForEach(provider.models) { option in
                            modelRow(title: option.label, detail: option.free ? "free" : nil, ref: option.ref)
                        }
                    }
                    if providers.isEmpty {
                        Text("Reading the models…").font(Theme.small).foregroundStyle(Theme.faint)
                    }
                }
                .listRowBackground(Theme.surface)
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.background)
        .navigationTitle("Model")
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadProviders() }
    }

    private func modelRow(title: String, detail: String?, ref: String?) -> some View {
        Button { onModel(ref) } label: {
            HStack {
                Text(title).font(Theme.small).foregroundStyle(Theme.text)
                if let detail { Text(detail).font(Theme.meta).foregroundStyle(Theme.faint) }
                Spacer()
                if model == ref { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
            }
            .contentShape(Rectangle())
        }
        .accessibilityIdentifier("model-\(ref ?? "default")")
    }

    private func effortRow(title: String, detail: String?, level: String?) -> some View {
        Button { onEffort(level) } label: {
            HStack {
                Text(title).font(Theme.small).foregroundStyle(Theme.text)
                if let detail { Text(detail).font(Theme.meta).foregroundStyle(Theme.faint) }
                Spacer()
                if effort == level { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
            }
            .contentShape(Rectangle())
        }
        .accessibilityIdentifier("effort-\(level ?? "default")")
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
