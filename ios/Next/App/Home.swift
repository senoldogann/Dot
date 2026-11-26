import DotEngine
import SwiftUI

// The home (PLAN §27, item 1881): bots, not tabs. Dot large and centred, then one row per agent; the owner's avatar icon
// opens the one settings sheet, "+" makes a bot; at the bottom the apps button and the search of bots, chats and words.

/// The app's shell: the home, a bot's chat pushed from it (the iPad: the home as the sidebar, the chat as the detail),
/// and the sheets the home opens.
struct HomeShell: View {
    let opensFirstChat: Bool
    /// Settings alone at launch (the screenshot harness and the Settings UI tests, debug scenes), at this start; the
    /// sheet over the home is the e2e test's (`BotsLiveUITests`).
    let settings: SettingsStart?
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?
    @State private var selection: BotRoute?
    @State private var showsSettings: Bool = false
    @State private var showsNewBot: Bool = false
    @State private var showsApps: Bool = false

    var body: some View {
        if let settings {
            SettingsSheet(start: settings, close: {})
        } else {
            home
        }
    }

    private var home: some View {
        AdaptiveSplit(selection: $selection) {
            HomeList(
                selection: $selection, openSettings: { showsSettings = true }, newBot: { showsNewBot = true }, openApps: { showsApps = true })
        } detail: { route in
            // One screen per route, as the phone's push makes it: a bot's held and chosen chats never carry to another
            // bot on the iPad's detail column (item 1923's review).
            BotChatScreen(route: route, drawsBack: sizeClass != .regular, back: { selection = nil })
                .id(route)
        } placeholder: {
            EmptyState(
                title: String(localized: "No chat open", comment: "iPad detail column when no chat is selected"), systemImage: "bubble.left.and.bubble.right",
                detail: String(localized: "Pick a bot on the left, or make a new one.", comment: "iPad detail column when no bot is selected"), actionTitle: nil,
                action: {}
            )
            .background(Tone.ground)
        }
        .sheet(isPresented: $showsSettings) {
            SettingsSheet(start: SettingsStart(path: [], query: "", editsFirstSchedule: false, triesRaise: false, connectApps: .closed), close: { showsSettings = false })
        }
        .sheet(isPresented: $showsNewBot) {
            NewBotSheet(
                made: { record in
                    showsNewBot = false
                    selection = BotRoute(bot: record.id, thread: nil)
                }, close: { showsNewBot = false })
        }
        .sheet(isPresented: $showsApps) { ConnectAppsSheet(opensDetail: nil) }
        .task { await model.loadAgents() }
        // Back on the home: the agents' home chats (outside the list's window) are read again, so a chat read stops
        // counting as unread.
        .onChange(of: selection == nil) { _, home in if home { Task { await model.loadAgents() } } }
        .onChange(of: model.list.rows.count) { _, count in
            if opensFirstChat, selection == nil, count > 0 { selection = BotRoute(bot: dotAgentId, thread: model.list.ordered.first?.summary.id) }
        }
    }
}

/// The bot list.
struct HomeList: View {
    @Binding var selection: BotRoute?
    let openSettings: () -> Void
    let newBot: () -> Void
    let openApps: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var query: String = ""
    @State private var searching: Bool = false
    @State private var hits: Loadable<[ChatHit]> = .loaded([])
    @State private var deleting: Bot?
    @State private var problem: SettingsFailure?
    @State private var nextUnread: Int = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @FocusState private var searchFocused: Bool

