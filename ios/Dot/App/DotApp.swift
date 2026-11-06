import SwiftUI
import UIKit
import UserNotifications

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

    override init() {
        let keyStore: PhoneKeyStore = PhoneKeyStore()
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        let client: DaemonClient = DaemonClient(session: URLSession(configuration: configuration), maxAttempts: 5)
        model = AppModel(
            hostStore: HostStore(), keyStore: keyStore,
            registrar: PushRegistrar(keyStore: keyStore, client: client), buildHost: buildHostFromInfoPlist())
        super.init()
    }

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
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

    /// Opens the alert's `route` (a path on the daemon) in the web view.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse
    ) async {
        guard let route: String = response.notification.request.content.userInfo["route"] as? String else {
            return
        }
        await MainActor.run {
            model.open(route: route)
        }
    }
}
