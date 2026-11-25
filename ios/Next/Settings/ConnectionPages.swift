import DotEngine
import SwiftUI
import UIKit
import UserNotifications

/// What one diagnosis found, with what the report needs.
struct DiagnosisRun: Sendable, Equatable {
    let probe: DiagnosisProbe
    let diagnosis: Diagnosis
    let trace: String
    let at: Date
}

extension AppModel {
    /// Probes the way to the Mac hop by hop (research G2 #5): the link, `/api/health` without the key, `/api/proof`
    /// (the key is sent only after the daemon proved it holds it), then `/api/status` with it.
    func diagnoseConnection() async -> DiagnosisRun? {
        guard let api: DaemonAPI = settingsAPI else { return nil }
        let link: LinkState = engine?.link.state ?? .up
        let trace: String = traceId(random: UInt64.random(in: 0...UInt64.max))
        guard link.online else {
            let probe: DiagnosisProbe = DiagnosisProbe(link: link, health: .notRun, status: .notRun, proof: .notRun)
            return DiagnosisRun(probe: probe, diagnosis: diagnose(probe), trace: trace, at: .now)
        }
        let health: ProbeAnswer = await api.probeHealth()
        var proof: ProofOutcome = .notRun
        var status: ProbeAnswer = .notRun
        if case .answered(let code, _) = health, (200..<300).contains(code) {
            proof = await api.proveKey(nonce: proofNonce())
            if proof != .mismatch { status = await api.probeStatus() }
        }
        let probe: DiagnosisProbe = DiagnosisProbe(link: link, health: health, status: status, proof: proof)
        return DiagnosisRun(probe: probe, diagnosis: diagnose(probe), trace: trace, at: .now)
    }
}

/// The name of a hop.
func hopTitle(_ hop: DiagnosisHop) -> String {
    switch hop {
    case .phone: String(localized: "This iPhone's network", comment: "Diagnosis hop: the phone's own Wi-Fi or cellular link")
    case .tailnet: String(localized: "Tailscale on this iPhone", comment: "Diagnosis hop: the tailnet name resolves")
    case .mac: String(localized: "The Mac", comment: "Diagnosis hop: the Mac answers on the tailnet")
    case .daemon: String(localized: "Dot on the Mac", comment: "Diagnosis hop: Dot's daemon answers")
    case .key: String(localized: "This phone's key", comment: "Diagnosis hop: the daemon takes the phone's key")
    }
}

/// What to do about a failed hop, in one line.
func problemWords(_ problem: HopProblem) -> String {
    switch problem {
    case .noNetwork: String(localized: "No network. Turn off Airplane Mode or join Wi-Fi.", comment: "Diagnosis: the phone has no link")
    case .nameNotFound:
        String(localized: "The Mac's tailnet name does not resolve. Open Tailscale on this iPhone and connect.", comment: "Diagnosis: the ts.net name is unknown: Tailscale is off on the phone")
    case .macSilent:
        String(localized: "Nothing answers. The Mac may sleep, be off, or have Tailscale off.", comment: "Diagnosis: the Mac does not answer")
    case .daemonNotListening: String(localized: "The Mac answers but Dot does not. Start Dot on the Mac.", comment: "Diagnosis: nothing listens on Dot's port")
    case .insecure: String(localized: "The secure connection failed. Check the Mac's Tailscale HTTPS certificate.", comment: "Diagnosis: TLS failed")
    case .daemonError(let code): String(localized: "Dot answered with an error (HTTP \(code)). Look at Dot on the Mac.", comment: "Diagnosis: the daemon answered with an error")
    case .keyRefused: String(localized: "The Mac refused this phone's key. Pair again with a new code.", comment: "Diagnosis: the key was refused")
    case .proofMismatch:
        String(localized: "This address did not prove it holds this phone's key, so the key was not sent. Pair again.", comment: "Diagnosis: the proof did not match")
    case .daemonTooOld: String(localized: "Update Dot on your Mac.", comment: "Diagnosis: the Mac's Dot is too old")
    case .other(let code): String(localized: "Failed with code \(code).", comment: "Diagnosis: an unclassified failure; the argument is the code")
    }
}

