import DotEngine
import SwiftUI

// A chat's tasks on the phone (item 2013, slice 3 of item 1982): the sheet the capsule's "N tasks" opens, the line a
// task's own screen shows under its title, and the toast that says where a message sent to a chat with several tasks
// went, with Open and Move. The web shows the same words.

extension EnvironmentValues {
    /// The chat whose screen pushed this one (a task opened from its chat's sheet): "Back to the chat" goes back to it
    /// instead of pushing it again. `nil` for a screen the list opened.
    @Entry var pushedFrom: String? = nil
}

/// A message sent from `thread`'s screen that went somewhere the owner did not pick, with the chat's tasks as they stood
/// after it (the titles, and where **Move** can send it).
struct RoutedSend: Identifiable, Equatable, Sendable {
    let id: UUID
    let thread: String
    let notice: RouteNotice
    let tasks: TasksView
}

// MARK: Words

/// A task's state as its row says it.
func taskPhaseWords(_ phase: TaskPhase) -> String {
    switch phase {
    case .working: String(localized: "Working", comment: "Status word: a run is at work")
    case .waits: String(localized: "Waits for you", comment: "Tasks sheet: the task's run ended asking the owner something")
    case .done: String(localized: "Done", comment: "Status word: the run finished")
    case .stopped: String(localized: "task.stopped", defaultValue: "Stopped", comment: "Tasks sheet: the task's run was stopped")
    case .failed: String(localized: "Failed", comment: "Status word: the run failed")
    }
}

/// A task's name in the sheet and the Move picker: "This chat" for the chat's own.
func taskTitleWords(_ task: ChatTaskView) -> String {
    task.own ? String(localized: "This chat", comment: "Title of the run inspector") : task.title
}

/// What the toast says of where a message went.
func routeNoticeWords(_ notice: RouteNotice) -> String {
    switch notice {
    case .startedNew(let title, _, _):
        String(localized: "Started a new task: \(title)", comment: "Toast: the message started a task of its own; the argument is its title")
    case .sentTo(let title, _, _, _):
        String(localized: "Sent to \(title)", comment: "Toast: the message went to another task of the chat; the argument is its title")
    case .stoppedAll:
        String(localized: "Stopped every task", comment: "Toast: the message was a stop and stopped every task of the chat")
    case .stopped(let title):
        String(localized: "Stopped \(title)", comment: "Toast: the message was a stop of one task; the argument is its title")
    }
}

// MARK: The tasks sheet

/// Every task of the chat (the capsule's "N tasks"): its name, its state, Stop on one at work, a tap opens its thread.
struct TasksSheet: View {
    let thread: String
    let open: (String) -> Void
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    /// The tasks read when the sheet opened: a task's state moves on in its own thread, which this chat's view may not
    /// have been read again for.
    @State private var fresh: TasksView?

    private var tasks: [ChatTaskView] { (fresh ?? model.chats[thread]?.view?.tasks)?.tasks ?? [] }

    var body: some View {
        List {
            ForEach(tasks) { task in
                row(task)
                    .listRowBackground(Color.clear)
                    .listRowSeparatorTint(Tone.separator)
                    .alignmentGuide(.listRowSeparatorLeading) { _ in 0 }
                    .listRowInsets(EdgeInsets(top: Space.xs, leading: Space.l, bottom: Space.xs, trailing: Space.l))
            }
        }
        .listStyle(.plain)
        .task { fresh = await model.tasks(of: thread) }
        .inspectorSheet(title: String(localized: "Tasks", comment: "Title of the sheet of a chat's tasks"), close: close)
    }

