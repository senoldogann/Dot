// ActivityKit handles are used on the main actor. Its SDK has no Sendable annotations for the handles.
@preconcurrency import ActivityKit
import DotEngine
import UIKit
import UserNotifications
import os

@MainActor
final class PhonePushDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    let push: PhonePush = PhonePush()
    let model: AppModel = AppModel()

    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        // ActivityKit can launch the app in the background to deliver an update token without drawing a window.
        if !LaunchOptions.read(UserDefaults.standard).drawsFixture {
            model.bind(push: push)
            model.load()
        }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken token: Data) {
        push.receivedDeviceToken(token)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {
        PhonePush.log.error("APNs registration failed error=\(error.localizedDescription, privacy: .public)")
    }

    // Use UIKit's completion handlers on the main actor. The async delegate form crashed the older app on a tap.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
        withCompletionHandler completion: @escaping @Sendable () -> Void
    ) {
        let route: String? = response.notification.request.content.userInfo["route"] as? String
        let run: String? = route.flatMap(PhoneRunLink.run(inRoute:))
        Task { @MainActor in
            if let run { push.open(run: run) }
            completion()
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification,
        withCompletionHandler completion: @escaping @Sendable (UNNotificationPresentationOptions) -> Void
    ) {
        let route: String? = notification.request.content.userInfo["route"] as? String
        let run: String? = route.flatMap(PhoneRunLink.run(inRoute:))
        Task { @MainActor in
            let options: UNNotificationPresentationOptions = if let run, push.shouldPresent(run: run) {
                [.banner, .list, .sound, .badge]
            } else {
                [.badge]
            }
            completion(options)
        }
    }
}

