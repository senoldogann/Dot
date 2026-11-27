import DotEngine
import Foundation

// The chat's settings sheet (PLAN §24, owner-refs/ref-model-picker-1/2.png), its pure rules: which models show in which
// group and order, what a pick of a model does to the level and the speed, and the owner's favourites and hidden models
// kept on this phone (the web keeps its favourites in the browser the same way, `modelpicker.js`).

/// What the sheet edits: the chat's choices (`PATCH /threads/{id}` for a chat that exists, `POST /runs` for a new one)
/// and "plan first" for the next message.
struct ChatSettingsDraft: Equatable, Sendable {
    var choices: ChatChoices
    var planFirst: Bool
}

/// The search's filter button: every model, the favourites alone, or the free ones.
enum ModelFilter: String, CaseIterable, Sendable {
    case all
    case favourites
    case free
}

/// One model row of the sheet.
struct ModelRow: Equatable, Sendable, Identifiable {
    let option: ModelOption
    let isDefault: Bool
    let isFavourite: Bool
    let isHidden: Bool
    var id: String { option.ref }
}

/// One provider's group: its models, favourites first, then in the daemon's order.
struct ModelGroup: Equatable, Sendable, Identifiable {
    let provider: String
    let rows: [ModelRow]
    var id: String { provider }
}

/// What the owner keeps on this phone about the models: the favourites (☆) and the hidden ones, by reference.
struct ModelMarks: Equatable, Sendable {
    var favourites: [String]
    var hidden: [String]

    static let favouritesKey: String = "models.favourites"
    static let hiddenKey: String = "models.hidden"

    static func read(_ defaults: UserDefaults) -> ModelMarks {
        ModelMarks(favourites: defaults.stringArray(forKey: favouritesKey) ?? [], hidden: defaults.stringArray(forKey: hiddenKey) ?? [])
    }

    func write(_ defaults: UserDefaults) {
        defaults.set(favourites, forKey: Self.favouritesKey)
        defaults.set(hidden, forKey: Self.hiddenKey)
    }

    /// The same marks with `ref` starred or unstarred.
    func togglingFavourite(_ ref: String) -> ModelMarks {
        ModelMarks(favourites: favourites.contains(ref) ? favourites.filter { $0 != ref } : favourites + [ref], hidden: hidden)
    }

    func togglingHidden(_ ref: String) -> ModelMarks {
        ModelMarks(favourites: favourites, hidden: hidden.contains(ref) ? hidden.filter { $0 != ref } : hidden + [ref])
    }
}

/// The groups the sheet shows. Only models that can call tools (a run needs them; a daemon that does not say keeps the
/// model); a provider that failed to list or lists none is left out. Hidden models are left out unless the owner
/// manages them (or one is the chat's model now); the search keeps a model whose name or reference holds every word;
/// the filter keeps the favourites or the free ones. Favourites come first in their group.
func modelGroups(
    _ providers: [ProviderModels], marks: ModelMarks, defaultRef: String?, chosenRef: String?, showsHidden: Bool, query: String, filter: ModelFilter
) -> [ModelGroup] {
    // Folded as Settings' search folds (case, accents, the Turkish dotless i); a word may match the provider's name.
    let words: [String] = searchWords(query)
    return providers.compactMap { provider in
        guard provider.error == nil else { return nil }
        let providerWords: [String] = searchWords(providerTitle(provider.provider) + " " + provider.provider)
        let rows: [ModelRow] = provider.models.compactMap { option in
            guard option.tools != false else { return nil }
            let hidden: Bool = marks.hidden.contains(option.ref)
            if hidden, !showsHidden, option.ref != chosenRef { return nil }
            let text: [String] = providerWords + searchWords(option.label + " " + option.ref)
            guard words.allSatisfy({ word in text.contains { $0.hasPrefix(word) } }) else { return nil }
            let favourite: Bool = marks.favourites.contains(option.ref)
            switch filter {
            case .all: break
            case .favourites: if !favourite { return nil }
            case .free: if option.free != true { return nil }
            }
            return ModelRow(option: option, isDefault: option.ref == defaultRef, isFavourite: favourite, isHidden: hidden)
        }
        guard !rows.isEmpty else { return nil }
        let ordered: [ModelRow] = rows.filter(\.isFavourite) + rows.filter { !$0.isFavourite }
        return ModelGroup(provider: provider.provider, rows: ordered)
    }
}