    private func row(_ task: ChatTaskView) -> some View {
        let phase: TaskPhase = taskPhase(task)
        return HStack(spacing: Space.s) {
            Button { open(task.threadId) } label: {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(taskTitleWords(task)).textRole(.body).foregroundStyle(Tone.text).lineLimit(2)
                    Text(taskPhaseWords(phase)).textRole(.label)
                        .foregroundStyle(phase == .waits ? ChatStatus.waitingOnYou.tone.color : phase == .failed ? Tone.failure : Tone.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: Metric.hit, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if phase == .working {
                Button(String(localized: "Stop", comment: "Chat toolbar: stops the run at work")) {
                    model.perform(.stop(run: task.runId, thread: task.threadId))
                }
                .buttonStyle(SecondaryButtonStyle())
            }
        }
        .accessibilityIdentifier("tasks.row")
    }
}

// MARK: A task's own screen

/// The line under a task's title: whose task it is, and the way back to its chat.
struct LaneLine: View {
    let chatTitle: String
    let back: () -> Void

    var body: some View {
        HStack(spacing: Space.s) {
            Text(String(localized: "A task of \(chatTitle)", comment: "Under a task's title: the chat it is a task of; the argument is the chat's title"))
                .textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(1)
            Spacer(minLength: 0)
            Button(String(localized: "Back to the chat", comment: "Under a task's title: opens the chat it is a task of"), action: back)
                .textRole(.footnote).fontWeight(.semibold).frame(minHeight: Metric.hit)
        }
        .padding(.horizontal, Space.gutter)
        .background(Tone.ground)
        .accessibilityIdentifier("lane.line")
    }
}

// MARK: The toast of a routed message

/// Where a message went, as a toast with **Open** (the task) and, for one sent to another task, **Move**.
struct RouteToastView: View {
    let notice: RouteNotice
    let open: (() -> Void)?
    let move: (() -> Void)?
    let dismiss: () -> Void

    var body: some View {
        HStack(spacing: Space.s) {
            HStack(spacing: Space.xs) {
                Symbol(name: "square.stack", context: .feedback).foregroundStyle(Tone.text)
                Text(routeNoticeWords(notice)).textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.text).lineLimit(2)
            }
            if let open { action(String(localized: "task.open", defaultValue: "Open", comment: "Toast of a routed message: opens the task it went to"), open) }
            if let move { action(String(localized: "Move", comment: "Toast of a routed message: sends it to another task"), move) }
        }
        .padding(.leading, Space.m)
        .padding(.trailing, open == nil && move == nil ? Space.m : Space.xxs)
        .padding(.vertical, Space.xxs)
        .frame(minHeight: Metric.hit + Space.xs)
        .floatingGlass(in: AnyShape(rounded(Radius.bubble)))
        .accessibilityElement(children: .contain)
        .accessibilityAction(.escape, dismiss)
        .accessibilityIdentifier("route.toast")
    }

    private func action(_ title: String, _ run: @escaping () -> Void) -> some View {
        Button(title, action: run)
            .textRole(.footnote).fontWeight(.semibold)
            .foregroundStyle(Tone.onAction)
            .padding(.horizontal, Space.m)
            .frame(minHeight: 32)
            .background(Tone.action, in: Capsule())
            .frame(minHeight: Metric.hit)
            .contentShape(Rectangle())
            .buttonStyle(.plain)
    }
}

// MARK: The model's side

extension AppModel {
    /// The tasks of the chat of `thread`, read now; `nil` when they could not be read (logged).
    func tasks(of thread: String) async -> TasksView? {
        guard let engine else { return nil }
        do throws(DaemonRequestError) {
            return try await engine.api.tasks(of: thread)
        } catch {
            logger.warning("the tasks could not be read thread=\(thread, privacy: .public) error=\(error.description, privacy: .public)")
            return nil
        }
    }

    /// A message sent from `thread` went where `routed` says: the chat's tasks are read for its title, and the notice is
    /// kept for that chat's screen. Nothing is kept when it went to `thread` itself or its task is unknown.
    func heard(_ routed: RoutedMessage, from thread: String) async {
        guard let tasks: TasksView = await tasks(of: thread) else { return }
        guard let notice: RouteNotice = routeNotice(routed, shown: thread, tasks: tasks) else {
            if routed.threadId != thread {
                logger.warning("a routed message names no task of its chat thread=\(thread, privacy: .public) task=\(routed.threadId, privacy: .public)")
            }
            return
        }
        routedSends.append(RoutedSend(id: UUID(), thread: thread, notice: notice, tasks: tasks))
    }

