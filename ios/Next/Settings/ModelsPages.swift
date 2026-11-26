import DotEngine
import SwiftUI

// MARK: Models (items 1759, 1770)

/// What the Models page reads at once; the code review is its failure when it could not be read, and the rest stands.
struct ModelsPicture: Sendable {
    let config: ModelsConfig
    let options: [ProviderModels]
    let review: Loadable<CodeReviewConfig>

    func with(config: ModelsConfig) -> ModelsPicture { ModelsPicture(config: config, options: options, review: review) }
    func with(review: Loadable<CodeReviewConfig>) -> ModelsPicture { ModelsPicture(config: config, options: options, review: review) }
}

/// The label of a model reference, from the daemon's list; the reference itself when the list does not hold it.
func modelLabel(_ ref: String, in options: [ProviderModels]) -> String {
    guard !ref.isEmpty else { return String(localized: "Not set", comment: "Models: a role with no model of its own") }
    for provider in options { if let model = provider.models.first(where: { $0.ref == ref }) { return model.label } }
    return ref
}

/// Settings › Agents › Models, what a phone needs of it (PLAN §26): the default model and the fast model, chosen from
/// the daemon's list, and the model that reviews each push of Dot's own code with its level of reasoning (item 1663),
/// which a phone may now change (item 1770). The chat size and the providers are set on the Mac.
struct ModelsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<ModelsPicture> = .loading
    @State private var problem: SettingsFailure?
    @State private var picking: ModelPick?

    private var macPlace: String { String(localized: "Settings › Models", comment: "Where on the Mac: the Models page") }

    var body: some View {
        SettingsScroll(title: String(localized: "Models", comment: "Settings row")) {
            if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
            LoadedPage(state: state, macPlace: macPlace, retry: { Task { await load() } }) { picture in
                GroupedSection(
                    header: String(localized: "Dot thinks with", comment: "Models section: the main model and the roles"),
                    footer: String(localized: "A new model applies from each task's next start; running tasks keep theirs.", comment: "Models: footer under the roles")
                ) {
                    pickRow(String(localized: "Main model", comment: "Models: the main model"), current: picture.config.main, use: .main, picture: picture) { api, ref throws(DaemonRequestError) in
                        try await api.setMainModel(ref)
                    }
                    pickRow(String(localized: "Fast model", comment: "Models: the role for quick small steps"), current: picture.config.fast, use: .fast, picture: picture) { api, ref throws(DaemonRequestError) in
                        try await api.setRole(.fast, model: ref)
                    }
                }
                switch picture.review {
                case .loaded(let review):
                    GroupedSection(
                        header: String(localized: "Code review", comment: "Models section: the pre-push review's models (item 1663)"),
                        footer: String(localized: "Reviews each change of Dot's own code before it is pushed. It holds from the next push.", comment: "Models: what the code review is")
                    ) {
                        Button { pickReview(review, picture: picture) } label: {
                            ListRow(
                                systemImage: nil, title: String(localized: "Code review model", comment: "Code review: the first model"), description: nil,
                                accessory: .detailAndChevron("\(modelLabel(review.model, in: picture.options)) · \(effortTitle(review.effort))"))
                        }
                        .buttonStyle(RowPressStyle())
                    }
                case .failed(let failure):
                    FailureBanner(failure: failure, macPlace: macPlace, retry: { Task { await load() } })
                case .loading:
                    EmptyView()
                }
            }
        }
        .sheet(item: $picking) { pick in ModelPickerSheet(pick: pick, defaultRef: state.value?.config.main ?? "") }
        .task { await load() }
        .refreshable { await load() }
    }

    private func pickRow(
        _ title: String, current: String, use: ModelUse, picture: ModelsPicture,
        choose: @escaping @Sendable (DaemonAPI, String) async throws(DaemonRequestError) -> ModelsConfig
    ) -> some View {
        Button {
            picking = ModelPick(id: title, title: title, current: current, effort: nil, candidates: modelCandidates(picture.options, for: use)) { ref, _ in
                await apply({ api throws(DaemonRequestError) in try await choose(api, ref) }) { picture, config in picture.with(config: config) }
            }
        } label: {
            ListRow(systemImage: nil, title: title, description: nil, accessory: .detailAndChevron(modelLabel(current, in: picture.options)))
        }
        .buttonStyle(RowPressStyle())
    }

    /// The code review's model and its reasoning; a model chosen leaves the fallbacks, where it may not stand twice.
    private func pickReview(_ review: CodeReviewConfig, picture: ModelsPicture) {
        picking = ModelPick(
            id: "review", title: String(localized: "Code review model", comment: "Code review: the first model"), current: review.model, effort: review.effort,
            candidates: modelCandidates(picture.options, for: .dotsTools)
        ) { ref, effort in
            var change: CodeReviewChange = CodeReviewChange(review)
            change.model = ref
            change.effort = effort ?? review.effort
            change.fallback = review.fallback.filter { $0 != ref }
            return await apply({ api throws(DaemonRequestError) in try await api.changeCodeReview(change) }) { picture, saved in picture.with(review: .loaded(saved)) }
        }
    }

    private func load() async {
        let status: Loadable<StatusView> = await model.read { api throws(DaemonRequestError) in try await api.fullStatus() }
        guard case .loaded(let current) = status else {
            if case .failed(let failure) = status { state = .failed(failure) }
            return
        }
        let options: Loadable<[ProviderModels]> = await model.read { api throws(DaemonRequestError) in try await api.models() }
        let review: Loadable<CodeReviewConfig> = await model.read { api throws(DaemonRequestError) in try await api.codeReview() }
        state = .loaded(ModelsPicture(config: current.models, options: options.value ?? [], review: review))
    }

    /// Makes one write; on success the page takes the answer, else it says why and keeps what stood.
    private func apply<Value: Sendable>(
        _ call: (DaemonAPI) async throws(DaemonRequestError) -> Value, into: (ModelsPicture, Value) -> ModelsPicture
    ) async -> SettingsFailure? {
        switch await model.write(call) {
        case .success(let value):
            if let picture = state.value { state = .loaded(into(picture, value)) }
            problem = nil
            return nil
        case .failure(let box):
            problem = box.failure
            return box.failure
        }
    }
}