/// The models at the top of the sheet: the chosen one, then the ones picked last (newest first), each once, only those
/// the daemon still offers and the groups would show (tools; a hidden one only while hidden models are managed, or when
/// it is the chosen one); at most `limit`.
func recentRows(
    _ providers: [ProviderModels], recent: [String], chosenRef: String?, defaultRef: String?, marks: ModelMarks, showsHidden: Bool, limit: Int
) -> [ModelRow] {
    let offered: [String: ModelOption] = Dictionary(
        providers.filter { $0.error == nil }.flatMap(\.models).map { ($0.ref, $0) }, uniquingKeysWith: { first, _ in first })
    var seen: Set<String> = []
    let refs: [String] = ([chosenRef].compactMap { $0 } + recent).filter { seen.insert($0).inserted }
    return refs.compactMap { ref in
        guard let option = offered[ref], option.tools != false else { return nil }
        let hidden: Bool = marks.hidden.contains(ref)
        if hidden, !showsHidden, ref != chosenRef { return nil }
        return ModelRow(option: option, isDefault: ref == defaultRef, isFavourite: marks.favourites.contains(ref), isHidden: hidden)
    }
    .prefix(limit).map { $0 }
}

/// The providers that listed no model, with the daemon's reason (no key, not installed, refused).
func failedProviders(_ providers: [ProviderModels]) -> [(provider: String, why: String)] {
    providers.compactMap { provider in provider.error.map { (provider.provider, $0) } }
}

/// The models picked last, newest first, kept on the phone.
enum RecentModels {
    static let key: String = "models.recent"

    static func read(_ defaults: UserDefaults) -> [String] { defaults.stringArray(forKey: key) ?? [] }

    /// The list after `ref` was picked: first, once, at most eight.
    static func adding(_ ref: String, to recent: [String]) -> [String] {
        Array(([ref] + recent.filter { $0 != ref }).prefix(8))
    }
}

/// The model a run of these choices uses: the one picked, else the daemon's main.
func effectiveModel(_ choices: ChatChoices, defaultRef: String?) -> String? { choices.model ?? defaultRef }

/// The choices after the owner picked a model: its own default level when the level held is not one it has, standard
/// speed when it has no fast mode.
func picking(_ option: ModelOption, in choices: ChatChoices) -> ChatChoices {
    let effort: String? =
        if let held = choices.effort, option.efforts.contains(held) { held } else { option.efforts.count < 2 ? nil : option.defaultEffort }
    let picked: ChatChoices = choices.with(model: option.ref, effort: effort)
    return option.fastMode == true ? picked : picked.with(speed: "standard")
}

/// The approval modes a chat can run under (`[policy] approvals`, strictest first), as the web's run modes name them.
/// Free mode is the owner's switch for all of Dot (item 922), not a chat's choice, so it is not here.
enum ApprovalChoice: String, CaseIterable, Sendable {
    case supervised
    case ask
    case auto
    case yolo

    var title: String {
        switch self {
        case .supervised: String(localized: "Supervised", comment: "Approval mode: the owner approves every change")
        case .ask: String(localized: "Auto-accept edits", comment: "Approval mode: edits go ahead, the rest is asked")
        case .auto: String(localized: "Auto", comment: "Approval mode: a second AI answers for the owner")
        case .yolo: String(localized: "Ask only to pay or send", comment: "Approval mode yolo: asks only before a payment, a message or a public post")
        }
    }

    var hint: String {
        switch self {
        case .supervised: String(localized: "You approve every change: files, commands, clicks, messages", comment: "Approval mode supervised, its hint")
        case .ask: String(localized: "Edits and everyday work go ahead; Dot asks you about the rest", comment: "Approval mode ask, its hint")
        case .auto: String(localized: "A second AI answers for you; you are asked only what it is unsure about", comment: "Approval mode auto, its hint")
        case .yolo: String(localized: "Dot asks only before a payment, a message or mail sent for you, or a public post", comment: "Approval mode yolo, its hint")
        }
    }
}

/// A reasoning level's name (`off`, `low`, …, `max`) as the sheet says it.
func effortTitle(_ effort: String) -> String {
    switch effort {
    case "off": String(localized: "Off", comment: "Reasoning level: none")
    case "on": String(localized: "On", comment: "Reasoning level: on, no levels")
    case "minimal": String(localized: "Minimal", comment: "Reasoning level")
    case "low": String(localized: "Low", comment: "Reasoning level")
    case "medium": String(localized: "Medium", comment: "Reasoning level")
    case "high": String(localized: "High", comment: "Reasoning level")
    case "xhigh": String(localized: "Extra high", comment: "Reasoning level")
    case "max": String(localized: "Max", comment: "Reasoning level")
    default: effort.capitalized
    }
}

