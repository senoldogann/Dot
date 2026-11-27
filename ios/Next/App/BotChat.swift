import DotEngine
import SwiftUI
import UIKit

// A bot's chat (PLAN §27, item 1883): the chat screen under the bot's own header: "‹ N" back with the unread of the
// other bots, a glass pill with the bot's face and name (a tap opens its profile) and the "⋯" menu. Opening a bot opens
// its latest chat; its earlier chats are in its profile.

/// What a chat shows of the bot it belongs to, and what its header's menu does. `nil` in the environment: a plain chat
/// (the debug fixtures).
struct BotChat: Sendable {
    let record: AgentRecord
    /// How many other bots have something unread, for "‹ N".
    let unreadOthers: Int
    /// Whether the chat is pushed on a phone (the iPad's detail column has no back): the unread count stands beside the
    /// system's back button.
    let drawsBack: Bool
    let back: @MainActor @Sendable () -> Void
    let openProfile: @MainActor @Sendable () -> Void
    let menu: @MainActor @Sendable (BotMenuAction) -> Void

    var isDot: Bool { record.id == dotAgentId }

    /// The same header with another doing for its menu (a pushed new chat answers its own New chat).
    func with(menu: @escaping @MainActor @Sendable (BotMenuAction) -> Void) -> BotChat {
        BotChat(record: record, unreadOthers: unreadOthers, drawsBack: drawsBack, back: back, openProfile: openProfile, menu: menu)
    }
}

/// The doings of a bot's "⋯" menu that the bot's screen carries out (Find in chat and Library are the chat's own).
enum BotMenuAction: Sendable {
    case rename
    case shareTemplate
    case newChat
    case delete
}

extension EnvironmentValues {
    @Entry var botChat: BotChat? = nil
}

/// Where the home goes: a bot, on a chat of its own (`thread` `nil`: its latest).
struct BotRoute: Hashable, Sendable {
    let bot: String
    let thread: String?
}

/// The chat a bot's screen shows (pure): the one chosen in its profile or begun in it, else the route's, else the one
/// it showed first and holds, the first of them not deleted; the bot's latest only when it holds none (item 1923: the
/// open chat changes only by the owner's hand, never because a newer chat of the bot came).
func botChatThread(chosen: String?, route: String?, held: String?, latest: String?, deleted: (String) -> Bool) -> String? {
    [chosen, route, held].compactMap { $0 }.first { !deleted($0) } ?? latest
}

/// A bot's chat with its header, its profile pushed from the pill, and the menu's sheets and confirmations.
struct BotChatScreen: View {
    let route: BotRoute
    let drawsBack: Bool
    let back: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var showsProfile: Bool = false
    @State private var renaming: Bool = false
    @State private var newName: String = ""
    @State private var deleting: Bool = false
    /// A new chat with the bot, pushed above its chat (item 1916).
    @State private var composing: Bool = false
    @State private var sharing: SharedFile?
    @State private var problem: SettingsFailure?
    /// A chat chosen in the profile, in place of the route's.
    @State private var chosen: String?
    /// The chat the screen showed first when the route named none (the bot's latest then), held so that a newer chat
    /// of the bot (a code review Dot starts, a chat begun on the Mac) never takes its place by itself (item 1923).
    @State private var held: String?

    private var bot: Bot? { model.bots.first { $0.id == route.bot } }

