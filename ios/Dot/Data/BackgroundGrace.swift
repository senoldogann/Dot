import UIKit
import os

/// The moment iOS gives an app that has just left the screen (about thirty seconds) to finish what it started: a message
/// on its way, a file being written. Held while there is such work, given back as soon as it is done.
@MainActor
final class BackgroundGrace {
    private var identifier: UIBackgroundTaskIdentifier = .invalid
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "grace")

    var isHeld: Bool { identifier != .invalid }

    /// Asks for the time; `onExpire` runs when iOS takes it back before `end` was called.
    func begin(onExpire: @escaping @MainActor () -> Void) {
        guard identifier == .invalid else { return }
        identifier = UIApplication.shared.beginBackgroundTask(withName: "dev.dotagent.phone.finish") { [weak self] in
            Task { @MainActor in
                self?.logger.info("the time for finishing ran out")
                onExpire()
                self?.end()
            }
        }
    }

    func end() {
        guard identifier != .invalid else { return }
        UIApplication.shared.endBackgroundTask(identifier)
        identifier = .invalid
    }
}
