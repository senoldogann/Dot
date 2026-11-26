import Foundation

/// A bot of the phone's home (PLAN §27, item 1881): Dot or one of the owner's agents, with the chats it wears. A chat
/// belongs to the agent whose face it wears (`actor_id`); every other chat (Dot's own, a schedule's, a code review's, a
/// campaign's, a team's) belongs to Dot.
public struct Bot: Sendable, Hashable, Identifiable {
    public let record: AgentRecord
    /// Its chats, the one that moved last first.
    public let chats: [ThreadRow]
    /// How many of its conversations (`isConversation`) the owner has not seen since they ended.
    public let unread: Int
    /// How many asks wait on the owner across its chats.
    public let asks: Int

    public var id: String { record.id }
    public var isDot: Bool { record.id == dotAgentId }
    /// The chat a tap on the bot opens: the conversation that moved last, or its chat that moved last when it has none.
    public var latest: ThreadRow? { chats.first { isConversation($0.summary) } ?? chats.first }
    /// When it last moved, for the home's order (its record's birth when it has no chat yet).
    public var movedAt: Double { chats.first?.summary.updatedAt ?? record.createdAt }

    public init(record: AgentRecord, chats: [ThreadRow], unread: Int, asks: Int) {
        self.record = record
        self.chats = chats
        self.unread = unread
        self.asks = asks
    }
}

/// The sources of the chats Dot holds of its own work, which the web folds under sections of their own (`sidebar.js`
/// `CHANNEL_SECTIONS`): its initiative's and its schedules', the work it sends the agents, the pre-push hook's code
/// reviews (item 1912: the hook writes one on every push, so Dot's face always wore an unread mark and a tap on it
/// opened the latest review).
let workSources: Set<String> = ["initiative", "schedule", "team", "review"]

/// Whether a chat is one the owner holds with the bot, not one of Dot's own work (pure).
public func isConversation(_ chat: ThreadSummary) -> Bool { !workSources.contains(chat.source) }

/// The bots of the home: Dot first, then the agents, pinned ones first (in the order `pinned` lists them), then the
/// one that moved last. `agents` is `GET /api/agents` (Dot first); with no Dot among them (not read yet), Dot is drawn
/// from `fallbackDot` so its chats are never lost. `rows` holds only the newest chats the list read (the daemon's
/// window); a bot's home chat (`AgentSummary.home`, found however many chats are newer) stands among its chats when the
/// window left it out, unless `gone` names it.
public func bots(agents: [AgentSummary], rows: [ThreadRow], pinned: [String], fallbackDot: AgentRecord, gone: Set<String>) -> [Bot] {
    let dotRecord: AgentRecord = agents.first { $0.record.id == dotAgentId }?.record ?? fallbackDot
    let others: [AgentRecord] = agents.map(\.record).filter { $0.id != dotAgentId }
    let known: Set<String> = Set(others.map(\.id))
    var byBot: [String: [ThreadRow]] = [:]
    for row in rows {
        let owner: String = row.summary.actorId.flatMap { known.contains($0) ? $0 : nil } ?? dotAgentId
        byBot[owner, default: []].append(row)
    }
    let homes: [String: ThreadSummary] = Dictionary(
        agents.compactMap { summary in summary.home.map { (summary.record.id, $0) } }, uniquingKeysWith: { first, _ in first })
    func bot(_ record: AgentRecord) -> Bot {
        var held: [ThreadRow] = byBot[record.id] ?? []
        // A home read with the agents is dropped once it went (deleted here, on the Mac or on another phone) or is going
        // (item 1965: Dot's profile still listed a chat the owner had deleted).
        if let home = homes[record.id], !gone.contains(home.id), !held.contains(where: { $0.summary.id == home.id }) {
            // `GET /api/agents` counts no asks: a home whose run is blocked waits on the owner, as the list says of it.
            held.append(ThreadRow(summary: home, revision: 0, asks: home.latestStatus == "blocked" ? 1 : 0, helpers: nil, background: nil))
        }
        let chats: [ThreadRow] = held.sorted { $0.summary.updatedAt > $1.summary.updatedAt }
        return Bot(record: record, chats: chats, unread: chats.filter { $0.summary.unread && isConversation($0.summary) }.count, asks: chats.reduce(0) { $0 + $1.asks })
    }
    let rank: [String: Int] = Dictionary(pinned.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
    let agentBots: [Bot] = others.map(bot).sorted { left, right in
        switch (rank[left.id], rank[right.id]) {
        case (let l?, let r?): return l < r
        case (.some, nil): return true
        case (nil, .some): return false
        case (nil, nil): return left.movedAt > right.movedAt
        }
    }
    return [bot(dotRecord)] + agentBots
}

/// How many bots other than `current` have something unread, muted ones aside: the "‹ N" of a bot's chat and the
/// home's "N unread" pill (with `current` `nil`).
public func unreadBots(_ bots: [Bot], except current: String?, muted: Set<String>) -> Int {
    bots.filter { $0.id != current && !muted.contains($0.id) && ($0.unread > 0 || $0.asks > 0) }.count
}

/// What the home's search finds (PLAN §27: bots, chats, messages): the bots whose name or role holds every word, the
/// chats whose title or last line does, and the daemon's finds in what the chats say, a chat named once.
public struct HomeFinds: Sendable, Equatable {
    public let bots: [Bot]
    public let chats: [ThreadRow]
    public let messages: [ChatHit]

    public var isEmpty: Bool { bots.isEmpty && chats.isEmpty && messages.isEmpty }
}

public func homeFinds(_ query: String, bots: [Bot], hits: [ChatHit]) -> HomeFinds {
    let words: [String] = query.split(whereSeparator: \.isWhitespace).map(String.init)
    guard !words.isEmpty else { return HomeFinds(bots: [], chats: [], messages: []) }
    func holds(_ texts: [String]) -> Bool {
        words.allSatisfy { word in texts.contains { $0.range(of: word, options: [.caseInsensitive, .diacriticInsensitive]) != nil } }
    }
    let foundBots: [Bot] = bots.filter { holds([$0.record.name, $0.record.role]) }
    let foundChats: [ThreadRow] = bots.flatMap(\.chats).filter { holds([$0.summary.title, $0.summary.lastMessage]) }
    let named: Set<String> = Set(foundChats.map(\.summary.id))
    var seen: Set<String> = named
    let messages: [ChatHit] = hits.filter { seen.insert($0.threadId).inserted }
    return HomeFinds(bots: foundBots, chats: foundChats, messages: messages)
}