    /// The chat's screen said the notice.
    func routedSaid(_ id: UUID) {
        routedSends.removeAll { $0.id == id }
    }

    /// **Move**: the queued message (or else the run) to the task of `to`, or to a new task (`nil`); where it went next
    /// is said as a routed message is. `nil` when it moved, else the daemon's words.
    func move(message: String?, run: String?, to: String?, from thread: String) async -> String? {
        guard let engine else { return String(localized: "Not paired with a Mac", comment: "A call that needs the Mac when this phone is not paired") }
        do throws(DaemonRequestError) {
            let routed: RoutedMessage
            if let message {
                routed = try await engine.api.moveQueued(message, to: to)
            } else if let run {
                routed = try await engine.api.moveRun(run, to: to)
            } else {
                preconditionFailure("a routed message to move names its queued message or its run")
            }
            await heard(routed, from: thread)
            return nil
        } catch {
            logger.error("a move failed thread=\(thread, privacy: .public) to=\(to ?? "new", privacy: .public) error=\(error.description, privacy: .public)")
            return error.detail
        }
    }
}

/// Shows the toast of a message this chat's screen sent that went to another task (item 2013), and the "Move to" picker
/// of its Move.
struct RouteToastHost: ViewModifier {
    let thread: String
    @Binding var routeToast: RoutedSend?
    @Binding var moving: RoutedSend?
    let open: (String) -> Void
    let tell: (ToastMessage) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .bottom) {
                if let shown = routeToast {
                    RouteToastView(
                        notice: shown.notice, open: opened(shown.notice).map { task in { routeToast = nil; open(task) } },
                        move: movable(shown.notice) ? { routeToast = nil; moving = shown } : nil, dismiss: { routeToast = nil }
                    )
                    .padding(.horizontal, Space.gutter)
                    .padding(.bottom, Space.xs)
                    .transition(reduceMotion ? .opacity : .move(edge: .bottom).combined(with: .opacity))
                    .task(id: shown.id) {
                        AccessibilityNotification.Announcement(routeNoticeWords(shown.notice)).post()
                        do { try await Task.sleep(for: .seconds(6)) } catch { return }
                        if routeToast?.id == shown.id { routeToast = nil }
                    }
                }
            }
            .onChange(of: model.routedSends, initial: true) { _, sends in
                guard let next = sends.first(where: { $0.thread == thread }) else { return }
                routeToast = next
                model.routedSaid(next.id)
            }
            .confirmationDialog(
                String(localized: "Move to", comment: "Title of the picker of where Move sends a routed message"),
                isPresented: Binding(get: { moving != nil }, set: { if !$0 { moving = nil } }), titleVisibility: .visible, presenting: moving
            ) { send in
                if let source = moveSource(send.notice) {
                    ForEach(moveTargets(from: source.thread, tasks: send.tasks)) { task in
                        Button(taskTitleWords(task)) { move(message: source.message, run: source.run, to: task.threadId) }
                    }
                    if source.toNewTask {
                        Button(String(localized: "New task", comment: "Move to: a task of its own")) {
                            move(message: source.message, run: source.run, to: nil)
                        }
                    }
                }
            }
    }

    private func move(message: String?, run: String?, to: String?) {
        Task {
            if let problem = await model.move(message: message, run: run, to: to, from: thread) {
                tell(ToastMessage(id: UUID(), text: String(localized: "Not moved: \(problem)", comment: "Toast: Move failed; the argument is the Mac's words"), systemImage: "exclamationmark.triangle", actionTitle: nil))
            }
        }
    }

    /// The task Open opens, `nil` for a notice that names none to open.
    private func opened(_ notice: RouteNotice) -> String? {
        switch notice {
        case .startedNew(_, let thread, _), .sentTo(_, let thread, _, _): thread
        case .stoppedAll, .stopped: nil
        }
    }

    private func movable(_ notice: RouteNotice) -> Bool {
        moveSource(notice) != nil
    }
}
