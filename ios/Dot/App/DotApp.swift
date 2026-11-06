import SwiftUI
import UIKit
import UserNotifications
import os

@main
struct DotApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate: AppDelegate

    var body: some Scene {
        WindowGroup {
            RootView(model: appDelegate.model)
        }
    }
}

/// Owns the app's model and receives what only the application delegate is told: the APNs device token and the
/// notification the owner tapped.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    let model: AppModel
    private let keyStore: PhoneKeyStore
    private let client: DaemonClient
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "approvals")

    override init() {
        let keyStore: PhoneKeyStore = PhoneKeyStore()
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        let client: DaemonClient = DaemonClient(session: URLSession(configuration: configuration), maxAttempts: 5)
        self.keyStore = keyStore
        self.client = client
        model = AppModel(
            hostStore: HostStore(), keyStore: keyStore,
            registrar: PushRegistrar(keyStore: keyStore, client: client), buildHost: buildHostFromInfoPlist())
        super.init()
    }

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        let center: UNUserNotificationCenter = UNUserNotificationCenter.current()
        center.delegate = self
        center.setNotificationCategories([approvalCategory()])
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        model.registrar.didRegister(deviceToken: deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {
        model.registrar.didFailToRegister(error: error)
    }

    /// Shows the daemon's alerts while the app is open, too.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
    }

    /// Answers an approval from the lock screen (Approve, Deny), or opens the alert's `route` in the web view.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse
    ) async {
        let info: [AnyHashable: Any] = response.notification.request.content.userInfo
        let action: String = response.actionIdentifier
        if action == approveAction || action == denyAction {
            guard let approvalId: String = info["approval_id"] as? String, isApprovalId(approvalId) else {
                logger.warning("an approval's answer without a valid approval id")
                return
            }
            await answer(approvalId: approvalId, approve: action == approveAction)
            return
        }
        guard let route: String = info["route"] as? String else {
            return
        }
        await MainActor.run {
            model.open(route: route)
        }
    }

    /// Sends the owner's answer to the daemon with the phone's key; one that does not reach it is said in a notification
    /// of its own, so that a run is not left waiting on an answer the owner believes given.
    nonisolated private func answer(approvalId: String, approve: Bool) async {
        guard let host: DaemonHost = await MainActor.run(body: { model.host }) else {
            await tellNotAnswered()
            return
        }
        do {
            guard let key: String = try keyStore.read() else {
                await tellNotAnswered()
                return
            }
            try await client.decide(approvalId: approvalId, approve: approve, host: host, key: key)
            logger.info("approval answered from the lock screen: approve=\(approve)")
        } catch DaemonRequestError.status(_, let code, _) where code == alreadyDecided {
            // Answered meanwhile, on the Mac or the page, or no longer open: there is nothing to answer again.
            logger.info("the approval was no longer open")
        } catch {
            logger.error("the approval's answer did not reach Dot: error=\(String(describing: error), privacy: .public)")
            await tellNotAnswered()
        }
    }

    nonisolated private func tellNotAnswered() async {
        let content: UNMutableNotificationContent = UNMutableNotificationContent()
        content.title = "Dot did not get your answer"
        content.body = "Open Dot to answer it again."
        let request: UNNotificationRequest = UNNotificationRequest(
            identifier: UUID().uuidString, content: content, trigger: nil)
        do {
            try await UNUserNotificationCenter.current().add(request)
        } catch {
            logger.error("the notification of an answer not given could not be shown: error=\(String(describing: error), privacy: .public)")
        }
    }
}

/// The actions of the daemon's approval alerts (`category` `DOT_APPROVAL`, item 1008).
private let approvalCategoryId: String = "DOT_APPROVAL"
private let approveAction: String = "DOT_APPROVE"
private let denyAction: String = "DOT_DENY"
/// The daemon's answer to a decision on an approval no longer open (answered, expired or its run ended).
private let alreadyDecided: Int = 409

/// Approve and Deny on an approval's alert, each asking for Face ID (or the passcode) first: a phone left locked on a
/// table answers nothing.
private func approvalCategory() -> UNNotificationCategory {
    let approve: UNNotificationAction = UNNotificationAction(
        identifier: approveAction, title: "Approve", options: [.authenticationRequired],
        icon: UNNotificationActionIcon(systemImageName: "checkmark"))
    let deny: UNNotificationAction = UNNotificationAction(
        identifier: denyAction, title: "Deny", options: [.authenticationRequired, .destructive],
        icon: UNNotificationActionIcon(systemImageName: "xmark"))
    return UNNotificationCategory(
        identifier: approvalCategoryId, actions: [approve, deny], intentIdentifiers: [], options: [])
}
