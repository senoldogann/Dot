import DotEngine
import SwiftUI

// MARK: Rules (items 1759, 1772)

/// What asks in an approval mode, in one line (the web's `approvals.*Hint`, shortened).
func approvalSummary(_ mode: String) -> String {
    switch mode {
    case "supervised": String(localized: "You approve every change: files, commands, clicks, messages.", comment: "Rules: what asks in supervised mode")
    case "ask": String(localized: "Edits and everyday work go ahead; Dot asks you about the rest.", comment: "Rules: what asks in ask mode")
    case "auto": String(localized: "A second AI answers for you; you are asked only what it is unsure about.", comment: "Rules: what asks in auto mode")
    case "yolo": String(localized: "Dot asks only before a payment, a message or mail sent for you, or a public post.", comment: "Rules: what asks in yolo mode")
    default: mode
    }
}

/// What the Rules page reads: the daemon's approval mode and free mode.
struct RulesPicture: Sendable {
    let approvals: String
    let free: FreeModeView?
}

/// Settings › Agents › Rules, what a phone needs of it (PLAN §26): the approval mode Dot's tasks start in, with what
/// asks in it, free mode, and the owner's rules.
struct RulesPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<RulesPicture> = .loading
    @State private var problem: SettingsFailure?

    private var macPlace: String { String(localized: "Settings › Rules", comment: "Where on the Mac: the Rules page") }

    var body: some View {
        SettingsScroll(title: String(localized: "Rules", comment: "Settings row: what Dot may do without asking")) {
            if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
            LoadedPage(state: state, macPlace: macPlace, retry: { Task { await load() } }) { picture in
                let free: Bool = picture.free?.free ?? false
                GroupedSection(
                    header: String(localized: "Approval mode", comment: "Rules: the mode tasks start in"),
                    footer: free ? String(localized: "Free mode is on: no task asks you anything.", comment: "Rules: free mode holds") : approvalSummary(picture.approvals)
                ) {
                    ListRow(systemImage: nil, title: String(localized: "Tasks start in", comment: "Rules: the default approval mode"), description: nil, accessory: .detail(approvalWords(picture.approvals)))
                }
                if let mode = picture.free {
                    GroupedSection(
                        header: nil,
                        footer: String(localized: "On, no task asks you anything, in any chat and any mode. It is switched on from the Mac, not a phone.", comment: "Rules: what free mode is")
                    ) {
                        ToggleRow(
                            systemImage: nil, title: String(localized: "Free mode: Dot asks you nothing", comment: "Rules: free mode's switch"), description: nil,
                            isOn: Binding(get: { mode.free }, set: { on in Task { await setFree(on) } }))
                    }
                }
            }
            SavedRulesSection(macPlace: macPlace)
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        let status: Loadable<StatusView> = await model.read { api throws(DaemonRequestError) in try await api.fullStatus() }
        guard case .loaded(let current) = status else {
            if case .failed(let failure) = status { state = .failed(failure) }
            return
        }
        let free: Loadable<FreeModeView> = await model.read { api throws(DaemonRequestError) in try await api.freeMode() }
        state = .loaded(RulesPicture(approvals: current.approvals, free: free.value))
    }

    private func setFree(_ on: Bool) async {
        switch await model.write({ api throws(DaemonRequestError) in try await api.setFreeMode(on) }) {
        case .success(let free):
            problem = nil
            if let picture = state.value { state = .loaded(RulesPicture(approvals: picture.approvals, free: free)) }
        case .failure(let box): problem = box.failure
        }
    }
}
