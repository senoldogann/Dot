import DotEngine
import SwiftUI

struct RepairPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<RepairStatus> = .loading
    @State private var problem: SettingsFailure?
    @State private var busy: Bool = false
    @State private var confirmsStop: Bool = false

    private var macPlace: String {
        String(localized: "Settings › Self-repair", comment: "Where on the Mac: the Self-repair page")
    }

    var body: some View {
        SettingsScroll(title: String(localized: "Self-repair", comment: "Settings: Dot's own repairs")) {
            if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
            LoadedPage(state: state, macPlace: macPlace, retry: { Task { await settle() } }) { repair in
                overview(repair)
                problemList(repair.problems)
                RepairWorkerControls(worker: repair.worker, lost: repair.workerLost) { worker in
                    Task { await changeWorker(worker) }
                }
                .disabled(busy)
            }
        }
        .task {
            await settle()
            await poll()
        }
        .refreshable { await settle() }
        .confirmationDialog(
            String(localized: "Stop the night?", comment: "Self-repair: confirms stopping the current work"),
            isPresented: $confirmsStop, titleVisibility: .visible
        ) {
            Button(String(localized: "Stop", comment: "Self-repair: stop the current work"), role: .destructive) { Task { await stop() } }
            Button(String(localized: "Cancel", comment: "Cancels the confirmation"), role: .cancel) {}
        }
    }

    private func overview(_ repair: RepairStatus) -> some View {
        GroupedSection(header: String(localized: "Overview", comment: "Self-repair: current state"), footer: nil) {
            ListRow(systemImage: "wrench.and.screwdriver", title: repair.live?.title ?? phaseWords(repair),
                    description: repair.live == nil ? nil : phaseWords(repair), accessory: .none)
            if repair.running {
                Button { confirmsStop = true } label: {
                    ListRow(systemImage: "stop.circle", title: String(localized: "Stop", comment: "Self-repair: stop the current work"),
                            description: nil, accessory: .none)
                }
                .buttonStyle(RowPressStyle()).disabled(busy)
            } else if !repair.repo.isEmpty {
                Button { Task { await start() } } label: {
                    ListRow(systemImage: "play.circle", title: String(localized: "Run now", comment: "Self-repair: start the work now"),
                            description: nil, accessory: .none)
                }
                .buttonStyle(RowPressStyle()).disabled(busy)
            }
        }
    }

    private func phaseWords(_ repair: RepairStatus) -> String {
        if repair.repo.isEmpty { return String(localized: "Off", comment: "Self-repair: no repository is configured") }
        if !repair.running { return String(localized: "Idle", comment: "Self-repair: no work in progress") }
        switch repair.live?.phase {
        case .review: return String(localized: "Waiting for the review", comment: "Self-repair: the review of the change is pending")
        case .checks: return String(localized: "Waiting for the checks", comment: "Self-repair: CI checks are pending")
        case .working, nil: return String(localized: "Working", comment: "Self-repair: a repair is underway")
        }
    }

    private func problemList(_ problems: [RepairProblem]) -> some View {
        GroupedSection(header: String(localized: "Problems", comment: "Self-repair: problems Dot has found"), footer: nil) {
            if problems.isEmpty {
                ListRow(systemImage: "checkmark.circle", title: String(localized: "Nothing in line", comment: "Self-repair: no problems to fix"),
                        description: String(localized: "Dot has found nothing going wrong again and again.", comment: "Self-repair: empty problems list"),
                        accessory: .none)
            }
            ForEach(problems) { problem in
                NavigationRow(systemImage: "exclamationmark.circle", title: problem.title, description: nil, detail: statusWords(problem.status)) {
                    SettingsScroll(title: String(localized: "Details", comment: "Self-repair: details of a problem")) {
                        GroupedSection(header: problem.title, footer: nil) {
                            ListRow(systemImage: nil, title: statusWords(problem.status), description: problem.note.isEmpty ? nil : problem.note,
                                    accessory: .detail(problem.count.formatted()))
                            ForEach(Array(problem.evidence.enumerated()), id: \.offset) { _, line in
                                Text(verbatim: line).textRole(.body).textSelection(.enabled).padding(Space.groupedGutter)
                            }
                            if let url = URL(string: problem.prURL), url.scheme == "https" {
                                Link(String(localized: "Pull request", comment: "Self-repair: opens the proposed change"), destination: url)
                                    .buttonStyle(SecondaryButtonStyle())
                            }
                        }
                    }
                }
            }
        }
    }

    private func statusWords(_ status: RepairProblem.Status) -> String {
        switch status {
        case .open: String(localized: "Open", comment: "Self-repair: a problem that is open")
        case .working: String(localized: "Working", comment: "Self-repair: a repair is underway")
        case .fixed: String(localized: "Fixed", comment: "Self-repair: a problem that was fixed")
        case .waiting: String(localized: "Needs you", comment: "Self-repair: a problem awaiting the owner")
        case .declined: String(localized: "Let go", comment: "Self-repair: a problem Dot stopped working on")
        case .closed: String(localized: "Closed", comment: "Self-repair: the proposed fix was closed")
        }
    }

    private func settle() async {
        await write { api throws(DaemonRequestError) in try await api.settleRepairs() }
    }

    private func start() async {
        await write { api throws(DaemonRequestError) in try await api.startRepairs() }
    }

    private func stop() async {
        await write { api throws(DaemonRequestError) in try await api.stopRepairs() }
    }

    private func changeWorker(_ worker: RepairWorker) async {
        await write { api throws(DaemonRequestError) in try await api.updateRepairWorker(worker) }
    }

    private func write(_ action: (DaemonAPI) async throws(DaemonRequestError) -> RepairStatus) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        switch await model.write(action) {
        case .success(let latest):
            state = .loaded(latest)
            problem = nil
        case .failure(let box):
            if state.value == nil { state = .failed(box.failure) }
            else { problem = box.failure }
        }
    }

    private func poll() async {
        while !Task.isCancelled {
            let running: Bool = state.value?.running == true
            do { try await Task.sleep(for: .seconds(running ? 5 : 30)) } catch { return }
            guard !Task.isCancelled, !busy, state.value != nil else { continue }
            busy = true
            let latest: Loadable<RepairStatus> = await model.read { api throws(DaemonRequestError) in try await api.repairStatus() }
            busy = false
            guard !Task.isCancelled else { return }
            switch latest {
            case .loaded:
                state = latest
                problem = nil
            case .failed(let failure): problem = failure
            case .loading: break
            }
        }
    }
}


