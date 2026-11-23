import BackgroundTasks
import UIKit
import os

/// The refresh iOS may give the app while it is out of sight (`BGAppRefreshTask`, item 1371): the list and the chats that
/// moved on are read and written, so that the next time the app opens it shows what the Mac has. iOS decides when (usually
/// after the owner's habits, not on a clock) and may never; nothing here depends on it, and the simulator does not run it. It
/// needs no entitlement: the identifier is named in the Info.plist (`BGTaskSchedulerPermittedIdentifiers`) with the
/// `fetch` background mode.
enum BackgroundRefresh {
    static let identifier: String = "dev.dotagent.phone.refresh"
    private static let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "background-refresh")

    /// Registers the handler; it must be done before the app finishes launching.
    @MainActor
    static func register(model: AppModel) {
        let registered: Bool = BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            guard let task: BGAppRefreshTask = task as? BGAppRefreshTask else { return }
            let work: Task<Void, Never> = Task { @MainActor in
                schedule()
                let reached: Bool = await model.native?.refreshInBackground(seconds: 20) ?? false
                task.setTaskCompleted(success: reached)
            }
            task.expirationHandler = {
                work.cancel()
                task.setTaskCompleted(success: false)
            }
        }
        if !registered { logger.error("the background refresh could not be registered") }
    }

    /// Asks iOS for a refresh a quarter of an hour from now or later.
    static func schedule() {
        let request: BGAppRefreshTaskRequest = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            // The simulator, or the owner turned Background App Refresh off: not a failure of the app.
            logger.info("no background refresh was scheduled: \(String(describing: error), privacy: .public)")
        }
    }
}
