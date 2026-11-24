import Foundation

/// How a chat or an agent stands: at rest, at work, or waiting for the owner.
enum ThreadState: Sendable {
    case idle, working, waiting
}

extension ThreadSummary {
    var state: ThreadState { isWaitingOnYou ? .waiting : isWorking ? .working : .idle }

    /// How the row stands when `helpers` of the chat's helpers are live (item 1466): a chat whose own run is over but whose
    /// helpers work is at work, not at rest; one that waits on the owner still waits.
    func state(helpersLive helpers: Int) -> ThreadState {
        let own: ThreadState = state
        return own == .idle && helpers > 0 ? .working : own
    }
}