    var body: some View {
        Group {
            if let bot {
                content(bot)
                    .environment(\.botChat, header(bot))
                    // A destination takes the environment of the stack, not of the view that declares it: the header goes in by hand.
                    .navigationDestination(isPresented: $showsProfile) { BotProfile(
                            botId: bot.id,
                            open: { thread in
                                chosen = thread
                                showsProfile = false
                            }).environment(\.botChat, header(bot)) }
                    .navigationDestination(isPresented: $composing) { PushedNewChat(botId: bot.id, header: header(bot)) }
                    // The chat shown first is held from the moment the screen appears, before its chat's own screen does
                    // (item 1923's review: a newer chat landing in between must not be the one held).
                    .onAppear { if chosen == nil, route.thread == nil, held == nil { held = bot.latest?.summary.id } }
                    // A new chat opened under its outbox key becomes the real chat, in place, once the daemon names it.
                    .onChange(of: chosen.flatMap { model.startedChats[$0] }) { _, thread in if let thread { chosen = thread } }
                    .sheet(item: $sharing) { file in ShareSheet(items: [file.url]).presentationDetents([.medium, .large]) }
                    .alert(String(localized: "Rename", comment: "Context menu of a chat"), isPresented: $renaming) {
                        TextField(String(localized: "Name", comment: "Rename alert: the field of the chat's new name"), text: $newName)
                        Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), role: .cancel) {}
                        Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits")) { rename(bot) }
                    }
                    .confirmationDialog(
                        String(localized: "Delete \(bot.record.name)?", comment: "Confirmation of deleting a bot; the argument is its name"), isPresented: $deleting,
                        titleVisibility: .visible
                    ) {
                        Button(String(localized: "Delete", comment: "Confirmation button that deletes a chat"), role: .destructive) { delete(bot) }
                    } message: {
                        Text(String(localized: "Its chats stay, as Dot's. Its face, role and instructions go, and its schedules are switched off.", comment: "Confirmation of deleting a bot: what happens"))
                    }
                    .safeAreaInset(edge: .top) {
                        if let problem {
                            FailureBanner(failure: problem, macPlace: agentsOnTheMac, retry: nil)
                                .padding(.horizontal, Space.gutter)
                        }
                    }
            } else if let failure = model.agentsProblem {
                // The list could not be read (a bot made a moment ago is not in the stale one): not a deleted bot.
                VStack {
                    FailureBanner(failure: failure, macPlace: agentsOnTheMac, retry: { Task { await model.loadAgents() } })
                        .padding(.horizontal, Space.gutter)
                    Spacer()
                }
                .background(Tone.ground)
            } else {
                EmptyState(
                    title: String(localized: "This bot is gone", comment: "A bot's chat when the bot was deleted"), systemImage: "person.crop.circle.badge.xmark",
                    detail: String(localized: "It was deleted on your Mac or another device; its chats are with Dot.", comment: "A bot's chat when the bot was deleted: detail"),
                    actionTitle: nil, action: {}
                )
                .background(Tone.ground)
            }
        }
    }

    @ViewBuilder private func content(_ bot: Bot) -> some View {
        let shown: String? = botChatThread(
            chosen: chosen, route: route.thread, held: held, latest: bot.latest?.summary.id, deleted: { model.chats[$0]?.freshness == .deleted })
        if let key = shown, model.startingChats[key] != nil {
            StartingChat(key: key)
        } else if let thread = shown {
            ChatScreen(thread: thread, initialPanel: nil, editsFirstQueued: false)
                .id(thread)
                .onAppear { if chosen == nil, route.thread == nil, held != thread { held = thread } }
        } else {
            // A bot with no chat yet: the new chat in place; its first message opens the chat, as a pushed one does.
            BotNewChat(botId: bot.id, opened: { chosen = $0 })
        }
    }

    /// What the chat shows of the bot and what its header's menu does.
    private func header(_ bot: Bot) -> BotChat {
        BotChat(
            record: bot.record, unreadOthers: unreadBots(model.bots, except: bot.id, muted: model.mutedBots), drawsBack: drawsBack,
            back: { back() }, openProfile: { showsProfile = true }, menu: { perform($0, bot: bot) })
    }

    private func perform(_ action: BotMenuAction, bot: Bot) {
        switch action {
        case .rename:
            newName = bot.record.name
            renaming = true
        case .shareTemplate:
            Task {
                switch await model.exportBot(bot.record) {
                case .success(let file):
                    problem = nil
                    sharing = SharedFile(url: file)
                case .failure(let box): problem = box.failure
                }
            }
        case .newChat: composing = true
        case .delete: deleting = true
        }
    }

    private func rename(_ bot: Bot) {
        let name: String = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != bot.record.name else { return }
        Task { problem = await model.renameBot(bot.record, to: name) }
    }

    private func delete(_ bot: Bot) {
        Task {
            problem = await model.deleteBot(bot.id)
            if problem == nil { back() }
        }
    }
}

/// The header of a bot's chat: "‹ N", the pill, "⋯". `find` and `library` are the chat's own (none on a bot with no chat).
struct BotHeader: ToolbarContent {
    let chat: BotChat
    /// The chat's state line when it is not live ("Offline · saved 4 min ago"), under the name in the pill.
    let subtitle: String?
    let find: (() -> Void)?
    let library: (() -> Void)?

