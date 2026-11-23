import SwiftUI

/// The chat list: the agents strip, then the chats, working and waiting ones marked. It draws from what the disk held
/// at launch; pulling it down reads the daemon again.
struct ChatListView: View {
    let model: AppModel
    let store: NativeStore
    @State private var path: [ThreadSummary] = []

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if !store.agents.isEmpty {
                    AgentStrip(store: store, onOpen: { path.append($0) })
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                if let line: String = connectionLine {
                    Text(line).font(Theme.meta).foregroundStyle(Theme.warn)
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
                ToolbarItem(placement: .topBarTrailing) {
                    Button { model.showsSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
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
                model.pendingRunId = nil
                if let detail: RunDetail = try? await store.api.detail(run: runId),
                    let thread: ThreadSummary = store.threads.first(where: { $0.id == detail.run.threadId })
                {
                    path = [thread]
                }
            }
        }
    }

    private var connectionLine: String? {
        switch store.connection {
        case .reconnecting: "Reconnecting to your Mac…"
        case .unauthorized: "This phone's key was refused. Pair again in Settings."
        default: nil
        }
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
    let onOpen: (ThreadSummary) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 16) {
                ForEach(store.agents) { agent in
                    Button {
                        if let home: ThreadSummary = agent.home { onOpen(home) }
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