    private var bots: [Bot] { model.bots }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    if let problem = problem ?? model.agentsProblem {
                        FailureBanner(failure: problem, macPlace: agentsOnTheMac, retry: {
                            self.problem = nil
                            Task { await model.loadAgents() }
                        })
                            .padding(.horizontal, Space.gutter)
                    }
                    if searching, !query.trimmingCharacters(in: .whitespaces).isEmpty {
                        HomeResults(finds: homeFinds(query, bots: bots, hits: hits.value ?? []), hits: hits, bots: bots, open: open)
                    } else {
                        list
                    }
                }
                .readableColumn()
            }
            .scrollDismissesKeyboard(.interactively)
            .refreshable { await model.loadAgents() }
            .background(Tone.ground)
            .overlay {
                if bots.count == 1, bots[0].chats.isEmpty, model.list.freshness == .empty, model.transport == .connecting {
                    LoadingState(message: String(localized: "Reaching your Mac…", comment: "Chat list while the first list read is under way"))
                }
            }
            .safeAreaInset(edge: .bottom) {
                VStack(spacing: Space.s) {
                    if !searching { unreadPill(proxy) }
                    bottomBar
                }
            }
        }
        .navigationTitle(String(localized: "Bots", comment: "Title of the home: the list of bots"))
        .toolbar(.visible, for: .navigationBar)
        .toolbarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button(String(localized: "Settings", comment: "Title of the settings tab"), systemImage: "person.crop.circle", action: openSettings)
                    .accessibilityIdentifier("home.settings")
            }
            ToolbarItem(placement: .principal) { Color.clear.frame(width: 1, height: 1).accessibilityHidden(true) }
            ToolbarItem(placement: .topBarTrailing) {
                Button(String(localized: "New bot", comment: "Home: makes a new bot"), systemImage: "plus", action: newBot)
                    .accessibilityIdentifier("home.newBot")
            }
        }
        .task(id: query) {
            let words: String = query.trimmingCharacters(in: .whitespaces)
            guard !words.isEmpty else {
                hits = .loaded([])
                return
            }
            do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
            hits = .loading
            hits = await model.searchChats(words)
        }
        .confirmationDialog(
            String(localized: "Delete \(deleting?.record.name ?? "")?", comment: "Confirmation of deleting a bot; the argument is its name"),
            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible
        ) {
            Button(String(localized: "Delete", comment: "Confirmation button that deletes a chat"), role: .destructive) {
                if let bot = deleting { Task { problem = await model.deleteBot(bot.id) } }
                deleting = nil
            }
        } message: {
            Text(String(localized: "Its chats stay, as Dot's. Its face, role and instructions go, and its schedules are switched off.", comment: "Confirmation of deleting a bot: what happens"))
        }
    }

    // MARK: The list

    @ViewBuilder private var list: some View {
        if let dot = bots.first {
            featured(dot).id(dot.id)
        }
        if let line = freshnessLine(
            freshness: model.list.freshness, transport: model.transport, savedAt: model.savedAt["list"], workingAt: nil, now: .now)
        {
            Text(line).textRole(.footnote).foregroundStyle(Tone.secondary).padding(.bottom, Space.s)
        }
        // A new chat still in the outbox (not yet taken by the Mac) shows at once, with where it stands.
        ForEach(model.pendingChats) { pending in
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(pending.text).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(2)
                Text(deliveryWords(pending.delivery)).textRole(.footnote).foregroundStyle(Tone.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: Metric.rowWithDescription, alignment: .leading)
            .padding(.horizontal, Space.gutter)
            .accessibilityElement(children: .combine)
        }
        LazyVStack(spacing: 0) {
            ForEach(bots.dropFirst()) { bot in
                Button { open(BotRoute(bot: bot.id, thread: nil)) } label: {
                    BotRowView(bot: bot, muted: model.mutedBots.contains(bot.id), pinned: model.pinnedBots.contains(bot.id), now: .now)
                }
                .buttonStyle(RowPressStyle())
                .background(selection?.bot == bot.id ? Tone.nested : Color.clear)
                .contextMenu { menu(bot) }
                .id(bot.id)
            }
        }
        if bots.count == 1, model.list.freshness == .live {
            Text(String(localized: "Make a bot with + to give a job its own chat.", comment: "Home: the hint when the owner has no agent yet"))
                .textRole(.footnote).foregroundStyle(Tone.secondary).multilineTextAlignment(.center)
                .padding(Space.xl)
        }
    }

    /// Dot, large and centred (Grok's featured bot, in Dot's own face).
    private func featured(_ dot: Bot) -> some View {
        Button { open(BotRoute(bot: dot.id, thread: nil)) } label: {
            VStack(spacing: Space.s) {
                BotFace(id: dot.id, look: dot.record.look, size: 84)
                    .overlay { BotStateRing(state: botState(dot), size: 96) }
                    .overlay(alignment: .bottomTrailing) {
                        if (dot.unread > 0 || dot.asks > 0), !model.mutedBots.contains(dot.id) {
                            Circle().fill(Tone.action).frame(width: 16, height: 16).overlay(Circle().stroke(Tone.ground, lineWidth: 3))
                        }
                    }
                Text(dot.record.name).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
            }
            .padding(.top, Space.l)
            .padding(.bottom, Space.xxl)
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .contextMenu { menu(dot) }
        .accessibilityLabel(botLabel(dot))
        .accessibilityIdentifier("home.bot.dot")
    }

    @ViewBuilder private func menu(_ bot: Bot) -> some View {
        if !bot.isDot {
            Button(
                model.pinnedBots.contains(bot.id)
                    ? String(localized: "Unpin", comment: "Swipe and context action on a pinned chat")
                    : String(localized: "Pin", comment: "Swipe and context action: keeps a chat at the top"),
                systemImage: model.pinnedBots.contains(bot.id) ? "pin.slash" : "pin"
            ) { model.togglePin(bot.id) }
        }
        Button(
            model.mutedBots.contains(bot.id)
                ? String(localized: "Unmute", comment: "Home: a bot's unread count counts again")
                : String(localized: "Mute", comment: "Home: a bot's unread chats are not counted"),
            systemImage: model.mutedBots.contains(bot.id) ? "bell" : "bell.slash"
        ) { model.toggleMute(bot.id) }
        if bot.unread > 0 {
            Button(String(localized: "Mark as read", comment: "Home: marks every chat of a bot read"), systemImage: "checkmark.message") { Task { problem = await model.markRead(bot) } }
        }
        if !bot.isDot {
            Divider()
            Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive) {
                // A bot's deletion always asks: the switch of Settings › General is about chats, and an agent goes for good.
                deleting = bot
            }
        }
    }

    // MARK: The bottom

    /// "N unread ⌄": each tap goes to the next bot with something unread.
    @ViewBuilder private func unreadPill(_ proxy: ScrollViewProxy) -> some View {
        let unread: [Bot] = bots.filter { !model.mutedBots.contains($0.id) && ($0.unread > 0 || $0.asks > 0) }
        if !unread.isEmpty {
            Button {
                let target: Bot = unread[nextUnread % unread.count]
                withAnimation(motionAnimation(.adjust, reduceMotion: reduceMotion)) { proxy.scrollTo(target.id, anchor: .center) }
                nextUnread += 1
            } label: {
                HStack(spacing: Space.xs) {
                    Text(String(localized: "\(unread.count) unread", comment: "Home: the pill that jumps to the next unread bot; the argument is how many"))
                        .textRole(.footnote).fontWeight(.medium).tabularNumbers()
                    Image(systemName: "chevron.down").font(.system(.footnote, weight: .semibold))
                }
                .foregroundStyle(Tone.onAction)
                .padding(.horizontal, Space.m)
                .frame(minHeight: 36)
                .background(Tone.action, in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("home.unread")
        }
    }

    /// The apps button and the search capsule.
    private var bottomBar: some View {
        HStack(spacing: Space.s) {
            if !searching {
                Button(action: openApps) {
                    Image(systemName: "square.grid.2x2")
                        .font(.system(.body, weight: .medium))
                        .foregroundStyle(Tone.text)
                        .frame(width: 48, height: 48)
                        .interactiveGlass(in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(String(localized: "Connect apps", comment: "Home: opens the sheet that connects apps"))
                .accessibilityIdentifier("home.apps")
            }
            HStack(spacing: Space.xs) {
                Image(systemName: "magnifyingglass").font(.system(.body, weight: .medium)).foregroundStyle(Tone.secondary)
                TextField(String(localized: "Search", comment: "Home: the search of bots, chats and messages"), text: $query)
                    .textRole(.body)
                    .focused($searchFocused)
                    .submitLabel(.search)
                    .accessibilityIdentifier("home.search")
                if searching {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark.circle.fill") {
                        query = ""
                        searchFocused = false
                        searching = false
                    }
                    .labelStyle(.iconOnly)
                    .foregroundStyle(Tone.tertiary)
                }
            }
            .padding(.horizontal, Space.m)
            .frame(height: 48)
            .interactiveGlass(in: Capsule())
            .onChange(of: searchFocused) { _, focused in if focused { searching = true } }
        }
        .padding(.horizontal, Space.gutter)
        .padding(.bottom, Space.xs)
    }

    private func open(_ route: BotRoute) {
        searchFocused = false
        selection = route
    }
}

/// One bot of the home: its face, its name, where it stands (item 1920: what waits on the owner, its work, what it
/// waits on) or else the last line of its latest chat, the day or a live mark while it works, the unread dot.
struct BotRowView: View {
    let bot: Bot
    let muted: Bool
    let pinned: Bool
    let now: Date
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        HStack(alignment: .center, spacing: Space.m) {
            BotFace(id: bot.id, look: bot.record.look, size: 44)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Text(bot.record.name).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(1)
                    if pinned { Image(systemName: "pin.fill").font(.system(.caption2, weight: .semibold)).foregroundStyle(Tone.tertiary) }
                    if muted { Image(systemName: "bell.slash").font(.system(.caption2, weight: .semibold)).foregroundStyle(Tone.tertiary) }
                    Spacer(minLength: Space.xs)
                    if let latest = bot.latest {
                        RowTrailing(state: state, time: rowTime(latest.summary.updatedAt, now: now))
                    }
                }
                HStack(alignment: .center, spacing: Space.xs) {
                    RowStateLine(state: state, preview: preview, lineLimit: dynamicTypeSize.isAccessibilitySize ? 3 : 1)
                    Spacer(minLength: Space.xs)
                    if (bot.unread > 0 || bot.asks > 0), !muted {
                        Circle().fill(Tone.action).frame(width: 9, height: 9).accessibilityHidden(true)
                    }
                }
            }
        }
        .padding(.horizontal, Space.gutter)
        .frame(minHeight: 72)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(botLabel(bot))
        .accessibilityIdentifier("home.bot.\(bot.id)")
    }

    private var state: RowState { botState(bot) }

    private var preview: String {
        if let latest = bot.latest, !latest.summary.lastMessage.isEmpty { return latest.summary.lastMessage }
        if !bot.record.role.isEmpty { return bot.record.role }
        return String(localized: "No chat yet", comment: "Home: a bot that has no chat")
    }
}

