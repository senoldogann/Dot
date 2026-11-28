import DotEngine
import SwiftUI

/// What System reads at once.
struct SystemPicture: Sendable {
    let health: HealthView
    let status: StatusView?
}

/// Uptime as the owner reads it: "26 h 0 min", "3 d 4 h".
func uptimeWords(_ seconds: Double, locale: Locale) -> String {
    let formatter: DateComponentsFormatter = DateComponentsFormatter()
    formatter.allowedUnits = seconds >= 86400 ? [.day, .hour] : [.hour, .minute]
    formatter.unitsStyle = .short
    var calendar: Calendar = Calendar.current
    calendar.locale = locale
    formatter.calendar = calendar
    return formatter.string(from: seconds) ?? ""
}

/// Settings › System › Health and version: whether Dot answers and how it stands (runs, approvals, scheduler, stop),
/// its version and the app's, its health checks, and restarting Dot when the Mac's service can bring it back.
struct SystemPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<SystemPicture> = .loading
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        SettingsScroll(title: String(localized: "Health and version", comment: "Settings row: the daemon's health, version, emergency stop")) {
            LoadedPage(state: state, macPlace: String(localized: "Settings › System", comment: "Where on the Mac: the System page"), retry: { Task { await load() } }) { picture in
                GroupedSection(header: String(localized: "Dot on the Mac", comment: "System section: the daemon"), footer: nil) {
                    ListRow(systemImage: picture.health.ok ? "checkmark.circle" : "xmark.circle", title: String(localized: "Health", comment: "System: whether the daemon is well"), description: nil, accessory: .detail(picture.health.ok ? String(localized: "Answers", comment: "System: the daemon answers") : String(localized: "Not well", comment: "System: the daemon reports a problem")))
                    ListRow(systemImage: "info.circle", title: String(localized: "Dot version", comment: "This Mac: the daemon's version"), description: nil, accessory: .detail(picture.health.version))
                    ListRow(systemImage: "bell.badge", title: String(localized: "Notifications service", comment: "System: the push service's state"), description: nil, accessory: .detail(serviceWords(picture.health.notifications)))
                    ListRow(systemImage: "cup.and.saucer", title: String(localized: "Keeping the Mac awake", comment: "System: the keep-awake state"), description: nil, accessory: .detail(awakeWords(picture.health.awake)))
                    if let status = picture.status {
                        ListRow(systemImage: "clock", title: String(localized: "Running for", comment: "System: the daemon's uptime"), description: nil, accessory: .detail(uptimeWords(status.uptimeS, locale: locale)))
                    }
                }
                if let status = picture.status {
                    GroupedSection(header: String(localized: "Now", comment: "System section: what Dot does now"), footer: nil) {
                        ListRow(systemImage: "bolt", title: String(localized: "Tasks at work", comment: "System: the number of active runs"), description: nil, accessory: .detail("\(status.activeRuns.count)"))
                        ListRow(systemImage: "exclamationmark.bubble", title: String(localized: "Waiting on you", comment: "Status word: a chat waits for the owner's approval or answer"), description: nil, accessory: .detail("\(status.pendingApprovals)"))
                        ListRow(systemImage: "calendar", title: String(localized: "Scheduler", comment: "System: whether schedules fire"), description: nil, accessory: .detail(status.schedulerRunning ? String(localized: "Running", comment: "This Mac: Tailscale runs") : String(localized: "Held", comment: "System: the scheduler is held (emergency stop)")))
                        ListRow(systemImage: "stop.circle", title: String(localized: "Emergency stop", comment: "System: whether the Mac's emergency stop holds"), description: nil, accessory: .detail(status.failsafeEngaged ? String(localized: "On", comment: "VoiceOver value of a switch that is on") : String(localized: "Off", comment: "VoiceOver value of a switch that is off")))
                        ListRow(systemImage: "checkmark.shield", title: String(localized: "Approval mode", comment: "Inspector row: the run's approval mode"), description: nil, accessory: .detail(approvalWords(status.approvals)))
                    }
                }
                SystemRestartRow(reconnected: load)
                GroupedSection(
                    header: String(localized: "This app", comment: "System section: DotNext itself"),
                    footer: String(localized: "Updating Dot is done on the Mac: Settings › System.", comment: "System: updating Dot is done on the Mac")
                ) {
                    ListRow(systemImage: "iphone", title: String(localized: "Dot Next version", comment: "System: the app's version"), description: nil, accessory: .detail(appVersionWords()))
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        let health: Loadable<HealthView> = await model.read { api throws(DaemonRequestError) in try await api.health() }
        switch health {
        case .loaded(let value):
            let status: StatusView? = await model.read { api throws(DaemonRequestError) in try await api.fullStatus() }.value
            state = .loaded(SystemPicture(health: value, status: status))
        case .failed(let failure): state = .failed(failure)
        case .loading: state = .loading
        }
    }

    private func serviceWords(_ word: String) -> String {
        switch word {
        case "ok": String(localized: "Working", comment: "System: a service works")
        case "down": String(localized: "Down", comment: "System: a service is down")
        default: String(localized: "Off", comment: "This Mac: Tailscale is off")
        }
    }

    private func awakeWords(_ word: String) -> String {
        switch word {
        case "holding": String(localized: "Now, while Dot works", comment: "System: the Mac is kept awake now")
        case "idle": String(localized: "When Dot works", comment: "System: keep-awake is on, nothing works now")
        default: String(localized: "Off", comment: "This Mac: Tailscale is off")
        }
    }
}

/// Settings › System › Usage: the tokens of the last seven days, one bar a day (`/api/usage`).
struct UsagePage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<UsageView> = .loading
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        SettingsScroll(title: String(localized: "Usage", comment: "Settings row: the tokens of the last days")) {
            LoadedPage(state: state, macPlace: String(localized: "Settings › Models › Usage", comment: "Where on the Mac: the usage"), retry: { Task { await load() } }) { usage in
                let most: Int = max(usage.days.map { $0.inputTokens + $0.outputTokens }.max() ?? 1, 1)
                GroupedSection(
                    header: String(localized: "Last 7 days", comment: "Usage: the days shown"),
                    footer: String(localized: "Tokens read and written by every model call, as the Mac counts them.", comment: "Usage: what is counted")
                ) {
                    ForEach(usage.days.reversed(), id: \.day) { day in
                        let total: Int = day.inputTokens + day.outputTokens
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            HStack {
                                Text(dayWords(day.day)).textRole(.body).foregroundStyle(Tone.text)
                                Spacer()
                                Text(String(localized: "\(tokenWords(total, locale: locale)) · \(day.runs) tasks", comment: "Usage: a day's tokens and tasks"))
                                    .textRole(.footnote).foregroundStyle(Tone.secondary).tabularNumbers()
                            }
                            GeometryReader { proxy in
                                Capsule().fill(Tone.nested).overlay(alignment: .leading) {
                                    Capsule().fill(Tone.text).frame(width: max(4, proxy.size.width * CGFloat(total) / CGFloat(most)))
                                }
                            }
                            .frame(height: 6)
                            .accessibilityHidden(true)
                        }
                        .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.usage(days: 7) }
    }

    private func dayWords(_ iso: String) -> String {
        guard let date = try? Date(iso, strategy: .iso8601.year().month().day()) else { return iso }
        return date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated).locale(locale))
    }
}
