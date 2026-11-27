import Foundation

// The composer's pickers (PLAN §22, the owner's shots): `/` commands and skills, `@` agents, teams, chats and accounts,
// `#` the files of this chat; one panel, filtered as the owner types. Pure: the app draws them and says their words.

/// What a picker row is.
public enum PickerKind: String, Sendable, Equatable {
    case command
    case skill
    case prompt
    case agent
    case team
    case chat
    case account
    case file
}

/// One row of the panel: the token it puts in the text, its name and a quiet description.
public struct PickerItem: Sendable, Equatable, Identifiable {
    public let kind: PickerKind
    public let token: String
    public let name: String
    public let detail: String
    public let starter: PalettePrompt?
    public var id: String { kind.rawValue + ":" + token }

    public init(kind: PickerKind, token: String, name: String, detail: String, starter: PalettePrompt? = nil) {
        self.kind = kind
        self.token = token
        self.name = name
        self.detail = detail
        self.starter = starter
    }
}

/// A section of the panel, under its quiet uppercase header (the app's words for `kind`).
public struct PickerSection: Sendable, Equatable, Identifiable {
    public let kind: PickerKind
    public let items: [PickerItem]
    public var id: String { kind.rawValue }
}

/// What the pickers offer besides Dot's own commands, read from the daemon (`/api/palette`, `/api/agents`,
/// `/api/teams`), the chat list and this chat's changed files.
public struct ComposerCatalog: Sendable, Equatable {
    public let skills: [PickerItem]
    public let prompts: [PickerItem]
    public let agents: [PickerItem]
    public let teams: [PickerItem]
    public let chats: [PickerItem]
    public let accounts: [PickerItem]
    public let files: [PickerItem]

    public init(skills: [PickerItem], prompts: [PickerItem] = [], agents: [PickerItem], teams: [PickerItem], chats: [PickerItem], accounts: [PickerItem], files: [PickerItem]) {
        self.skills = skills
        self.prompts = prompts
        self.agents = agents
        self.teams = teams
        self.chats = chats
        self.accounts = accounts
        self.files = files
    }

    public static let empty: ComposerCatalog = ComposerCatalog(skills: [], agents: [], teams: [], chats: [], accounts: [], files: [])
}

/// The word being typed after a trigger at the start of a word, at the end of the text.
public enum PickerTrigger: Sendable, Equatable {
    case slash(String)
    case at(String)
    case hash(String)
}

public func pickerTrigger(in text: String) -> PickerTrigger? {
    guard let last: Substring = text.split(separator: " ", omittingEmptySubsequences: false).last,
        let word: Substring = last.split(separator: "\n", omittingEmptySubsequences: false).last, let mark: Character = word.first
    else { return nil }
    // An account's token holds a second "@": the word stays open while it is typed.
    let typed: String = String(word.dropFirst())
    switch mark {
    case "/": return .slash(typed)
    case "@": return .at(typed)
    case "#": return .hash(typed)
    default: return nil
    }
}

/// The panel's sections for a trigger (§22): `/` Dot's commands then the skills; `@` agents and teams first, then
/// chats, then accounts; `#` the chat's files. A row shows when its name or token holds what was typed; empty sections
/// are left out; each section shows at most `limit` rows.
public func pickerSections(_ trigger: PickerTrigger, commands: [PickerItem], catalog: ComposerCatalog, limit: Int) -> [PickerSection] {
    let (typed, groups): (String, [(PickerKind, [PickerItem])]) =
        switch trigger {
        case .slash(let word): (word, [(.command, commands), (.skill, catalog.skills), (.prompt, catalog.prompts)])
        case .at(let word): (word, [(.agent, catalog.agents), (.team, catalog.teams), (.chat, catalog.chats), (.account, catalog.accounts)])
        case .hash(let word): (word, [(.file, catalog.files)])
        }
    return groups.compactMap { kind, items in
        let shown: [PickerItem] = items.filter { item in
            typed.isEmpty || item.name.localizedStandardContains(typed) || item.token.localizedStandardContains(typed)
        }
        return shown.isEmpty ? nil : PickerSection(kind: kind, items: Array(shown.prefix(limit)))
    }
}

/// What a message carries besides its words (`QueueMessageRequest`): the skills and accounts picked in the composer and
/// "plan first".
public struct MessagePicks: Codable, Sendable, Equatable {
    public let skills: [String]
    public let accounts: [String]
    public let planFirst: Bool

    public init(skills: [String], accounts: [String], planFirst: Bool) {
        self.skills = skills
        self.accounts = accounts
        self.planFirst = planFirst
    }

    public static let none: MessagePicks = MessagePicks(skills: [], accounts: [], planFirst: false)
}

/// The words of a message and what it picked: `/plan` at its start asks for a plan first; a `/skill` or an `@account`
/// token of the catalog becomes a pick and leaves the words (the web shows them as chips). Other words stay as typed.
public func messagePicks(_ text: String, catalog: ComposerCatalog) -> (text: String, picks: MessagePicks) {
    var words: [String] = text.components(separatedBy: " ")
    var planFirst: Bool = false
    if let first: String = words.first, first.lowercased() == "/plan" || first.lowercased() == "/plan\n" {
        planFirst = true
        words.removeFirst()
    }
    let skills: [String: String] = Dictionary(catalog.skills.map { ($0.token, $0.name) }, uniquingKeysWith: { first, _ in first })
    let accounts: [String: String] = Dictionary(catalog.accounts.map { ($0.token, $0.name) }, uniquingKeysWith: { first, _ in first })
    var pickedSkills: [String] = []
    var pickedAccounts: [String] = []
    let kept: [String] = words.filter { word in
        if let skill: String = skills[word] {
            if !pickedSkills.contains(skill) { pickedSkills.append(skill) }
            return false
        }
        if let account: String = accounts[word] {
            if !pickedAccounts.contains(account) { pickedAccounts.append(account) }
            return false
        }
        return true
    }
    let words_: String = kept.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    return (words_, MessagePicks(skills: pickedSkills, accounts: pickedAccounts, planFirst: planFirst))
}

/// A command the composer does itself rather than send as words.
public enum ComposerCommand: Sendable, Equatable {
    /// `/btw` or `/side <question>`: a side question the run answers without seeing it.
    case aside(String)
    /// `/loop 30m <message>`: the message now and again on that rhythm.
    case loop(every: String, text: String)
    /// `/loop` without a rhythm or a message: the composer says how to write it.
    case loopHow
    case stop
    /// `/model`: the model and effort of the chat's next message.
    case model
    /// Words to send.
    case message
}

public func composerCommand(_ text: String) -> ComposerCommand {
    let trimmed: String = text.trimmingCharacters(in: .whitespacesAndNewlines)
    let lower: String = trimmed.lowercased()
    for prefix in ["/btw", "/side"] where lower == prefix || lower.hasPrefix(prefix + " ") || lower.hasPrefix(prefix + "\n") {
        let question: String = String(trimmed.dropFirst(prefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
        return question.isEmpty ? .message : .aside(question)
    }
    if lower == "/loop" || lower.hasPrefix("/loop ") {
        guard let match = trimmed.firstMatch(of: /^\/loop\s+(\d+\s*[mhMH])\s+([\s\S]+)$/) else { return .loopHow }
        return .loop(every: String(match.1).replacingOccurrences(of: " ", with: "").lowercased(), text: String(match.2).trimmingCharacters(in: .whitespacesAndNewlines))
    }
    if lower == "/stop" { return .stop }
    if lower == "/model" { return .model }
    return .message
}
