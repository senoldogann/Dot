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
    /// The inbox of what waits on the owner (item 1442).
    @State private var showsInbox: Bool = false

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if !store.agents.isEmpty {
                    AgentStrip(store: store, onOpen: { open(agent: $0) })
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                if store.awareness.needsYou.count > 0 {
                    NeedsYouEntry(count: store.awareness.needsYou.count, preview: inboxPreview, onOpen: { showsInbox = true })
                        .listRowInsets(EdgeInsets(top: Space.s, leading: Space.l, bottom: Space.s, trailing: Space.l))
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                if let words: String = openFailed {
                    Button { openFailed = nil } label: {
                        Label(words, systemImage: "exclamationmark.circle").font(Theme.small).foregroundStyle(Theme.warn)
                    }
                    .listRowBackground(Color.clear).listRowSeparator(.hidden)
                }
                if !waiting.isEmpty { section("Needs you", tone: Theme.warn, threads: waiting) }
                if !working.isEmpty { section("Working", tone: Theme.accent, threads: working) }
                if !others.isEmpty { section(waiting.isEmpty && working.isEmpty ? nil : "Chats", tone: Theme.faint, threads: others) }
                if store.threads.isEmpty && !store.refreshing {
                    DotEmptyState(
                        symbol: "bubble.left.and.text.bubble.right", title: "No chats yet",
                        message: "Message Dot from the Mac, or start a chat here.", actionTitle: "New chat",
                        action: { composing = .home }
                    )
                    .padding(.top, Space.xl)
                    .listRowBackground(Color.clear).listRowSeparator(.hidden)
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background { DotBackground() }
            .dotScrollEdges()
            .refreshable { await store.refreshEverything() }
            .navigationTitle("Dot")
            .navigationBarTitleDisplayMode(.large)
            .dotAnimation(Motion.smooth, value: store.threads.map { "\($0.id)\($0.state)" })
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { composing = .home } label: { Image(systemName: "square.and.pencil") }
                        .accessibilityLabel("New chat")
                        .accessibilityIdentifier("new-chat")
                        .sensoryFeedback(.impact(weight: .light), trigger: composing != nil)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { model.showsSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
            }
            .sheet(item: $composing) { target in
                NewChatView(store: store, agent: target.agent, onStarted: { path = [$0] })
                    .dotSheet()
            }
            .sheet(isPresented: $showsInbox) {
                NeedsYouView(store: store, onOpen: { runId in model.pendingRunId = runId }).dotSheet()
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
            .task {
                // Debug builds: `-DotShowInbox 1` opens the inbox once the list is on the screen.
                if model.debugInbox {
                    try? await Task.sleep(for: .milliseconds(900))
                    showsInbox = true
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

    /// What the newest thing that waits on the owner asks, for the entry's second line.
    private var inboxPreview: String? {
        NeedsYouRules.rows(from: store.awareness.needsYou).first.map(NeedsYouRules.preview(of:))
    }

    private var waiting: [ThreadSummary] { store.threads.filter(\.isWaitingOnYou) }
    private var working: [ThreadSummary] { store.threads.filter { $0.isWorking && !$0.isWaitingOnYou } }
    private var others: [ThreadSummary] { store.threads.filter { !$0.isWorking && !$0.isWaitingOnYou } }

    /// One group of chats: a small header with its count, then the rows, each with the mark-as-read swipe.
    @ViewBuilder private func section(_ title: String?, tone: Color, threads: [ThreadSummary]) -> some View {
        Section {
            ForEach(threads) { thread in
                Button { path.append(thread) } label: { ThreadRow(thread: thread) }
                    .buttonStyle(RowPressStyle())
                    .listRowBackground(thread.isWaitingOnYou ? Theme.warn.opacity(0.08) : Color.clear)
                    .listRowSeparatorTint(Theme.hairline)
                    .alignmentGuide(.listRowSeparatorLeading) { _ in 70 }
                    .listRowInsets(EdgeInsets(top: 0, leading: Space.l, bottom: 0, trailing: Space.l))
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        if thread.unread {
                            Button { store.markSeen(thread.id) } label: { Label("Read", systemImage: "envelope.open") }
                                .tint(Theme.accentFill)
                        }
                    }
            }
        } header: {
            if let title { SectionLabel(title: title, count: threads.count, tone: tone).padding(.leading, Space.xs) }
        }
    }
}

/// A row's press: a light dim, so a tap in a list of plain rows answers.
private struct RowPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.55 : 1)
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

/// A chat row: who it is with, its title, its last words, how long ago, and how it stands: waiting on you (a pill that
/// asks), working (a ring that turns and a moving mark), unread (a dot).
struct ThreadRow: View {
    let thread: ThreadSummary
    @Environment(\.dynamicTypeSize) private var typeSize: DynamicTypeSize

    var body: some View {
        Group { if typeSize.isAccessibilitySize { stacked } else { regular } }
            .accessibilityElement(children: .combine)
            .accessibilityValue(thread.isWaitingOnYou ? "Needs you" : (thread.isWorking ? "Working" : ""))
    }

    /// At the accessibility sizes a row has no room for two columns: the mark and the age go over the words.
    private var stacked: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(spacing: Space.m) {
                Avatar(id: thread.actorId ?? "dot", name: thread.title, size: 36, state: thread.state)
                Text(shortAge(thread.updatedAt)).font(Theme.meta).foregroundStyle(Theme.faint)
                Spacer(minLength: 0)
                if thread.unread { Circle().fill(Theme.accent).frame(width: 10, height: 10).accessibilityLabel("Unread") }
            }
            Text(thread.title.isEmpty ? "New chat" : thread.title)
                .font(.body.weight(thread.unread ? .semibold : .regular)).foregroundStyle(Theme.text).lineLimit(3)
            Text(thread.lastMessage).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(3)
        }
        .padding(.vertical, Space.m)
        .contentShape(Rectangle())
    }

    private var regular: some View {
        HStack(alignment: .top, spacing: Space.m + 2) {
            Avatar(id: thread.actorId ?? "dot", name: thread.title, size: 40, state: thread.state)
                .padding(.top, Space.xs)
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                    Text(thread.title.isEmpty ? "New chat" : thread.title)
                        .font(.body.weight(thread.unread ? .semibold : .regular))
                        .foregroundStyle(Theme.text).lineLimit(1)
                    Spacer(minLength: Space.xs)
                    Text(shortAge(thread.updatedAt)).font(Theme.meta).foregroundStyle(Theme.faint)
                }
                HStack(spacing: Space.s) {
                    Text(thread.lastMessage).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(thread.state == .idle ? 2 : 1)
                    Spacer(minLength: Space.xs)
                    if thread.loopCron != nil {
                        Image(systemName: "repeat").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.faint)
                            .accessibilityLabel("Repeats")
                    }
                    if thread.unread { Circle().fill(Theme.accent).frame(width: 8, height: 8).accessibilityLabel("Unread") }
                }
            }
        }
        .padding(.vertical, Space.m)
        .contentShape(Rectangle())
    }
}