/// The report "Copy diagnostics" puts on the pasteboard: what was tried, what each hop gave, and the trace id; never
/// the key.
func diagnosisReport(_ run: DiagnosisRun, host: String, appVersion: String, system: String) -> String {
    func answer(_ probe: ProbeAnswer) -> String {
        switch probe {
        case .answered(let code, let millis): "HTTP \(code) in \(millis) ms"
        case .transport(let code, let millis): "no answer (URLError \(code)) after \(millis) ms"
        case .notRun: "not tried"
        }
    }
    let hops: [String] = run.diagnosis.hops.map { hop, result in
        let word: String
        switch result {
        case .passed: word = "ok"
        case .failed(let problem): word = "FAILED: \(problemWords(problem))"
        case .unknown: word = "not reached"
        }
        return "- \(hop.rawValue): \(word)"
    }
    return ([
        "Dot Next connection diagnosis",
        "trace: \(run.trace)",
        "at: \(run.at.formatted(.iso8601))",
        "app: \(appVersion) · \(system)",
        "mac: \(host)",
        "link: \(run.probe.link.online ? run.probe.link.interface : "offline")",
        "GET /api/health: \(answer(run.probe.health))",
        "GET /api/proof: \(String(describing: run.probe.proof))",
        "GET /api/status: \(answer(run.probe.status))",
    ] + hops).joined(separator: "\n")
}

