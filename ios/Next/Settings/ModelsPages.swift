import DotEngine
import SwiftUI

// MARK: Models (items 1759, 1770)

/// What the Models page reads at once; each part read on its own (the code review, the night's learning, Dot's own work,
/// self-repair) is its failure when it could not be read, and the rest stands.
struct ModelsPicture: Sendable {
    let config: ModelsConfig
    let options: [ProviderModels]
    let review: Loadable<CodeReviewConfig>
    let learning: Loadable<LearningModels>
    let look: Loadable<InitiativeLook>
    let repair: Loadable<RepairWorkerView>

    func with(config: ModelsConfig) -> ModelsPicture {
        ModelsPicture(config: config, options: options, review: review, learning: learning, look: look, repair: repair)
    }
    func with(review: Loadable<CodeReviewConfig>) -> ModelsPicture {
        ModelsPicture(config: config, options: options, review: review, learning: learning, look: look, repair: repair)
    }
    func with(learning: Loadable<LearningModels>) -> ModelsPicture {
        ModelsPicture(config: config, options: options, review: review, learning: learning, look: look, repair: repair)
    }
    func with(look: Loadable<InitiativeLook>) -> ModelsPicture {
        ModelsPicture(config: config, options: options, review: review, learning: learning, look: look, repair: repair)
    }
    func with(repair: Loadable<RepairWorkerView>) -> ModelsPicture {
        ModelsPicture(config: config, options: options, review: review, learning: learning, look: look, repair: repair)
    }
}

/// The label of a model reference, from the daemon's list; the reference itself when the list does not hold it.
func modelLabel(_ ref: String, in options: [ProviderModels]) -> String {
    guard !ref.isEmpty else { return String(localized: "Not set", comment: "Models: a role with no model of its own") }
    for provider in options { if let model = provider.models.first(where: { $0.ref == ref }) { return model.label } }
    return ref
}