/// A provider's name as the group header says it, the web's own (`providers.js` LABELS); the daemon's id otherwise.
func providerTitle(_ provider: String) -> String {
    switch provider {
    case "openai": "OpenAI"
    case "anthropic": "Anthropic"
    case "opencode_go": "OpenCode Go"
    case "openrouter": "OpenRouter"
    case "google": "Google"
    case "antigravity": "Google Antigravity"
    case "opencode": "OpenCode"
    case "claude_code": "Claude Code"
    case "ollama": "Ollama"
    case "typesafe": "TypeSafe"
    default: provider
    }
}

/// The provider a model reference belongs to: the group of the daemon's list that holds it, else the reference's own
/// prefix ("claude_code:opus" → "claude_code"; a reference with none: empty, which wears the generic mark).
func providerOfModel(_ ref: String, in options: [ProviderModels]) -> String {
    if let group = options.first(where: { $0.models.contains { $0.ref == ref } }) { return group.provider }
    return ref.contains(":") ? String(ref.split(separator: ":", maxSplits: 1)[0]) : ""
}

/// Whether the chat follows new words after the scroll changed (pure): at the end it always does; away from it, the owner's
/// own scroll stops it, and without one it keeps what it was (a lazy stack's moving height is no scroll of theirs). The
/// owner's scroll counts when it begins as well as when the end moves away: a chat whose end moved away first, by itself,
/// would otherwise never be seen to be left, and the jump button would never come.
func followsAfterScroll(following: Bool, atEnd: Bool, ownerScrolls: Bool) -> Bool {
    if atEnd { return true }
    return ownerScrolls ? false : following
}

/// A model's name without its provider's name in front ("Anthropic · Opus 5.5" and "Claude Code: Opus" give "Opus 5.5" and
/// "Opus"): the provider's mark stands beside it. A label that does not start so, or is no more than the provider's name,
/// is kept (pure).
func modelNameWithoutProvider(_ label: String, provider: String) -> String {
    let title: String = providerTitle(provider)
    let marks: String = "·:/-–—"
    guard !title.isEmpty, let range = label.range(of: title, options: [.anchored, .caseInsensitive]) else { return label }
    // A mark of punctuation must stand between the provider's name and the model's: "Mistral Large" of the provider Mistral
    // is a model's own name.
    let tail: Substring = label[range.upperBound...].drop { $0 == " " }
    guard let mark = tail.first, marks.contains(mark) else { return label }
    let rest: Substring = tail.dropFirst().drop { $0 == " " }
    return rest.isEmpty ? label : String(rest)
}

/// What the expanded composer's chip says in grey after the model's name: its level and "Fast" when fast mode is on
/// ("Medium Fast"); `nil` when neither (pure).
func chipDetail(effort: String?, fast: Bool) -> String? {
    let parts: [String] = [effort.map(effortTitle), fast ? String(localized: "Fast", comment: "The composer's model chip: fast mode is on") : nil].compactMap { $0 }
    return parts.isEmpty ? nil : parts.joined(separator: " ")
}

/// What the composer's model chip draws for a model (item 1992): its provider's mark, except for a model of a family this
/// app has no mark of that a gateway hosts (DeepSeek, Kimi, GLM, Qwen, MiniMax on OpenCode Go, OpenRouter or Ollama), which
/// wore the gateway's: OpenCode's mark is a plain white block, so every such model read as an empty rectangle. Those get a
/// neutral symbol.
enum ModelMark: Equatable {
    case asset(String)
    case symbol(String)
}

func modelMark(ref: String, provider: String) -> ModelMark {
    let gateways: Set<String> = ["opencode", "opencode_go", "openrouter", "ollama"]
    let name: String = (ref.split(separator: ":", maxSplits: 1).last.map(String.init) ?? ref).split(separator: "/").last.map(String.init) ?? ref
    let families: [String] = ["deepseek", "kimi", "glm", "qwen", "minimax"]
    if gateways.contains(provider), families.contains(where: { name.lowercased().hasPrefix($0) }) { return .symbol("sparkles") }
    return .asset(providerMarkAsset(provider))
}

/// The asset of a provider's mark (`Resources/Assets.xcassets/provider-*`, the web's `static/icons/providers`): Zen's
/// free models wear OpenCode Go's, the Claude plan Anthropic's, Antigravity Google's; the rest the generic one.
func providerMarkAsset(_ provider: String) -> String {
    switch provider {
    case "anthropic", "claude_code": "provider-anthropic"
    case "openai": "provider-openai"
    case "google", "antigravity": "provider-google"
    case "opencode", "opencode_go": "provider-opencode_go"
    case "openrouter": "provider-openrouter"
    case "ollama": "provider-ollama"
    default: "provider-generic"
    }
}