/// Settings › This Mac: the link's state, the address, the proof of the key, the hop-by-hop diagnosis with Copy
/// diagnostics, and this phone's key.
struct MacPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var run: DiagnosisRun?
    @State private var diagnosing: Bool = false
    @State private var setup: Loadable<PhoneSetupView> = .loading
    @State private var health: HealthView?
    @State private var forgetAsked: Bool = false
    @State private var toast: ToastMessage?

    var body: some View {
        SettingsScroll(title: String(localized: "This Mac", comment: "Settings row: the paired Mac")) {
            if model.snapshot.transport == .keyRefused || run?.diagnosis.firstFailure?.0 == .key {
                FailureBanner(failure: .keyRefused, macPlace: "", retry: nil)
            }
            GroupedSection(header: nil, footer: nil) {
                ListRow(systemImage: "dot.radiowaves.left.and.right", title: String(localized: "Status", comment: "This Mac: the link's state"), description: nil, accessory: .detail(transportWord(model.snapshot.transport)))
                ListRow(systemImage: "network", title: String(localized: "Address", comment: "This Mac: the Mac's tailnet address"), description: nil, accessory: .detail(host))
                    .contextMenu {
                        Button(String(localized: "Copy", comment: "Context menu: copies the text"), systemImage: "doc.on.doc") { UIPasteboard.general.string = host }
                    }
                if let name = model.macName {
                    ListRow(systemImage: "desktopcomputer", title: String(localized: "Name", comment: "This Mac: the Mac's name"), description: nil, accessory: .detail(name))
                }
                if let health {
                    ListRow(systemImage: "info.circle", title: String(localized: "Dot version", comment: "This Mac: the daemon's version"), description: nil, accessory: .detail(health.version))
                }
                ListRow(systemImage: "checkmark.seal", title: String(localized: "Proof of key", comment: "This Mac: whether the Mac proved it holds the phone's key"), description: proofWords, accessory: .none)
            }
            diagnosisSection
            if case .loaded(let phone) = setup {
                GroupedSection(
                    header: String(localized: "Tailscale on the Mac", comment: "This Mac section: the Mac's tailnet"),
                    footer: phone.tailnet.problem
                ) {
                    ListRow(systemImage: "point.3.connected.trianglepath.dotted", title: String(localized: "Tailscale", comment: "This Mac: Tailscale on the Mac"), description: nil, accessory: .detail(phone.tailnet.running ? String(localized: "Running", comment: "This Mac: Tailscale runs") : String(localized: "Off", comment: "This Mac: Tailscale is off")))
                    ListRow(systemImage: "lock", title: String(localized: "Served over HTTPS", comment: "This Mac: Dot is served on the tailnet"), description: nil, accessory: .detail(phone.tailnet.served ? String(localized: "Yes", comment: "A yes value") : String(localized: "No", comment: "A no value")))
                    ListRow(systemImage: "cup.and.saucer", title: String(localized: "Keeps the Mac awake", comment: "This Mac: keep-awake while Dot works"), description: nil, accessory: .detail(phone.keepAwake ? String(localized: "Yes", comment: "A yes value") : String(localized: "No", comment: "A no value")))
                }
            }
            GroupedSection(
                header: String(localized: "This phone's key", comment: "This Mac section: the phone's own key"),
                footer: String(localized: "To make a new code or remove a phone: open on the Mac Settings › Phone. A phone's key cannot do either.", comment: "This Mac: footer under the key: the Mac's continuation")
            ) {
                Button { forgetAsked = true } label: {
                    ListRow(systemImage: "key", title: String(localized: "Forget this phone's key", comment: "Settings row: forgets the key this phone was paired with"), description: nil, accessory: .none)
                }
                .buttonStyle(RowPressStyle())
                .confirmationDialog(String(localized: "Forget this phone's key? You pair again with a new code.", comment: "Confirmation of forgetting the phone's key"), isPresented: $forgetAsked, titleVisibility: .visible) {
                    Button(String(localized: "Forget the key", comment: "Confirmation button that forgets the phone's key"), role: .destructive) { Task { await model.forget() } }
                }
            }
        }
        .task {
            await diagnoseNow()
            setup = await model.read { api throws(DaemonRequestError) in try await api.phoneSetup() }
            health = await model.read { api throws(DaemonRequestError) in try await api.health() }.value
        }
        .toastHost($toast, seconds: 2) { _ in }
    }

    private var host: String { model.host?.name ?? model.settingsAPI?.host.name ?? "—" }

    private var proofWords: String {
        switch run?.probe.proof {
        case .proven: String(localized: "Your Mac proved it holds this phone's key before the key was sent.", comment: "This Mac: the proof matched")
        case .mismatch: String(localized: "This address did not prove it holds this phone's key; the key was not sent.", comment: "This Mac: the proof did not match")
        case .unsupported: String(localized: "This Dot cannot prove it holds the key; update Dot on your Mac.", comment: "This Mac: the daemon has no proof route")
        case .notRun, nil: String(localized: "Not checked yet.", comment: "This Mac: the proof was not asked")
        }
    }

    @ViewBuilder private var diagnosisSection: some View {
        GroupedSection(
            header: String(localized: "Connection", comment: "This Mac section: the hop-by-hop diagnosis"),
            footer: run.map { String(localized: "Checked at \(clockWords($0.at, format: TimestampFormat.current(.standard), locale: .current)) · trace \($0.trace)", comment: "This Mac: when the diagnosis ran and its trace id") }
        ) {
            if let run {
                ForEach(run.diagnosis.hops, id: \.0) { hop, result in hopRow(hop, result) }
            } else {
                ListRow(systemImage: nil, title: String(localized: "Checking each hop…", comment: "This Mac: the diagnosis is running"), description: nil, accessory: .none)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Space.s) { diagnosisButtons }
                VStack(alignment: .leading, spacing: Space.s) { diagnosisButtons }
            }
            .padding(Space.groupedGutter)
        }
    }

    @ViewBuilder private var diagnosisButtons: some View {
                Button(String(localized: "Check again", comment: "This Mac: runs the diagnosis again"), systemImage: "arrow.clockwise") { Task { await diagnoseNow() } }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(diagnosing)
                Button(String(localized: "Copy diagnostics", comment: "This Mac: copies the diagnosis report"), systemImage: "doc.on.doc") {
                    guard let run else { return }
                    UIPasteboard.general.string = diagnosisReport(run, host: host, appVersion: appVersionWords(), system: "iOS \(UIDevice.current.systemVersion)")
                    toast = ToastMessage(id: UUID(), text: String(localized: "Diagnostics copied", comment: "Toast: the diagnosis report is on the pasteboard"), systemImage: "checkmark.circle", actionTitle: nil)
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(run == nil)
    }

    private func hopRow(_ hop: DiagnosisHop, _ result: HopResult) -> some View {
        let (symbol, tone, words): (String, Color, String?) =
            switch result {
            case .passed: ("checkmark.circle.fill", Tone.secondary, nil)
            case .failed(let problem): ("xmark.circle.fill", Tone.failure, problemWords(problem))
            case .unknown: ("circle.dashed", Tone.tertiary, String(localized: "Not reached", comment: "Diagnosis: a hop after the failing one"))
            }
        return HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            Symbol(name: symbol, context: .feedback).foregroundStyle(tone).frame(minWidth: Metric.iconSlot)
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(hopTitle(hop)).textRole(.body).foregroundStyle(Tone.text)
                if let words { Text(words).textRole(.footnote).foregroundStyle(result == .unknown ? Tone.tertiary : Tone.text) }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Space.groupedGutter)
        .padding(.vertical, Space.s)
        .frame(minHeight: Metric.row)
        .accessibilityElement(children: .combine)
    }

    private func diagnoseNow() async {
        diagnosing = true
        run = await model.diagnoseConnection()
        diagnosing = false
    }
}

