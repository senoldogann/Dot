import Foundation

/// One thing that waits on the owner (`GET /api/needs-you`, items 1422 and 1423): an approval a run waits for, a question
/// (`ask_user`), a request for a secret (`ask_secret`) or a hand-off (a step only the owner can do). `id` is the approval's,
/// which the decision routes take; `title` is the line of what is asked, at most 120 code points (the daemon cuts it there, with
/// no mark that it did) and, for a secret and a hand-off, a fixed line that carries nothing of what the step is.
struct NeedsYouItem: Codable, Equatable, Hashable, Sendable, Identifiable {
    enum Kind: String, Codable, Sendable {
        case approval
        case question
        case secret
        case handoff
        /// A kind a newer daemon names that this build does not know: shown by its title, answered in the chat.
        case other

        init(from decoder: any Decoder) throws {
            let raw: String = try decoder.singleValueContainer().decode(String.self)
            self = Kind(rawValue: raw) ?? .other
        }
    }

    let id: String
    let kind: Kind
    let runId: String
    /// The chat to open (the top chat for a helper's run).
    let threadId: String
    /// Who asks: the owner's agent that works on the run, or Dot.
    let agent: String
    let title: String
    /// When it was asked, in seconds since the epoch.
    let since: Double
    /// `dotagent://run/<run id>`.
    let route: String
    /// The labels a question offers, and the one its run recommended; both are left out when there is none.
    var choices: [String]?
    var recommended: String?

    enum CodingKeys: String, CodingKey {
        case id, kind, agent, title, since, route, choices, recommended
        case runId = "run_id"
        case threadId = "thread_id"
    }
}

/// Everything that waits on the owner, newest first, and how many things that is (the app's badge).
struct NeedsYouList: Codable, Equatable, Sendable {
    var count: Int
    var items: [NeedsYouItem]

    static let empty: NeedsYouList = NeedsYouList(count: 0, items: [])

    init(count: Int, items: [NeedsYouItem]) {
        self.count = count
        self.items = items
    }

    enum CodingKeys: String, CodingKey { case count, items }

    /// An item that does not read (a shape a newer daemon writes) is left out of the list, not the whole list; `count` still
    /// says how many things wait, so the badge is right.
    init(from decoder: any Decoder) throws {
        let container: KeyedDecodingContainer<CodingKeys> = try decoder.container(keyedBy: CodingKeys.self)
        count = try container.decode(Int.self, forKey: .count)
        items = try container.decode([Maybe].self, forKey: .items).compactMap(\.item)
    }

    private struct Maybe: Decodable {
        let item: NeedsYouItem?
        init(from decoder: any Decoder) throws { item = try? NeedsYouItem(from: decoder) }
    }
}

/// A row of the inbox: who asks, where, and what is asked in words, with what the owner may answer here. A pure view of one
/// item: no screen, no clock.
struct NeedsYouRow: Equatable, Sendable, Identifiable {
    struct Choice: Equatable, Sendable, Identifiable {
        let label: String
        let recommended: Bool
        /// The place the run gave the option in its list: the run chooses the words, and two may say the same, so each is its own button.
        let position: Int
        var id: Int { position }
    }

    enum Ask: Equatable, Sendable {
        /// What Dot wants to do (`title`), the line of it for the mono block (`command`) and why it asks. `mayAllow` is false
        /// when the line may be the start of a longer one: the owner reads all of it in the chat before they allow it.
        case approval(title: String, command: String, why: String?, mayAllow: Bool)
        case question(text: String, choices: [Choice])
        /// Never answered on the phone: a secret is entered on the Mac.
        case secret
        case handoff(line: String)
        case other(line: String)
    }

    let item: NeedsYouItem
    /// The title of the chat, when the phone knows it.
    let chat: String?
    let ask: Ask
    /// The owner's answer is kept and waits to be sent (the outbox): the row says so and takes no second answer.
    let answering: Bool

    var id: String { item.id }
    var who: String { item.agent.isEmpty ? "Dot" : item.agent }

