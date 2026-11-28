import DotEngine
import SwiftUI
import UIKit

private var diagnosticsPlace: String { String(localized: "Settings › Diagnostics", comment: "Where on the Mac: diagnostics") }

struct DiagnosticsPage: View {
    var body: some View {
        SettingsScroll(title: String(localized: "Diagnostics", comment: "Maintenance: diagnose Dot on the Mac")) {
            GroupedSection(header: nil, footer: nil) {
                NavigationRow(systemImage: "cpu", title: String(localized: "Processes", comment: "Diagnostics: processes on the Mac"),
                              description: nil, detail: nil) { DiagnosticProcessesPage() }
                NavigationRow(systemImage: "internaldrive", title: String(localized: "Disk", comment: "Diagnostics: Dot's use of disk space"),
                              description: nil, detail: nil) { DiagnosticDiskPage() }
                NavigationRow(systemImage: "pause.circle", title: String(localized: "Pauses", comment: "Diagnostics: times Dot stopped responding"),
                              description: nil, detail: nil) { DiagnosticPausesPage() }
                NavigationRow(systemImage: "doc.text", title: String(localized: "Logs", comment: "Diagnostics: Dot's log files"),
                              description: nil, detail: nil) { DiagnosticLogsPage() }
            }
        }
    }
}

private struct DiagnosticProcessesPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.scenePhase) private var phase: ScenePhase
    @State private var state: Loadable<DiagnosticProcesses> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Processes", comment: "Diagnostics: processes on the Mac")) {
            LoadedPage(state: state, macPlace: diagnosticsPlace, retry: { Task { await load() } }) { picture in
                GroupedSection(header: nil, footer: nil) {
                    ForEach(picture.processes) { process in
                        let cpu = (process.cpuPercent / 100).formatted(.percent.precision(.fractionLength(1)))
                        let running = Duration.seconds(process.runningSeconds).formatted(.units(allowed: [.days, .hours, .minutes, .seconds], width: .abbreviated))
                        ListRow(systemImage: nil, title: process.name,
                                description: String(localized: "PID \(process.pid) · \(cpu) CPU · \(running)", comment: "Diagnostics: process identifier, CPU use and time running"),
                                accessory: .detail(fileSize(process.memoryBytes)))
                    }
                }
            }
        }
        .refreshable { await load() }
        .task(id: phase) {
            guard phase == .active else { return }
            while !Task.isCancelled {
                await load()
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
            }
        }
    }

    private func load() async {
        let latest = await model.read { api throws(DaemonRequestError) in try await api.diagnosticProcesses() }
        if !Task.isCancelled { state = latest }
    }
}

private struct DiagnosticDiskPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<DiagnosticDisk> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Disk", comment: "Diagnostics: Dot's use of disk space")) {
            LoadedPage(state: state, macPlace: diagnosticsPlace, retry: { Task { await load() } }) { disk in
                GroupedSection(header: nil, footer: nil) {
                    ForEach(disk.folders) { folder in
                        ListRow(systemImage: nil, title: folder.name, description: folder.paths.joined(separator: "\n"),
                                accessory: .detail(fileSize(folder.bytes)))
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.diagnosticDisk() }
    }
}

private struct DiagnosticPausesPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<DiagnosticPauses> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Pauses", comment: "Diagnostics: times Dot stopped responding")) {
            LoadedPage(state: state, macPlace: diagnosticsPlace, retry: { Task { await load() } }) { pauses in
                GroupedSection(header: nil, footer: nil) {
                    if pauses.stalls.isEmpty {
                        ListRow(systemImage: "checkmark.circle",
                                title: String(localized: "Dot has not stopped answering for more than \(pauses.threshold.formatted()) seconds since it started.",
                                              comment: "Diagnostics: no pause longer than the given number of seconds"),
                                description: nil, accessory: .none)
                    }
                    ForEach(Array(pauses.stalls.enumerated()), id: \.offset) { _, pause in
                        DisclosureGroup {
                            Text(verbatim: pause.stack).textRole(.code).textSelection(.enabled).padding(Space.groupedGutter)
                        } label: {
                            ListRow(systemImage: nil, title: Date(timeIntervalSince1970: pause.beganAt).formatted(),
                                    description: pause.ongoing ? String(localized: "Still paused", comment: "Diagnostics: a pause is ongoing") : nil,
                                    accessory: .detail(Duration.seconds(pause.seconds).formatted()))
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.diagnosticPauses() }
    }
}

private struct DiagnosticLogsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<DiagnosticLogs> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Logs", comment: "Diagnostics: Dot's log files")) {
            LoadedPage(state: state, macPlace: diagnosticsPlace, retry: { Task { await load() } }) { logs in
                GroupedSection(header: nil, footer: nil) {
                    if logs.files.isEmpty {
                        ListRow(systemImage: nil, title: String(localized: "No logs yet", comment: "Diagnostics: the Mac has no logs"),
                                description: nil, accessory: .none)
                    }
                    ForEach(logs.files) { file in
                        NavigationRow(systemImage: nil, title: file.name, description: nil, detail: fileSize(file.bytes)) {
                            DiagnosticLogPage(name: file.name)
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.diagnosticLogs() }
    }
}

private struct DiagnosticLogPage: View {
    let name: String
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<DiagnosticLogTail> = .loading
    @State private var filter: String = ""

    var body: some View {
        SettingsScroll(title: name) {
            LoadedPage(state: state, macPlace: diagnosticsPlace, retry: { Task { await load() } }) { tail in
                let needle = filter.trimmingCharacters(in: .whitespacesAndNewlines)
                let lines = needle.isEmpty ? tail.lines : tail.lines.filter { $0.localizedCaseInsensitiveContains(needle) }
                GroupedSection(header: nil, footer: nil) {
                    TextField(String(localized: "Filter the lines", comment: "Diagnostics: search the loaded log lines"), text: $filter)
                        .textRole(.body).padding(Space.groupedGutter)
                    if lines.isEmpty {
                        Text(needle.isEmpty
                             ? String(localized: "The log is empty.", comment: "Diagnostics: the log has no lines")
                             : String(localized: "No line holds these words.", comment: "Diagnostics: the search found nothing"))
                            .textRole(.body).padding(Space.groupedGutter)
                    } else {
                        Text(verbatim: lines.joined(separator: "\n")).textRole(.code).textSelection(.enabled)
                            .padding(Space.groupedGutter).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                Button(String(localized: "Copy", comment: "Diagnostics: copies the shown log lines")) {
                    UIPasteboard.general.string = lines.joined(separator: "\n")
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(lines.isEmpty)
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.diagnosticLog(name, lines: 300) }
    }
}
