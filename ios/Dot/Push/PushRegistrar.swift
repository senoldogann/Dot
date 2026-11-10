import ActivityKit
import Foundation
import UIKit
import UserNotifications
import os

/// Registers with the daemon what Apple gives this phone: the device token and the Live Activity's update token, and
/// opens the Live Activity when the owner starts a run here.
///
/// It runs once the phone's key and the daemon's host are both known. The device token is kept in memory only. No
/// push-to-start token is given: the daemon starts no activity, which would come with a notification (item 1096).
@MainActor
final class PushRegistrar {
    private let keyStore: PhoneKeyStore
    private let client: DaemonClient
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "push")

    private var host: DaemonHost?
    private var deviceToken: String?
    private var started: Bool = false
    private var opening: Bool = false
    private var observedActivities: Set<String> = []
    private var tasks: [Task<Void, Never>] = []

    init(keyStore: PhoneKeyStore, client: DaemonClient) {
        self.keyStore = keyStore
        self.client = client
    }

    /// Starts registering for `host`: asks for notification permission (iOS asks only once), registers for remote
    /// notifications and observes the Live Activity tokens. Calling it again for the same host does nothing.
    func start(host: DaemonHost) {
        if started && self.host == host {
            return
        }
        stop()
        self.host = host
        started = true
        tasks.append(Task { [weak self] in await self?.requestPermissionAndRegister() })
        tasks.append(Task { [weak self] in await self?.observeActivities() })
    }

    /// Stops observing and forgets the tokens, as when the owner changes the daemon's address.
    func stop() {
        tasks.forEach { $0.cancel() }
        tasks = []
        started = false
        opening = false
        host = nil
        deviceToken = nil
        observedActivities = []
    }

    /// Called by the app delegate with the token APNs gave this device.
    func didRegister(deviceToken data: Data) {
        let token: String = hexString(data)
        logger.info("device token received: token=\(redactToken(token), privacy: .public)")
        deviceToken = token
        sendRegistration()
    }

    func didFailToRegister(error: any Error) {
        logger.error("remote notification registration failed: error=\(error.localizedDescription, privacy: .public)")
    }

    private func requestPermissionAndRegister() async {
        do {
            let granted: Bool = try await UNUserNotificationCenter.current().requestAuthorization(options: [
                .alert, .sound, .badge,
            ])
            logger.info("notification permission: granted=\(granted)")
        } catch {
            logger.error("notification permission request failed: error=\(error.localizedDescription, privacy: .public)")
        }
        // A device token is needed for Live Activity updates even when alerts are not allowed.
        UIApplication.shared.registerForRemoteNotifications()
    }

    /// Opens the phone's one Live Activity, unless one is up (item 1096): from the app in front, which it is when the
    /// owner starts a run here, it comes without a notification. It opens with no row; the daemon fills it once its
    /// token arrives, and ends it when no run is left. The activity the daemon may still keep for this phone (swiped
    /// away while the app was in the background, its forget not sent yet) is forgotten first, so that the new one's
    /// token is kept as new, its age counted from now.
    func openActivity() {
        guard started, let host, !opening, !activityUp() else {
            return
        }
        guard ActivityAuthorizationInfo().areActivitiesEnabled else {
            logger.info("live activities are switched off for Dot: no card opened")
            return
        }
        let key: String
        do {
            guard let saved: String = try keyStore.read() else {
                return
            }
            key = saved
        } catch {
            logger.error("phone key unreadable: error=\(error.description, privacy: .public)")
            return
        }
        // One at a time: a second run started meanwhile must not forget the token the first one's activity gave.
        opening = true
        let client: DaemonClient = self.client
        tasks.append(
            Task { [weak self] in
                do {
                    try await client.forgetActivity(token: nil, host: host, key: key)
                    self?.requestActivity()
                } catch {
                    self?.logger.error(
                        "forgetting the old activity failed: error=\(String(describing: error), privacy: .public)")
                }
                self?.opening = false
            })
    }

    private func activityUp() -> Bool {
        Activity<DotFleetAttributes>.activities.contains { (activity: Activity<DotFleetAttributes>) in
            activity.activityState == .active || activity.activityState == .stale
        }
    }

    private func requestActivity() {
        guard !activityUp() else {
            return
        }
        do {
            let activity: Activity<DotFleetAttributes> = try Activity<DotFleetAttributes>.request(
                attributes: DotFleetAttributes(fleetId: fleetActivityId),
                content: ActivityContent(state: DotFleetAttributes.ContentState(active: 1, rows: []), staleDate: nil),
                pushType: .token)
            observe(activity)
        } catch {
            logger.error("opening the live activity failed: error=\(error.localizedDescription, privacy: .public)")
        }
    }

    private func observeActivities() async {
        let current: [Activity<DotFleetAttributes>] = Activity<DotFleetAttributes>.activities
        // None on this phone: one the daemon still has a token of was swiped away or ended while the app was closed,
        // and the next run the owner starts here opens a new one (items 1093, 1096).
        if current.isEmpty {
            forget(token: nil)
        }
        for activity: Activity<DotFleetAttributes> in current {
            observe(activity)
        }
        for await activity: Activity<DotFleetAttributes> in Activity<DotFleetAttributes>.activityUpdates {
            observe(activity)
        }
    }

    private func observe(_ activity: Activity<DotFleetAttributes>) {
        guard !observedActivities.contains(activity.id) else {
            return
        }
        observedActivities.insert(activity.id)
        let runId: String = activity.attributes.fleetId
        tasks.append(
            Task { [weak self] in
                for await state: ActivityState in activity.activityStateUpdates {
                    guard let self else {
                        return
                    }
                    // Swiped away by the owner: its token goes nowhere any more. One that ended was ended by the
                    // daemon, which forgot it, or by iOS after eight hours, which the daemon tells by its age.
                    if state == .dismissed {
                        self.logger.info("activity gone: state=\(String(describing: state), privacy: .public)")
                        // Its own token: a newer activity's, which the daemon may keep by now, stays. One that never
                        // had a token gave the daemon none to forget.
                        if let token: String = activity.pushToken.map(hexString) {
                            self.forget(token: token)
                        }
                        return
                    }
                }
            })
        tasks.append(
            Task { [weak self] in
                for await data: Data in activity.pushTokenUpdates {
                    guard let self else {
                        return
                    }
                    let token: String = hexString(data)
                    self.logger.info(
                        "activity token received: run=\(runId, privacy: .public) token=\(redactToken(token), privacy: .public)"
                    )
                    self.post(ActivityRegistration(runId: runId, token: token))
                }
            })
    }

    /// Posts the device token, and no start token (item 1096).
    private func sendRegistration() {
        guard let deviceToken else {
            return
        }
        let registration: APNsRegistration = APNsRegistration(
            deviceToken: deviceToken, startToken: nil, environment: apnsEnvironment())
        withCredentials { (client: DaemonClient, host: DaemonHost, key: String) in
            try await client.registerAPNs(registration, host: host, key: key)
        }
    }

    private func forget(token: String?) {
        withCredentials { (client: DaemonClient, host: DaemonHost, key: String) in
            try await client.forgetActivity(token: token, host: host, key: key)
        }
    }

    private func post(_ registration: ActivityRegistration) {
        withCredentials { (client: DaemonClient, host: DaemonHost, key: String) in
            try await client.registerActivity(registration, host: host, key: key)
        }
    }

    /// Runs `request` with the host and the key read from the Keychain, logging a failure after the client's retries.
    private func withCredentials(
        _ request: @escaping @Sendable (DaemonClient, DaemonHost, String) async throws -> Void
    ) {
        guard let host else {
            return
        }
        let key: String
        do {
            guard let saved: String = try keyStore.read() else {
                logger.warning("no phone key yet: registration waits for pairing")
                return
            }
            key = saved
        } catch {
            logger.error("phone key unreadable: error=\(error.description, privacy: .public)")
            return
        }
        let client: DaemonClient = self.client
        let logger: Logger = self.logger
        tasks.append(
            Task {
                do {
                    try await request(client, host, key)
                } catch {
                    logger.error("registration with the daemon failed: error=\(String(describing: error), privacy: .public)")
                }
            })
    }
}