/// What VoiceOver reads for a bot: its name, what waits, its last line.
func botLabel(_ bot: Bot) -> String {
    var parts: [String] = [bot.record.name]
    if let words = rowStateWords(botState(bot)) { parts.append(words) }
    if bot.unread > 0 { parts.append(String(localized: "Unread", comment: "VoiceOver: a bot has unread chats")) }
    if let latest = bot.latest, !latest.summary.lastMessage.isEmpty { parts.append(latest.summary.lastMessage) }
    return parts.joined(separator: ", ")
}

/// The finds of the home's search: bots, chats, and what the chats say.
struct HomeResults: View {
    let finds: HomeFinds
    let hits: Loadable<[ChatHit]>
    let bots: [Bot]
    let open: (BotRoute) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Space.groupedGutter) {
            if !finds.bots.isEmpty {
                section(String(localized: "Bots", comment: "Title of the home: the list of bots")) {
                    ForEach(finds.bots) { bot in
                        Button { open(BotRoute(bot: bot.id, thread: nil)) } label: { BotRowView(bot: bot, muted: false, pinned: false, now: .now) }
                            .buttonStyle(RowPressStyle())
                    }
                }
            }
            if !finds.chats.isEmpty {
                section(String(localized: "Chats", comment: "Title of the chat list")) {
                    ForEach(finds.chats, id: \.summary.id) { row in
                        Button { open(BotRoute(bot: owner(of: row.summary.id), thread: row.summary.id)) } label: {
                            ListRow(systemImage: nil, title: row.summary.title, description: row.summary.lastMessage, accessory: .detail(rowTime(row.summary.updatedAt, now: .now)))
                                .padding(.horizontal, Space.gutter)
                        }
                        .buttonStyle(RowPressStyle())
                    }
                }
            }
            if !finds.messages.isEmpty {
                section(String(localized: "Messages", comment: "Home search: finds in what the chats say")) {
                    ForEach(finds.messages, id: \.self) { hit in
                        Button { open(BotRoute(bot: owner(of: hit.threadId), thread: hit.threadId)) } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                // A chat older than the ones the phone holds has no title here (the search names none).
                                if !title(of: hit.threadId).isEmpty {
                                    Text(title(of: hit.threadId)).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(1)
                                }
                                Text(snippetText(hit.snippet)).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(2)
                            }
                            .frame(maxWidth: .infinity, minHeight: Metric.rowWithDescription, alignment: .leading)
                            .padding(.horizontal, Space.gutter)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(RowPressStyle())
                    }
                }
            }
            switch hits {
            case .loading:
                ProgressView().frame(maxWidth: .infinity).padding(Space.l)
            case .failed(let failure):
                Text(
                    String(
                        localized: "Messages could not be searched: \(failureWords(failure, macPlace: ""))",
                        comment: "Home search: the Mac's search of messages failed; the argument is why")
                )
                .textRole(.footnote).foregroundStyle(Tone.secondary).padding(.horizontal, Space.gutter)
            case .loaded:
                if finds.isEmpty {
                    Text(String(localized: "Nothing found", comment: "Home search: no bot, chat or message holds the words"))
                        .textRole(.body).foregroundStyle(Tone.secondary).frame(maxWidth: .infinity).padding(Space.xl)
                }
            }
        }
        .padding(.vertical, Space.s)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary).padding(.horizontal, Space.gutter).padding(.bottom, Space.xxs)
            content()
        }
    }

    private func owner(of thread: String) -> String {
        bots.first { bot in bot.chats.contains { $0.summary.id == thread } }?.id ?? dotAgentId
    }

    private func title(of thread: String) -> String {
        bots.lazy.flatMap(\.chats).first { $0.summary.id == thread }?.summary.title ?? ""
    }
}

