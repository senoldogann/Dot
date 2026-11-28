import Foundation

// The manager review of the team (item 2068), as `GET /api/team-review` gives it (`TeamReviewView`,
// `agent/team_review.py`): who reviews and how often, the newest review's findings and its proposals, each a card the
// owner accepts or skips. Nothing changes until the owner accepts one.

/// One thing a review found (`TeamFinding`). `count` and `limit` by kind: a stuck order's, an idle bot's and an
/// objective's days; a failing bot's failed runs and its runs. `subject` is "" for a title read from outside content.
public struct TeamFinding: Codable, Sendable, Hashable {
    public let kind: String
    public let agentId: String
    public let agentName: String
    public let subjectId: String
    public let subject: String
    public let count: Int
    public let limit: Int
    public let reasons: [OverloadReason]

    enum CodingKeys: String, CodingKey {
        case kind, subject, count, limit, reasons
        case agentId = "agent_id"
        case agentName = "agent_name"
        case subjectId = "subject_id"
    }
}

/// A bot a split makes (`SplitPart`).
public struct SplitPart: Codable, Sendable, Hashable {
    public let name: String
    public let role: String
    public let routineIds: [String]
    public let routineNames: [String]

    enum CodingKeys: String, CodingKey {
        case name, role
        case routineIds = "routine_ids"
        case routineNames = "routine_names"
    }
}

/// A change the manager proposes (`TeamProposal`): `move_orders`, `split_bot` or `retire_routine`; `status` is
/// `waiting`, `accepted` or `skipped`.
public struct TeamProposal: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let kind: String
    public let finding: Int
    public let why: String
    public let agentId: String
    public let agentName: String
    public let orderIds: [String]
    public let orderTitles: [String]
    public let toAgent: String
    public let toName: String
    public let parts: [SplitPart]
    public let routineId: String
    public let routineName: String
    public let status: String
    public let decidedAt: Double?

    enum CodingKeys: String, CodingKey {
        case id, kind, finding, why, parts, status
        case agentId = "agent_id"
        case agentName = "agent_name"
        case orderIds = "order_ids"
        case orderTitles = "order_titles"
        case toAgent = "to_agent"
        case toName = "to_name"
        case routineId = "routine_id"
        case routineName = "routine_name"
        case decidedAt = "decided_at"
    }
}

/// One review (`TeamReview`): when, by which manager, what it found, the manager's summary and its proposals.
public struct TeamReview: Codable, Sendable, Hashable {
    public let at: Double
    public let managerId: String
    public let managerName: String
    public let findings: [TeamFinding]
    public let summary: String
    public let proposals: [TeamProposal]

    enum CodingKeys: String, CodingKey {
        case at, findings, summary, proposals
        case managerId = "manager_id"
        case managerName = "manager_name"
    }
}

/// The manager review as its page shows it (`TeamReviewView`): `every` is `off`, `daily` or `weekly`.
public struct TeamReviewView: Codable, Sendable, Hashable {
    public let manager: String
    public let every: String
    public let lastAt: Double?
    public let nextAt: Double?
    public let running: Bool
    public let review: TeamReview?
    public let error: String

    enum CodingKeys: String, CodingKey {
        case manager, every, running, review, error
        case lastAt = "last_at"
        case nextAt = "next_at"
    }
}

/// The body of `PUT /api/team-review` (`TeamReviewSettings`).
public struct TeamReviewSettings: Codable, Sendable, Hashable {
    public let manager: String
    public let every: String

    public init(manager: String, every: String) {
        self.manager = manager
        self.every = every
    }
}
