import ActivityKit
import Foundation
import UIKit
import UserNotifications
import os

/// Registers with the daemon what Apple gives this phone: the device token, the Live Activity push-to-start token and
/// each activity's update token.
///
/// It runs once the phone's key and the daemon's host are both known. The last tokens are kept in memory only; every
/// registration sends both the device token and the start token.
@MainActor
final class PushRegistrar {
    private let keyStore: PhoneKeyStore
    private let client: DaemonClient
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "push")

    private var host: DaemonHost?
    private var deviceToken: String?
    private var startToken: String?
    private var started: Bool = false
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
        tasks.append(Task { [weak self] in await self?.observeStartTokens() })
        tasks.append(Task { [weak self] in await self?.observeActivities() })
    }

    /// Stops observing and forgets the tokens, as when the owner changes the daemon's address.
    func stop() {
        tasks.forEach { $0.cancel() }
        tasks = []
        started = false
        host = nil
        deviceToken = nil
        startToken = nil
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

    private func observeStartTokens() async {
        for await data: Data in Activity<DotRunAttributes>.pushToStartTokenUpdates {
            let token: String = hexString(data)
            logger.info("push-to-start token received: token=\(redactToken(token), privacy: .public)")
            startToken = token
            sendRegistration()
        }
    }

    private func observeActivities() async {
        for activity: Activity<DotRunAttributes> in Activity<DotRunAttributes>.activities {
            observe(activity)
        }
        for await activity: Activity<DotRunAttributes> in Activity<DotRunAttributes>.activityUpdates {
            observe(activity)
        }
    }

    private func observe(_ activity: Activity<DotRunAttributes>) {
        guard !observedActivities.contains(activity.id) else {
            return
        }
        observedActivities.insert(activity.id)
        let runId: String = activity.attributes.runId
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

    /// Posts the device and start tokens; waits for the device token, since the daemon keys a phone's tokens on it.
    private func sendRegistration() {
        guard let deviceToken else {
            return
        }
        let registration: APNsRegistration = APNsRegistration(
            deviceToken: deviceToken, startToken: startToken, environment: apnsEnvironment())
        withCredentials { (client: DaemonClient, host: DaemonHost, key: String) in
            try await client.registerAPNs(registration, host: host, key: key)
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