/// A search snippet with its matched words (the daemon's `[...]`) in bold.
func snippetText(_ snippet: String) -> AttributedString {
    var out: AttributedString = AttributedString()
    var rest: Substring = snippet[...]
    while let open = rest.firstIndex(of: "["), let close = rest[open...].firstIndex(of: "]") {
        out += AttributedString(String(rest[..<open]))
        var bold: AttributedString = AttributedString(String(rest[rest.index(after: open)..<close]))
        bold.inlinePresentationIntent = .stronglyEmphasized
        out += bold
        rest = rest[rest.index(after: close)...]
    }
    out += AttributedString(String(rest))
    return out
}

/// A new bot (PLAN §27): its name and what it should do; the daemon makes it and it greets the owner in its chat.
struct NewBotSheet: View {
    let made: (AgentRecord) -> Void
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var name: String = ""
    @State private var does: String = ""
    @State private var making: Bool = false
    @State private var problem: SettingsFailure?
    @FocusState private var nameFocused: Bool

    private var ready: Bool { !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !making }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    BotFace(id: name.isEmpty ? "new" : name, look: nil, size: 72).frame(maxWidth: .infinity).padding(.vertical, Space.s)
                    if let problem {
                        FailureBanner(failure: problem, macPlace: agentsOnTheMac, retry: nil)
                    }
                    GroupedSection(header: String(localized: "Name", comment: "Rename alert: the field of the chat's new name"), footer: nil) {
                        TextField(String(localized: "Researcher, Editor, Trader…", comment: "New bot: example names in the name field"), text: $name)
                            .textRole(.body)
                            .focused($nameFocused)
                            .submitLabel(.next)
                            .padding(.horizontal, Space.groupedGutter)
                            .frame(minHeight: Metric.row)
                            .accessibilityIdentifier("newBot.name")
                    }
                    GroupedSection(
                        header: String(localized: "What should it do?", comment: "New bot: the field of the bot's job"),
                        footer: String(localized: "It greets you in its chat and asks what you want first.", comment: "New bot: what happens once it is made")
                    ) {
                        TextField(
                            String(localized: "Watch my inbox each morning and tell me what needs an answer", comment: "New bot: an example job"), text: $does,
                            axis: .vertical
                        )
                        .textRole(.body)
                        .lineLimit(3...8)
                        .padding(Space.groupedGutter)
                        .accessibilityIdentifier("newBot.does")
                    }
                }
                .padding(.horizontal, Space.gutter)
                .readableColumn()
            }
            .background(Tone.sheet)
            .navigationTitle(String(localized: "New bot", comment: "Home: makes a new bot"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if making {
                        ProgressView()
                    } else {
                        Button(String(localized: "Create", comment: "New bot: makes it"), systemImage: "checkmark", role: .confirm) { create() }
                            .disabled(!ready)
                            .accessibilityIdentifier("newBot.create")
                    }
                }
            }
            .onAppear { nameFocused = true }
        }
        .presentationDetents([.large])
    }

    private func create() {
        guard ready else { return }
        making = true
        Task {
            switch await model.createBot(name: name, does: does) {
            case .success(let record): made(record)
            case .failure(let box): problem = box.failure
            }
            making = false
        }
    }
}
