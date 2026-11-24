import Foundation

// The model and the reasoning level of a chat, as the web composer keeps them (item 199): the choice is the chat's, kept by
// the daemon (`PATCH /api/threads/{id}` with `choices`) and sent with each message, since the daemon starts a message on
// what the message names. Pure rules, no UI.

/// The reasoning levels as words ("xhigh" is "Extra high").
func effortWords(_ level: String) -> String {
    switch level {
    case "xhigh": "Extra high"
    case "off": "Off"
    case "on": "On"
    default: level.capitalizedFirst
    }
}

extension ModelOption {
    /// The levels the owner may pick on this model, lowest first; none when it offers fewer than two (no choice).
    var pickableEfforts: [String] {
        guard let efforts, efforts.count >= 2 else { return [] }
        return efforts
    }
}

extension [ProviderModels] {
    /// The model with this reference, wherever it is listed.
    func option(_ ref: String?) -> ModelOption? {
        guard let ref else { return nil }
        for provider in self {
            if let found: ModelOption = provider.models.first(where: { $0.ref == ref }) { return found }
        }
        return nil
    }

    /// The models a picker offers: those that can use tools, by provider, a provider with none left out.
    var pickable: [ProviderModels] {
        compactMap { provider in
            let usable: [ModelOption] = provider.models.filter(\.tools)
            return usable.isEmpty ? nil : ProviderModels(provider: provider.provider, models: usable, error: nil)
        }
    }
}

extension ChatChoices {
    /// The choices of a chat that has none kept: what its first message started with is not known here, so every part
    /// takes the daemon's own (`auto` mode, the default model and level).
    static let fresh: ChatChoices = ChatChoices(mode: "auto", model: nil, effort: nil, speed: "standard", approvals: nil, goalMode: nil)

    /// These choices with another model (`nil`: the default model). A level the new model does not offer is dropped, as
    /// the composer does not ask for it (item 116), and so is fast mode, which only some models have.
    func picking(model ref: String?, among providers: [ProviderModels]) -> ChatChoices {
        var next: ChatChoices = self
        if ref != model { next.speed = "standard" }
        next.model = ref
        if let level: String = effort, let option: ModelOption = providers.option(ref), !option.pickableEfforts.contains(level) {
            next.effort = nil
        }
        return next
    }

    /// These choices with another reasoning level (`nil`: the default one).
    func picking(effort level: String?) -> ChatChoices {
        var next: ChatChoices = self
        next.effort = level
        return next
    }

    /// The level the chat works at, in words, for a row: the kept one, else the model's default.
    func effortLine(among providers: [ProviderModels], main: String?) -> String {
        if let effort { return effortWords(effort) }
        if let option: ModelOption = providers.option(model ?? main), let level: String = option.defaultEffort, !option.pickableEfforts.isEmpty {
            return "\(effortWords(level)) (default)"
        }
        return "Default"
    }
}

/// What a message carries of the chat's choices, written as the web's composer writes them: the mode, model, level, speed,
/// approvals and goal mode of the chat, the agent whose chat it is. A message of a chat whose choices are not known goes
/// as before (the daemon's own).
struct MessageChoices: Equatable, Sendable, Codable {
    var mode: String?
    var model: String?
    var effort: String?
    var speed: String?
    var approvals: String?
    var goalMode: Bool?
    var agentId: String?

    static let none: MessageChoices = MessageChoices()

    init(mode: String? = nil, model: String? = nil, effort: String? = nil, speed: String? = nil, approvals: String? = nil,
        goalMode: Bool? = nil, agentId: String? = nil)
    {
        self.mode = mode
        self.model = model
        self.effort = effort
        self.speed = speed
        self.approvals = approvals
        self.goalMode = goalMode
        self.agentId = agentId
    }

    init(_ choices: ChatChoices?, agentId: String?) {
        guard let choices else {
            self.init(agentId: agentId)
            return
        }
        self.init(
            mode: choices.mode, model: choices.model, effort: choices.effort, speed: choices.speed, approvals: choices.approvals,
            goalMode: choices.goalMode, agentId: agentId)
    }
}
