import DotEngine
import SwiftUI

private var systemPlace: String {
    String(localized: "Settings › System", comment: "Where on the Mac: the System page")
}

struct SystemChecksPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var checks: Loadable<[SystemCheck]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Health checks", comment: "System: the Mac's diagnostic checklist")) {
            LoadedPage(state: checks, macPlace: systemPlace, retry: { Task { await load() } }) { checks in
                GroupedSection(header: nil, footer: nil) {
                    ForEach(Array(checks.enumerated()), id: \.offset) { _, check in
                        DisclosureGroup {
                            Text(verbatim: check.detail)
                                .textRole(.body).foregroundStyle(Tone.secondary)
                                .textSelection(.enabled)
                                .padding(Space.groupedGutter)
                        } label: {
                            ListRow(systemImage: check.ok ? "checkmark.circle" : "exclamationmark.circle",
                                    title: check.name, description: nil, accessory: .detail(checkWords(check)))
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func checkWords(_ check: SystemCheck) -> String {
        if check.ok { return String(localized: "OK", comment: "Status: all is well") }
        return check.required
            ? String(localized: "Failing", comment: "Health checks: a required check failed")
            : String(localized: "Optional", comment: "Health checks: an optional check failed")
    }

    private func load() async {
        checks = await model.read { api throws(DaemonRequestError) in try await api.systemChecks() }
    }
}

private enum RestartProgress: Equatable {
    case idle
    case sending
    case reconnecting
    case connected
    case unconfirmed
    case failed(SettingsFailure)

    var busy: Bool { self == .sending || self == .reconnecting }
}

struct SystemRestartRow: View {
    let reconnected: () async -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var availability: Loadable<RestartAvailability> = .loading
    @State private var confirming: Bool = false
    @State private var progress: RestartProgress = .idle

    private var title: String {
        String(localized: "Restart Dot", comment: "System: restart Dot on the Mac")
    }

    var body: some View {
        GroupedSection(header: nil, footer: nil) {
            NavigationRow(systemImage: "stethoscope",
                          title: String(localized: "Health checks", comment: "System: the Mac's diagnostic checklist"),
                          description: nil, detail: nil) { SystemChecksPage() }
            switch availability {
            case .loaded(let permissions):
                if permissions.service {
                    Button { confirming = true } label: {
                        ListRow(systemImage: "arrow.clockwise", title: title, description: nil, accessory: .none)
                    }
                    .buttonStyle(RowPressStyle())
                    .disabled(progress.busy)
                } else if let problem = permissions.serviceProblem {
                    FailureBanner(failure: .other(problem), macPlace: systemPlace, retry: { Task { await load() } })
                }
            case .failed(let failure):
                FailureBanner(failure: failure, macPlace: systemPlace, retry: { Task { await load() } })
            case .loading:
                EmptyView()
            }
            restartStatus
        }
        .task { await load() }
        .confirmationDialog(
            String(localized: "Restart now?", comment: "System: confirm restarting Dot"),
            isPresented: $confirming, titleVisibility: .visible
        ) {
            Button(title, role: .destructive) { Task { await restart() } }
                .accessibilityIdentifier("system.confirmRestart")
            Button(String(localized: "Cancel", comment: "Cancels the confirmation"), role: .cancel) {}
        }
        .task(id: progress == .reconnecting) {
            if progress == .reconnecting { await waitForDot() }
        }
    }

    @State private var before: (uptime: Double, at: ContinuousClock.Instant)?

    @ViewBuilder private var restartStatus: some View {
        switch progress {
        case .idle:
            EmptyView()
        case .sending, .reconnecting:
            Notice(tone: .info, message: String(localized: "Reconnecting…", comment: "System: waiting for Dot to start again"), dismiss: nil)
        case .connected:
            Notice(tone: .info, message: String(localized: "Dot is back", comment: "System: Dot answered after restarting"), dismiss: nil)
        case .unconfirmed:
            Notice(tone: .warn, message: String(localized: "Dot has not confirmed the restart yet. Check your Mac if it stays unreachable.", comment: "System: the restart could not be confirmed"), dismiss: nil)
        case .failed(let failure):
            FailureBanner(failure: failure, macPlace: systemPlace, retry: nil)
        }
    }

    private func load() async {
        availability = await model.read { api throws(DaemonRequestError) in try await api.restartAvailability() }
    }

    private func restart() async {
        guard !progress.busy else { return }
        progress = .sending
        switch await model.read({ api throws(DaemonRequestError) in try await api.fullStatus() }) {
        case .loaded(let status): before = (status.uptimeS, .now)
        case .failed(let failure): progress = .failed(failure); return
        case .loading: progress = .idle; return
        }
        switch await model.write({ api throws(DaemonRequestError) in try await api.restartDot() }) {
        case .success(let receipt): progress = receipt.restarting ? .reconnecting : .unconfirmed
        case .failure(let box): progress = .failed(box.failure)
        }
    }

    private func waitForDot() async {
        guard let before else { return }
        let deadline = ContinuousClock.now + .seconds(30)
        while ContinuousClock.now < deadline {
            do { try await Task.sleep(for: .seconds(1)) } catch { return }
            guard !Task.isCancelled else { return }
            let state = await model.read { api throws(DaemonRequestError) in try await api.fullStatus() }
            guard !Task.isCancelled else { return }
            if case .failed(.keyRefused) = state {
                progress = .failed(.keyRefused)
                return
            }
            let elapsed = before.at.duration(to: .now).components
            let expected = before.uptime + Double(elapsed.seconds) + Double(elapsed.attoseconds) / 1e18
            if let status = state.value, status.uptimeS < expected - 0.5 {
                await reconnected()
                progress = .connected
                return
            }
        }
        progress = .unconfirmed
    }
}