/// The agents, each a chip of glass: its avatar with a ring that shows whether it works or waits on the owner, and its
/// name. Dot first. A tap opens the agent's own chat.
struct AgentStrip: View {
    let store: NativeStore
    let onOpen: (AgentSummary) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            DotGlassGroup(spacing: Space.s) {
                HStack(spacing: Space.s) {
                    ForEach(store.agents) { agent in
                        let current: ThreadState = state(of: agent)
                        Button {
                            onOpen(agent)
                        } label: {
                            HStack(spacing: Space.s) {
                                Avatar(id: agent.id, name: agent.record.name, size: 28, state: current)
                                VStack(alignment: .leading, spacing: 0) {
                                    Text(agent.record.name).font(.subheadline.weight(.medium)).foregroundStyle(Theme.text).lineLimit(1)
                                    if current != .idle || agent.record.pausedAt != nil {
                                        Text(current == .waiting ? "Needs you" : current == .working ? "Working" : "Paused")
                                            .font(Theme.tiny.weight(.semibold))
                                            .foregroundStyle(current == .waiting ? Theme.warn : current == .working ? Theme.accent : Theme.faint)
                                    }
                                }
                            }
                            .padding(.leading, Space.s).padding(.trailing, Space.m).frame(minHeight: 44)
                            .dotGlass(in: Capsule(), tint: current == .waiting ? Theme.warn.opacity(0.18) : nil, interactive: true)
                        }
                        .buttonStyle(.plain)
                        .opacity(agent.record.pausedAt == nil ? 1 : 0.6)
                        .accessibilityLabel("\(agent.record.name)\(current == .idle ? "" : ", \(current == .waiting ? "waiting on you" : "working")")")
                    }
                }
            }
            .padding(.horizontal, Space.l).padding(.vertical, Space.s)
        }
        .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
    }

    private func state(of agent: AgentSummary) -> ThreadState {
        guard let status: String = agent.latest?.status else { return .idle }
        return status == "blocked" ? .waiting : status.isActiveRunStatus ? .working : .idle
    }
}
