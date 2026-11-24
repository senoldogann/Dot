import SwiftUI

/// The chat list: the agents strip, then the chats, working and waiting ones marked. It draws from what the disk held
/// at launch; pulling it down reads the daemon again.
struct ChatListView: View {
    let model: AppModel
    let store: NativeStore
    @State private var path: [ThreadSummary] = []
    /// Why a tapped notification opened nothing.
    @State private var openFailed: String?
    @State private var composing: NewChatTarget?

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if !store.agents.isEmpty {
                    AgentStrip(store: store, onOpen: { open(agent: $0) })
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                if let words: String = openFailed {
                    Button { openFailed = nil } label: { Text(words).font(Theme.meta).foregroundStyle(Theme.warn) }
                        .listRowBackground(Color.clear).listRowSeparator(.hidden)
                }
                ForEach(store.threads) { thread in
                    Button { path.append(thread) } label: { ThreadRow(thread: thread) }
                        .buttonStyle(.plain)
                        .listRowBackground(Color.clear)
                        .listRowSeparatorTint(Theme.hairline)
                        .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
                }
                if store.threads.isEmpty {
                    Text(store.refreshing ? "" : "No chats yet. Message Dot from the Mac or here.")
                        .font(Theme.small).foregroundStyle(Theme.faint)
                        .listRowBackground(Color.clear).listRowSeparator(.hidden)
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .refreshable { await store.refreshList() }
            .navigationTitle("Dot")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.background, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { composing = .home } label: { Image(systemName: "square.and.pencil") }
                        .accessibilityLabel("New chat")
                        .accessibilityIdentifier("new-chat")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { model.showsSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
            }
            .sheet(item: $composing) { target in
                NewChatView(store: store, agent: target.agent, onStarted: { path = [$0] })
                    .tint(Theme.accent)
                    .preferredColorScheme(.dark)
                    .presentationBackground(Theme.background)
            }
            .navigationDestination(for: ThreadSummary.self) { thread in
                ChatView(store: store, thread: thread, inspectorOpen: model.debugInspector)
            }
            .task(id: store.threads.map(\.id)) {
                // Debug builds: the list is read again after the first draw, so the chat is opened again if its id changed.
                if let prefix: String = model.debugOpenChat,
                    let thread: ThreadSummary = store.threads.first(where: { $0.title.hasPrefix(prefix) }),
                    path.first?.id != thread.id
                {
                    path = [thread]
                }
            }
            .task(id: model.pendingRunId) {
                guard let runId: String = model.pendingRunId else { return }
                // Cleared when the chat is open, not before: this task is keyed on the id, and clearing it first would
                // cancel the very request that reads the run.
                await open(runId: runId)
                if model.pendingRunId == runId { model.pendingRunId = nil }
            }
        }
    }

    /// Opens the chat of a run a notification, a Live Activity or a link named. The chat the list does not hold yet (the
    /// app was just launched from the tap, or the chat is new) is read from the daemon, and failing that drawn from the
    /// run's own page, so a tap never lands on a refusal while the daemon answers.
    private func open(runId: String) async {
        do {
            let detail: RunDetail = try await store.api.detail(run: runId)
            var thread: ThreadSummary? = store.threads.first(where: { $0.id == detail.run.threadId })
            if thread == nil {
                await store.refreshList()
                thread = store.threads.first(where: { $0.id == detail.run.threadId })
            }
            composing = nil
            openFailed = nil
            path = [thread ?? ThreadSummary.opened(from: detail)]
        } catch {
            // The task was replaced by a later tap (or the screen went): that is not a failure to open.
            if Task.isCancelled { return }
            openFailed = "Could not open that run."
        }
    }

    private func open(agent: AgentSummary) {
        if let home: ThreadSummary = agent.home {
            path.append(home)
        } else {
            composing = .agent(agent)
        }
    }

}

/// What the new-chat sheet is for: a chat of its own from the home, or an agent's first message.
enum NewChatTarget: Identifiable {
    case home
    case agent(AgentSummary)

    var id: String {
        switch self {
        case .home: "home"
        case .agent(let agent): "agent:\(agent.id)"
        }
    }

    var agent: AgentSummary? {
        if case .agent(let agent) = self { return agent }
        return nil
    }
}

/// A chat row: its title, its last words, how long ago, and how it stands (working, waiting on you, unread).
struct ThreadRow: View {
    let thread: ThreadSummary

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Avatar(id: thread.actorId ?? "dot", name: thread.title, size: 34, state: thread.state)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(thread.title.isEmpty ? "New chat" : thread.title)
                        .font(.system(size: 15, weight: thread.unread ? .semibold : .regular))
                        .foregroundStyle(Theme.text).lineLimit(1)
                    Spacer(minLength: 4)
                    Text(shortAge(thread.updatedAt)).font(Theme.meta).foregroundStyle(Theme.faint)
                }
                HStack(spacing: 6) {
                    if thread.isWaitingOnYou {
                        Text("Waiting on you").font(Theme.small).foregroundStyle(Theme.warn)
                    } else if thread.isWorking {
                        Text("Working").font(Theme.small).foregroundStyle(Theme.accent)
                    } else {
                        Text(thread.lastMessage).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(1)
                    }
                    Spacer(minLength: 4)
                    if thread.loopCron != nil {
                        Image(systemName: "repeat").font(.system(size: 10)).foregroundStyle(Theme.faint)
                    }
                    if thread.unread { Circle().fill(Theme.accent).frame(width: 7, height: 7) }
                }
            }
        }
        .padding(.vertical, 11)
        .contentShape(Rectangle())
    }
}

/// The agents: an avatar each, Dot first, with a dot for the ones that work or wait on the owner. A tap opens the
/// agent's own chat.
struct AgentStrip: View {
    let store: NativeStore
    let onOpen: (AgentSummary) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 16) {
                ForEach(store.agents) { agent in
                    Button {
                        onOpen(agent)
                    } label: {
                        VStack(spacing: 5) {
                            Avatar(id: agent.id, name: agent.record.name, size: 44, state: state(of: agent))
                            Text(agent.record.name).font(Theme.tiny).foregroundStyle(Theme.muted).lineLimit(1).frame(width: 56)
                        }
                    }
                    .buttonStyle(.plain)
                    .opacity(agent.record.pausedAt == nil ? 1 : 0.5)
                    .accessibilityLabel("\(agent.record.name)\(state(of: agent) == .idle ? "" : ", \(state(of: agent) == .waiting ? "waiting on you" : "working")")")
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
        }
    }

    private func state(of agent: AgentSummary) -> ThreadState {
        guard let status: String = agent.latest?.status else { return .idle }
        return status == "blocked" ? .waiting : status.isActiveRunStatus ? .working : .idle
    }
}