    var body: some ToolbarContent {
        // The system's own back button stays (with its swipe); beside it, how many other bots have something unread.
        if chat.drawsBack, chat.unreadOthers > 0 {
            ToolbarItem(placement: .topBarLeading) {
                Button(action: chat.back) {
                    Text(verbatim: "\(chat.unreadOthers)")
                        .textRole(.label).fontWeight(.semibold).tabularNumbers()
                        .foregroundStyle(Tone.onAction)
                        .padding(.horizontal, 6)
                        .frame(minWidth: 20, minHeight: 20)
                        .background(Tone.action, in: Capsule())
                }
                .accessibilityLabel(
                    String(localized: "Back, \(chat.unreadOthers) bots unread", comment: "VoiceOver: the back button of a bot's chat with the other bots' unread count"))
            }
        }
        ToolbarItem(placement: .principal) {
            Button(action: chat.openProfile) {
                HStack(spacing: Space.xs) {
                    BotFace(id: chat.record.id, look: chat.record.look, size: 26)
                    VStack(alignment: .leading, spacing: 0) {
                        Text(chat.record.name).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(1)
                        if let subtitle { Text(subtitle).textRole(.micro).foregroundStyle(Tone.secondary).lineLimit(1) }
                    }
                }
                .padding(.leading, Space.xs)
                .padding(.trailing, Space.m)
                .frame(minHeight: Metric.hit)
                .interactiveGlass(in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(chat.record.name)
            .accessibilityHint(String(localized: "Opens the bot's profile", comment: "VoiceOver hint of the pill in a bot's chat header"))
            .accessibilityIdentifier("botChat.pill")
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Button(String(localized: "View profile", comment: "Bot chat menu: opens the bot's profile"), systemImage: "person.crop.circle", action: chat.openProfile)
                if !chat.isDot {
                    Button(String(localized: "Rename", comment: "Context menu of a chat"), systemImage: "square.and.pencil") { chat.menu(.rename) }
                }
                if let find { Button(String(localized: "Find in chat", comment: "Bot chat menu: searches the open chat"), systemImage: "magnifyingglass", action: find) }
                if let library { Button(String(localized: "Library", comment: "Bot chat menu: the files and pages this bot made"), systemImage: "folder", action: library) }
                if !chat.isDot {
                    Button(String(localized: "Share as template…", comment: "Bot chat menu: shares the agent as a file others can import"), systemImage: "square.and.arrow.up") {
                        chat.menu(.shareTemplate)
                    }
                }
                Button(String(localized: "New chat", comment: "Chat list toolbar: starts a new chat"), systemImage: "plus.bubble") { chat.menu(.newChat) }
                if !chat.isDot {
                    Divider()
                    Button(String(localized: "Delete…", comment: "Context menu of a chat: asks before deleting"), systemImage: "trash", role: .destructive) { chat.menu(.delete) }
                }
            } label: {
                Image(systemName: "ellipsis")
            }
            .accessibilityLabel(String(localized: "More", comment: "VoiceOver: the ⋯ menu of a bot's chat"))
        }
    }
}

/// The system's share sheet for a file.
struct ShareSheet: UIViewControllerRepresentable {
    let items: [URL]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

/// A file handed to the share sheet.
struct SharedFile: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

/// The blocks of a chat whose words hold `words` (Find in chat), in order: the owner's messages, the answers, the notes
/// and the thoughts, also those a finished run folded into its work (the fold is what a find scrolls to); a tool's output
/// is not searched. Empty words find nothing.
func findMatches(_ items: [TranscriptItem], words: String) -> [String] {
    let wanted: String = words.trimmingCharacters(in: .whitespaces)
    guard !wanted.isEmpty else { return [] }
    return items.filter { holds($0, wanted) }.map(\.id)
}

/// Whether an item, or an item a fold holds, has the words.
private func holds(_ item: TranscriptItem, _ wanted: String) -> Bool {
    switch item {
    case .owner(_, let text, _), .note(_, let text), .answer(_, let text, _, _), .thought(_, let text, _): text.localizedStandardContains(wanted)
    case .work(let fold): fold.items.contains { holds($0, wanted) }
    default: false
    }
}

/// The bar of Find in chat, above the transcript: the words, "2 of 5", up and down, Done.
struct FindBar: View {
    @Binding var words: String
    let index: Int
    let count: Int
    let step: (Int) -> Void
    let close: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: Space.s) {
            HStack(spacing: Space.xs) {
                Image(systemName: "magnifyingglass").foregroundStyle(Tone.secondary)
                TextField(String(localized: "Find in chat", comment: "Bot chat menu: searches the open chat"), text: $words)
                    .textRole(.body)
                    .focused($focused)
                    .submitLabel(.search)
                    .onSubmit { step(1) }
                if !words.isEmpty {
                    Text(count == 0 ? String(localized: "None", comment: "Find in chat: no block holds the words") : String(localized: "\(index + 1) of \(count)", comment: "Find in chat: which find is shown of how many"))
                        .textRole(.label).foregroundStyle(Tone.secondary).tabularNumbers()
                }
            }
            .padding(.horizontal, Space.m)
            .frame(minHeight: Metric.hit)
            .interactiveGlass(in: Capsule())
            Button(String(localized: "Previous", comment: "Find in chat: the find before"), systemImage: "chevron.up") { step(-1) }
                .labelStyle(.iconOnly).disabled(count == 0)
            Button(String(localized: "Next", comment: "Find in chat: the find after"), systemImage: "chevron.down") { step(1) }
                .labelStyle(.iconOnly).disabled(count == 0)
            Button(String(localized: "Done", comment: "Find in chat: closes the bar"), action: close).fontWeight(.medium)
        }
        .foregroundStyle(Tone.text)
        .padding(.horizontal, Space.gutter)
        .padding(.vertical, Space.xs)
        .onAppear { focused = true }
    }
}