    /// The doing the outbox keeps for an answer given here, or `nil` when none may be given: a secret is never answered on the
    /// phone (the daemon's decision), a hand-off and a kind this build does not know are answered in the chat, an approval
    /// is allowed only when its whole line was shown, and a question needs words. An id that is not an approval's is no path.
    func decision(approve: Bool, note: String = "") -> OutboxItem.Action? {
        guard isApprovalId(item.id), !answering else { return nil }
        let words: String = note.trimmingCharacters(in: .whitespacesAndNewlines)
        switch ask {
        case .approval(_, _, _, let mayAllow):
            if approve && !mayAllow { return nil }
            return .decision(approval: item.id, run: item.runId, thread: item.threadId, approve: approve, note: "")
        case .question:
            if approve && words.isEmpty { return nil }
            return .decision(approval: item.id, run: item.runId, thread: item.threadId, approve: approve, note: approve ? words : "")
        case .secret, .handoff, .other:
            return nil
        }
    }
}

enum NeedsYouRules {
    /// How long the daemon cuts a title (`TITLE_CHARS` in `notify/needs_you.py`), without a mark: a title that long may be the
    /// start of a longer line, and a line that may be cut is not allowed from a list. The daemon counts code points, so this
    /// does: a line with combining marks or an emoji sequence is shorter in characters than it was cut at.
    static let titleLimit: Int = 120

    /// The rows of a list, newest first (the daemon sorts them, and a list read from disk or merged keeps no order that this
    /// does not give). `chatTitle` says what a chat is called; `answering` are the approvals whose answer waits in the outbox.
    static func rows(from list: NeedsYouList, chatTitle: (String) -> String? = { _ in nil }, answering: Set<String> = []) -> [NeedsYouRow] {
        list.items.enumerated().sorted { left, right in
            left.element.since != right.element.since ? left.element.since > right.element.since : left.offset < right.offset
        }.map { _, item in
            NeedsYouRow(item: item, chat: chatTitle(item.threadId), ask: ask(of: item), answering: answering.contains(item.id))
        }
    }

    static func ask(of item: NeedsYouItem) -> NeedsYouRow.Ask {
        switch item.kind {
        case .approval:
            let tool: String = ApprovalPresentation.tool(inferredFrom: item.title)
            let presentation: ApprovalPresentation = ApprovalPresentation.make(tool: tool, summary: item.title)
            return .approval(
                title: tool.isEmpty ? "Dot wants to do this" : presentation.title, command: presentation.command, why: presentation.why,
                mayAllow: !item.title.isEmpty && item.title.unicodeScalars.count < titleLimit)
        case .question:
            // The place is the one the run gave the option: an empty label is left out and does not move the others.
            let choices: [NeedsYouRow.Choice] = (item.choices ?? []).enumerated().compactMap { place, label in
                label.isEmpty ? nil : NeedsYouRow.Choice(label: label, recommended: label == item.recommended, position: place)
            }
            return .question(text: item.title, choices: choices)
        case .secret:
            return .secret
        case .handoff:
            return .handoff(line: HandoffCard.make(from: item)?.headline ?? item.title)
        case .other:
            return .other(line: item.title)
        }
    }

    /// The lifecycle notices after which what waits on the owner may be different: a card asked or answered, a question a run
    /// left, a run that ended (its cards close with it). Any other notice (a run started, a queue moved) leaves it alone.
    static func refreshes(onNotice kind: String) -> Bool {
        ["approval_requested", "approval_decided", "question_left", "run_finished"].contains(kind)
    }

    /// The approvals whose answer waits in the outbox.
    static func answering(in outbox: [OutboxItem]) -> Set<String> {
        Set(
            outbox.compactMap { item in
                if case .decision(let approval, _, _, _, _) = item.action { return approval }
                return nil
            })
    }

    /// The words of the entry in the chat list: how many things wait, and what the newest asks.
    static func entry(count: Int) -> String {
        count == 1 ? "1 thing waits on you" : "\(count) things wait on you"
    }

    /// What the newest row asks, in a few words, for the entry's second line.
    static func preview(of row: NeedsYouRow) -> String {
        switch row.ask {
        case .approval(let title, let command, _, _): command.isEmpty ? title : "\(title): \(command)"
        case .question(let text, _): text
        case .secret: "Dot needs a secret: enter it on your Mac"
        case .handoff(let line), .other(let line): line
        }
    }
}