/// The app's version and build, as About shows them.
func appVersionWords() -> String {
    let info: [String: Any] = Bundle.main.infoDictionary ?? [:]
    return "\(info["CFBundleShortVersionString"] as? String ?? "?") (\(info["CFBundleVersion"] as? String ?? "?"))"
}

/// Settings › Notifications: the kinds of push this phone gets (`/api/phone/push-settings`, a phone's key only); a
/// switch is flipped at once and put back when the Mac refuses it.
struct NotificationsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<PushSettings> = .loading
    @State private var problem: SettingsFailure?
    @State private var permission: UNAuthorizationStatus?

    var body: some View {
        SettingsScroll(title: String(localized: "Notifications", comment: "Settings row")) {
            if permission == .denied {
                VStack(alignment: .leading, spacing: Space.s) {
                    Notice(tone: .warn, message: String(localized: "Notifications are off for Dot Next in iOS Settings.", comment: "Notifications: the OS permission is denied"), dismiss: nil)
                    Button(String(localized: "Open iOS Settings", comment: "Opens the app's page in iOS Settings")) {
                        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
            }
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › General", comment: "Where on the Mac: the General page"), retry: nil) }
            LoadedPage(state: state, macPlace: String(localized: "Settings › General", comment: "Where on the Mac: the General page"), retry: { Task { await load() } }) { settings in
                GroupedSection(
                    header: String(localized: "Tell me when", comment: "Notifications section: the kinds of push"),
                    footer: String(localized: "Dot Next gets notifications once it replaces the old Dot app on this iPhone; until then the old app keeps them. These switches are kept on your Mac for this phone.", comment: "Notifications footer: no push before the swap (owner's decision)")
                ) {
                    ToggleRow(
                        systemImage: "exclamationmark.bubble", title: String(localized: "Dot needs you", comment: "Push kind: an approval or a question waits"),
                        description: String(localized: "Approvals and questions that wait on you", comment: "Push kind description"), isOn: binding(settings, \.needsYou))
                    ToggleRow(
                        systemImage: "envelope.badge", title: String(localized: "Drafts ready", comment: "Push kind: a draft waits to be sent"),
                        description: String(localized: "A message Dot wrote for you to send", comment: "Push kind description"), isOn: binding(settings, \.ready))
                    ToggleRow(
                        systemImage: "checkmark.circle", title: String(localized: "Done or failed", comment: "Push kind: a task ended"),
                        description: String(localized: "A task you started ended", comment: "Push kind description"), isOn: binding(settings, \.done))
                }
            }
        }
        .task {
            await load()
            permission = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.pushSettings() }
    }

    private func binding(_ settings: PushSettings, _ key: WritableKeyPath<PushSettings, Bool>) -> Binding<Bool> {
        Binding(
            get: { state.value?[keyPath: key] ?? settings[keyPath: key] },
            set: { on in
                guard var changed = state.value else { return }
                let before: PushSettings = changed
                changed[keyPath: key] = on
                state = .loaded(changed)
                let sent: PushSettings = changed
                Task {
                    switch await model.write({ api throws(DaemonRequestError) in try await api.changePushSettings(sent) }) {
                    case .success(let saved):
                        state = .loaded(saved)
                        problem = nil
                    case .failure(let box):
                        state = .loaded(before)
                        problem = box.failure
                    }
                }
            })
    }
}
