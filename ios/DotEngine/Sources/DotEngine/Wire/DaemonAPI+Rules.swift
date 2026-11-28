import Foundation

public enum RuleAnswer: String, Decodable, Sendable {
    case allow, preapproved, ask, handoff
}

public enum PhoneRuleAnswer: String, CaseIterable, Encodable, Sendable {
    case ask, handoff

    public var answer: RuleAnswer {
        switch self {
        case .ask: .ask
        case .handoff: .handoff
        }
    }
}

public struct SavedRule: Decodable, Identifiable, Sendable {
    public let id: String
    public let enabled: Bool
    public let behavior: RuleAnswer
    public let toolGlob: String
    public let matchRegex: String
    public let description: String
    public let neverDecidedBy: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case enabled
        case behavior
        case toolGlob = "tool_glob"
        case matchRegex = "match_regex"
        case description
        case neverDecidedBy = "never_decided_by"
    }
}

public struct DraftRule: Decodable, Sendable {
    public let behavior: RuleAnswer
    public let toolGlob: String
    public let matchRegex: String
    public let description: String

    private enum CodingKeys: String, CodingKey {
        case behavior
        case toolGlob = "tool_glob"
        case matchRegex = "match_regex"
        case description
    }
}

public struct RuleTryout: Decodable, Sendable {
    public let scanned: Int
    public let fits: Int
    public let wouldDecide: Int
    public let partial: Bool
    public let problem: String?

    private enum CodingKeys: String, CodingKey {
        case scanned
        case fits
        case wouldDecide = "would_decide"
        case partial
        case problem
    }
}

public struct RuleActivity: Decodable, Sendable {
    public let rules: [Entry]
    public let partial: Bool
    public let problem: String?

    public struct Entry: Decodable, Sendable {
        public let ruleId: String
        public let decides: Int

        private enum CodingKeys: String, CodingKey {
            case ruleId = "rule_id"
            case decides
        }
    }
}

private struct RuleDraftBody: Encodable { let sentence: String }
private struct RuleTryoutBody: Encodable {
    let behavior: PhoneRuleAnswer
    let toolGlob: String
    let matchRegex: String
    let days: Int

    private enum CodingKeys: String, CodingKey {
        case behavior
        case toolGlob = "tool_glob"
        case matchRegex = "match_regex"
        case days
    }
}
private struct AddRuleBody: Encodable {
    let behavior: PhoneRuleAnswer
    let toolGlob: String
    let matchRegex: String
    let description: String

    private enum CodingKeys: String, CodingKey {
        case behavior
        case toolGlob = "tool_glob"
        case matchRegex = "match_regex"
        case description
    }
}

extension DaemonAPI {
    public func rules() async throws(DaemonRequestError) -> [SavedRule] {
        try await get("/api/rules", query: [])
    }

    public func ruleActivity(days: Int) async throws(DaemonRequestError) -> RuleActivity {
        try await get("/api/rules/usage", query: [URLQueryItem(name: "days", value: String(days))])
    }

    public func draftRule(_ sentence: String) async throws(DaemonRequestError) -> DraftRule {
        try await send("POST", "/api/rules/draft", body: Self.json(RuleDraftBody(sentence: sentence)))
    }

    public func previewRule(_ draft: DraftRule, answer: PhoneRuleAnswer, days: Int) async throws(DaemonRequestError) -> RuleTryout {
        let body = RuleTryoutBody(behavior: answer, toolGlob: draft.toolGlob, matchRegex: draft.matchRegex, days: days)
        return try await send("POST", "/api/rules/preview", body: Self.json(body))
    }

    public func addRule(_ draft: DraftRule, answer: PhoneRuleAnswer) async throws(DaemonRequestError) -> SavedRule {
        let body = AddRuleBody(behavior: answer, toolGlob: draft.toolGlob, matchRegex: draft.matchRegex, description: draft.description)
        return try await send("POST", "/api/rules", body: Self.json(body))
    }
}
