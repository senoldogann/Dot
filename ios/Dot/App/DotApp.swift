import SwiftUI
import UIKit
import UserNotifications
import os

@main
struct DotApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate: AppDelegate

    @Environment(\.scenePhase) private var scenePhase: ScenePhase

    var body: some Scene {
        WindowGroup {
            RootView(model: appDelegate.model)
        }
        // The app is in front again: every stream reconnects at once, resuming from its last event. Out of sight it keeps what
        // it holds, closes its streams after a few seconds and asks the system for a refresh now and then.
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                if let native: NativeStore = appDelegate.model.native {
                    native.enterForeground()
                    native.reconnect()
                }
            case .background:
                appDelegate.model.native?.enterBackground()
                BackgroundRefresh.schedule()
            case .inactive:
                break
            @unknown default:
                break
            }
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
        BackgroundRefresh.register(model: model)
        return true
    }

    /// A push that says there is something new (`content-available`) or arrives while the app is open: the list and the chats
    /// that moved on are read now, within the time iOS gives.
    func application(
        _ application: UIApplication, didReceiveRemoteNotification userInfo: [AnyHashable: Any],
        fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        Task { @MainActor in
            let reached: Bool = await model.native?.refreshInBackground(seconds: 15) ?? false
            completionHandler(reached ? .newData : .failed)
        }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        model.registrar.didRegister(deviceToken: deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {
        model.registrar.didFailToRegister(error: error)
    }

    /// Shows the daemon's alerts while the app is open, too.
    ///
    /// The forms with a completion handler (item 1114): the `async` form of these methods completed on a thread of
    /// Swift's own, and UIKit, which updates the app's snapshot as a tap's handling is done, stopped the app with an
    /// assertion at every tap of a notification. iOS calls this one on the main thread.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping @Sendable (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list, .sound])
    }

    /// Answers an approval from the lock screen (Approve, Deny), or opens the alert's `route` in the web view; tells
    /// iOS it is done on the main thread (item 1114).
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping @Sendable () -> Void
    ) {
        let info: [AnyHashable: Any] = response.notification.request.content.userInfo
        let action: String = response.actionIdentifier
        let approvalId: String? = info["approval_id"] as? String
        let route: String? = info["route"] as? String
        Task { @MainActor in
            await self.handle(action: action, approvalId: approvalId, route: route)
            completionHandler()
        }
    }

    private func handle(action: String, approvalId: String?, route: String?) async {
        if action == approveAction || action == denyAction {
            guard let approvalId, isApprovalId(approvalId) else {
                logger.warning("an approval's answer without a valid approval id")
                return
            }
            await answer(approvalId: approvalId, approve: action == approveAction)
            return
        }
        // The plain tap on the alert and its Open action both open the run's chat (when the alert names the run).
        guard let route else {
            return
        }
        model.open(route: route)
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
private let openAction: String = "DOT_OPEN"
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
    // Open brings the app to the front on the run's chat, where the step's whole text is: Approve and Deny answer
    // without opening the app.
    let open: UNNotificationAction = UNNotificationAction(
        identifier: openAction, title: "Open", options: [.foreground],
        icon: UNNotificationActionIcon(systemImageName: "arrow.up.forward.app"))
    return UNNotificationCategory(
        identifier: approvalCategoryId, actions: [approve, deny, open], intentIdentifiers: [], options: [])
}
