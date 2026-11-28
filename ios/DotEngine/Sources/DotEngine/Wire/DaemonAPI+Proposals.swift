import Foundation

// What Dot proposes besides Self-improvement's habits, for Activity's "Dot's proposals" (PLAN §26: its proposals reach
// the owner through Activity): a merge or rewrite of the skills it learned (item 585, `routes_system.py`) and a plugin
// the nightly scout found (item 712, `routes_gallery.py`). The owner answers each; the Skills and Plugins pages are the
// Mac's.

/// A learned skill as a proposal would write it (`PlaybookDraft`): the kind of work, what it is for and its steps.
public struct SkillDraft: Codable, Sendable, Hashable {
    public let kind: String
    public let description: String
    public let steps: [String]
}

/// A change to learned skills that waits for the owner's yes (`SkillProposal`): `kind` is `merge` or `rewrite`, `names`
/// the skills it is made from (the one that stays first), `tainted` whether what it writes may carry content third
/// parties can author.
public struct SkillProposal: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let kind: String
    public let names: [String]
    public let draft: SkillDraft
    public let reason: String
    public let tainted: Bool
}

/// A proposal as the daemon shows it (`ProposalView`): `stale` says why it cannot be applied now, `nil` while it can.
public struct SkillProposalView: Codable, Sendable, Hashable, Identifiable {
    public let proposal: SkillProposal
    public let stale: String?

    public var id: String { proposal.id }
}

/// `GET /api/skills/proposals`.
public struct SkillProposalsView: Codable, Sendable, Hashable {
    public let items: [SkillProposalView]
}

/// A plugin the scout proposes (`ScoutCandidateView`): new or an update (`kind`), who publishes it and how far that is
/// trusted (`tier`), what it does, why it fits, what its skills need, what the review made of it (`risk`, `nil` before
/// one) and its skills.
public struct PluginProposal: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let kind: String
    public let name: String
    public let publisher: String
    public let tier: String
    public let does: String
    public let why: String
    public let needs: String
    public let risk: String?
    public let skills: [String]

    /// One per plugin and kind: an update of a plugin may wait beside nothing else of its name.
    public var key: String { "\(kind):\(id)" }
}

/// `GET /api/plugin-scout`: the proposals waiting for the owner, newest first.
public struct ScoutProposals: Codable, Sendable, Hashable {
    public let proposals: [PluginProposal]
}

extension DaemonAPI {
    /// The skill changes that wait for the owner.
    public func skillProposals() async throws(DaemonRequestError) -> SkillProposalsView {
        try await get("/api/skills/proposals", query: [])
    }

    /// The owner's yes: the draft is written as the skill's new version (409 when its skills changed since).
    public func acceptSkillProposal(_ id: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/skills/proposals/\(id)/accept", body: nil)
    }

    /// The owner's no: the proposal is dropped.
    public func dismissSkillProposal(_ id: String) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/skills/proposals/\(id)", body: nil)
    }

    /// The scout's proposals waiting for the owner.
    public func pluginProposals() async throws(DaemonRequestError) -> ScoutProposals {
        try await get("/api/plugin-scout", query: [])
    }

    /// Installs the proposed plugin, or takes the proposed update, at the commit the scout checked.
    public func installPluginProposal(_ proposal: PluginProposal) async throws(DaemonRequestError) {
        try await scoutDecision(proposal, "install")
    }

    /// Skips the proposal: the scout does not propose it again.
    public func skipPluginProposal(_ proposal: PluginProposal) async throws(DaemonRequestError) {
        try await scoutDecision(proposal, "skip")
    }

    private func scoutDecision(_ proposal: PluginProposal, _ decision: String) async throws(DaemonRequestError) {
        let path: String = "/api/plugin-scout/\(proposal.id)/\(decision)"
        _ = try await data(request(method: "POST", path: path, query: [URLQueryItem(name: "kind", value: proposal.kind)], body: nil), path: path)
    }
}
