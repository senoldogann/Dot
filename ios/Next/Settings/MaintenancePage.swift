import DotEngine
import SwiftUI

// Settings › System › Maintenance (item 1790, the owner's T3 reference).

func programSupportWords(_ support: String) -> String {
    switch support {
    case "supported": String(localized: "Measured with Dot", comment: "Maintenance: Dot was measured on this version")
    case "graceful": String(localized: "Older than measured", comment: "Maintenance: the version is older than Dot was measured on")
    case "broken": String(localized: "Does not work with Dot", comment: "Maintenance: Dot refuses runs on this version")
    case "missing": String(localized: "Not installed", comment: "Maintenance: the program is not on the Mac")
    default: String(localized: "Not measured", comment: "Maintenance: Dot keeps no measure of this version")
    }
}

struct MaintenancePicture: Sendable {
    let status: StatusView
    let health: HealthView?
    let programs: ProgramsView?
}

/// Settings › System › Maintenance (the owner's T3 reference): the Mac, with Dot's version and the provider programs'
/// against what Dot was measured on, and whether interrupted tasks continue after an update or a restart (`[agent]
/// resume`). There is no switch to check provider updates: Dot reads each provider's models live when Models opens, and
/// it never updates a provider's program on its own (Claude Code updates itself).
struct MaintenancePage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<MaintenancePicture> = .loading
    @State private var problem: SettingsFailure?

    private var macPlace: String { String(localized: "Settings › System", comment: "Where on the Mac: the System page") }

    var body: some View {
        SettingsScroll(title: String(localized: "Maintenance", comment: "Settings row: updates and what happens after a restart")) {
            if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
            LoadedPage(state: state, macPlace: macPlace, retry: { Task { await load() } }) { picture in
                GroupedSection(header: String(localized: "Your Mac", comment: "Maintenance section: the paired Mac"), footer: nil) {
                    NavigationRow(
                        systemImage: "server.rack", title: model.macName ?? String(localized: "This Mac", comment: "Settings row: the paired Mac"),
                        description: String(localized: "Dot and provider versions", comment: "Maintenance: the Mac row's detail"), detail: nil
                    ) { ProgramsPage(dotVersion: picture.health?.version ?? picture.status.version, programs: picture.programs) }
                }
                GroupedSection(
                    header: String(localized: "Updates", comment: "Maintenance section: updates and restarts"),
                    footer: String(localized: "New models appear when Models opens: Dot asks each provider then. Dot never updates a provider's program on its own; Claude Code updates itself.", comment: "Maintenance: why there is no switch to check provider updates")
                ) {
                    ToggleRow(
                        systemImage: "arrow.uturn.forward", title: String(localized: "Continue after restart", comment: "Maintenance: resume interrupted tasks after a restart"),
                        description: String(localized: "Resume interrupted tasks after an update or restart.", comment: "Maintenance: what continue after restart does"), isOn: resumeBinding(picture))
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        switch await model.read({ api throws(DaemonRequestError) in try await api.fullStatus() }) {
        case .loaded(let status):
            async let health: HealthView? = model.read { api throws(DaemonRequestError) in try await api.health() }.value
            async let programs: ProgramsView? = model.read { api throws(DaemonRequestError) in try await api.programs() }.value
            state = .loaded(MaintenancePicture(status: status, health: await health, programs: await programs))
        case .failed(let failure): state = .failed(failure)
        case .loading: state = .loading
        }
    }

    private func with(_ picture: MaintenancePicture, resume: ResumeConfig) -> MaintenancePicture {
        let status: StatusView = picture.status
        return MaintenancePicture(
            status: StatusView(
                version: status.version, uptimeS: status.uptimeS, activeRuns: status.activeRuns, pendingApprovals: status.pendingApprovals,
                schedulerRunning: status.schedulerRunning, failsafeEngaged: status.failsafeEngaged, approvals: status.approvals, models: status.models, resume: resume),
            health: picture.health, programs: picture.programs)
    }

    private func resumeBinding(_ picture: MaintenancePicture) -> Binding<Bool> {
        Binding(
            get: { (state.value ?? picture).status.resume.resume == "safe" },
            set: { on in
                guard let before = state.value else { return }
                state = .loaded(with(before, resume: ResumeConfig(resume: on ? "safe" : "off", resumeWithinS: before.status.resume.resumeWithinS)))
                Task {
                    switch await model.write({ api throws(DaemonRequestError) in try await api.setResume(on ? "safe" : "off") }) {
                    case .success(let saved):
                        if let now = state.value { state = .loaded(with(now, resume: saved)) }
                        problem = nil
                    case .failure(let box):
                        state = .loaded(before)
                        problem = box.failure
                    }
                }
            })
    }
}

/// Dot's version and each provider program's, with how Dot fares on it and what to do when it is older than measured.
struct ProgramsPage: View {
    let dotVersion: String
    let programs: ProgramsView?

    var body: some View {
        SettingsScroll(title: String(localized: "Dot and provider versions", comment: "Maintenance: the Mac row's detail")) {
            GroupedSection(header: nil, footer: nil) {
                ListRow(systemImage: "info.circle", title: String(localized: "Dot version", comment: "This Mac: the daemon's version"), description: nil, accessory: .detail(dotVersion))
            }
            if let programs {
                GroupedSection(
                    header: String(localized: "Provider programs", comment: "Maintenance section: claude and opencode on the Mac"),
                    footer: programs.programs.contains { $0.support == "graceful" || $0.support == "broken" }
                        ? String(localized: "Update Claude Code on the Mac with 'claude update'.", comment: "Maintenance: how to update an old Claude Code") : nil
                ) {
                    ForEach(programs.programs) { program in
                        ListRow(
                            systemImage: program.support == "broken" ? "xmark.octagon" : program.support == "missing" ? "circle.dashed" : "terminal",
                            title: program.name, description: program.support == "missing" ? program.command : "\(programSupportWords(program.support)) · \(program.why)",
                            accessory: .detail(program.version ?? programSupportWords(program.support)))
                    }
                }
            } else {
                Notice(tone: .info, message: String(localized: "Update Dot on your Mac to see the provider programs' versions.", comment: "Maintenance: the daemon has no programs route"), dismiss: nil)
            }
        }
    }
}
