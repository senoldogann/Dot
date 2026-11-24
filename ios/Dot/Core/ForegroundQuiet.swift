import Foundation

/// How an alert that arrives while the app is open is drawn (item 1446).
enum AlertPresentation: Equatable, Sendable {
    /// Not drawn at all: the owner is reading the chat it is about.
    case quiet
    /// A banner, with its sound and its place in the list of alerts.
    case banner
}

/// The app and the daemon's lease agree on when Dot is quiet (items 1420 and 1440): while the app says it is in front of the
/// owner the daemon keeps the end of a task from this phone, and while the owner reads a chat the app does not draw an alert
/// for that chat either. An alert for another chat (a question another task asks) is drawn as a banner as before, and so is
/// every alert while the app is not the one thing in front of the owner (inactive: a system sheet, the app switcher).
/// No screen, no system: the delegate asks, and draws what this says.
enum ForegroundQuiet {
    /// `runIdsOnScreen` are the runs of the chat on the screen (its own and its helpers'), `nil` when no chat is: the list or
    /// Settings. `alertRunId` is the run the alert's route names (`#/runs/<id>`), `nil` for an alert that is about none (a
    /// campaign, a schedule, Dot's own message): it is not known to be about the chat on the screen, so it is drawn.
    static func presentation(appIsActive: Bool, runIdsOnScreen: Set<String>?, alertRunId: String?) -> AlertPresentation {
        guard appIsActive, let onScreen: Set<String> = runIdsOnScreen, let alertRunId, onScreen.contains(alertRunId) else {
            return .banner
        }
        return .quiet
    }
}