/// Owns ActivityKit and UIKit callbacks for the current paired link. Token bytes stay in memory.
@MainActor
final class PhonePush {
    nonisolated static let log: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "push")
    private static let isSimulator: Bool = {
        #if targetEnvironment(simulator)
            true
        #else
            false
        #endif
    }()

    private var api: DaemonAPI?
    private var deviceToken: PhonePushToken?
    private var postedDevice: PhonePushToken?
    private var deviceWork: Task<Void, Never>?
    private var startWork: Task<Void, Never>?
    private var activityWork: Task<Void, Never>?
    private var activityStateWatch: Task<Void, Never>?
    private var activityWatch: Task<Void, Never>?
    private var activityID: String?
    private var offeredRuns: Set<String> = []
    private var pendingRun: String?
    private weak var model: AppModel?

    func bind(to model: AppModel) {
        self.model = model
        if let pendingRun {
            self.pendingRun = nil
            model.requestedRun = pendingRun
        }
    }

    func open(run: String) {
        if let model { model.requestedRun = run } else { pendingRun = run }
    }

    func shouldPresent(run: String) -> Bool {
        guard let model, let thread = model.visiblePushThread else { return true }
        return model.list.rows.first { $0.summary.id == thread }?.summary.latestRunId != run
    }

    func connect(to api: DaemonAPI) {
        self.api = api
        guard !Self.isSimulator else { return }
        refreshStartToken()
        refreshActivities()
        if UIApplication.shared.applicationState == .active { foreground() }
    }

    func foreground() {
        guard !Self.isSimulator else { return }
        guard api != nil else { return }
        Task {
            do {
                _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            } catch {
                Self.log.error("Notification permission request failed error=\(error.localizedDescription, privacy: .public)")
            }
            if api != nil { UIApplication.shared.registerForRemoteNotifications() }
        }
        postedDevice = nil
        postDevice()
        refreshStartToken()
        refreshActivities()
    }

    func disconnect() async {
        let paired: DaemonAPI? = api
        api = nil
        postedDevice = nil
        offeredRuns = []
        let tasks: [Task<Void, Never>] = [deviceWork, startWork, activityWatch, activityWork, activityStateWatch].compactMap { $0 }
        tasks.forEach { $0.cancel() }
        for task in tasks { await task.value }
        deviceWork = nil
        startWork = nil
        activityWatch = nil
        activityWork = nil
        activityStateWatch = nil
        activityID = nil
        guard let paired else { return }
        async let start: Bool = Self.post("forget start token", { () async throws(DaemonRequestError) in try await paired.forgetLiveStart() })
        for activity in Activity<DotFleetAttributes>.activities {
            await activity.end(nil, dismissalPolicy: .immediate)
            if let token = activity.pushToken.flatMap(PhonePushToken.init) {
                _ = await Self.post("forget activity token") { () async throws(DaemonRequestError) in try await paired.forgetFleetPush(token) }
            }
        }
        _ = await start
    }

    func receivedDeviceToken(_ token: Data) {
        guard let token = PhonePushToken(token) else {
            Self.log.error("APNs returned a device token of unexpected length")
            return
        }
        deviceToken = token
        postDevice()
    }

    private func postDevice() {
        guard let api, let deviceToken, deviceToken != postedDevice, deviceWork == nil else { return }
        guard let entitlement = Bundle.main.object(forInfoDictionaryKey: "DotApsEnvironment") as? String,
            let environment = PhonePushEnvironment(entitlement: entitlement)
        else {
            Self.log.error("The build has no recognized APNs environment")
            return
        }
        deviceWork = Task { [weak self] in
            guard let self else { return }
            var next: PhonePushToken? = deviceToken
            // Serialize rotations so a retry of the old token cannot overwrite the new one.
            while let token = next, !Task.isCancelled {
                if await Self.post("device token", { () async throws(DaemonRequestError) in try await api.registerDevicePush(token, environment: environment) }) {
                    postedDevice = token
                }
                next = self.deviceToken == token ? nil : self.deviceToken
            }
            deviceWork = nil
        }
    }

    private func refreshStartToken() {
        guard let api, startWork == nil else { return }
        startWork = Task {
            // A change of permission and a rotation are observed together. Each pass reads the current value so
            // a token emitted before Live Activities were switched off cannot register it again after deletion.
            let changes: AsyncStream<Bool> = AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
                let tokens: Task<Void, Never> = Task {
                    for await _ in Activity<DotFleetAttributes>.pushToStartTokenUpdates { continuation.yield(true) }
                }
                let permission: Task<Void, Never> = Task {
                    for await enabled in ActivityAuthorizationInfo().activityEnablementUpdates { continuation.yield(enabled) }
                }
                continuation.onTermination = { _ in tokens.cancel(); permission.cancel() }
                continuation.yield(ActivityAuthorizationInfo().areActivitiesEnabled)
            }
            var sent: PhonePushToken?
            for await _ in changes {
                if Task.isCancelled { return }
                if !ActivityAuthorizationInfo().areActivitiesEnabled {
                    _ = await Self.post("forget start token") { () async throws(DaemonRequestError) in try await api.forgetLiveStart() }
                    sent = nil
                } else if let token = Activity<DotFleetAttributes>.pushToStartToken.flatMap(PhonePushToken.init), sent != token {
                    if await Self.post("start token", { () async throws(DaemonRequestError) in try await api.registerLiveStart(token) }) { sent = token }
                }
            }
        }
    }

    private func refreshActivities() {
        guard api != nil, activityWatch == nil else { return }
        activityWatch = Task { [weak self] in
            for activity in Activity<DotFleetAttributes>.activities { self?.observe(activity) }
            for await activity in Activity<DotFleetAttributes>.activityUpdates {
                if Task.isCancelled { return }
                self?.observe(activity)
            }
        }
    }

    private func observe(_ activity: Activity<DotFleetAttributes>) {
        guard let api, activityID != activity.id,
            activity.activityState == .active || activity.activityState == .stale
        else { return }
        let previous: Task<Void, Never>? = activityWork
        previous?.cancel()
        activityStateWatch?.cancel()
        activityID = activity.id
        let tokens: Task<Void, Never> = Task { @MainActor in
            await previous?.value
            guard !Task.isCancelled else { return }
            await Self.sendActivityTokens(activity, api: api)
        }
        activityWork = tokens
        activityStateWatch = Task { @MainActor in
            if activity.activityState == .active || activity.activityState == .stale {
                for await state in activity.activityStateUpdates where state == .ended || state == .dismissed { break }
            }
            guard !Task.isCancelled else { return }
            tokens.cancel()
            await tokens.value
            if let token = activity.pushToken.flatMap(PhonePushToken.init) {
                _ = await Self.post("forget activity token") { () async throws(DaemonRequestError) in try await api.forgetFleetPush(token) }
            }
        }
    }

    private static func sendActivityTokens(_ activity: Activity<DotFleetAttributes>, api: DaemonAPI) async {
        var sent: PhonePushToken?
        if let token = activity.pushToken.flatMap(PhonePushToken.init),
            await post("activity token", { () async throws(DaemonRequestError) in try await api.registerFleetPush(token) })
        { sent = token }
        for await bytes in activity.pushTokenUpdates {
            if Task.isCancelled { return }
            guard let token = PhonePushToken(bytes), token != sent else { continue }
            if await post("activity token", { () async throws(DaemonRequestError) in try await api.registerFleetPush(token) }) { sent = token }
        }
    }

    /// Sources excluded from the daemon's fleet in notify/apns_watch.py.
    private static let unwatchedSources: Set<String> = ["schedule", "watcher", "initiative", "reply", "eval"]

    /// Starts one fleet card from a fresh connected list. The daemon owns its live updates and ending.
    func show(_ list: ListProjection) {
        guard !Self.isSimulator else { return }
        guard api != nil, list.freshness == .live else { return }
        let running: [ThreadRow] = list.ordered.filter {
            let summary: ThreadSummary = $0.summary
            return !Self.unwatchedSources.contains(summary.source) && (summary.latestStatus == "running" || summary.latestStatus == "blocked")
        }
        let ids: Set<String> = Set(running.map(\.summary.latestRunId))
        offeredRuns.formIntersection(ids)
        guard UIApplication.shared.applicationState == .active, !ids.subtracting(offeredRuns).isEmpty,
            ActivityAuthorizationInfo().areActivitiesEnabled
        else { return }
        offeredRuns.formUnion(ids)
        guard !Activity<DotFleetAttributes>.activities.contains(where: { $0.activityState == .active || $0.activityState == .stale }) else { return }
        do {
            let rows: [DotFleetAttributes.Row] = running.prefix(4).map { row in
                let summary: ThreadSummary = row.summary
                let waiting: Bool = summary.latestStatus == "blocked"
                return DotFleetAttributes.Row(
                    runId: summary.latestRunId, title: summary.title, agent: "", status: waiting ? "waiting" : "working",
                    kind: waiting ? "waiting" : "working", line: waiting ? "Waiting for you" : "Working", startedAt: summary.latestCreatedAt)
            }
            let activity: Activity<DotFleetAttributes> = try Activity.request(
                attributes: DotFleetAttributes(fleetId: fleetActivityId),
                content: ActivityContent(state: DotFleetAttributes.ContentState(active: running.count, rows: rows), staleDate: nil),
                pushType: .token)
            observe(activity)
        } catch {
            Self.log.error("Live Activity request failed error=\(error.localizedDescription, privacy: .public)")
        }
    }

    /// The API makes one request per attempt. APNs tokens and daemon response bodies stay out of push logs.
    @discardableResult
    private static nonisolated func post(_ operation: String, _ send: @Sendable () async throws(DaemonRequestError) -> Void) async -> Bool {
        for backoff: Int in [1, 2, 4, 0] {
            if Task.isCancelled { return false }
            do {
                try await send()
                return true
            } catch {
                if Task.isCancelled { return false }
                log.warning("Push registration failed operation=\(operation, privacy: .public) status=\(error.statusCode ?? 0) retry=\(error.isRetryable && backoff > 0)")
                guard error.isRetryable, backoff > 0 else { return false }
                do { try await Task.sleep(for: .seconds(backoff)) } catch is CancellationError { return false } catch {
                    preconditionFailure("Task.sleep throws only CancellationError: \(error)")
                }
            }
        }
        return false
    }
}
