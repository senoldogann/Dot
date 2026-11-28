import Foundation

// A chat's tasks as DotNext shows them (item 2013, slice 3 of item 1982): the count in the capsule, each task's state
// word, and the toast that says where a message sent to a chat with several tasks went. Pure.

/// What a task's row says of it: at work, waiting on the owner, or how its newest run ended.
public enum TaskPhase: String, Sendable, Equatable {
    case working
    case waits
    case done
    case stopped
    case failed
}

/// The phase of a task: its `state`, and for an ended one its run's `status` (`cancelled` is a stop, `failed` a failure,
/// any other end is done).
public func taskPhase(_ task: ChatTaskView) -> TaskPhase {
    switch task.state {
    case "working": return .working
    case "waits": return .waits
    default:
        switch task.status {
        case "cancelled": return .stopped
        case "failed": return .failed
        default: return .done
        }
    }
}

/// How many tasks of the chat work or wait on the owner: the capsule's "N tasks"; 0 while the chat has no lane.
public func openTaskCount(_ tasks: TasksView?) -> Int {
    (tasks?.tasks ?? []).filter { taskPhase($0) == .working || taskPhase($0) == .waits }.count
}

/// Whether the chat has tasks of its own besides itself (a lane): the composer's Stop then stops every task.
public func hasTasks(_ tasks: TasksView?) -> Bool {
    !(tasks?.tasks ?? []).isEmpty
}

/// What the screen says after a message went somewhere the owner did not pick (item 2012's routing).
public enum RouteNotice: Sendable, Equatable {
    /// It started a new task, `title`, in `thread`, whose run is `run`: what **Move** moves.
    case startedNew(title: String, thread: String, run: String?)
    /// It went to another task than the one on screen: queued there, steered into it, or its next run. `message` is the
    /// queued message it became, `run` the run its words started: what **Move** moves. A steer moves nothing: its run
    /// was at work on its own task already, so both are `nil`.
    case sentTo(title: String, thread: String, message: String?, run: String?)
    /// It stopped every task of the chat.
    case stoppedAll
    /// It stopped one task, `title`.
    case stopped(title: String)
}

/// The notice for `routed`, sent from the screen of `shown`, with the chat's tasks as they stand after it; `nil` when
/// there is nothing to say (it went to the task on screen) or the task it names is not among `tasks`.
public func routeNotice(_ routed: RoutedMessage, shown: String, tasks: TasksView) -> RouteNotice? {
    let title: String? = tasks.tasks.first { $0.threadId == routed.threadId }?.title
    switch routed.kind {
    case "stopped":
        if routed.threadId == tasks.chatId { return .stoppedAll }
        return title.map { .stopped(title: $0) }
    case "new":
        return title.map { .startedNew(title: $0, thread: routed.threadId, run: routed.runId) }
    case "queued", "continued":
        guard routed.threadId != shown else { return nil }
        return title.map { .sentTo(title: $0, thread: routed.threadId, message: routed.messageId, run: routed.runId) }
    case "steered":
        guard routed.threadId != shown else { return nil }
        return title.map { .sentTo(title: $0, thread: routed.threadId, message: nil, run: nil) }
    default:
        return nil
    }
}

/// What **Move** moves for `notice`: the task it went to, the queued message or the run its words started, and whether
/// a new task is a place it can go (not for one that is a new task already); `nil` when nothing can be moved (a steer
/// into a run already at work, a stop).
public struct MoveSource: Sendable, Equatable {
    public let thread: String
    public let message: String?
    public let run: String?
    public let toNewTask: Bool
}

public func moveSource(_ notice: RouteNotice) -> MoveSource? {
    switch notice {
    case .startedNew(_, let thread, let run):
        return run.map { MoveSource(thread: thread, message: nil, run: $0, toNewTask: false) }
    case .sentTo(_, let thread, let message, let run):
        guard message != nil || run != nil else { return nil }
        return MoveSource(thread: thread, message: message, run: run, toNewTask: true)
    case .stoppedAll, .stopped:
        return nil
    }
}

/// Where **Move** can send a message that went to `from`: every other task of the chat, in the list's order.
public func moveTargets(from: String, tasks: TasksView) -> [ChatTaskView] {
    tasks.tasks.filter { $0.threadId != from }
}