/// Model and speed controls shared by Models and Self-repair.
struct RepairWorkerControls: View {
    let worker: RepairWorker
    let lost: Bool
    let save: (RepairWorker) -> Void

    var body: some View {
    GroupedSection(
        header: String(localized: "Self-repair", comment: "Models section: how Claude Code works when Dot repairs itself"),
        footer: lost
            ? String(localized: "The kept choice could not be read: the nights stop until one is chosen again.", comment: "Models: self-repair's choice was lost")
            : String(localized: "The model of your Claude plan that repairs Dot's own problems at night, from its next session.", comment: "Models: what self-repair's model is")
    ) {
        menuRow(String(localized: "Self-repair model", comment: "Models: self-repair's model"), value: RepairChoice.modelTitle(worker.model), choices: RepairChoice.models) { value in
            save(RepairWorker(model: value, effort: worker.effort, fast: worker.fast))
        }
        menuRow(String(localized: "Reasoning", comment: "Chat settings row: the model's reasoning level"), value: RepairChoice.levelTitle(worker.effort), choices: RepairChoice.efforts) { value in
            save(RepairWorker(model: worker.model, effort: value, fast: worker.fast))
        }
        ToggleRow(
            systemImage: nil, title: String(localized: "Fast mode", comment: "Chat settings row: the model's fast mode"),
            description: String(localized: "Billed to your plan as extra usage; without it the session runs at the standard speed.", comment: "Models: self-repair's fast mode"),
            isOn: Binding(get: { worker.fast }, set: { save(RepairWorker(model: worker.model, effort: worker.effort, fast: $0)) }))
            .accessibilityIdentifier("models.repairFast")
    }
    }

    /// A choice among a few named values (self-repair's model and effort): the system's menu.
    private func menuRow(_ title: String, value: String, choices: [(String, String)], choose: @escaping (String) -> Void) -> some View {
        Menu {
            ForEach(choices, id: \.0) { choice in Button(choice.1) { choose(choice.0) } }
        } label: {
            ListRow(systemImage: nil, title: title, description: nil, accessory: .detailAndChevron(value))
        }
        .buttonStyle(RowPressStyle())
    }
}
