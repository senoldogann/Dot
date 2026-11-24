import Foundation

/// How a chat or an agent stands: at rest, at work, or waiting for the owner.
enum ThreadState: Sendable {
    case idle, working, waiting
}

extension ThreadSummary {
    var state: ThreadState { isWaitingOnYou ? .waiting : isWorking ? .working : .idle }
}
