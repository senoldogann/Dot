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
    /// This phone's performance record as the Mac keeps it (item 1926); `nil` from a daemon before it.
    let performance: PhoneMetricsSummary?
}

/// A number of the performance record with at most one decimal, in the owner's locale.
private func short(_ value: Double) -> String { value.formatted(.number.precision(.fractionLength(0...1))) }

/// The performance section's rows: what each measure came to over the last days, plainly. Pure.
func performanceRows(_ summary: PhoneMetricsSummary) -> [PerformanceRow] {
    var rows: [PerformanceRow] = []
    if summary.foregroundMin > 0 || summary.cpuS > 0 {
        rows.append(PerformanceRow(
            id: "cpu", symbol: "cpu", title: String(localized: "Processor", comment: "Maintenance › Performance: CPU time row"),
            detail: String(localized: "\(short(summary.cpuS)) s in \(short(summary.foregroundMin)) min open", comment: "Maintenance › Performance: CPU seconds used over minutes on screen")))
    }
    rows.append(PerformanceRow(
        id: "hangs", symbol: "hourglass", title: String(localized: "Hangs", comment: "Maintenance › Performance: the app froze"),
        detail: summary.hangReports == 0 && summary.hangS == 0
            ? String(localized: "None", comment: "Maintenance › Performance: no hang, no crash")
            : String(localized: "\(summary.hangReports) reported · \(short(summary.hangS)) s", comment: "Maintenance › Performance: hang reports and their total time")))
    rows.append(PerformanceRow(
        id: "crashes", symbol: "exclamationmark.triangle", title: String(localized: "Crashes", comment: "Maintenance › Performance: crash reports row"),
        detail: summary.crashes == 0 ? String(localized: "None", comment: "Maintenance › Performance: no hang, no crash") : "\(summary.crashes)"))
    if let launch = summary.launchMs {
        rows.append(PerformanceRow(
            id: "launch", symbol: "bolt", title: String(localized: "Launch", comment: "Maintenance › Performance: time to the first frame"),
            detail: String(localized: "\(short(launch)) ms", comment: "Maintenance › Performance: milliseconds")))
    }
    if let memory = summary.peakMemoryMb {
        rows.append(PerformanceRow(
            id: "memory", symbol: "memorychip", title: String(localized: "Memory at most", comment: "Maintenance › Performance: peak memory row"),
            detail: String(localized: "\(short(memory)) MB", comment: "Maintenance › Performance: megabytes")))
    }
    rows.append(PerformanceRow(
        id: "data", symbol: "arrow.up.arrow.down", title: String(localized: "Data with your Mac", comment: "Maintenance › Performance: bytes read from and sent to the Mac"),
        detail: String(localized: "\(short(summary.dotInMb)) MB in · \(short(summary.dotOutMb)) MB out", comment: "Maintenance › Performance: megabytes read and sent")))
    rows.append(PerformanceRow(
        id: "reconnects", symbol: "arrow.triangle.2.circlepath", title: String(localized: "Reconnects", comment: "Maintenance › Performance: live streams that broke"),
        detail: String(localized: "\(summary.reconnects) in \(short(summary.openMin)) min", comment: "Maintenance › Performance: stream breaks over minutes open")))
    if let redraws = summary.redrawsPerMin {
        rows.append(PerformanceRow(
            id: "redraws", symbol: "rectangle.on.rectangle", title: String(localized: "Redraws", comment: "Maintenance › Performance: how often a chat was drawn again"),
            detail: String(localized: "\(short(redraws)) a minute", comment: "Maintenance › Performance: redraws per minute")))
    }
    return rows
}

/// One row of the performance section.
struct PerformanceRow: Identifiable, Equatable, Sendable {
    let id: String
    let symbol: String
    let title: String
    let detail: String
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
                GroupedSection(header: nil, footer: nil) {
                    NavigationRow(systemImage: "stethoscope", title: String(localized: "Diagnostics", comment: "Maintenance: diagnose Dot on the Mac"),
                                  description: nil, detail: nil) { DiagnosticsPage() }
                    NavigationRow(systemImage: "globe", title: String(localized: "Browser", comment: "Settings: Dot's browser"),
                                  description: nil, detail: nil) { BrowserSettingsPage() }
                }
                if let performance = picture.performance { performanceSection(performance) }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    /// This phone's performance over the last days, as the Mac keeps it: measured by iOS and by the app, sent to the Mac
    /// only.
    private func performanceSection(_ summary: PhoneMetricsSummary) -> some View {
        GroupedSection(
            header: String(localized: "This phone's performance", comment: "Maintenance section: the app's own measures"),
            footer: summary.reports == 0
                ? String(localized: "Nothing measured yet. iOS hands its measures about once a day; they go to your Mac only.", comment: "Maintenance › Performance: before the first report")
                : String(localized: "The last \(summary.days) days, measured by iOS and by Dot on this phone. Kept on your Mac only.", comment: "Maintenance › Performance: what the numbers are and where they stay")
        ) {
            if summary.reports > 0 {
                ForEach(performanceRows(summary)) { row in
                    ListRow(systemImage: row.symbol, title: row.title, description: nil, accessory: .detail(row.detail))
                        .accessibilityIdentifier("performance.\(row.id)")
                }
            }
        }
    }

    private func load() async {
        switch await model.read({ api throws(DaemonRequestError) in try await api.fullStatus() }) {
        case .loaded(let status):
            async let health: HealthView? = model.read { api throws(DaemonRequestError) in try await api.health() }.value
            async let programs: ProgramsView? = model.read { api throws(DaemonRequestError) in try await api.programs() }.value
            async let performance: PhoneMetricsSummary? = model.read { api throws(DaemonRequestError) in try await api.metricsSummary() }.value
            state = .loaded(MaintenancePicture(status: status, health: await health, programs: await programs, performance: await performance))
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
            health: picture.health, programs: picture.programs, performance: picture.performance)
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