/// Settings › Agents › Models, every model setting of the Mac's Settings › Models a phone may change (item 1907): the
/// main, fast, vision and review models, the security research model (item 1889), the fallbacks in their order, Dot's own
/// work, the night's learning, self-repair (its model, effort and fast mode) and the code review; the default level of
/// reasoning as the Mac's config holds it, read only, as the web shows it. Each model is picked on the app's one model
/// catalog. The chat size and the providers are set on the Mac.
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
                roles(picture)
                fallbacks(picture)
                GroupedSection(
                    header: String(localized: "Reasoning", comment: "Chat settings row: the model's reasoning level"),
                    footer: String(localized: "The level a task asks of its model when none is chosen for it. It is set in the Mac's config.", comment: "Models: the default level of reasoning, read only")
                ) {
                    ListRow(
                        systemImage: nil, title: String(localized: "Default level", comment: "Chat settings: the reasoning segment that leaves the level to the model's default"),
                        description: nil, accessory: .detail(effortTitle(picture.config.effort)))
                }
                ownWork(picture)
                learning(picture)
                repair(picture)
                review(picture)
            }
        }
        .sheet(item: $picking) { pick in ModelPickerSheet(pick: pick, defaultRef: state.value?.config.main ?? "") }
        .task { await load() }
        .refreshable { await load() }
    }

    // MARK: Sections

    @ViewBuilder private func roles(_ picture: ModelsPicture) -> some View {
        let main: String = String(localized: "Main model", comment: "Models: the main model")
        GroupedSection(
            header: String(localized: "Dot thinks with", comment: "Models section: the main model and the roles"),
            footer: String(localized: "A new model applies from each task's next start; running tasks keep theirs.", comment: "Models: footer under the roles")
        ) {
            pickRow(main, current: picture.config.main, use: .main, none: nil, picture: picture) { api, ref throws(DaemonRequestError) in
                try await api.setMainModel(ref)
            }
            pickRow(String(localized: "Fast model", comment: "Models: the role for quick small steps"), current: picture.config.fast, use: .fast, none: nil, picture: picture) {
                api, ref throws(DaemonRequestError) in try await api.setRole(.fast, model: ref)
            }
            pickRow(String(localized: "Vision model", comment: "Models: the role that reads images for a model that cannot"), current: picture.config.vision, use: .vision, none: nil, picture: picture) {
                api, ref throws(DaemonRequestError) in try await api.setRole(.vision, model: ref)
            }
            pickRow(String(localized: "Review model", comment: "Models: the role that double-checks Dot's work"), current: picture.config.review, use: .dotsTools, none: nil, picture: picture) {
                api, ref throws(DaemonRequestError) in try await api.setRole(.review, model: ref)
            }
            pickRow(
                String(localized: "Security research model", comment: "Models: the model security research in a scope starts on (item 1889)"),
                current: picture.config.kinds?["security"] ?? "", use: .main, none: main, picture: picture
            ) { api, ref throws(DaemonRequestError) in try await api.setKindModel("security", model: ref) }
        }
    }

    @ViewBuilder private func fallbacks(_ picture: ModelsPicture) -> some View {
        GroupedSection(
            header: String(localized: "Fallbacks", comment: "Models section: the models a task moves to when its own cannot answer"),
            footer: String(localized: "When a task's model cannot answer (its allowance is used up, it is too busy, its key was refused, its safety filter stopped the reply), the task goes on with the first of these that can, each once at most.", comment: "Models: what the fallbacks do")
        ) {
            ForEach(Array(picture.config.standby.enumerated()), id: \.element) { index, ref in
                HStack(spacing: Space.s) {
                    ListRow(systemImage: nil, title: modelLabel(ref, in: picture.options), description: nil, accessory: .none)
                    Button {
                        var next: [String] = picture.config.standby
                        next.remove(at: index)
                        saveStandby(next)
                    } label: {
                        Image(systemName: "minus.circle").font(.system(.body, weight: .regular)).foregroundStyle(Tone.secondary)
                            .frame(width: Metric.hit, height: Metric.hit).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .padding(.trailing, Space.xxs)
                    .accessibilityLabel(String(localized: "Remove \(modelLabel(ref, in: picture.options))", comment: "VoiceOver: removes a fallback model; the argument is its name"))
                }
                .accessibilityIdentifier("models.fallback")
            }
            Button {
                let title: String = String(localized: "Add a fallback model", comment: "Models: adds a model to the fallbacks")
                picking = ModelPick(
                    id: "standby", title: title, current: "", effort: nil,
                    candidates: modelCandidates(picture.options, for: .fast).map { provider in
                        ProviderModels(provider: provider.provider, models: provider.models.filter { !picture.config.standby.contains($0.ref) }, error: nil)
                    }.filter { !$0.models.isEmpty },
                    none: nil
                ) { ref, _ in
                    await apply({ api throws(DaemonRequestError) in try await api.setStandby(picture.config.standby + [ref]) }) { picture, config in picture.with(config: config) }
                }
            } label: {
                ListRow(systemImage: "plus", title: String(localized: "Add a fallback model", comment: "Models: adds a model to the fallbacks"), description: nil, accessory: .chevron)
            }
            .buttonStyle(RowPressStyle())
            .accessibilityIdentifier("models.addFallback")
        }
    }

    @ViewBuilder private func ownWork(_ picture: ModelsPicture) -> some View {
        switch picture.look {
        case .loaded(let look):
            GroupedSection(
                header: String(localized: "Dot's own work", comment: "Models section: the model that decides Dot's own work"),
                footer: String(localized: "Decides what is worth doing when Dot works on its own while the Mac is idle. The work itself runs on the main model.", comment: "Models: what the own work model does")
            ) {
                pickRow(
                    String(localized: "Own work model", comment: "Models: the model that looks for Dot's own work"), current: look.lookModel, use: .dotsTools,
                    none: String(localized: "Main model", comment: "Models: the main model"), picture: picture
                ) { api, ref throws(DaemonRequestError) in try await api.setLookModel(ref) } into: { picture, look in picture.with(look: .loaded(look)) }
            }
        case .failed(let failure):
            FailureBanner(failure: failure, macPlace: macPlace, retry: { Task { await load() } })
        case .loading:
            EmptyView()
        }
    }

    @ViewBuilder private func learning(_ picture: ModelsPicture) -> some View {
        switch picture.learning {
        case .loaded(let learning):
            let onReview: String = String(localized: "Review model", comment: "Models: the role that double-checks Dot's work")
            GroupedSection(
                header: String(localized: "The night's learning", comment: "Models section: the roles of the improvement loop"),
                footer: String(localized: "Each role plays on the review model unless one is chosen for it.", comment: "Models: the night's learning roles")
            ) {
                ForEach(LearningRole.shown, id: \.self) { role in
                    pickRow(role.title, current: learning.model(of: role), use: .dotsTools, none: onReview, picture: picture) { api, ref throws(DaemonRequestError) in
                        try await api.setLearning(role, model: ref)
                    } into: { picture, learning in picture.with(learning: .loaded(learning)) }
                }
            }
        case .failed(let failure):
            FailureBanner(failure: failure, macPlace: macPlace, retry: { Task { await load() } })
        case .loading:
            EmptyView()
        }
    }

    @ViewBuilder private func repair(_ picture: ModelsPicture) -> some View {
        switch picture.repair {
        case .loaded(let view):
            RepairWorkerControls(worker: view.worker, lost: view.workerLost, save: saveWorker)
        case .failed(let failure):
            FailureBanner(failure: failure, macPlace: macPlace, retry: { Task { await load() } })
        case .loading:
            EmptyView()
        }
    }

    @ViewBuilder private func review(_ picture: ModelsPicture) -> some View {
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

    // MARK: Rows

    /// A model setting written into the Mac's `[models]`: the answer is its whole config.
    private func pickRow(
        _ title: String, current: String, use: ModelUse, none: String?, picture: ModelsPicture,
        choose: @escaping @Sendable (DaemonAPI, String) async throws(DaemonRequestError) -> ModelsConfig
    ) -> some View {
        pickRow(title, current: current, use: use, none: none, picture: picture, choose: choose) { picture, config in picture.with(config: config) }
    }

    /// A model setting whose answer `into` puts back on the page.
    private func pickRow<Value: Sendable>(
        _ title: String, current: String, use: ModelUse, none: String?, picture: ModelsPicture,
        choose: @escaping @Sendable (DaemonAPI, String) async throws(DaemonRequestError) -> Value, into: @escaping (ModelsPicture, Value) -> ModelsPicture
    ) -> some View {
        Button {
            picking = ModelPick(id: title, title: title, current: current, effort: nil, candidates: modelCandidates(picture.options, for: use), none: none) { ref, _ in
                await apply({ api throws(DaemonRequestError) in try await choose(api, ref) }, into: into)
            }
        } label: {
            ListRow(
                systemImage: nil, title: title, description: nil,
                accessory: .detailAndChevron(current.isEmpty ? (none ?? modelLabel(current, in: picture.options)) : modelLabel(current, in: picture.options)))
        }
        .buttonStyle(RowPressStyle())
        .accessibilityIdentifier("models.row")
    }

    /// The code review's model and its reasoning; a model chosen leaves the fallbacks, where it may not stand twice.
    private func pickReview(_ review: CodeReviewConfig, picture: ModelsPicture) {
        picking = ModelPick(
            id: "review", title: String(localized: "Code review model", comment: "Code review: the first model"), current: review.model, effort: review.effort,
            candidates: modelCandidates(picture.options, for: .dotsTools), none: nil
        ) { ref, effort in
            var change: CodeReviewChange = CodeReviewChange(review)
            change.model = ref
            change.effort = effort ?? review.effort
            change.fallback = review.fallback.filter { $0 != ref }
            return await apply({ api throws(DaemonRequestError) in try await api.changeCodeReview(change) }) { picture, saved in picture.with(review: .loaded(saved)) }
        }
    }

    private func saveStandby(_ models: [String]) {
        Task { _ = await apply({ api throws(DaemonRequestError) in try await api.setStandby(models) }) { picture, config in picture.with(config: config) } }
    }

    private func saveWorker(_ worker: RepairWorker) {
        Task { _ = await apply({ api throws(DaemonRequestError) in try await api.setRepairWorker(worker) }) { picture, view in picture.with(repair: .loaded(view)) } }
    }

    // MARK: Reading and writing

    private func load() async {
        let status: Loadable<StatusView> = await model.read { api throws(DaemonRequestError) in try await api.fullStatus() }
        guard case .loaded(let current) = status else {
            if case .failed(let failure) = status { state = .failed(failure) }
            return
        }
        async let options: Loadable<[ProviderModels]> = model.read { api throws(DaemonRequestError) in try await api.models() }
        async let review: Loadable<CodeReviewConfig> = model.read { api throws(DaemonRequestError) in try await api.codeReview() }
        async let learning: Loadable<LearningModels> = model.read { api throws(DaemonRequestError) in try await api.learningModels() }
        async let look: Loadable<InitiativeLook> = model.read { api throws(DaemonRequestError) in try await api.initiativeLook() }
        async let repair: Loadable<RepairWorkerView> = model.read { api throws(DaemonRequestError) in try await api.repairWorker() }
        state = .loaded(
            ModelsPicture(
                config: current.models, options: await options.value ?? [], review: await review, learning: await learning, look: await look,
                repair: await repair))
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

extension LearningRole {
    /// The roles the Mac's page offers: the selector is built but no step asks it yet, so neither page offers it.
    static let shown: [LearningRole] = [.proposer, .composer, .experience]

    var title: String {
        switch self {
        case .proposer: String(localized: "Proposer", comment: "Models: the night's role that proposes a better way of working")
        case .composer: String(localized: "Composer", comment: "Models: the night's role that weighs the versions tried")
        case .selector: String(localized: "Selector", comment: "Models: the night's role that picks among versions")
        case .experience: String(localized: "Experience writer", comment: "Models: the night's role that writes down what was learned")
        }
    }
}

/// Self-repair's choices, as the Mac's page names them (`repair/claude.py`).
enum RepairChoice {
    static let models: [(String, String)] = [
        ("default", String(localized: "Claude Code's own choice", comment: "Models: self-repair leaves the model to Claude Code")),
        ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("fable", "Fable"),
    ]

    static let efforts: [(String, String)] =
        [("default", String(localized: "Claude Code's own choice", comment: "Models: self-repair leaves the model to Claude Code"))]
        + ["low", "medium", "high", "xhigh", "max"].map { ($0, effortTitle($0)) }

    static func modelTitle(_ value: String) -> String { models.first { $0.0 == value }?.1 ?? value }

    static func levelTitle(_ value: String) -> String { efforts.first { $0.0 == value }?.1 ?? value }
}
